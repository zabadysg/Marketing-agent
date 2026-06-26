from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.enums import PostStatus
from app.models.post import Post
from app.schemas.plan import PostResponse
from app.schemas.post import PostEditRequest, RejectRequest
from app.services.action_log import log_action
from app.services.post_status import InvalidTransition, transition

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
