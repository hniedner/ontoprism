"""Generation-atomic, typed xref publication.

Revision ID: 0018_xref_generations
Revises: 0017_uberon_search
Create Date: 2026-08-12
"""

from alembic import op

revision: str = "0018_xref_generations"
down_revision: str | None = "0017_uberon_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE concept_xref RENAME TO concept_xref_legacy")
    op.execute(
        """CREATE TABLE xref_generation (
          id text PRIMARY KEY CHECK (id ~ '^[0-9a-f]{64}$'),
          source text NOT NULL,
          content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
          graph_iri text NOT NULL UNIQUE,
          state text NOT NULL CHECK (state IN ('prepared', 'published')),
          predecessor_id text REFERENCES xref_generation(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          published_at timestamptz,
          UNIQUE (source, content_sha256)
        )"""
    )
    op.execute(
        """CREATE TABLE xref_active_generation (
          source text PRIMARY KEY,
          generation_id text NOT NULL UNIQUE REFERENCES xref_generation(id),
          activated_at timestamptz NOT NULL DEFAULT now()
        )"""
    )
    op.execute(
        """CREATE TABLE concept_xref (
          generation_id text NOT NULL REFERENCES xref_generation(id),
          run_id text REFERENCES xref_run(id),
          subject_system text NOT NULL,
          subject_version text NOT NULL,
          subject_id text NOT NULL,
          predicate_id text NOT NULL,
          object_system text NOT NULL,
          object_version text NOT NULL,
          object_id text NOT NULL,
          mapping_justification text NOT NULL,
          confidence double precision NOT NULL CHECK (confidence BETWEEN 0 AND 1),
          lifecycle_state text NOT NULL,
          review_status text NOT NULL,
          author text NOT NULL,
          evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
          PRIMARY KEY (
            generation_id, subject_system, subject_version, subject_id,
            predicate_id, object_system, object_version, object_id
          )
        )"""
    )
    op.execute(
        "CREATE INDEX idx_concept_xref_forward ON concept_xref "
        "(subject_system, subject_version, subject_id, generation_id) "
        "INCLUDE (object_system, object_version, object_id, predicate_id, "
        "lifecycle_state, confidence)"
    )
    op.execute(
        "CREATE INDEX idx_concept_xref_reverse ON concept_xref "
        "(object_system, object_version, object_id, generation_id) "
        "INCLUDE (subject_system, subject_version, subject_id, predicate_id, "
        "lifecycle_state, confidence)"
    )
    op.execute(
        """CREATE TABLE xref_legacy_quarantine (
          quarantined_at timestamptz NOT NULL DEFAULT now(),
          reason text NOT NULL,
          legacy_row jsonb NOT NULL
        )"""
    )
    op.execute(
        "INSERT INTO xref_legacy_quarantine (reason, legacy_row) "
        "SELECT 'missing typed endpoint generation identity', to_jsonb(old) "
        "FROM concept_xref_legacy AS old"
    )
    op.execute("DROP TABLE concept_xref_legacy")


def downgrade() -> None:
    op.execute("ALTER TABLE concept_xref RENAME TO concept_xref_generation")
    op.execute(
        """CREATE TABLE concept_xref (
          run_id text NOT NULL REFERENCES xref_run(id),
          subject_id text NOT NULL,
          predicate_id text NOT NULL,
          object_id text NOT NULL,
          mapping_justification text NOT NULL,
          confidence double precision NOT NULL,
          subject_source_version text NOT NULL,
          object_source_version text NOT NULL,
          lifecycle_state text NOT NULL DEFAULT 'proposed',
          review_status text NOT NULL DEFAULT 'unreviewed',
          author text NOT NULL DEFAULT '',
          evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
          PRIMARY KEY (run_id, subject_id, predicate_id, object_id)
        )"""
    )
    op.execute("DROP TABLE concept_xref_generation")
    op.execute("DROP TABLE xref_active_generation")
    op.execute("DROP TABLE xref_generation")
    op.execute("DROP TABLE xref_legacy_quarantine")
