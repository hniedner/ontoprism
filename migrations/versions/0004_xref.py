"""External xref provenance tables

Tables for the NCIt<->upstream mapping layer (issue #71): xref_run and concept_xref.
See docs/design/ncit-external-integration.md §8.3.

Revision ID: 0004_xref
Revises: 0003_decomposition
Create Date: 2026-07-11
"""

from alembic import op

revision: str = "0004_xref"
down_revision: str | None = "0003_decomposition"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE xref_run ("
        "  id            text PRIMARY KEY,"
        "  source        text NOT NULL,"
        "  status        text NOT NULL DEFAULT 'running',"
        "  ncit_version  text NOT NULL,"
        "  source_version text NOT NULL,"
        "  started_at    timestamptz NOT NULL,"
        "  finished_at   timestamptz,"
        "  metrics       jsonb"
        ")"
    )
    op.execute(
        "CREATE TABLE concept_xref ("
        "  run_id                 text NOT NULL REFERENCES xref_run(id),"
        "  subject_id             text NOT NULL,"
        "  predicate_id           text NOT NULL,"
        "  object_id              text NOT NULL,"
        "  mapping_justification  text NOT NULL,"
        "  confidence             double precision NOT NULL,"
        "  subject_source_version text NOT NULL,"
        "  object_source_version  text NOT NULL,"
        "  lifecycle_state        text NOT NULL DEFAULT 'proposed',"
        "  review_status          text NOT NULL DEFAULT 'unreviewed',"
        "  author                 text NOT NULL DEFAULT '',"
        "  PRIMARY KEY (run_id, subject_id, predicate_id, object_id)"
        ")"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS concept_xref")
    op.execute("DROP TABLE IF EXISTS xref_run")
