from typing import Any

from pydantic import BaseModel


class ChatSessionCreate(BaseModel):
    title: str | None = None


class ChatMessageCreate(BaseModel):
    content: str


class ChatMessageResponse(BaseModel):
    id: str
    session_id: str
    role: str
    content: str
    metadata_: dict[str, Any]
    created_at: str

    model_config = {"from_attributes": True}


class ChatSessionResponse(BaseModel):
    id: str
    workspace_id: str
    title: str | None
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class ChatSessionDetailResponse(ChatSessionResponse):
    messages: list[ChatMessageResponse]


class SendMessageResponse(BaseModel):
    message_id: str
