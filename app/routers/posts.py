from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.enums import AutonomyLevel, PostStatus
from app.models.post import Post
from app.models.workspace import Workspace
from app.schemas.plan import PostResponse
from app.schemas.post import PostEditRequest, RegenerateRequest, RejectRequest, ScheduleRequest
from app.services.action_log import log_action
from app.services.post_status import InvalidTransition, transition
from app.clients.postiz import PostizRateLimitError
from app.services.publishing import AlreadyScheduledError, schedule_post
from app.services.regenerate import regenerate_post

router = APIRouter(prefix="/posts", tags=["posts"])


async def _get_post_or_404(db: AsyncSession, post_id: str) -> Post:
    result = await db.execute(select(Post).where(Post.id == post_id))
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


@router.post("/{post_id}:approve", response_model=PostResponse)
async def approve_post(post_id: str, db: AsyncSession = Depends(get_db)):
    post = await _get_post_or_404(db, post_id)
    try:
        transition(post, PostStatus.approved)
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await log_action(
        db, post.workspace_id, "api", "approve_post", {"post_id": post_id}
    )
    await db.commit()
    await db.refresh(post)
    return post


@router.post("/{post_id}:reject", response_model=PostResponse)
async def reject_post(
    post_id: str,
    body: RejectRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
):
    post = await _get_post_or_404(db, post_id)
    try:
        transition(post, PostStatus.rejected)
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    reason = body.reason if body else None
    await log_action(
        db,
        post.workspace_id,
        "api",
        "reject_post",
        {"post_id": post_id, "reason": reason},
    )
    await db.commit()
    await db.refresh(post)
    return post


@router.patch("/{post_id}", response_model=PostResponse)
async def edit_post(
    post_id: str,
    body: PostEditRequest,
    db: AsyncSession = Depends(get_db),
):
    post = await _get_post_or_404(db, post_id)

    if body.content is not None:
        post.content = body.content
    if body.hashtags is not None:
        post.hashtags = body.hashtags
    if body.suggested_time is not None:
        post.suggested_time = body.suggested_time

    # Editing an approved post resets it to pending_approval for re-review.
    if PostStatus(post.status) == PostStatus.approved:
        transition(post, PostStatus.pending_approval)

    changes = body.model_dump(exclude_none=True)
    await log_action(
        db,
        post.workspace_id,
        "api",
        "edit_post",
        {"post_id": post_id, "changes": changes},
    )
    await db.commit()
    await db.refresh(post)
    return post


@router.post("/{post_id}:regenerate", response_model=PostResponse, status_code=202)
async def regenerate(
    post_id: str,
    background_tasks: BackgroundTasks,
    body: RegenerateRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
):
    post = await _get_post_or_404(db, post_id)
    note = body.note if body else None
    background_tasks.add_task(regenerate_post, post_id=post_id, note=note)
    return post


@router.post("/{post_id}:schedule", response_model=PostResponse)
async def schedule(
    post_id: str,
    body: ScheduleRequest,
    db: AsyncSession = Depends(get_db),
):
    post = await _get_post_or_404(db, post_id)

    if PostStatus(post.status) != PostStatus.approved:
        raise HTTPException(
            status_code=409,
            detail=f"Post must be approved before scheduling (current: {post.status})",
        )

    # Autonomy gate: supervised workspaces require explicit human action (this endpoint).
    # assisted/autonomous workspaces allow the same endpoint — future phases may add
    # auto-trigger hooks, but the gate logic is the same for now.
    ws_result = await db.execute(select(Workspace).where(Workspace.id == post.workspace_id))
    workspace = ws_result.scalar_one_or_none()
    if workspace and AutonomyLevel(workspace.autonomy_level) == AutonomyLevel.supervised:
        pass  # explicit call is the required path; nothing to block

    try:
        when = datetime.fromisoformat(body.when.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid datetime format for 'when'")

    try:
        updated = await schedule_post(
            db=db,
            post=post,
            integration_id=body.integration_id,
            provider=body.provider,
            when=when,
        )
    except AlreadyScheduledError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except PostizRateLimitError:
        raise HTTPException(
            status_code=503, detail="Postiz rate limit reached; try again later"
        )

    return updated
