from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.workspace import WorkspaceCreate, WorkspaceResponse
from app.services.workspace import create_workspace, get_workspace

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.post("", response_model=WorkspaceResponse, status_code=201)
async def create_workspace_endpoint(
    data: WorkspaceCreate, db: AsyncSession = Depends(get_db)
):
    return await create_workspace(db, data)


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace_endpoint(
    workspace_id: str, db: AsyncSession = Depends(get_db)
):
    ws = await get_workspace(db, workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return ws
