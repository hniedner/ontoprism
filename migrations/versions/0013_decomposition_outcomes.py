"""Persist explicit per-concept decomposition outcomes (#255).

Revision ID: 0013_decomposition_outcomes
Revises: 0012_nested_definition_groups
Create Date: 2026-07-30
"""

from alembic import op

revision: str = "0013_decomposition_outcomes"
down_revision: str | None = "0012_nested_definition_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE decomp_work_item
            ADD COLUMN outcome text,
            ADD COLUMN semantic_types jsonb
        """
    )
    op.execute(
        """
        UPDATE decomp_work_item
        SET
            outcome = CASE
                WHEN state <> 'complete' THEN NULL
                WHEN is_decomposed THEN 'decomposed'
                WHEN is_residual THEN 'residual'
                ELSE 'unknown'
            END,
            semantic_types = CASE
                WHEN state <> 'complete' THEN NULL
                WHEN semantic_type IS NULL THEN '[]'::jsonb
                ELSE jsonb_build_array(semantic_type)
            END
        """
    )
    op.execute(
        """
        ALTER TABLE decomp_work_item
            ADD CONSTRAINT ck_decomp_work_item_outcome
                CHECK (
                    outcome IS NULL
                    OR outcome IN (
                        'decomposed',
                        'residual',
                        'semantic-excluded',
                        'atomic-no-op',
                        'unknown'
                    )
                ),
            ADD CONSTRAINT ck_decomp_work_item_semantic_types
                CHECK (
                    semantic_types IS NULL
                    OR jsonb_typeof(semantic_types) = 'array'
                ),
            ADD CONSTRAINT ck_decomp_work_item_outcome_shape
                CHECK (
                    (
                        state <> 'complete'
                        AND outcome IS NULL
                        AND semantic_types IS NULL
                    )
                    OR
                    (
                        state = 'complete'
                        AND outcome IS NOT NULL
                        AND semantic_types IS NOT NULL
                        AND (
                            (
                                outcome = 'decomposed'
                                AND is_decomposed
                                AND NOT is_residual
                                AND constituent_count > 0
                            )
                            OR
                            (
                                outcome = 'residual'
                                AND NOT is_decomposed
                                AND is_residual
                                AND constituent_count = 0
                                AND minted_count = 0
                            )
                            OR
                            (
                                outcome IN (
                                    'semantic-excluded',
                                    'atomic-no-op',
                                    'unknown'
                                )
                                AND NOT is_decomposed
                                AND NOT is_residual
                                AND constituent_count = 0
                                AND minted_count = 0
                            )
                        )
                    )
                )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE decomp_work_item
            DROP CONSTRAINT IF EXISTS ck_decomp_work_item_outcome_shape,
            DROP CONSTRAINT IF EXISTS ck_decomp_work_item_semantic_types,
            DROP CONSTRAINT IF EXISTS ck_decomp_work_item_outcome,
            DROP COLUMN IF EXISTS semantic_types,
            DROP COLUMN IF EXISTS outcome
        """
    )
