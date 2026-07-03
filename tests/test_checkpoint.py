"""Tests for Phase 3 durable generation state: init_graph, idempotency guard, recovery."""
import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import select

from app.agents import graph as _graph_mod
from app.models.content_plan import ContentPlan
from app.models.enums import PlanStatus, PostStatus
from app.models.post import Post
from app.models.workspace import Workspace


@pytest.fixture(autouse=True)
def restore_graph():
    """Restore the module-level generation_graph after each test."""
    original = _graph_mod.generation_graph
    yield
    _graph_mod.generation_graph = original


def _finished_posts(n=7):
    return [
        {
            "day": i + 1, "theme": f"Theme {i}", "format": "post",
            "angle": f"Angle {i}", "content": "content", "hashtags": [], "suggested_time": "",
        }
        for i in range(n)
    ]


# ── Graph wiring ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_init_graph_replaces_module_graph():
    from app.agents.graph import init_graph

    original = _graph_mod.generation_graph
    # Must pass a real BaseCheckpointSaver instance — LangGraph 1.2.6 validates the type
    # in ensure_valid_checkpointer; a plain MagicMock raises TypeError.
    init_graph(MemorySaver())

    assert _graph_mod.generation_graph is not original


@pytest.mark.asyncio
async def test_generation_uses_module_ref():
    """run_generation must call _graph_mod.generation_graph.ainvoke at call-time, not a stale import."""
    from tests.conftest import _TestSessionLocal
    from app.services.generation import run_generation

    ws_id = str(uuid.uuid4())
    plan_id = str(uuid.uuid4())

    async with _TestSessionLocal() as db:
        db.add(Workspace(id=ws_id, name="ModRef WS"))
        await db.flush()
        db.add(ContentPlan(
            id=plan_id, workspace_id=ws_id, goal="test", status=PlanStatus.generating.value
        ))
        await db.commit()

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(
        return_value={"finished_posts": _finished_posts(), "action_logs": []}
    )

    with patch.object(_graph_mod, "generation_graph", mock_graph):
        await run_generation(plan_id, ws_id, {}, None, _TestSessionLocal)

    mock_graph.ainvoke.assert_awaited_once()


# ── Idempotency guard ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_idempotency_guard_skips_duplicate_insert():
    """If posts already exist for the plan, run_generation must not insert duplicates."""
    from tests.conftest import _TestSessionLocal
    from app.services.generation import run_generation

    ws_id = str(uuid.uuid4())
    plan_id = str(uuid.uuid4())

    async with _TestSessionLocal() as db:
        db.add(Workspace(id=ws_id, name="Idempotency WS"))
        await db.flush()
        db.add(ContentPlan(
            id=plan_id, workspace_id=ws_id, goal="test", status=PlanStatus.generating.value
        ))
        await db.flush()
        for i in range(7):
            db.add(Post(
                plan_id=plan_id, workspace_id=ws_id,
                day=i + 1, theme=f"T{i}", format="post",
                angle="A", content="c", hashtags=[], suggested_time="",
                status=PostStatus.pending_approval.value,
            ))
        await db.commit()

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(
        return_value={"finished_posts": _finished_posts(), "action_logs": []}
    )

    with patch.object(_graph_mod, "generation_graph", mock_graph):
        await run_generation(plan_id, ws_id, {}, None, _TestSessionLocal)

    async with _TestSessionLocal() as db:
        result = await db.execute(select(Post).where(Post.plan_id == plan_id))
        assert len(result.scalars().all()) == 7

    async with _TestSessionLocal() as db:
        result = await db.execute(select(ContentPlan).where(ContentPlan.id == plan_id))
        assert result.scalar_one().status == PlanStatus.ready.value


# ── Recovery ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_recover_no_checkpoint_marks_failed():
    from tests.conftest import _TestSessionLocal
    from app.services.recovery import recover_stuck_plans

    ws_id = str(uuid.uuid4())
    plan_id = str(uuid.uuid4())

    async with _TestSessionLocal() as db:
        db.add(Workspace(id=ws_id, name="Recovery WS 1"))
        await db.flush()
        db.add(ContentPlan(
            id=plan_id, workspace_id=ws_id, goal="test", status=PlanStatus.generating.value
        ))
        await db.commit()

    mock_cp = AsyncMock()
    mock_cp.aget_tuple = AsyncMock(return_value=None)

    await recover_stuck_plans(mock_cp, _TestSessionLocal)

    async with _TestSessionLocal() as db:
        result = await db.execute(select(ContentPlan).where(ContentPlan.id == plan_id))
        assert result.scalar_one().status == PlanStatus.failed.value


@pytest.mark.asyncio
async def test_recover_with_existing_posts_marks_ready():
    """Checkpoint exists + posts already inserted → mark ready, do NOT re-fire generation."""
    from tests.conftest import _TestSessionLocal
    from app.services.recovery import recover_stuck_plans

    ws_id = str(uuid.uuid4())
    plan_id = str(uuid.uuid4())

    async with _TestSessionLocal() as db:
        db.add(Workspace(id=ws_id, name="Recovery WS 2"))
        await db.flush()
        db.add(ContentPlan(
            id=plan_id, workspace_id=ws_id, goal="test", status=PlanStatus.generating.value
        ))
        await db.flush()
        db.add(Post(
            plan_id=plan_id, workspace_id=ws_id,
            day=1, theme="T", format="post", angle="A",
            content="c", hashtags=[], suggested_time="",
            status=PostStatus.pending_approval.value,
        ))
        await db.commit()

    mock_cp = AsyncMock()
    mock_cp.aget_tuple = AsyncMock(return_value=MagicMock())  # truthy — checkpoint exists

    with patch("app.services.generation.run_generation", new_callable=AsyncMock) as mock_run:
        await recover_stuck_plans(mock_cp, _TestSessionLocal)

    async with _TestSessionLocal() as db:
        result = await db.execute(select(ContentPlan).where(ContentPlan.id == plan_id))
        assert result.scalar_one().status == PlanStatus.ready.value
    mock_run.assert_not_called()


@pytest.mark.asyncio
async def test_recover_with_checkpoint_fires_generation():
    """Checkpoint exists + no posts → re-fire run_generation for the stuck plan."""
    from tests.conftest import _TestSessionLocal
    from app.services.recovery import recover_stuck_plans

    ws_id = str(uuid.uuid4())
    plan_id = str(uuid.uuid4())

    async with _TestSessionLocal() as db:
        db.add(Workspace(id=ws_id, name="Recovery WS 3"))
        await db.flush()
        db.add(ContentPlan(
            id=plan_id, workspace_id=ws_id, goal="test goal", status=PlanStatus.generating.value
        ))
        await db.commit()

    mock_cp = AsyncMock()
    mock_cp.aget_tuple = AsyncMock(return_value=MagicMock())  # truthy — checkpoint exists

    with patch("app.services.generation.run_generation", new_callable=AsyncMock) as mock_run:
        await recover_stuck_plans(mock_cp, _TestSessionLocal)
        await asyncio.sleep(0)  # allow the scheduled asyncio.create_task to run

        mock_run.assert_called_once()
        assert mock_run.call_args.args[0] == plan_id
        assert mock_run.call_args.args[1] == ws_id
