import pytest
from sqlalchemy import select

from app.models.action_log import ActionLog
from app.models.content_plan import ContentPlan
from app.models.enums import PlanStatus
from app.models.post import Post


async def _seed_post(workspace_id: str, status: str = "pending_approval") -> str:
    """Insert a ContentPlan + Post directly, bypassing the generation pipeline."""
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
            theme="Test theme",
            format="post",
            angle="Test angle",
            content="Test content",
            hashtags=["#test"],
            suggested_time="09:00",
            status=status,
        )
        db.add(post)
        await db.commit()
        await db.refresh(post)
        return post.id


@pytest.mark.asyncio
async def test_approve_post(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "Approve WS"})
    workspace_id = ws.json()["id"]
    post_id = await _seed_post(workspace_id)

    resp = await test_client.post(f"/api/posts/{post_id}:approve")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["id"] == post_id


@pytest.mark.asyncio
async def test_reject_post_no_reason(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "Reject WS"})
    workspace_id = ws.json()["id"]
    post_id = await _seed_post(workspace_id)

    resp = await test_client.post(f"/api/posts/{post_id}:reject")
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


@pytest.mark.asyncio
async def test_reject_post_with_reason(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "Reject Reason WS"})
    workspace_id = ws.json()["id"]
    post_id = await _seed_post(workspace_id)

    resp = await test_client.post(
        f"/api/posts/{post_id}:reject", json={"reason": "Tone is off-brand"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


@pytest.mark.asyncio
async def test_approve_already_rejected_returns_409(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "409 WS"})
    workspace_id = ws.json()["id"]
    post_id = await _seed_post(workspace_id, status="rejected")

    resp = await test_client.post(f"/api/posts/{post_id}:approve")
    assert resp.status_code == 409
    assert "rejected" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_reject_draft_returns_409(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "Draft 409 WS"})
    workspace_id = ws.json()["id"]
    post_id = await _seed_post(workspace_id, status="draft")

    resp = await test_client.post(f"/api/posts/{post_id}:reject")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_approve_logs_action(test_client, db_session):
    ws = await test_client.post("/api/workspaces", json={"name": "Log WS"})
    workspace_id = ws.json()["id"]
    post_id = await _seed_post(workspace_id)

    await test_client.post(f"/api/posts/{post_id}:approve")

    result = await db_session.execute(
        select(ActionLog).where(
            ActionLog.workspace_id == workspace_id,
            ActionLog.action == "approve_post",
        )
    )
    log = result.scalar_one_or_none()
    assert log is not None
    assert log.actor == "api"
    assert log.payload["post_id"] == post_id


@pytest.mark.asyncio
async def test_reject_logs_action_with_reason(test_client, db_session):
    ws = await test_client.post("/api/workspaces", json={"name": "Log Reject WS"})
    workspace_id = ws.json()["id"]
    post_id = await _seed_post(workspace_id)

    await test_client.post(
        f"/api/posts/{post_id}:reject", json={"reason": "needs work"}
    )

    result = await db_session.execute(
        select(ActionLog).where(
            ActionLog.workspace_id == workspace_id,
            ActionLog.action == "reject_post",
        )
    )
    log = result.scalar_one_or_none()
    assert log is not None
    assert log.payload["reason"] == "needs work"


@pytest.mark.asyncio
async def test_approve_nonexistent_post_returns_404(test_client):
    resp = await test_client.post("/api/posts/no-such-id:approve")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_approve_then_reject_sequence(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "Sequence WS"})
    workspace_id = ws.json()["id"]
    post_id = await _seed_post(workspace_id)

    # pending_approval → approved
    r1 = await test_client.post(f"/api/posts/{post_id}:approve")
    assert r1.status_code == 200
    assert r1.json()["status"] == "approved"

    # approved → rejected (legal)
    r2 = await test_client.post(f"/api/posts/{post_id}:reject")
    assert r2.status_code == 200
    assert r2.json()["status"] == "rejected"

    # rejected → approved (illegal)
    r3 = await test_client.post(f"/api/posts/{post_id}:approve")
    assert r3.status_code == 409
