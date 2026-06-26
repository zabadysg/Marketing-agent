import pytest
from sqlalchemy import select

from app.models.action_log import ActionLog
from app.services.action_log import log_action


@pytest.mark.asyncio
async def test_log_action_inserts_and_reads_back(db_session):
    entry = await log_action(
        db=db_session,
        workspace_id="ws-test-001",
        actor="test_actor",
        action="test_action",
        payload={"key": "value"},
        result={"ok": True},
    )
    await db_session.commit()

    result = await db_session.execute(
        select(ActionLog).where(ActionLog.id == entry.id)
    )
    row = result.scalar_one()
    assert row.workspace_id == "ws-test-001"
    assert row.actor == "test_actor"
    assert row.action == "test_action"
    assert row.payload == {"key": "value"}
    assert row.result == {"ok": True}
    assert row.created_at is not None
