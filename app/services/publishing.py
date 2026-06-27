from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.postiz import PostizClient, PostizRateLimitError  # noqa: F401
from app.models.enums import PostStatus
from app.models.post import Post
from app.services.action_log import log_action
from app.services.post_status import transition


class AlreadyScheduledError(Exception):
    pass


async def schedule_post(
    db: AsyncSession,
    post: Post,
    integration_id: str,
    provider: str,
    when: datetime,
) -> Post:
    """Schedule post via Postiz. Idempotent: raises AlreadyScheduledError if
    postiz_post_id is already set and status is scheduled/published.
    PostizRateLimitError is re-raised without mutating post status.
    """
    current = PostStatus(post.status)
    if post.postiz_post_id and current in (PostStatus.scheduled, PostStatus.published):
        raise AlreadyScheduledError(
            f"Post {post.id} is already {current.value} "
            f"(postiz_post_id={post.postiz_post_id})"
        )

    content = post.content
    if post.hashtags:
        content = f"{content}\n{' '.join(post.hashtags)}"

    async with PostizClient() as client:
        result = await client.schedule_post(
            integration_id=integration_id,
            content=content,
            provider=provider,
            scheduled_at=when,
        )

    # Postiz returns a list of created post objects
    first = result[0] if isinstance(result, list) else result
    postiz_id = first.get("id") or ""
    post.postiz_post_id = str(postiz_id)
    transition(post, PostStatus.scheduled)

    await log_action(
        db,
        post.workspace_id,
        "api",
        "schedule_post",
        {
            "post_id": post.id,
            "integration_id": integration_id,
            "provider": provider,
            "when": when.isoformat(),
            "postiz_post_id": post.postiz_post_id,
        },
    )
    await db.commit()
    await db.refresh(post)
    return post
