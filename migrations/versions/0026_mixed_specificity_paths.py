"""Persist truthful mixed-edge specificity collapse paths.

Revision ID: 0026_mixed_specificity_paths
Revises: 0025_disposition_invariants
Create Date: 2026-09-10
"""

from alembic import op

revision: str = "0026_mixed_specificity_paths"
down_revision: str | None = "0025_disposition_invariants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE decomp_occurrence_disposition
        DROP CONSTRAINT decomp_occurrence_disposition_disposition_check,
        DROP CONSTRAINT ck_decomp_disposition_filler_relation,
        ADD COLUMN specificity_path jsonb NOT NULL DEFAULT '[]'::jsonb,
        ADD CONSTRAINT decomp_occurrence_disposition_disposition_check CHECK (
            disposition IN (
                'retained-routed', 'retained-unknown', 'collapsed-is-a',
                'collapsed-r82', 'collapsed-mixed', 'retained-policy-veto'
            )
        ),
        ADD CONSTRAINT ck_decomp_disposition_filler_relation CHECK (
            (disposition IN (
                'retained-routed', 'retained-unknown', 'retained-policy-veto'
            ) AND retained_filler = source_filler)
            OR
            (disposition IN ('collapsed-is-a', 'collapsed-r82', 'collapsed-mixed')
                AND retained_filler <> source_filler)
        ),
        ADD CONSTRAINT ck_decomp_disposition_specificity_path CHECK (
            jsonb_typeof(specificity_path) = 'array'
            AND ((disposition = 'collapsed-mixed') =
                (jsonb_array_length(specificity_path) >= 2))
        )
        """
    )
    op.execute(
        "ALTER TABLE decomp_occurrence_disposition "
        "ALTER COLUMN specificity_path DROP DEFAULT"
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE decomp_occurrence_disposition
        DROP CONSTRAINT ck_decomp_disposition_specificity_path,
        DROP CONSTRAINT ck_decomp_disposition_filler_relation,
        DROP CONSTRAINT decomp_occurrence_disposition_disposition_check,
        DROP COLUMN specificity_path,
        ADD CONSTRAINT decomp_occurrence_disposition_disposition_check CHECK (
            disposition IN (
                'retained-routed', 'retained-unknown', 'collapsed-is-a',
                'collapsed-r82', 'retained-policy-veto'
            )
        ),
        ADD CONSTRAINT ck_decomp_disposition_filler_relation CHECK (
            (disposition IN (
                'retained-routed', 'retained-unknown', 'retained-policy-veto'
            ) AND retained_filler = source_filler)
            OR
            (disposition IN ('collapsed-is-a', 'collapsed-r82')
                AND retained_filler <> source_filler)
        )
        """
    )
