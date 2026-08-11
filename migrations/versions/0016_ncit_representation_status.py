"""Carry published pre-coordination status in the NCIt search cache.

Existing search publications are invalidated because their rows predate the status
column and therefore cannot answer status-filtered queries faithfully. The next normal
search-index refresh republishes a complete cache from QLever.

Revision ID: 0016_ncit_representation_status
Revises: 0015_source_manifests
Create Date: 2026-08-11
"""

from alembic import op

revision: str = "0016_ncit_representation_status"
down_revision: str | None = "0015_source_manifests"
branch_labels = None
depends_on = None

_CHECK = "ck_ncit_search_representation_status"


def upgrade() -> None:
    op.execute("ALTER TABLE ncit_search ADD COLUMN representation_status text")
    op.execute(
        "ALTER TABLE ncit_search ADD CONSTRAINT "
        f"{_CHECK} CHECK (representation_status IS NULL OR "
        "representation_status = 'legacy-precoordinated')"
    )
    op.execute("DELETE FROM ncit_search_manifest")


def downgrade() -> None:
    op.execute("DELETE FROM ncit_search_manifest")
    op.execute(f"ALTER TABLE ncit_search DROP CONSTRAINT IF EXISTS {_CHECK}")
    op.execute("ALTER TABLE ncit_search DROP COLUMN representation_status")
