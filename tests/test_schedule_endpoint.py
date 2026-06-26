from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.clients.postiz import PostizRateLimitError
from app.models.content_plan import ContentPlan
from app.models.enums import PlanStatus, PostStatus
from app.models.post import Post

SCHEDULE_BODY = {
    "integration_id": "integ-1",
    "provider": "twitter",
    "when": "2026-07-01T09:00:00Z",
}


def _mock_postiz_client(postiz_id: str = "pz-abc") -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.schedule_post = AsyncMock(return_value={"id": postiz_id})
    return client


async def _seed_post(workspace_id: str, status: str = "approved") -> str:
    from tests.conftest import _TestSessionLocal

    async with _TestSessionLocal() as db:
        plan = ContentPlan(
            workspace_id=workspace_id,
            goal="test",
            status=PlanStatus.ready.value,
        )
        db.add(plan)
        await db.flush()

        post = Post(
            plan_id=plan.id,
            workspace_id=workspace_id,
            day=1,
            theme="T",
            format="post",
            angle="A",
            content="Content",
            hashtags=["#t"],
            suggested_time="09:00",
            status=status,
        )
        db.add(post)
        await db.commit()
        await db.refresh(post)
        return post.id


@pytest.mark.asyncio
async def test_schedule_approved_post(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "Sched WS"})
    workspace_id = ws.json()["id"]
    post_id = await _seed_post(workspace_id)

    with patch("app.services.publishing.PostizClient", return_value=_mock_postiz_client()):
        resp = await test_client.post(f"/api/posts/{post_id}:schedule", json=SCHEDULE_BODY)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "scheduled"


@pytest.mark.asyncio
async def test_schedule_unapproved_post_returns_409(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "Sched 409 WS"})
    workspace_id = ws.json()["id"]
    post_id = await _seed_post(workspace_id, status="pending_approval")

    resp = await test_client.post(f"/api/posts/{post_id}:schedule", json=SCHEDULE_BODY)
    assert resp.status_code == 409
    assert "approved" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_schedule_already_scheduled_returns_409(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "Dup Sched WS"})
    workspace_id = ws.json()["id"]
    post_id = await _seed_post(workspace_id)

    mock = _mock_postiz_client()
    with patch("app.services.publishing.PostizClient", return_value=mock):
        r1 = await test_client.post(f"/api/posts/{post_id}:schedule", json=SCHEDULE_BODY)
    assert r1.status_code == 200

    with patch("app.services.publishing.PostizClient", return_value=mock):
        r2 = await test_client.post(f"/api/posts/{post_id}:schedule", json=SCHEDULE_BODY)
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_schedule_rate_limit_leaves_post_approved(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "RL Sched WS"})
    workspace_id = ws.json()["id"]
    post_id = await _seed_post(workspace_id)

    rl_client = MagicMock()
    rl_client.__aenter__ = AsyncMock(return_value=rl_client)
    rl_client.__aexit__ = AsyncMock(return_value=None)
    rl_client.schedule_post = AsyncMock(side_effect=PostizRateLimitError("429", 429))

    with patch("app.services.publishing.PostizClient", return_value=rl_client):
        resp = await test_client.post(f"/api/posts/{post_id}:schedule", json=SCHEDULE_BODY)

    assert resp.status_code == 503

    from tests.conftest import _TestSessionLocal

    async with _TestSessionLocal() as db:
        result = await db.execute(select(Post).where(Post.id == post_id))
        post = result.scalar_one()
        assert post.status == PostStatus.approved.value


@pytest.mark.asyncio
async def test_schedule_invalid_datetime_returns_422(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "Bad DT WS"})
    workspace_id = ws.json()["id"]
    post_id = await _seed_post(workspace_id)

    resp = await test_client.post(
        f"/api/posts/{post_id}:schedule",
        json={"integration_id": "i", "provider": "x", "when": "not-a-date"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_schedule_nonexistent_post_returns_404(test_client):
    resp = await test_client.post("/api/posts/no-such-id:schedule", json=SCHEDULE_BODY)
    assert resp.status_code == 404
