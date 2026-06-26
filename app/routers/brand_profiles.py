from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.brand_profile import BrandProfileUpsert, BrandProfileResponse
from app.services.brand_profile import get_brand_profile, upsert_brand_profile
from app.services.workspace import get_workspace

router = APIRouter(prefix="/workspaces", tags=["brand"])


@router.put("/{workspace_id}/brand", response_model=BrandProfileResponse)
async def upsert_brand_endpoint(
    workspace_id: str,
    data: BrandProfileUpsert,
    db: AsyncSession = Depends(get_db),
):
    ws = await get_workspace(db, workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return await upsert_brand_profile(db, workspace_id, data)


@router.get("/{workspace_id}/brand", response_model=BrandProfileResponse)
async def get_brand_endpoint(
    workspace_id: str, db: AsyncSession = Depends(get_db)
):
    bp = await get_brand_profile(db, workspace_id)
    if not bp:
        raise HTTPException(status_code=404, detail="Brand profile not set")
    return bp
