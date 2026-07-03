import asyncio
import os
import shutil
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.knowledge_document import KnowledgeDocument
from app.schemas.knowledge import KnowledgeChunkResponse, KnowledgeDocumentResponse
from app.services.action_log import log_action
from app.services.knowledge_ingestion import ingest_document
from app.services.knowledge_search import search_knowledge
from app.services.workspace import get_workspace

router = APIRouter(prefix="/workspaces", tags=["knowledge"])


@router.post(
    "/{workspace_id}/knowledge/documents",
    response_model=KnowledgeDocumentResponse,
    status_code=202,
)
async def upload_document(
    workspace_id: str,
    file: UploadFile,
    background_tasks: BackgroundTasks,
    doc_type: str = Form(default="other"),
    db: AsyncSession = Depends(get_db),
):
    ws = await get_workspace(db, workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    doc_id = str(uuid.uuid4())
    # storage_path is relative to UPLOADS_DIR
    rel_dir = os.path.join(workspace_id, doc_id)
    storage_path = os.path.join(rel_dir, file.filename or "document")

    abs_dir = os.path.join(settings.uploads_dir, rel_dir)
    abs_path = os.path.join(settings.uploads_dir, storage_path)

    await asyncio.to_thread(os.makedirs, abs_dir, exist_ok=True)

    with open(abs_path, "wb") as out_file:
        await asyncio.to_thread(shutil.copyfileobj, file.file, out_file)

    doc = KnowledgeDocument(
        id=doc_id,
        workspace_id=workspace_id,
        filename=file.filename or "document",
        doc_type=doc_type,
        storage_path=storage_path,
    )
    db.add(doc)
    await log_action(
        db=db,
        workspace_id=workspace_id,
        actor="user",
        action="knowledge.document.uploaded",
        payload={"filename": file.filename, "doc_type": doc_type},
    )
    await db.commit()
    await db.refresh(doc)

    background_tasks.add_task(ingest_document, doc_id, storage_path, doc_type, workspace_id)

    return doc


@router.get(
    "/{workspace_id}/knowledge/documents",
    response_model=list[KnowledgeDocumentResponse],
)
async def list_documents(
    workspace_id: str,
    db: AsyncSession = Depends(get_db),
):
    ws = await get_workspace(db, workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    result = await db.execute(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.workspace_id == workspace_id)
        .order_by(KnowledgeDocument.uploaded_at.desc())
    )
    return list(result.scalars().all())


@router.delete(
    "/{workspace_id}/knowledge/documents/{doc_id}",
    status_code=204,
)
async def delete_document(
    workspace_id: str,
    doc_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == doc_id,
            KnowledgeDocument.workspace_id == workspace_id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Derive directory from storage_path: {workspace_id}/{doc_id}/...
    doc_dir = os.path.join(settings.uploads_dir, workspace_id, doc_id)

    await db.delete(doc)
    await log_action(
        db=db,
        workspace_id=workspace_id,
        actor="user",
        action="knowledge.document.deleted",
        payload={"doc_id": doc_id, "filename": doc.filename},
    )
    await db.commit()

    # Remove files from disk after the DB commit succeeds
    await asyncio.to_thread(shutil.rmtree, doc_dir, True)


@router.get(
    "/{workspace_id}/knowledge/search",
    response_model=list[KnowledgeChunkResponse],
)
async def search_documents(
    workspace_id: str,
    q: str,
    k: int = 5,
    db: AsyncSession = Depends(get_db),
):
    ws = await get_workspace(db, workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    chunks = await search_knowledge(q, workspace_id, db, k=k)
    return chunks
