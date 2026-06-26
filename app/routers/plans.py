from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.content_plan import ContentPlan
from app.models.enums import PlanStatus
from app.models.post import Post
from app.schemas.plan import PlanCreateRequest, PlanResponse
from app.services.brand_profile import get_brand_profile
from app.services.generation import run_generation
from app.services.workspace import get_workspace

router = APIRouter(prefix="/workspaces", tags=["plans"])


@router.post("/{workspace_id}/plans:generate", response_model=PlanResponse, status_code=202)
async def generate_plan(
    workspace_id: str,
    data: PlanCreateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    ws = await get_workspace(db, workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    bp = await get_brand_profile(db, workspace_id)
    if not bp:
        raise HTTPException(status_code=422, detail="Brand profile not set — call PUT /brand first")

    plan = ContentPlan(
        workspace_id=workspace_id,
        goal=data.goal,
        status=PlanStatus.generating.value,
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)

    brand_dict = {
        "name": bp.name,
        "audience": bp.audience,
        "tone": bp.tone,
        "language": bp.language,
        "avoid": bp.avoid or [],
        "extra": bp.extra or {},
    }

    background_tasks.add_task(
        run_generation,
        plan_id=plan.id,
        workspace_id=workspace_id,
        brand_profile=brand_dict,
        goal=data.goal,
    )

    return PlanResponse(
        id=plan.id,
        workspace_id=workspace_id,
        goal=plan.goal,
        status=plan.status,
        error=None,
        posts=[],
        created_at=str(plan.created_at),
    )


@router.get("/{workspace_id}/plans/{plan_id}", response_model=PlanResponse)
async def get_plan(
    workspace_id: str,
    plan_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ContentPlan)
        .where(ContentPlan.id == plan_id, ContentPlan.workspace_id == workspace_id)
    )
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    posts_result = await db.execute(
        select(Post)
        .where(Post.plan_id == plan_id)
        .order_by(Post.day)
    )
    posts = posts_result.scalars().all()

    return PlanResponse(
        id=plan.id,
        workspace_id=plan.workspace_id,
        goal=plan.goal,
        status=plan.status,
        error=plan.error,
        posts=list(posts),
        created_at=str(plan.created_at),
    )
