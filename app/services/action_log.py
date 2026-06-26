from sqlalchemy.ext.asyncio import AsyncSession

from app.models.action_log import ActionLog


async def log_action(
    db: AsyncSession,
    workspace_id: str,
    actor: str,
    action: str,
    payload: dict,
    result: dict | None = None,
) -> ActionLog:
    entry = ActionLog(
        workspace_id=workspace_id,
        actor=actor,
        action=action,
        payload=payload,
        result=result,
    )
    db.add(entry)
    await db.flush()
    return entry
