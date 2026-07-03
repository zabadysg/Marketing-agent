"""Tests for knowledge document upload, ingestion, search, and deletion.

Mocking convention: AsyncMock + patch(), matching test_agents.py / test_graph.py.
File system operations in the upload router are neutralised with a real tempdir +
mocked settings.uploads_dir; ingest_document is stubbed out for endpoint tests and
called directly (with _parse_file + embed_many patched) for service tests.
"""

import tempfile
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import KnowledgeDocument


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _create_workspace(test_client, name: str) -> str:
    resp = await test_client.post("/api/workspaces", json={"name": name})
    return resp.json()["id"]


async def _seed_document(workspace_id: str, filename: str = "test.txt", status: str = "processing") -> str:
    from tests.conftest import _TestSessionLocal

    async with _TestSessionLocal() as db:
        doc = KnowledgeDocument(
            workspace_id=workspace_id,
            filename=filename,
            doc_type="other",
            storage_path=f"{workspace_id}/seed-doc/{filename}",
            status=status,
        )
        db.add(doc)
        await db.commit()
        await db.refresh(doc)
        return doc.id


FAKE_VEC = [0.1] * 768


# ── Upload endpoint ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_document_returns_202(test_client):
    ws_id = await _create_workspace(test_client, "Upload WS")

    with tempfile.TemporaryDirectory() as tmp_dir, \
         patch("app.routers.knowledge.settings") as mock_settings, \
         patch("app.routers.knowledge.ingest_document"):
        mock_settings.uploads_dir = tmp_dir

        resp = await test_client.post(
            f"/api/workspaces/{ws_id}/knowledge/documents",
            files={"file": ("brand.txt", b"Brand guidelines here", "text/plain")},
            data={"doc_type": "guidelines"},
        )

    assert resp.status_code == 202
    body = resp.json()
    assert body["filename"] == "brand.txt"
    assert body["status"] == "processing"
    assert body["workspace_id"] == ws_id
    assert body["doc_type"] == "guidelines"


@pytest.mark.asyncio
async def test_upload_document_creates_db_record(test_client):
    from tests.conftest import _TestSessionLocal

    ws_id = await _create_workspace(test_client, "Upload DB WS")

    with tempfile.TemporaryDirectory() as tmp_dir, \
         patch("app.routers.knowledge.settings") as mock_settings, \
         patch("app.routers.knowledge.ingest_document"):
        mock_settings.uploads_dir = tmp_dir

        resp = await test_client.post(
            f"/api/workspaces/{ws_id}/knowledge/documents",
            files={"file": ("guide.txt", b"content here", "text/plain")},
            data={"doc_type": "other"},
        )

    doc_id = resp.json()["id"]
    async with _TestSessionLocal() as db:
        doc = await db.get(KnowledgeDocument, doc_id)

    assert doc is not None
    assert doc.status == "processing"
    assert doc.filename == "guide.txt"


@pytest.mark.asyncio
async def test_upload_document_404_for_missing_workspace(test_client):
    with tempfile.TemporaryDirectory() as tmp_dir, \
         patch("app.routers.knowledge.settings") as mock_settings:
        mock_settings.uploads_dir = tmp_dir

        resp = await test_client.post(
            "/api/workspaces/no-such-ws/knowledge/documents",
            files={"file": ("x.txt", b"x", "text/plain")},
            data={"doc_type": "other"},
        )

    assert resp.status_code == 404


# ── List endpoint ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_documents_empty(test_client):
    ws_id = await _create_workspace(test_client, "List Empty WS")
    resp = await test_client.get(f"/api/workspaces/{ws_id}/knowledge/documents")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_documents_returns_seeded_doc(test_client):
    ws_id = await _create_workspace(test_client, "List Seeded WS")
    await _seed_document(ws_id, "my-doc.pdf")

    resp = await test_client.get(f"/api/workspaces/{ws_id}/knowledge/documents")
    assert resp.status_code == 200
    docs = resp.json()
    assert len(docs) == 1
    assert docs[0]["filename"] == "my-doc.pdf"


# ── Ingestion service ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ingest_document_creates_chunks_and_sets_indexed(test_client):
    from tests.conftest import _TestSessionLocal
    from app.services.knowledge_ingestion import ingest_document

    ws_id = await _create_workspace(test_client, "Ingest WS")
    doc_id = await _seed_document(ws_id, "brand.txt")

    sample_text = ("This is sample brand content. " * 20).strip()

    with patch("app.services.knowledge_ingestion._parse_file", return_value=sample_text), \
         patch("app.services.knowledge_ingestion.embed_many", new=AsyncMock(return_value=[FAKE_VEC])):
        await ingest_document(doc_id, "fake/path/brand.txt", "txt", ws_id, _TestSessionLocal)

    async with _TestSessionLocal() as db:
        result = await db.execute(
            select(KnowledgeChunk).where(KnowledgeChunk.document_id == doc_id)
        )
        chunks = list(result.scalars().all())
        doc = await db.get(KnowledgeDocument, doc_id)

    assert len(chunks) >= 1
    assert chunks[0].workspace_id == ws_id
    assert doc.status == "indexed"


@pytest.mark.asyncio
async def test_ingest_document_chunk_metadata(test_client):
    from tests.conftest import _TestSessionLocal
    from app.services.knowledge_ingestion import ingest_document

    ws_id = await _create_workspace(test_client, "Ingest Meta WS")
    doc_id = await _seed_document(ws_id, "my-brand.txt")

    with patch("app.services.knowledge_ingestion._parse_file", return_value="word " * 50), \
         patch("app.services.knowledge_ingestion.embed_many", new=AsyncMock(return_value=[FAKE_VEC])):
        await ingest_document(doc_id, "fake/path/my-brand.txt", "txt", ws_id, _TestSessionLocal)

    async with _TestSessionLocal() as db:
        result = await db.execute(
            select(KnowledgeChunk).where(KnowledgeChunk.document_id == doc_id)
        )
        chunk = result.scalars().first()

    assert chunk.metadata_["chunk_index"] == 0
    assert chunk.metadata_["filename"] == "my-brand.txt"


@pytest.mark.asyncio
async def test_ingest_document_sets_failed_on_parse_error(test_client):
    from tests.conftest import _TestSessionLocal
    from app.services.knowledge_ingestion import ingest_document

    ws_id = await _create_workspace(test_client, "Ingest Fail WS")
    doc_id = await _seed_document(ws_id)

    with patch("app.services.knowledge_ingestion._parse_file", side_effect=IOError("file not found")):
        await ingest_document(doc_id, "bad/path.txt", "txt", ws_id, _TestSessionLocal)

    async with _TestSessionLocal() as db:
        doc = await db.get(KnowledgeDocument, doc_id)

    assert doc.status == "failed"


@pytest.mark.asyncio
async def test_ingest_document_sets_failed_on_empty_text(test_client):
    from tests.conftest import _TestSessionLocal
    from app.services.knowledge_ingestion import ingest_document

    ws_id = await _create_workspace(test_client, "Ingest Empty WS")
    doc_id = await _seed_document(ws_id)

    # Empty text produces no chunks → should set failed
    with patch("app.services.knowledge_ingestion._parse_file", return_value=""):
        await ingest_document(doc_id, "empty.txt", "txt", ws_id, _TestSessionLocal)

    async with _TestSessionLocal() as db:
        doc = await db.get(KnowledgeDocument, doc_id)

    assert doc.status == "failed"


@pytest.mark.asyncio
async def test_ingest_document_noop_for_missing_doc():
    from tests.conftest import _TestSessionLocal
    from app.services.knowledge_ingestion import ingest_document

    # Non-existent doc_id should silently exit without raising
    await ingest_document("no-such-id", "path.txt", "txt", "ws-x", _TestSessionLocal)


# ── Search service & endpoint ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_knowledge_returns_matching_chunk(test_client):
    from tests.conftest import _TestSessionLocal
    from app.services.knowledge_search import search_knowledge

    ws_id = await _create_workspace(test_client, "Search WS")
    doc_id = await _seed_document(ws_id, "search-doc.txt")

    async with _TestSessionLocal() as db:
        db.add(KnowledgeChunk(
            document_id=doc_id,
            workspace_id=ws_id,
            content="Our brand is innovative and customer-first.",
            embedding=FAKE_VEC,
            metadata_={"chunk_index": 0},
        ))
        await db.commit()

    async with _TestSessionLocal() as db:
        with patch("app.services.knowledge_search.embed_text", new=AsyncMock(return_value=FAKE_VEC)):
            results = await search_knowledge("brand innovation", ws_id, db, k=5)

    assert len(results) == 1
    assert results[0].content == "Our brand is innovative and customer-first."
    assert results[0].workspace_id == ws_id


@pytest.mark.asyncio
async def test_search_endpoint_returns_200(test_client):
    ws_id = await _create_workspace(test_client, "Search Endpoint WS")

    with patch("app.routers.knowledge.search_knowledge", new=AsyncMock(return_value=[])):
        resp = await test_client.get(
            f"/api/workspaces/{ws_id}/knowledge/search?q=test"
        )

    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_search_endpoint_404_for_missing_workspace(test_client):
    with patch("app.routers.knowledge.search_knowledge", new=AsyncMock(return_value=[])):
        resp = await test_client.get(
            "/api/workspaces/no-such-ws/knowledge/search?q=test"
        )
    assert resp.status_code == 404


# ── Delete endpoint ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_document_returns_204(test_client):
    ws_id = await _create_workspace(test_client, "Delete WS")
    doc_id = await _seed_document(ws_id, "to-delete.txt")

    with patch("app.routers.knowledge.shutil.rmtree"):
        resp = await test_client.delete(
            f"/api/workspaces/{ws_id}/knowledge/documents/{doc_id}"
        )

    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_document_removes_db_record(test_client):
    from tests.conftest import _TestSessionLocal

    ws_id = await _create_workspace(test_client, "Delete DB WS")
    doc_id = await _seed_document(ws_id, "db-delete.txt")

    with patch("app.routers.knowledge.shutil.rmtree"):
        await test_client.delete(
            f"/api/workspaces/{ws_id}/knowledge/documents/{doc_id}"
        )

    async with _TestSessionLocal() as db:
        doc = await db.get(KnowledgeDocument, doc_id)

    assert doc is None


@pytest.mark.asyncio
async def test_delete_document_cascades_chunks(test_client):
    from tests.conftest import _TestSessionLocal

    ws_id = await _create_workspace(test_client, "Delete Cascade WS")
    doc_id = await _seed_document(ws_id, "cascade.txt")

    async with _TestSessionLocal() as db:
        db.add(KnowledgeChunk(
            document_id=doc_id,
            workspace_id=ws_id,
            content="Content to cascade-delete.",
            embedding=FAKE_VEC,
            metadata_={"chunk_index": 0},
        ))
        await db.commit()

    with patch("app.routers.knowledge.shutil.rmtree"):
        await test_client.delete(
            f"/api/workspaces/{ws_id}/knowledge/documents/{doc_id}"
        )

    async with _TestSessionLocal() as db:
        result = await db.execute(
            select(KnowledgeChunk).where(KnowledgeChunk.document_id == doc_id)
        )
        chunks = result.scalars().all()

    assert chunks == []


@pytest.mark.asyncio
async def test_delete_document_404_for_missing_doc(test_client):
    ws_id = await _create_workspace(test_client, "Delete 404 WS")

    resp = await test_client.delete(
        f"/api/workspaces/{ws_id}/knowledge/documents/no-such-doc"
    )
    assert resp.status_code == 404
