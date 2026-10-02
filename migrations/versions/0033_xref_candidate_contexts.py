"""Persist candidate source-role routes for mapping review (#151)."""

from alembic import op

revision = "0033_xref_candidate_contexts"
down_revision = "0032_ncit_semantic_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE concept_xref "
        "ADD COLUMN candidate_contexts jsonb NOT NULL DEFAULT '[]'::jsonb, "
        "ADD CONSTRAINT concept_xref_candidate_contexts_array "
        "CHECK (jsonb_typeof(candidate_contexts) = 'array')"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE concept_xref DROP COLUMN candidate_contexts")
