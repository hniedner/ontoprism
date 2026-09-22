"""Replace ambiguity-group labels with a boolean ambiguity flag.

Revision ID: 0029_boolean_axis_ambiguity
Revises: 0028_distinct_group_identities
Create Date: 2026-09-22
"""

from alembic import op

revision: str = "0029_boolean_axis_ambiguity"
down_revision: str | None = "0028_distinct_group_identities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'decomp_constituent'
                  AND column_name = 'axis_ambiguity_group_id'
            ) THEN
                ALTER TABLE decomp_constituent
                ADD COLUMN axis_ambiguous boolean NOT NULL DEFAULT false;
                UPDATE decomp_constituent SET axis_ambiguous =
                    axis_ambiguity_group_id IS NOT NULL;
                ALTER TABLE decomp_constituent
                DROP CONSTRAINT ck_decomp_constituent_axis_ambiguity_group,
                DROP COLUMN axis_ambiguity_group_id;
            ELSIF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'decomp_constituent'
                  AND column_name = 'axis_ambiguous'
            ) THEN
                RAISE EXCEPTION '0028 ambiguity column is missing';
            END IF;
            ALTER TABLE decomp_constituent
            ALTER COLUMN axis_ambiguous DROP DEFAULT;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE decomp_constituent ADD COLUMN axis_ambiguity_group_id text")
    op.execute(
        "UPDATE decomp_constituent SET axis_ambiguity_group_id = axis "
        "WHERE axis_ambiguous"
    )
    op.execute(
        """
        ALTER TABLE decomp_constituent
        ADD CONSTRAINT ck_decomp_constituent_axis_ambiguity_group CHECK (
            axis_ambiguity_group_id IS NULL OR length(axis_ambiguity_group_id) > 0
        ),
        DROP COLUMN axis_ambiguous
        """
    )
