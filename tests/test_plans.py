from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.agents.schemas import ContentIdea, ContentOutput, CriticOutput, StrategyOutput
from app.models.action_log import ActionLog

BRAND_PAYLOAD = {
    "name": "Acme",
    "audience": "SMBs",
    "tone": "professional",
    "language": "en",
}

SEVEN_IDEAS = StrategyOutput(
    ideas=[
        ContentIdea(day=i + 1, theme=f"Theme {i+1}", format="post", angle=f"Angle {i+1}")
        for i in range(7)
    ]
)

CONTENT = ContentOutput(
    content="Great post content", hashtags=["#test"], suggested_time="09:00"
)

APPROVED = CriticOutput(approved=True, issues=[])


def _make_mock_agents():
    strategy = MagicMock()
    strategy.generate = AsyncMock(return_value=SEVEN_IDEAS)
    content = MagicMock()
    content.write = AsyncMock(return_value=CONTENT)
    critic = MagicMock()
    critic.review = AsyncMock(return_value=APPROVED)
    return strategy, content, critic


@pytest.mark.asyncio
async def test_generate_and_poll_plan(test_client, db_session):
    from tests.conftest import _TestSessionLocal

    # Create workspace + brand
    ws = await test_client.post("/api/workspaces", json={"name": "Plan WS"})
    workspace_id = ws.json()["id"]
    await test_client.put(f"/api/workspaces/{workspace_id}/brand", json=BRAND_PAYLOAD)

    strategy, content, critic = _make_mock_agents()

    with (
        patch("app.agents.graph._strategy_agent", strategy),
        patch("app.agents.graph._content_agent", content),
        patch("app.agents.graph._critic_agent", critic),
    ):
        # POST plans:generate — inject test session factory so background task
        # writes to the same DB instance the test assertions read from.
        with patch("app.routers.plans.run_generation"):
            resp = await test_client.post(
                f"/api/workspaces/{workspace_id}/plans:generate",
                json={"goal": "launch product"},
            )
            assert resp.status_code == 202
            plan_id = resp.json()["id"]
            assert resp.json()["status"] == "generating"

            # Run the background task synchronously with the test session factory
            from app.services.generation import run_generation as real_run_gen
            brand_dict = {
                "name": "Acme", "audience": "SMBs",
                "tone": "professional", "language": "en",
                "avoid": [], "extra": {},
            }
            await real_run_gen(
                plan_id=plan_id,
                workspace_id=workspace_id,
                brand_profile=brand_dict,
                goal="launch product",
                session_factory=_TestSessionLocal,
            )

    # GET plan — should be ready with 7 posts
    resp2 = await test_client.get(f"/api/workspaces/{workspace_id}/plans/{plan_id}")
    assert resp2.status_code == 200
    body = resp2.json()
    assert body["status"] == "ready"
    assert len(body["posts"]) == 7
    for post in body["posts"]:
        assert post["status"] == "pending_approval"

    # Confirm 15 action_log entries (1 strategy + 7 content + 7 critic)
    result = await db_session.execute(
        select(ActionLog).where(ActionLog.workspace_id == workspace_id)
    )
    logs = result.scalars().all()
    # workspace created log + brand log + 15 generation logs = 17 total
    generation_logs = [entry for entry in logs if entry.actor in ("strategy_node", "content_node", "critic_node")]
    assert len(generation_logs) == 15


@pytest.mark.asyncio
async def test_get_plan_not_found(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "Empty Plan WS"})
    workspace_id = ws.json()["id"]
    resp = await test_client.get(f"/api/workspaces/{workspace_id}/plans/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_generate_plan_no_brand(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "No Brand WS"})
    workspace_id = ws.json()["id"]
    resp = await test_client.post(
        f"/api/workspaces/{workspace_id}/plans:generate", json={}
    )
    assert resp.status_code == 422
