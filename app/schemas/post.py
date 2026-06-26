from pydantic import BaseModel


class RejectRequest(BaseModel):
    reason: str | None = None


class PostEditRequest(BaseModel):
    content: str | None = None
    hashtags: list[str] | None = None
    suggested_time: str | None = None
