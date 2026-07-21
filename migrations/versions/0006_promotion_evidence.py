"""Per-promotion evidence on concept_xref (#122, D36)

A promoted bridge recorded *that* it was promoted but never *why*: the ``Evidence``
tuples the decision used (label agreement, xref assertion, structural corroboration, SME
curation) were computed in ``promotion.validate_candidate`` and discarded. D36 adds an
``evidence`` ``jsonb`` column holding those tuples, so an SME reviewing a bridge — and a
regression asking "why did this pair stop promoting?" — can read the answer off the row.

``NOT NULL DEFAULT '[]'`` so a candidate (no evidence) is always an empty array, never
null: read-back is then always an iterable. Backfilling existing rows to ``[]`` is
correct — they predate evidence capture, and ``[]`` says exactly that.

Revision ID: 0006_promotion_evidence
Revises: 0005_search_weights
Create Date: 2026-07-21
"""

from alembic import op

revision: str = "0006_promotion_evidence"
down_revision: str | None = "0005_search_weights"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE concept_xref "
        "ADD COLUMN evidence jsonb NOT NULL DEFAULT '[]'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE concept_xref DROP COLUMN IF EXISTS evidence")
