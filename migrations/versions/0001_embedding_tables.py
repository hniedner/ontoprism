"""embedding tables (pgvector + HNSW cosine)

Reproduces the two 768-dim embedding tables the semantic-similarity endpoints read
(``ncit_concepts``, ``cde_repository``). These previously existed only because they were
pg_dump'd from the sibling fairdata DB; this migration makes the schema reproducible.

For the pre-existing dev DB (the clone), run ``alembic stamp head`` once to mark this
applied without re-creating the tables; a fresh DB uses ``alembic upgrade head``.

Revision ID: 0001_embedding_tables
Revises:
Create Date: 2026-07-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_embedding_tables"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# doc_id key: NCIt concept code / caDSR "{public_id}:{version}". Matches the live schema
# (introspected): text PK, vector(768) NOT NULL, jsonb metadata, HNSW cosine index.
_EMBEDDING_TABLES = ("ncit_concepts", "cde_repository")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    for table in _EMBEDDING_TABLES:
        op.execute(
            f"CREATE TABLE {table} ("
            "  doc_id text PRIMARY KEY,"
            "  embedding vector(768) NOT NULL,"
            "  metadata jsonb NOT NULL DEFAULT '{}'::jsonb"
            ")"
        )
        op.execute(
            f"CREATE INDEX idx_{table}_hnsw ON {table} "
            "USING hnsw (embedding vector_cosine_ops) "
            "WITH (m=16, ef_construction=64)"
        )


def downgrade() -> None:
    for table in _EMBEDDING_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
