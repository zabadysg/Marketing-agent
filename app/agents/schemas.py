from pydantic import BaseModel, Field


class ContentIdea(BaseModel):
    day: int
    theme: str
    format: str
    angle: str


class StrategyOutput(BaseModel):
    ideas: list[ContentIdea] = Field(..., min_length=7, max_length=7)


class ContentOutput(BaseModel):
    content: str
    hashtags: list[str]
    suggested_time: str


class CriticOutput(BaseModel):
    approved: bool
    issues: list[str]
    fixed_body: str | None = None
