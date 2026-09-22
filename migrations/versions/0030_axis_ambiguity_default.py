"""Default omitted ambiguity flags to false.

Revision ID: 0030_axis_ambiguity_default
Revises: 0029_boolean_axis_ambiguity
Create Date: 2026-09-22
"""

from alembic import op

revision: str = "0030_axis_ambiguity_default"
down_revision: str | None = "0029_boolean_axis_ambiguity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE decomp_constituent ALTER COLUMN axis_ambiguous SET DEFAULT false"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE decomp_constituent ALTER COLUMN axis_ambiguous DROP DEFAULT"
    )
