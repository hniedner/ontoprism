"""Atomic embedding-corpus publication manifests and staging (#174)

Existing serving rows are deliberately not backfilled as complete: the migration
cannot recover their source/model provenance or prove completeness. An operator must
run the explicit validated build before readers treat either corpus as active.

Revision ID: 0007_embedding_publication
Revises: 0006_promotion_evidence
Create Date: 2026-07-24
"""

from alembic import op

revision: str = "0007_embedding_publication"
down_revision: str | None = "0006_promotion_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION embedding_text_array_unique(items text[]) RETURNS boolean
        LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
            SELECT count(*) = count(DISTINCT item) FROM unnest(items) item
        $$
        """
    )
    op.execute(
        """
        CREATE TABLE embedding_corpus_manifest (
            build_id uuid PRIMARY KEY,
            corpus text NOT NULL CHECK (corpus IN ('ncit', 'cadsr')),
            state text NOT NULL CHECK (state IN ('building', 'failed', 'complete')),
            is_active boolean NOT NULL DEFAULT false,
            source_version text NOT NULL,
            source_hash text NOT NULL,
            model_id text NOT NULL,
            model_revision text NOT NULL,
            vector_dimension integer NOT NULL CHECK (vector_dimension = 768),
            expected_row_count integer NOT NULL CHECK (expected_row_count > 0),
            actual_row_count integer CHECK (actual_row_count > 0),
            code_commit text NOT NULL,
            required_doc_ids text[] NOT NULL DEFAULT '{}',
            error_message text,
            created_at timestamptz NOT NULL DEFAULT now(),
            completed_at timestamptz,
            CHECK (nullif(btrim(source_version), '') IS NOT NULL),
            CHECK (nullif(btrim(source_hash), '') IS NOT NULL),
            CHECK (nullif(btrim(model_id), '') IS NOT NULL),
            CHECK (nullif(btrim(model_revision), '') IS NOT NULL),
            CHECK (nullif(btrim(code_commit), '') IS NOT NULL),
            CHECK (cardinality(required_doc_ids) > 0),
            CHECK (array_position(required_doc_ids, '') IS NULL),
            CHECK (embedding_text_array_unique(required_doc_ids)),
            CHECK (
                (state = 'building' AND NOT is_active
                    AND actual_row_count IS NULL AND completed_at IS NULL
                    AND error_message IS NULL)
                OR (state = 'failed' AND NOT is_active
                    AND actual_row_count IS NULL AND completed_at IS NULL
                    AND nullif(btrim(error_message), '') IS NOT NULL)
                OR (state = 'complete'
                    AND actual_row_count = expected_row_count
                    AND completed_at IS NOT NULL AND error_message IS NULL)
            )
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_embedding_corpus_active "
        "ON embedding_corpus_manifest (corpus) WHERE is_active"
    )
    op.execute(
        """
        CREATE TABLE embedding_corpus_staging (
            build_id uuid NOT NULL REFERENCES embedding_corpus_manifest(build_id)
                ON DELETE CASCADE,
            doc_id text NOT NULL,
            embedding vector(768) NOT NULL,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (build_id, doc_id)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS embedding_corpus_staging")
    op.execute("DROP TABLE IF EXISTS embedding_corpus_manifest")
    op.execute("DROP FUNCTION IF EXISTS embedding_text_array_unique(text[])")
