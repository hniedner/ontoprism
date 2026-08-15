"""Bind ICD-O relational read columns to the certified JSON payload.

Revision ID: 0020_icdo_record_consistency
Revises: 0019_icdo_repositories
Create Date: 2026-08-14
"""

from alembic import op

revision: str = "0020_icdo_record_consistency"
down_revision: str | None = "0019_icdo_repositories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_icdo_record_code_payload",
        "icdo_record",
        "code = payload->>'code'",
    )
    op.create_check_constraint(
        "ck_icdo_record_level_payload",
        "icdo_record",
        "level = payload->>'level'",
    )
    op.create_check_constraint(
        "ck_icdo_record_behaviour_payload",
        "icdo_record",
        "behaviour IS NOT DISTINCT FROM payload->>'behaviour'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_icdo_record_behaviour_payload", "icdo_record", type_="check")
    op.drop_constraint("ck_icdo_record_level_payload", "icdo_record", type_="check")
    op.drop_constraint("ck_icdo_record_code_payload", "icdo_record", type_="check")
