"""Background service for parsing, chunking, and embedding uploaded knowledge documents."""
import asyncio
import logging
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.enums import DocumentStatus
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import KnowledgeDocument
from app.services.embeddings import embed_many

logger = logging.getLogger(__name__)

_CHUNK_TARGET_WORDS = 500
_OVERLAP_WORDS = 50


def _parse_pdf(path: str) -> str:
    from pypdf import PdfReader
    reader = PdfReader(path)
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages)


def _parse_docx(path: str) -> str:
    from docx import Document
    doc = Document(path)
    return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _parse_txt(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _parse_file(path: str, doc_type: str) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return _parse_pdf(path)
    elif ext in (".docx", ".doc"):
        return _parse_docx(path)
    else:
        return _parse_txt(path)


def _chunk_text(text: str) -> list[str]:
    """Split text into ~500-word chunks with 50-word overlap at paragraph boundaries."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current_parts: list[str] = []
    current_words = 0
    overlap_tail: list[str] = []

    for para in paragraphs:
        word_count = len(para.split())
        if current_words + word_count > _CHUNK_TARGET_WORDS and current_parts:
            chunk_text = " ".join(current_parts)
            chunks.append(chunk_text)
            all_words = chunk_text.split()
            overlap_tail = all_words[-_OVERLAP_WORDS:]
            current_parts = [" ".join(overlap_tail), para]
            current_words = len(overlap_tail) + word_count
        else:
            current_parts.append(para)
            current_words += word_count

    if current_parts:
        chunks.append(" ".join(current_parts))

    return chunks


async def ingest_document(
    doc_id: str,
    storage_path: str,
    doc_type: str,
    workspace_id: str,
    session_factory: async_sessionmaker = AsyncSessionLocal,
) -> None:
    """Parse, chunk, embed, and store a knowledge document. Runs as a BackgroundTask."""
    abs_path = os.path.join(settings.uploads_dir, storage_path)

    async with session_factory() as db:
        result = await db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id)
        )
        doc = result.scalar_one_or_none()
        if not doc:
            logger.warning("ingest_document: doc %s not found", doc_id)
            return

        try:
            raw_text = await asyncio.to_thread(_parse_file, abs_path, doc_type)
            chunks = _chunk_text(raw_text)

            if not chunks:
                logger.warning("ingest_document: no chunks extracted from doc %s", doc_id)
                doc.status = DocumentStatus.failed.value
                await db.commit()
                return

            embeddings = await embed_many(chunks)

            for idx, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
                db.add(
                    KnowledgeChunk(
                        document_id=doc_id,
                        workspace_id=workspace_id,
                        content=chunk_text,
                        embedding=embedding,
                        metadata_={
                            "chunk_index": idx,
                            "doc_type": doc_type,
                            "filename": doc.filename,
                        },
                    )
                )

            doc.status = DocumentStatus.indexed.value
            await db.commit()
            logger.info("ingest_document: indexed %d chunks for doc %s", len(chunks), doc_id)

        except Exception:
            logger.exception("ingest_document: failed for doc %s", doc_id)
            doc.status = DocumentStatus.failed.value
            await db.commit()
