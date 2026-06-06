"""Add pgvector extension and embedding column to interactions.

Enables the vector extension (bundled in the pgvector Docker image), adds a
``vector(384)`` column for all-MiniLM-L6-v2 embeddings, and creates an
ivfflat approximate-nearest-neighbour index for cosine-distance search.

The index ``lists=10`` is tuned for small tables (demo / dev).  For tables
approaching 100 K rows run:

    REINDEX INDEX CONCURRENTLY idx_interactions_embedding;

after setting the index to ``lists = sqrt(row_count)``.

Revision ID: 0004
Revises:     0003
Create Date: 2026-06-06
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The pgvector Docker image ships with the extension pre-built; this is a
    # no-op if it was already enabled.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute(
        "ALTER TABLE interactions "
        "ADD COLUMN IF NOT EXISTS embedding vector(384)"
    )

    # ivfflat uses a coarse quantization index for approximate search.
    # lists=10 is suitable for tables up to ~10 K rows; revisit for production.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_interactions_embedding "
        "ON interactions USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 10)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_interactions_embedding")
    op.execute(
        "ALTER TABLE interactions DROP COLUMN IF EXISTS embedding"
    )
    # Intentionally NOT dropping the vector extension — other tables may use it.
