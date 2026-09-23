"""Persist per-run R101 conservation categories.

Revision ID: 0031_r101_run_conservation
Revises: 0030_axis_ambiguity_default
Create Date: 2026-09-23
"""

from alembic import op

revision: str = "0031_r101_run_conservation"
down_revision: str | None = "0030_axis_ambiguity_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE decomp_occurrence_disposition
        ADD COLUMN r82_path jsonb NOT NULL DEFAULT '[]'::jsonb,
        ADD CONSTRAINT ck_decomp_disposition_r82_path CHECK (
            jsonb_typeof(r82_path) = 'array'
            AND (disposition = 'collapsed-r82' OR jsonb_array_length(r82_path) = 0)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE decomp_r101_conservation (
            run_id text NOT NULL,
            concept_code text NOT NULL,
            occurrence_id text NOT NULL,
            source_fact_id text NOT NULL,
            source_filler text NOT NULL CHECK (source_filler ~ '^C[0-9]+$'),
            category text NOT NULL CHECK (category IN (
                'projected', 'unchanged-unprojected', 'one-step-r82',
                'closure-only-r82', 'unresolved'
            )),
            reason text NOT NULL CHECK (length(reason) > 0),
            r82_path jsonb NOT NULL,
            PRIMARY KEY (run_id, concept_code, occurrence_id),
            FOREIGN KEY (run_id, concept_code, occurrence_id)
                REFERENCES decomp_source_occurrence(run_id, concept_code, occurrence_id)
                ON DELETE CASCADE,
            FOREIGN KEY (run_id, concept_code, source_fact_id)
                REFERENCES decomp_definition_fact(run_id, concept_code, fact_id)
                ON DELETE CASCADE,
            CHECK (jsonb_typeof(r82_path) = 'array'),
            CHECK (category != 'one-step-r82' OR jsonb_array_length(r82_path) = 1),
            CHECK (category != 'closure-only-r82' OR
                jsonb_array_length(r82_path) >= 2),
            CHECK (category IN ('one-step-r82', 'closure-only-r82', 'unresolved') OR
                jsonb_array_length(r82_path) = 0)
        )
        """
    )
    op.execute(
        "ALTER TABLE decomp_occurrence_disposition ALTER COLUMN r82_path DROP DEFAULT"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS decomp_r101_conservation")
    op.execute(
        "ALTER TABLE decomp_occurrence_disposition "
        "DROP CONSTRAINT ck_decomp_disposition_r82_path, DROP COLUMN r82_path"
    )
