"""In-process embedding service using BAAI/bge-base-en-v1.5 via sentence-transformers.

Documents are embedded as-is. Queries use the BGE asymmetric retrieval prefix so that
cosine similarity between query and document vectors is meaningful.

NOTE: if the embedding model is changed, all KnowledgeChunk rows must be deleted and
re-ingested — embeddings from different models are not comparable even at the same dimension.
"""
import asyncio
import logging

from sentence_transformers import SentenceTransformer

from app.config import settings

logger = logging.getLogger(__name__)

# Module-level singleton; loaded once on import.
# The SentenceTransformer constructor reads HF_HOME for cached weights.
_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        logger.info("Loading embedding model %s", settings.embedding_model)
        _model = SentenceTransformer(settings.embedding_model)
    return _model


# For bge-base-en-v1.5, the model card specifies this prefix for retrieval queries.
# Verify against https://huggingface.co/BAAI/bge-base-en-v1.5 if updating the model.
_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


async def embed_text(text: str) -> list[float]:
    """Embed a single query string (applies BGE instruction prefix)."""
    model = _get_model()
    vec = await asyncio.to_thread(
        model.encode, _QUERY_PREFIX + text, normalize_embeddings=True
    )
    return vec.tolist()


async def embed_many(texts: list[str]) -> list[list[float]]:
    """Embed a batch of document strings (no prefix — document side of asymmetric retrieval)."""
    model = _get_model()
    vecs = await asyncio.to_thread(
        model.encode, texts, normalize_embeddings=True, batch_size=32
    )
    return vecs.tolist()
