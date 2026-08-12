"""Add source-bound Uberon/CL FTS cache.

Revision ID: 0017_uberon_search
Revises: 0016_ncit_representation_status
Create Date: 2026-08-12
"""

from alembic import op

revision: str = "0017_uberon_search"
down_revision: str | None = "0016_ncit_representation_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE uberon_search (
          code text PRIMARY KEY,
          source text NOT NULL CHECK (
            (source = 'uberon' AND code ~ '^UBERON:[0-9]+$') OR
            (source = 'cl' AND code ~ '^CL:[0-9]+$')
          ),
          label text NOT NULL,
          synonyms text NOT NULL DEFAULT '',
          tsv tsvector GENERATED ALWAYS AS (
            setweight(to_tsvector('english', coalesce(label, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(synonyms, '')), 'B')
          ) STORED
        )"""
    )
    op.execute("CREATE INDEX idx_uberon_search_tsv ON uberon_search USING gin(tsv)")
    op.execute(
        "CREATE INDEX idx_uberon_search_source_code ON uberon_search(source, code)"
    )
    op.execute(
        """CREATE TABLE uberon_search_manifest (
          singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
          source_identity text NOT NULL CHECK (source_identity ~ '^[0-9a-f]{64}$'),
          source_hash text NOT NULL CHECK (source_hash ~ '^[0-9a-f]{64}$'),
          row_count bigint NOT NULL CHECK (row_count > 0),
          built_at timestamptz NOT NULL DEFAULT now()
        )"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS uberon_search_manifest")
    op.execute("DROP TABLE IF EXISTS uberon_search")
