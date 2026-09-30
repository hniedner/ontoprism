"""Replace lossy NCIt Semantic Type projection; rebuild required (owner #506)."""

from alembic import op

revision = "0032_ncit_semantic_types"
down_revision = "0031_r101_run_conservation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A scalar cannot recover the full source set. Refuse search until rebuilt.
    op.execute("DELETE FROM ncit_search_manifest")
    op.execute("TRUNCATE ncit_search")
    op.execute(
        "ALTER TABLE ncit_search DROP COLUMN semantic_type, "
        "ADD COLUMN semantic_types text[] NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_ncit_search_semantic_types "
        "ON ncit_search USING gin (semantic_types)"
    )


def downgrade() -> None:
    op.execute("DELETE FROM ncit_search_manifest")
    op.execute("TRUNCATE ncit_search")
    op.execute(
        "ALTER TABLE ncit_search DROP COLUMN semantic_types, "
        "ADD COLUMN semantic_type text"
    )
