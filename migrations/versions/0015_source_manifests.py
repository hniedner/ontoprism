"""Bind search and embedding publications to active proxy identities (#266).

Existing embedding publications are deliberately deactivated: their canonical row
hashes are known, but no migration can recover which certified proxy manifest produced
them. Historical rows remain inspectable with a null source identity; a publication can
be active only when the new identity is present and valid.

Revision ID: 0015_source_manifests
Revises: 0014_definition_presence
Create Date: 2026-08-10
"""

from alembic import op

revision: str = "0015_source_manifests"
down_revision: str | None = "0014_definition_presence"
branch_labels = None
depends_on = None


_IMMUTABLE_WITH_IDENTITY = """
CREATE OR REPLACE FUNCTION reject_embedding_provenance_update() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
    IF (NEW.build_id, NEW.corpus, NEW.source_identity, NEW.source_version,
        NEW.source_hash, NEW.model_id, NEW.model_revision, NEW.vector_dimension,
        NEW.expected_row_count, NEW.code_commit, NEW.required_doc_ids)
       IS DISTINCT FROM
       (OLD.build_id, OLD.corpus, OLD.source_identity, OLD.source_version,
        OLD.source_hash, OLD.model_id, OLD.model_revision, OLD.vector_dimension,
        OLD.expected_row_count, OLD.code_commit, OLD.required_doc_ids) THEN
        RAISE EXCEPTION 'embedding build provenance is immutable';
    END IF;
    RETURN NEW;
END $$
"""

_IMMUTABLE_WITHOUT_IDENTITY = """
CREATE OR REPLACE FUNCTION reject_embedding_provenance_update() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
    IF (NEW.build_id, NEW.corpus, NEW.source_version, NEW.source_hash, NEW.model_id,
        NEW.model_revision, NEW.vector_dimension, NEW.expected_row_count,
        NEW.code_commit, NEW.required_doc_ids)
       IS DISTINCT FROM
       (OLD.build_id, OLD.corpus, OLD.source_version, OLD.source_hash, OLD.model_id,
        OLD.model_revision, OLD.vector_dimension, OLD.expected_row_count,
        OLD.code_commit, OLD.required_doc_ids) THEN
        RAISE EXCEPTION 'embedding build provenance is immutable';
    END IF;
    RETURN NEW;
END $$
"""


def upgrade() -> None:
    op.execute("ALTER TABLE embedding_corpus_manifest ADD source_identity text")
    op.execute("UPDATE embedding_corpus_manifest SET is_active = false WHERE is_active")
    op.execute(
        "ALTER TABLE embedding_corpus_manifest ADD CONSTRAINT "
        "ck_embedding_source_identity_format CHECK (source_identity IS NULL OR "
        "source_identity ~ '^[0-9a-f]{64}$')"
    )
    op.execute(
        "ALTER TABLE embedding_corpus_manifest ADD CONSTRAINT "
        "ck_embedding_active_source_identity CHECK "
        "(NOT is_active OR source_identity IS NOT NULL)"
    )
    op.execute(_IMMUTABLE_WITH_IDENTITY)
    op.execute(
        """
        CREATE TABLE ncit_search_manifest (
            singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
            source_identity text NOT NULL
                CHECK (source_identity ~ '^[0-9a-f]{64}$'),
            source_hash text NOT NULL CHECK (source_hash ~ '^[0-9a-f]{64}$'),
            row_count bigint NOT NULL CHECK (row_count > 0),
            built_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ncit_search_manifest")
    op.execute(_IMMUTABLE_WITHOUT_IDENTITY)
    op.execute(
        "ALTER TABLE embedding_corpus_manifest "
        "DROP CONSTRAINT IF EXISTS ck_embedding_active_source_identity"
    )
    op.execute(
        "ALTER TABLE embedding_corpus_manifest "
        "DROP CONSTRAINT IF EXISTS ck_embedding_source_identity_format"
    )
    op.execute("ALTER TABLE embedding_corpus_manifest DROP COLUMN source_identity")
