from pydantic import BaseModel


class RejectRequest(BaseModel):
    reason: str | None = None


class PostEditRequest(BaseModel):
    content: str | None = None
    hashtags: list[str] | None = None
    suggested_time: str | None = None


class RegenerateRequest(BaseModel):
    note: str | None = None


class ScheduleRequest(BaseModel):
    integration_id: str
    provider: str
    when: str  # ISO-8601 datetime string, e.g. "2026-07-01T09:00:00Z"
