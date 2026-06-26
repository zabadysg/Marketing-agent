from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import Workspace
from app.schemas.workspace import WorkspaceCreate
from app.services.action_log import log_action


async def create_workspace(db: AsyncSession, data: WorkspaceCreate) -> Workspace:
    ws = Workspace(name=data.name, autonomy_level=data.autonomy_level.value)
    db.add(ws)
    await db.flush()
    await log_action(
        db=db,
        workspace_id=ws.id,
        actor="system",
        action="workspace.created",
        payload={"name": data.name, "autonomy_level": data.autonomy_level.value},
    )
    await db.commit()
    await db.refresh(ws)
    return ws


async def get_workspace(db: AsyncSession, workspace_id: str) -> Workspace | None:
    result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    return result.scalar_one_or_none()
