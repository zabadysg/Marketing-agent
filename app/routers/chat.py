import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.chat import MessageRole
from app.schemas.chat import (
    ChatMessageResponse,
    ChatSessionCreate,
    ChatSessionDetailResponse,
    ChatSessionResponse,
    SendMessageResponse,
)
from app.services import event_bus
from app.services.brand_profile import brand_profile_to_dict, get_brand_profile
from app.services.chat import (
    auto_title_session,
    get_messages,
    get_or_create_session,
    get_session,
    list_sessions,
    save_message,
)
from app.services.knowledge_search import search_knowledge
from app.services.workspace import get_workspace

router = APIRouter(prefix="/workspaces", tags=["chat"])


def _session_to_response(session) -> ChatSessionResponse:
    return ChatSessionResponse(
        id=session.id,
        workspace_id=session.workspace_id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def _message_to_response(msg) -> ChatMessageResponse:
    return ChatMessageResponse(
        id=msg.id,
        session_id=msg.session_id,
        role=msg.role,
        content=msg.content,
        metadata_=msg.metadata_,
        agent_id=msg.agent_id,
        meeting_id=msg.meeting_id,
        turn_index=msg.turn_index,
        created_at=msg.created_at,
    )


@router.post(
    "/{workspace_id}/chat/sessions",
    response_model=ChatSessionResponse,
    status_code=201,
)
async def create_session(
    workspace_id: str,
    body: ChatSessionCreate,
    db: AsyncSession = Depends(get_db),
):
    ws = await get_workspace(db, workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    session = await get_or_create_session(db, workspace_id)
    if body.title:
        session.title = body.title
    await db.commit()
    await db.refresh(session)
    return _session_to_response(session)


@router.get(
    "/{workspace_id}/chat/sessions",
    response_model=list[ChatSessionResponse],
)
async def get_sessions(
    workspace_id: str,
    db: AsyncSession = Depends(get_db),
):
    ws = await get_workspace(db, workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    sessions = await list_sessions(db, workspace_id)
    return [_session_to_response(s) for s in sessions]


@router.get(
    "/{workspace_id}/chat/sessions/{session_id}",
    response_model=ChatSessionDetailResponse,
)
async def get_session_detail(
    workspace_id: str,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    session = await get_session(db, workspace_id, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")

    messages = await get_messages(db, session_id)
    return ChatSessionDetailResponse(
        id=session.id,
        workspace_id=session.workspace_id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
        messages=[_message_to_response(m) for m in messages],
    )


@router.delete(
    "/{workspace_id}/chat/sessions/{session_id}",
    status_code=204,
)
async def delete_session(
    workspace_id: str,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    session = await get_session(db, workspace_id, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")

    await db.delete(session)
    await db.commit()


@router.post(
    "/{workspace_id}/chat/sessions/{session_id}/messages",
    response_model=SendMessageResponse,
    status_code=202,
)
async def send_message(
    workspace_id: str,
    session_id: str,
    body: dict,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    from app.agents.chat_agent import run_chat_agent

    ws = await get_workspace(db, workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    bp = await get_brand_profile(db, workspace_id)
    if not bp or bp.onboarding_status != "active":
        raise HTTPException(
            status_code=400,
            detail="Brand profile required to start a chat session",
        )

    session = await get_session(db, workspace_id, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")

    user_content = body.get("content", "").strip()
    if not user_content:
        raise HTTPException(status_code=422, detail="Message content is required")

    # Save user message
    user_msg = await save_message(
        db,
        session_id=session_id,
        workspace_id=workspace_id,
        role=MessageRole.user,
        content=user_content,
    )
    await auto_title_session(db, session, user_content)
    await db.commit()

    # Load conversation history (last N messages before this one)
    history_rows = await get_messages(db, session_id, limit=HISTORY_WINDOW + 1)
    history = [
        {"role": m.role, "content": m.content}
        for m in history_rows
        if m.id != user_msg.id
    ][-HISTORY_WINDOW:]

    # Pre-retrieve top-3 knowledge chunks as baseline context
    chunks = await search_knowledge(user_content, workspace_id, db, k=3)
    retrieved_context = "\n---\n".join(c.content for c in chunks)

    brand_dict = brand_profile_to_dict(bp)

    import uuid
    placeholder_id = str(uuid.uuid4())

    if event_bus.exists(session_id):
        event_bus.close(session_id)
    event_bus.create(session_id)

    mode = body.get("mode", "chat")
    if mode == "meeting":
        from app.agents.meeting_agent import run_meeting_agent
        meeting_id = str(uuid.uuid4())
        background_tasks.add_task(
            run_meeting_agent,
            session_id=session_id,
            meeting_id=meeting_id,
            workspace_id=workspace_id,
            user_message=user_content,
            brand_profile=brand_dict,
            retrieved_context=retrieved_context,
        )
        return SendMessageResponse(message_id=placeholder_id, meeting_id=meeting_id)

    background_tasks.add_task(
        run_chat_agent,
        session_id=session_id,
        workspace_id=workspace_id,
        user_message=user_content,
        history=history,
        brand_profile=brand_dict,
        retrieved_context=retrieved_context,
    )

    return SendMessageResponse(message_id=placeholder_id)


HISTORY_WINDOW = 20


@router.get(
    "/{workspace_id}/chat/sessions/{session_id}/stream",
)
async def stream_session(
    workspace_id: str,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    session = await get_session(db, workspace_id, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")

    if not event_bus.exists(session_id):
        async def already_done():
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return StreamingResponse(already_done(), media_type="text/event-stream")

    async def generator():
        while True:
            event = await event_bus.read(session_id, timeout=25.0)
            if event is None:
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                break
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") in ("done", "error"):
                break

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
