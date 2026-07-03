"""Vector similarity search over the knowledge base."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_chunk import KnowledgeChunk
from app.services.embeddings import embed_text


async def search_knowledge(
    query: str,
    workspace_id: str,
    db: AsyncSession,
    k: int = 5,
) -> list[KnowledgeChunk]:
    """Return the k most relevant chunks for a query using cosine similarity."""
    query_embedding = await embed_text(query)
    result = await db.execute(
        select(KnowledgeChunk)
        .where(KnowledgeChunk.workspace_id == workspace_id)
        .order_by(KnowledgeChunk.embedding.cosine_distance(query_embedding))
        .limit(k)
    )
    return list(result.scalars().all())
