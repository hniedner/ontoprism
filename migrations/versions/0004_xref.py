"""Additive alignment provenance tables.

Initial tables for NCIt alignments derived from corroborating terminologies (issue #71):
xref_run and concept_xref. Migration 0018 replaces the row model with typed,
generation-atomic publication. See docs/design/ncit-alignment-integration.md §8.3.

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
        "  status        text NOT NULL DEFAULT 'running' "
        "CHECK (status IN ('running','completed','failed')) ,"
        "  ncit_version  text NOT NULL,"
        "  source_version text NOT NULL,"
        "  started_at    timestamptz NOT NULL,"
        "  finished_at   timestamptz,"
        "  metrics       jsonb,"
        "  CHECK ((status = 'running' AND finished_at IS NULL AND metrics IS NULL) OR "
        "         (status IN ('completed','failed') AND finished_at IS NOT NULL "
        "          AND metrics IS NOT NULL))"
        ")"
    )
    op.execute(
        """CREATE FUNCTION prevent_terminal_xref_run_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.status IN ('completed','failed') AND NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'terminal xref_run rows are immutable';
          END IF;
          RETURN NEW;
        END $$"""
    )
    op.execute(
        "CREATE TRIGGER xref_run_terminal_immutable BEFORE UPDATE ON xref_run "
        "FOR EACH ROW EXECUTE FUNCTION prevent_terminal_xref_run_update()"
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
    op.execute("DROP FUNCTION IF EXISTS prevent_terminal_xref_run_update()")
