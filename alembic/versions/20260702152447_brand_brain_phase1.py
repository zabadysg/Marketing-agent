"""brand brain phase1

Revision ID: 20260702152447
Revises: 755ee710511d
Create Date: 2026-07-02 15:24:47

Adds:
- pgvector extension
- Expanded BrandProfile columns (renames name→brand_name, audience→audience_segments, drops language)
- knowledge_documents table
- knowledge_chunks table with vector(768) embedding column and IVFFlat index
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260702152447"
down_revision: Union[str, None] = "755ee710511d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Enable pgvector ──────────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ── 2. Rename / drop existing BrandProfile columns ─────────────────────
    # name → brand_name (preserve data)
    op.alter_column("brand_profiles", "name", new_column_name="brand_name")
    # audience → _audience_legacy (temporary; migrated to audience_segments below)
    op.alter_column("brand_profiles", "audience", new_column_name="_audience_legacy")
    # language is dropped (unused beyond a language tag)
    op.drop_column("brand_profiles", "language")

    # ── 3. Loosen NOT NULL on renamed and kept columns ──────────────────────
    op.alter_column("brand_profiles", "brand_name", nullable=True)
    op.alter_column("brand_profiles", "tone", nullable=True)

    # ── 4. Add new BrandProfile columns ────────────────────────────────────
    op.add_column("brand_profiles", sa.Column("company_name", sa.String(), nullable=True))
    op.add_column("brand_profiles", sa.Column("industry", sa.String(), nullable=True))
    op.add_column(
        "brand_profiles",
        sa.Column("products", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "brand_profiles",
        sa.Column("audience_segments", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column("brand_profiles", sa.Column("voice_guidelines", sa.Text(), nullable=True))
    op.add_column("brand_profiles", sa.Column("positioning", sa.Text(), nullable=True))
    op.add_column(
        "brand_profiles",
        sa.Column("goals", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "brand_profiles",
        sa.Column(
            "onboarding_status",
            sa.Enum(
                "in_progress", "pending_review", "active",
                name="onboardingstatus",
                native_enum=False,
            ),
            nullable=False,
            server_default="in_progress",
        ),
    )

    # ── 5. Migrate legacy audience text → first audience_segment entry ─────
    op.execute(
        """
        UPDATE brand_profiles
        SET audience_segments = jsonb_build_array(
            jsonb_build_object(
                'name', 'Primary Audience',
                'description', _audience_legacy,
                'pain_points', '[]'::jsonb,
                'channels', '[]'::jsonb
            )
        )
        WHERE _audience_legacy IS NOT NULL AND _audience_legacy != ''
        """
    )
    op.drop_column("brand_profiles", "_audience_legacy")

    # ── 6. knowledge_documents table ───────────────────────────────────────
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column(
            "workspace_id",
            sa.String(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("doc_type", sa.String(), nullable=False, server_default="other"),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "processing", "indexed", "failed",
                name="documentstatus",
                native_enum=False,
            ),
            nullable=False,
            server_default="processing",
        ),
        sa.Column("uploaded_at", sa.String(), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_knowledge_documents_workspace_id",
        "knowledge_documents",
        ["workspace_id"],
    )

    # ── 7. knowledge_chunks table + vector column ───────────────────────────
    # The vector column cannot be expressed as a standard sa.Column type;
    # it is added via raw SQL after table creation.
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column(
            "document_id",
            sa.String(),
            sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.String(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.String(), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("ALTER TABLE knowledge_chunks ADD COLUMN embedding vector(768)")
    # IVFFlat index with lists=10 is appropriate for small datasets (< 100K chunks).
    # Increase lists or switch to HNSW if the corpus grows significantly.
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_embedding "
        "ON knowledge_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10)"
    )
    op.create_index("ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"])
    op.create_index("ix_knowledge_chunks_workspace_id", "knowledge_chunks", ["workspace_id"])


def downgrade() -> None:
    raise NotImplementedError(
        "Brand Brain phase1 migration is irreversible. "
        "To roll back, restore from a database backup taken before this migration."
    )
