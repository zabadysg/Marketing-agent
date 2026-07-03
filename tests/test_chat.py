"""Tests for Phase 2 chat API."""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.brand_profile import BrandProfile
from app.models.chat import ChatMessage, ChatSession, MessageRole
from app.models.content_plan import ContentPlan
from app.models.enums import OnboardingStatus, PlanStatus, PostStatus
from app.models.post import Post


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _seed_workspace(test_client, name: str) -> str:
    resp = await test_client.post("/api/workspaces", json={"name": name})
    assert resp.status_code == 201
    return resp.json()["id"]


async def _seed_brand(workspace_id: str) -> None:
    from tests.conftest import _TestSessionLocal

    async with _TestSessionLocal() as db:
        bp = BrandProfile(
            workspace_id=workspace_id,
            brand_name="TestBrand",
            tone="friendly",
            onboarding_status=OnboardingStatus.active.value,
        )
        db.add(bp)
        await db.commit()


async def _seed_session(workspace_id: str) -> str:
    from tests.conftest import _TestSessionLocal

    async with _TestSessionLocal() as db:
        session = ChatSession(workspace_id=workspace_id)
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session.id


# ── Session CRUD ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_session(test_client):
    ws_id = await _seed_workspace(test_client, "Chat WS 1")

    resp = await test_client.post(f"/api/workspaces/{ws_id}/chat/sessions", json={})
    assert resp.status_code == 201
    body = resp.json()
    assert body["workspace_id"] == ws_id
    assert "id" in body


@pytest.mark.asyncio
async def test_list_sessions(test_client):
    ws_id = await _seed_workspace(test_client, "Chat WS 2")
    await _seed_session(ws_id)
    await _seed_session(ws_id)

    resp = await test_client.get(f"/api/workspaces/{ws_id}/chat/sessions")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_get_session_includes_messages(test_client):
    from tests.conftest import _TestSessionLocal

    ws_id = await _seed_workspace(test_client, "Chat WS 3")
    session_id = await _seed_session(ws_id)

    async with _TestSessionLocal() as db:
        msg = ChatMessage(
            id=str(uuid.uuid4()),
            session_id=session_id,
            workspace_id=ws_id,
            role=MessageRole.user.value,
            content="Hello",
            metadata_={},
        )
        db.add(msg)
        await db.commit()

    resp = await test_client.get(f"/api/workspaces/{ws_id}/chat/sessions/{session_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["messages"]) == 1
    assert body["messages"][0]["content"] == "Hello"


@pytest.mark.asyncio
async def test_delete_session_cascades(test_client):
    from tests.conftest import _TestSessionLocal

    ws_id = await _seed_workspace(test_client, "Chat WS 4")
    session_id = await _seed_session(ws_id)

    async with _TestSessionLocal() as db:
        msg = ChatMessage(
            id=str(uuid.uuid4()),
            session_id=session_id,
            workspace_id=ws_id,
            role=MessageRole.user.value,
            content="Bye",
            metadata_={},
        )
        db.add(msg)
        await db.commit()

    resp = await test_client.delete(f"/api/workspaces/{ws_id}/chat/sessions/{session_id}")
    assert resp.status_code == 204

    # Verify messages gone via DB
    async with _TestSessionLocal() as db:
        result = await db.execute(
            select(ChatMessage).where(ChatMessage.session_id == session_id)
        )
        assert result.scalars().all() == []


# ── Send message ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_message_202(test_client):
    ws_id = await _seed_workspace(test_client, "Chat WS 5")
    await _seed_brand(ws_id)
    session_id = await _seed_session(ws_id)

    with (
        patch("app.routers.chat.search_knowledge", new_callable=AsyncMock, return_value=[]),
        patch("app.agents.chat_agent.run_chat_agent"),  # background task — don't run
    ):
        resp = await test_client.post(
            f"/api/workspaces/{ws_id}/chat/sessions/{session_id}/messages",
            json={"content": "What is our tone?"},
        )
    assert resp.status_code == 202
    assert "message_id" in resp.json()


@pytest.mark.asyncio
async def test_send_message_saves_user_msg(test_client):
    from tests.conftest import _TestSessionLocal

    ws_id = await _seed_workspace(test_client, "Chat WS 6")
    await _seed_brand(ws_id)
    session_id = await _seed_session(ws_id)

    with (
        patch("app.routers.chat.search_knowledge", new_callable=AsyncMock, return_value=[]),
        patch("app.agents.chat_agent.run_chat_agent"),
    ):
        await test_client.post(
            f"/api/workspaces/{ws_id}/chat/sessions/{session_id}/messages",
            json={"content": "Tell me about our brand."},
        )

    async with _TestSessionLocal() as db:
        result = await db.execute(
            select(ChatMessage).where(
                ChatMessage.session_id == session_id,
                ChatMessage.role == MessageRole.user.value,
            )
        )
        msgs = result.scalars().all()
    assert len(msgs) == 1
    assert msgs[0].content == "Tell me about our brand."


@pytest.mark.asyncio
async def test_send_message_404_missing_workspace(test_client):
    fake_ws = str(uuid.uuid4())
    fake_session = str(uuid.uuid4())

    resp = await test_client.post(
        f"/api/workspaces/{fake_ws}/chat/sessions/{fake_session}/messages",
        json={"content": "Hello"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_send_message_no_brand_profile(test_client):
    ws_id = await _seed_workspace(test_client, "Chat WS 7")
    session_id = await _seed_session(ws_id)

    resp = await test_client.post(
        f"/api/workspaces/{ws_id}/chat/sessions/{session_id}/messages",
        json={"content": "Hello"},
    )
    assert resp.status_code == 400
    assert "brand profile" in resp.json()["detail"].lower()


# ── SSE stream ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stream_returns_event_stream(test_client):
    ws_id = await _seed_workspace(test_client, "Chat WS 8")
    session_id = await _seed_session(ws_id)

    resp = await test_client.get(
        f"/api/workspaces/{ws_id}/chat/sessions/{session_id}/stream"
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]


# ── Title auto-set ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_title_auto_set(test_client):
    from tests.conftest import _TestSessionLocal

    ws_id = await _seed_workspace(test_client, "Chat WS 9")
    await _seed_brand(ws_id)
    session_id = await _seed_session(ws_id)

    with (
        patch("app.routers.chat.search_knowledge", new_callable=AsyncMock, return_value=[]),
        patch("app.agents.chat_agent.run_chat_agent"),
    ):
        await test_client.post(
            f"/api/workspaces/{ws_id}/chat/sessions/{session_id}/messages",
            json={"content": "What is the brand mission?"},
        )

    async with _TestSessionLocal() as db:
        result = await db.execute(
            select(ChatSession).where(ChatSession.id == session_id)
        )
        session = result.scalar_one()
    assert session.title == "What is the brand mission?"


# ── Tools ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_draft_post_tool():
    from tests.conftest import _TestSessionLocal
    from app.agents.chat_tools import make_chat_tools

    ws_id = str(uuid.uuid4())

    async with _TestSessionLocal() as db:
        from app.models.workspace import Workspace
        ws = Workspace(id=ws_id, name="Tool Test WS")
        db.add(ws)
        await db.commit()

    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
    from tests.conftest import _test_engine
    factory = async_sessionmaker(_test_engine, class_=AsyncSession, expire_on_commit=False)

    tools, tool_map = make_chat_tools(ws_id, {"brand_name": "Test"}, factory)
    result = await tool_map["create_draft_post"].ainvoke({
        "content": "Hello LinkedIn!",
        "hashtags": ["#test"],
        "suggested_time": "10:00",
        "theme": "Product launch",
    })

    assert "post_id" in result
    assert "preview" in result

    async with _TestSessionLocal() as db:
        r = await db.execute(select(Post).where(Post.id == result["post_id"]))
        post = r.scalar_one()

    assert post.status == PostStatus.draft.value
    assert post.day == 0
    assert post.angle == "Chat draft"


@pytest.mark.asyncio
async def test_trigger_plan_creates_content_plan():
    from tests.conftest import _TestSessionLocal
    from app.agents.chat_tools import make_chat_tools

    ws_id = str(uuid.uuid4())

    async with _TestSessionLocal() as db:
        from app.models.workspace import Workspace
        ws = Workspace(id=ws_id, name="Tool Test WS 2")
        db.add(ws)
        await db.commit()

    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
    from tests.conftest import _test_engine
    factory = async_sessionmaker(_test_engine, class_=AsyncSession, expire_on_commit=False)

    tools, tool_map = make_chat_tools(ws_id, {"brand_name": "Test"}, factory)

    with patch("app.services.generation.run_generation", new_callable=AsyncMock):
        result = await tool_map["trigger_plan_generation"].ainvoke({"goal": "SMB growth"})

    assert "Plan ID:" in result
    plan_id = result.split("Plan ID: ")[-1].strip()

    async with _TestSessionLocal() as db:
        r = await db.execute(select(ContentPlan).where(ContentPlan.id == plan_id))
        plan = r.scalar_one()

    assert plan.status == PlanStatus.generating.value
    assert plan.goal == "SMB growth"


# ── :submit endpoint ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_draft_to_pending(test_client):
    from tests.conftest import _TestSessionLocal

    ws_id = await _seed_workspace(test_client, "Submit WS")

    async with _TestSessionLocal() as db:
        plan = ContentPlan(
            workspace_id=ws_id, goal="__chat_drafts__", status=PlanStatus.ready.value
        )
        db.add(plan)
        await db.flush()
        post = Post(
            plan_id=plan.id,
            workspace_id=ws_id,
            day=0,
            theme="Product",
            format="post",
            angle="Chat draft",
            content="Draft content",
            hashtags=[],
            suggested_time="",
            status=PostStatus.draft.value,
        )
        db.add(post)
        await db.commit()
        await db.refresh(post)
        post_id = post.id

    resp = await test_client.post(f"/api/posts/{post_id}:submit")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending_approval"
