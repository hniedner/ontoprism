"""NCIt search FTS cache (Postgres tsvector + GIN)

A materialized full-text search cache over NCIt concept label + synonyms, so search
and browse are served from an index instead of a live SPARQL ``CONTAINS`` scan over
~204k ``owl:Class`` per keystroke. The Oxigraph store stays the source of truth; this
cache is (re)populated from it (see ontolib.terminologies.ncit.search_index) and the
API falls back to SPARQL when the cache is empty.

Revision ID: 0002_ncit_search
Revises: 0001_embedding_tables
Create Date: 2026-07-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_ncit_search"
down_revision: str | None = "0001_embedding_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE ncit_search ("
        "  code text PRIMARY KEY,"
        "  label text NOT NULL,"
        "  semantic_type text,"
        "  synonyms text NOT NULL DEFAULT '',"
        "  tsv tsvector GENERATED ALWAYS AS ("
        "    to_tsvector('english', coalesce(label,'') || ' ' || coalesce(synonyms,''))"
        "  ) STORED"
        ")"
    )
    op.execute("CREATE INDEX idx_ncit_search_tsv ON ncit_search USING gin(tsv)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ncit_search")
