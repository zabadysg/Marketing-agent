import pytest
from sqlalchemy import select

from app.models.action_log import ActionLog


@pytest.mark.asyncio
async def test_create_and_get_workspace(test_client, db_session):
    # Create
    resp = await test_client.post(
        "/api/workspaces", json={"name": "Test Workspace"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Test Workspace"
    assert body["autonomy_level"] == "supervised"
    workspace_id = body["id"]

    # Get
    resp2 = await test_client.get(f"/api/workspaces/{workspace_id}")
    assert resp2.status_code == 200
    assert resp2.json()["id"] == workspace_id

    # action_log row was written
    result = await db_session.execute(
        select(ActionLog).where(ActionLog.workspace_id == workspace_id)
    )
    log = result.scalar_one()
    assert log.action == "workspace.created"


@pytest.mark.asyncio
async def test_get_workspace_not_found(test_client):
    resp = await test_client.get("/api/workspaces/nonexistent-id")
    assert resp.status_code == 404
