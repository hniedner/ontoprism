"""Preserve explicitly empty complete definitions.

Revision ID: 0014_definition_presence
Revises: 0013_decomposition_outcomes
Create Date: 2026-08-05
"""

from alembic import op

revision: str = "0014_definition_presence"
down_revision: str | None = "0013_decomposition_outcomes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE decomp_work_item
            ADD COLUMN has_complete_definition boolean NOT NULL DEFAULT false
        """
    )
    op.execute(
        """
        UPDATE decomp_work_item AS work
        SET has_complete_definition = true
        WHERE EXISTS (
            SELECT 1 FROM decomp_definition_fact AS fact
            WHERE fact.run_id = work.run_id
              AND fact.concept_code = work.concept_code
        ) OR EXISTS (
            SELECT 1 FROM decomp_definition_group AS definition_group
            WHERE definition_group.run_id = work.run_id
              AND definition_group.concept_code = work.concept_code
        )
        """
    )
    op.execute(
        """
        ALTER TABLE decomp_run
            ADD COLUMN publication_predecessor_captured boolean NOT NULL
                DEFAULT false,
            ADD COLUMN publication_predecessor jsonb,
            ADD CONSTRAINT ck_decomp_run_publication_predecessor
                CHECK (
                    (
                        -- SQL NULL before capture; jsonb 'null' once captured for a
                        -- first publication, which legitimately has no predecessor.
                        publication_predecessor IS NULL
                        OR jsonb_typeof(publication_predecessor) = 'null'
                        OR (
                            publication_predecessor_captured
                            AND jsonb_typeof(publication_predecessor) = 'object'
                        )
                    )
                    AND (
                        publication_state NOT IN
                            ('legacy', 'not_requested', 'pending')
                        OR (
                            NOT publication_predecessor_captured
                            AND publication_predecessor IS NULL
                        )
                    )
                )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE decomp_run
            DROP CONSTRAINT IF EXISTS ck_decomp_run_publication_predecessor,
            DROP COLUMN IF EXISTS publication_predecessor,
            DROP COLUMN IF EXISTS publication_predecessor_captured
        """
    )
    op.execute(
        """
        ALTER TABLE decomp_work_item
            DROP COLUMN IF EXISTS has_complete_definition
        """
    )
