import uuid
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessage, ChatSession, MessageRole
from app.models.content_plan import ContentPlan
from app.models.enums import PlanStatus

logger = logging.getLogger(__name__)


async def get_or_create_session(
    db: AsyncSession,
    workspace_id: str,
    session_id: str | None = None,
) -> ChatSession:
    if session_id:
        result = await db.execute(
            select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.workspace_id == workspace_id,
            )
        )
        session = result.scalar_one_or_none()
        if session:
            return session

    session = ChatSession(workspace_id=workspace_id)
    db.add(session)
    await db.flush()
    return session


async def get_session(
    db: AsyncSession,
    workspace_id: str,
    session_id: str,
) -> ChatSession | None:
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.workspace_id == workspace_id,
        )
    )
    return result.scalar_one_or_none()


async def list_sessions(
    db: AsyncSession,
    workspace_id: str,
) -> list[ChatSession]:
    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.workspace_id == workspace_id)
        .order_by(ChatSession.updated_at.desc())
    )
    return list(result.scalars().all())


async def get_messages(
    db: AsyncSession,
    session_id: str,
    limit: int = 20,
) -> list[ChatMessage]:
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def save_message(
    db: AsyncSession,
    session_id: str,
    workspace_id: str,
    role: MessageRole,
    content: str,
    metadata: dict | None = None,
) -> ChatMessage:
    msg = ChatMessage(
        id=str(uuid.uuid4()),
        session_id=session_id,
        workspace_id=workspace_id,
        role=role.value,
        content=content,
        metadata_=metadata or {},
    )
    db.add(msg)
    await db.flush()
    return msg


async def auto_title_session(
    db: AsyncSession,
    session: ChatSession,
    first_message: str,
) -> None:
    if not session.title:
        session.title = first_message[:60]
        await db.flush()


async def get_or_create_chat_draft_plan(
    db: AsyncSession,
    workspace_id: str,
) -> ContentPlan:
    # TODO: race condition — two concurrent calls can create two draft plans.
    # Both are functional; accepted as low-probability technical debt.
    # Fix with UniqueConstraint("workspace_id", "goal") migration if it becomes a problem.
    result = await db.execute(
        select(ContentPlan).where(
            ContentPlan.workspace_id == workspace_id,
            ContentPlan.goal == "__chat_drafts__",
        ).limit(1)
    )
    plan = result.scalar_one_or_none()
    if plan is None:
        plan = ContentPlan(
            workspace_id=workspace_id,
            goal="__chat_drafts__",
            status=PlanStatus.ready.value,
        )
        db.add(plan)
        await db.flush()
    return plan
