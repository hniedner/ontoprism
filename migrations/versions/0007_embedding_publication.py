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
        CREATE FUNCTION reject_embedding_provenance_update() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            IF (NEW.corpus, NEW.source_version, NEW.source_hash, NEW.model_id,
                NEW.model_revision, NEW.vector_dimension, NEW.expected_row_count,
                NEW.code_commit, NEW.required_doc_ids)
               IS DISTINCT FROM
               (OLD.corpus, OLD.source_version, OLD.source_hash, OLD.model_id,
                OLD.model_revision, OLD.vector_dimension, OLD.expected_row_count,
                OLD.code_commit, OLD.required_doc_ids) THEN
                RAISE EXCEPTION 'embedding build provenance is immutable';
            END IF;
            RETURN NEW;
        END $$
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
            CHECK (source_hash ~ '^[0-9a-f]{64}$'),
            CHECK (nullif(btrim(model_id), '') IS NOT NULL),
            CHECK (nullif(btrim(model_revision), '') IS NOT NULL),
            CHECK (model_revision ~ '^[0-9a-f]{40}$'),
            CHECK (nullif(btrim(code_commit), '') IS NOT NULL),
            CHECK (code_commit ~ '^[0-9a-f]{40}$'),
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
        "CREATE TRIGGER embedding_provenance_immutable BEFORE UPDATE ON "
        "embedding_corpus_manifest FOR EACH ROW EXECUTE FUNCTION "
        "reject_embedding_provenance_update()"
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
    op.execute("DROP FUNCTION IF EXISTS reject_embedding_provenance_update()")
    op.execute("DROP FUNCTION IF EXISTS embedding_text_array_unique(text[])")
