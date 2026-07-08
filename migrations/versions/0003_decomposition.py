"""Decomposition provenance tables

Tables for the decomposition engine (Issue #4): decomp_run, decomp_constituent, and
minted_concept.  Tracks run manifests, per-concept constituents, and proposed mint IDs.
See docs/design/ncit-decomposition-engine.md §4.5.

Revision ID: 0003_decomposition
Revises: 0002_ncit_search
Create Date: 2026-07-07
"""

from alembic import op

revision: str = "0003_decomposition"
down_revision: str | None = "0002_ncit_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Run manifest - one row per decomposition pass.
    op.execute(
        "CREATE TABLE decomp_run ("
        "  id            text PRIMARY KEY,"
        "  branch        text NOT NULL,"
        "  status        text NOT NULL DEFAULT 'running',"
        "  ncit_version  text NOT NULL,"
        "  started_at    timestamptz NOT NULL,"
        "  finished_at   timestamptz,"
        "  metrics       jsonb"
        ")"
    )

    # Per-concept constituents.
    op.execute(
        "CREATE TABLE decomp_constituent ("
        "  run_id        text NOT NULL REFERENCES decomp_run(id),"
        "  concept_code  text NOT NULL,"
        "  axis          text NOT NULL,"
        "  filler_code   text NOT NULL,"
        "  axis_source   text NOT NULL,"
        "  most_specific boolean NOT NULL,"
        "  PRIMARY KEY (run_id, concept_code, axis, filler_code)"
        ")"
    )

    # Minted concepts - proposals for curator approval.
    op.execute(
        "CREATE TABLE minted_concept ("
        "  id            text PRIMARY KEY,"
        "  run_id        text NOT NULL REFERENCES decomp_run(id),"
        "  axis          text NOT NULL,"
        "  label         text NOT NULL,"
        "  source_signal text NOT NULL DEFAULT '',"
        "  status        text NOT NULL DEFAULT 'proposed'"
        ")"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS decomp_constituent")
    op.execute("DROP TABLE IF EXISTS minted_concept")
    op.execute("DROP TABLE IF EXISTS decomp_run")
