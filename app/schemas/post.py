from pydantic import BaseModel


class RejectRequest(BaseModel):
    reason: str | None = None
