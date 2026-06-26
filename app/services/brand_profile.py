from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.brand_profile import BrandProfile
from app.schemas.brand_profile import BrandProfileUpsert
from app.services.action_log import log_action


async def upsert_brand_profile(
    db: AsyncSession, workspace_id: str, data: BrandProfileUpsert
) -> BrandProfile:
    result = await db.execute(
        select(BrandProfile).where(BrandProfile.workspace_id == workspace_id)
    )
    bp = result.scalar_one_or_none()

    if bp is None:
        bp = BrandProfile(workspace_id=workspace_id)
        db.add(bp)

    bp.name = data.name
    bp.audience = data.audience
    bp.tone = data.tone
    bp.language = data.language
    bp.avoid = data.avoid
    bp.extra = data.extra

    await db.flush()
    await log_action(
        db=db,
        workspace_id=workspace_id,
        actor="system",
        action="brand_profile.upserted",
        payload=data.model_dump(),
    )
    await db.commit()
    await db.refresh(bp)
    return bp


async def get_brand_profile(db: AsyncSession, workspace_id: str) -> BrandProfile | None:
    result = await db.execute(
        select(BrandProfile).where(BrandProfile.workspace_id == workspace_id)
    )
    return result.scalar_one_or_none()
