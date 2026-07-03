from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    filename: str
    doc_type: str
    storage_path: str
    status: str
    uploaded_at: str | datetime


class KnowledgeChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    document_id: str
    workspace_id: str
    content: str
    chunk_metadata: dict = Field(alias="metadata_")
    created_at: str | datetime
