import pytest
from sqlalchemy import select

from app.models.action_log import ActionLog
from app.models.content_plan import ContentPlan
from app.models.enums import PlanStatus
from app.models.post import Post


async def _seed_post(workspace_id: str, status: str = "pending_approval") -> str:
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
            theme="Theme",
            format="post",
            angle="Angle",
            content="Original content",
            hashtags=["#original"],
            suggested_time="09:00",
            status=status,
        )
        db.add(post)
        await db.commit()
        await db.refresh(post)
        return post.id


@pytest.mark.asyncio
async def test_edit_content(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "Edit WS"})
    workspace_id = ws.json()["id"]
    post_id = await _seed_post(workspace_id)

    resp = await test_client.patch(
        f"/api/posts/{post_id}", json={"content": "Updated content"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == "Updated content"
    assert body["hashtags"] == ["#original"]  # unchanged


@pytest.mark.asyncio
async def test_edit_hashtags_and_time(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "Edit Hash WS"})
    workspace_id = ws.json()["id"]
    post_id = await _seed_post(workspace_id)

    resp = await test_client.patch(
        f"/api/posts/{post_id}",
        json={"hashtags": ["#new", "#tags"], "suggested_time": "14:00"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["hashtags"] == ["#new", "#tags"]
    assert body["suggested_time"] == "14:00"
    assert body["content"] == "Original content"  # unchanged


@pytest.mark.asyncio
async def test_edit_approved_resets_to_pending_approval(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "Edit Approved WS"})
    workspace_id = ws.json()["id"]
    post_id = await _seed_post(workspace_id, status="approved")

    resp = await test_client.patch(
        f"/api/posts/{post_id}", json={"content": "Revised content"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending_approval"


@pytest.mark.asyncio
async def test_edit_pending_approval_keeps_status(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "Edit PA WS"})
    workspace_id = ws.json()["id"]
    post_id = await _seed_post(workspace_id, status="pending_approval")

    resp = await test_client.patch(
        f"/api/posts/{post_id}", json={"content": "New content"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending_approval"


@pytest.mark.asyncio
async def test_edit_logs_action(test_client, db_session):
    ws = await test_client.post("/api/workspaces", json={"name": "Edit Log WS"})
    workspace_id = ws.json()["id"]
    post_id = await _seed_post(workspace_id)

    await test_client.patch(f"/api/posts/{post_id}", json={"content": "Logged"})

    result = await db_session.execute(
        select(ActionLog).where(
            ActionLog.workspace_id == workspace_id,
            ActionLog.action == "edit_post",
        )
    )
    log = result.scalar_one_or_none()
    assert log is not None
    assert log.payload["changes"]["content"] == "Logged"


@pytest.mark.asyncio
async def test_edit_nonexistent_returns_404(test_client):
    resp = await test_client.patch("/api/posts/no-such-id", json={"content": "x"})
    assert resp.status_code == 404
