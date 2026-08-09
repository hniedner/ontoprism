"""Preserve the NCIt source role behind each normalized axis (#157).

Revision ID: 0010_constituent_source_role
Revises: 0009_complete_definition
Create Date: 2026-07-30
"""

from alembic import op

revision: str = "0010_constituent_source_role"
down_revision: str | None = "0009_complete_definition"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE decomp_constituent
            ADD COLUMN source_role text,
            ADD CONSTRAINT ck_decomp_constituent_source_role
                CHECK (source_role IS NULL OR source_role ~ '^R[0-9]+$')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE decomp_constituent
            DROP CONSTRAINT IF EXISTS ck_decomp_constituent_source_role,
            DROP COLUMN IF EXISTS source_role
        """
    )
