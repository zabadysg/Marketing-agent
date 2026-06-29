import secrets

from pydantic import BaseModel, ConfigDict
from fastapi import APIRouter, Depends, HTTPException, Query, Security
from fastapi.security import APIKeyHeader
from sqlalchemy import func, select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.action_log import ActionLog
from app.models.content_plan import ContentPlan
from app.models.post import Post
from app.models.workspace import Workspace

_admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


def require_admin_key(key: str | None = Security(_admin_key_header)) -> None:
    configured = settings.admin_api_key.get_secret_value()
    if not configured:
        raise HTTPException(status_code=503, detail="Admin API key not configured on server")
    if not key or not secrets.compare_digest(key, configured):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Key header")


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_key)])


# ── Schemas ────────────────────────────────────────────────────────────────────

class StatsResponse(BaseModel):
    workspaces: int
    plans_generating: int
    plans_ready: int
    plans_failed: int
    posts_pending: int
    posts_approved: int
    posts_scheduled: int
    posts_published: int
    posts_rejected: int
    action_logs: int


class AdminWorkspace(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    autonomy_level: str
    created_at: str


class AdminPlan(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    workspace_name: str
    goal: str | None
    status: str
    error: str | None
    post_count: int
    created_at: str


class AdminPost(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    plan_id: str
    workspace_id: str
    workspace_name: str
    day: int
    theme: str
    format: str
    content: str
    hashtags: list[str]
    suggested_time: str
    status: str
    postiz_post_id: str | None
    created_at: str


class AdminLog(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    actor: str
    action: str
    payload: dict
    result: dict | None
    created_at: str


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/stats", response_model=StatsResponse)
async def get_stats(db: AsyncSession = Depends(get_db)):
    ws_count = (await db.execute(select(func.count()).select_from(Workspace))).scalar_one()
    logs_count = (await db.execute(select(func.count()).select_from(ActionLog))).scalar_one()

    plan_rows = (await db.execute(
        select(ContentPlan.status, func.count()).group_by(ContentPlan.status)
    )).all()
    plan_map = {r[0]: r[1] for r in plan_rows}

    post_rows = (await db.execute(
        select(Post.status, func.count()).group_by(Post.status)
    )).all()
    post_map = {r[0]: r[1] for r in post_rows}

    return StatsResponse(
        workspaces=ws_count,
        plans_generating=plan_map.get("generating", 0),
        plans_ready=plan_map.get("ready", 0),
        plans_failed=plan_map.get("failed", 0),
        posts_pending=post_map.get("pending_approval", 0),
        posts_approved=post_map.get("approved", 0),
        posts_scheduled=post_map.get("scheduled", 0),
        posts_published=post_map.get("published", 0),
        posts_rejected=post_map.get("rejected", 0),
        action_logs=logs_count,
    )


@router.get("/workspaces", response_model=list[AdminWorkspace])
async def list_all_workspaces(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Workspace).order_by(Workspace.created_at.desc()))
    return result.scalars().all()


@router.delete("/workspaces/{workspace_id}", status_code=204)
async def delete_workspace(workspace_id: str, db: AsyncSession = Depends(get_db)):
    ws = await db.get(Workspace, workspace_id)
    if not ws:
        raise HTTPException(404, "Workspace not found")
    # Cascade: delete posts, plans, logs
    plan_ids = (await db.execute(
        select(ContentPlan.id).where(ContentPlan.workspace_id == workspace_id)
    )).scalars().all()
    if plan_ids:
        await db.execute(delete(Post).where(Post.plan_id.in_(plan_ids)))
    await db.execute(delete(ContentPlan).where(ContentPlan.workspace_id == workspace_id))
    await db.execute(delete(ActionLog).where(ActionLog.workspace_id == workspace_id))
    await db.delete(ws)
    await db.commit()


@router.get("/plans", response_model=list[AdminPlan])
async def list_all_plans(db: AsyncSession = Depends(get_db)):
    plans = (await db.execute(
        select(ContentPlan).order_by(ContentPlan.created_at.desc())
    )).scalars().all()

    ws_ids = list({p.workspace_id for p in plans})
    ws_map: dict[str, str] = {}
    if ws_ids:
        rows = (await db.execute(
            select(Workspace.id, Workspace.name).where(Workspace.id.in_(ws_ids))
        )).all()
        ws_map = {r[0]: r[1] for r in rows}

    post_counts = {}
    if plans:
        plan_ids = [p.id for p in plans]
        rows = (await db.execute(
            select(Post.plan_id, func.count()).where(Post.plan_id.in_(plan_ids)).group_by(Post.plan_id)
        )).all()
        post_counts = {r[0]: r[1] for r in rows}

    return [
        AdminPlan(
            id=p.id,
            workspace_id=p.workspace_id,
            workspace_name=ws_map.get(p.workspace_id, "—"),
            goal=p.goal,
            status=p.status,
            error=p.error,
            post_count=post_counts.get(p.id, 0),
            created_at=str(p.created_at),
        )
        for p in plans
    ]


@router.delete("/plans/{plan_id}", status_code=204)
async def delete_plan(plan_id: str, db: AsyncSession = Depends(get_db)):
    plan = await db.get(ContentPlan, plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    await db.execute(delete(Post).where(Post.plan_id == plan_id))
    await db.delete(plan)
    await db.commit()


@router.get("/posts", response_model=list[AdminPost])
async def list_all_posts(status: str | None = None, db: AsyncSession = Depends(get_db)):
    q = select(Post).order_by(Post.created_at.desc())
    if status:
        q = q.where(Post.status == status)
    posts = (await db.execute(q)).scalars().all()

    ws_ids = list({p.workspace_id for p in posts})
    ws_map: dict[str, str] = {}
    if ws_ids:
        rows = (await db.execute(
            select(Workspace.id, Workspace.name).where(Workspace.id.in_(ws_ids))
        )).all()
        ws_map = {r[0]: r[1] for r in rows}

    return [
        AdminPost(
            id=p.id,
            plan_id=p.plan_id,
            workspace_id=p.workspace_id,
            workspace_name=ws_map.get(p.workspace_id, "—"),
            day=p.day,
            theme=p.theme,
            format=p.format,
            content=p.content,
            hashtags=p.hashtags or [],
            suggested_time=p.suggested_time,
            status=p.status,
            postiz_post_id=p.postiz_post_id,
            created_at=str(p.created_at),
        )
        for p in posts
    ]


@router.delete("/posts/{post_id}", status_code=204)
async def delete_post(post_id: str, db: AsyncSession = Depends(get_db)):
    post = await db.get(Post, post_id)
    if not post:
        raise HTTPException(404, "Post not found")
    await db.delete(post)
    await db.commit()


@router.get("/logs", response_model=list[AdminLog])
async def list_logs(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ActionLog)
        .order_by(ActionLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()
