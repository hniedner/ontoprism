"""Enforce canonical occurrence disposition filler and route states.

Revision ID: 0025_disposition_invariants
Revises: 0024_decomposition_run_stages
Create Date: 2026-09-10
"""

from alembic import op

revision: str = "0025_disposition_invariants"
down_revision: str | None = "0024_decomposition_run_stages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE decomp_occurrence_disposition
        ADD CONSTRAINT ck_decomp_disposition_filler_relation CHECK (
            (disposition IN (
                'retained-routed', 'retained-unknown', 'retained-policy-veto'
            ) AND retained_filler = source_filler)
            OR
            (disposition IN ('collapsed-is-a', 'collapsed-r82')
                AND retained_filler <> source_filler)
        ),
        ADD CONSTRAINT ck_decomp_disposition_r82_endpoints CHECK (
            (disposition = 'collapsed-r82'
                AND r82_part = retained_filler AND r82_whole = source_filler)
            OR
            (disposition <> 'collapsed-r82'
                AND r82_part IS NULL AND r82_whole IS NULL)
        ),
        ADD CONSTRAINT ck_decomp_disposition_semantic_route CHECK (
            semantic_route IN (
                'semantic-evidence-not-requested', 'missing-p106', 'p106-organ',
                'p106-non-organ-anatomy', 'reviewed-primary-subsite',
                'reviewed-lineage', 'reviewed-contextual-override',
                'unknown-role', 'role-contract'
            )
        )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE decomp_occurrence_disposition
        DROP CONSTRAINT IF EXISTS ck_decomp_disposition_semantic_route,
        DROP CONSTRAINT IF EXISTS ck_decomp_disposition_r82_endpoints,
        DROP CONSTRAINT IF EXISTS ck_decomp_disposition_filler_relation
        """
    )
