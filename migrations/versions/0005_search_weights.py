"""Weight the NCIt search index: a concept's label outranks its synonyms

``0002_ncit_search`` built ``tsv`` by concatenating ``label`` and ``synonyms`` into
one unweighted ``to_tsvector``.  ``ts_rank`` scores by term *frequency*, so a concept
carrying thirty synonyms that each mention "neoplasm" outscored the concept whose
label **is** "Neoplasm" -- on the live store that put C3262 at rank ~256 for the query
``neoplasm``, behind concepts whose labels do not contain the word at all.

``setweight`` fixes it at the index: the label enters as weight ``A`` and the synonyms
as ``B``, and ``ts_rank``'s default weights (A=1.0, B=0.4) then rank a name match above
a synonym match with no change to the query.  A generated ``STORED`` column is
recomputed for every existing row when it is added, so the cache needs no repopulation.

Revision ID: 0005_search_weights
Revises: 0004_xref
Create Date: 2026-07-14
"""

from alembic import op

revision: str = "0005_search_weights"
down_revision: str | None = "0004_xref"
branch_labels = None
depends_on = None

_WEIGHTED = (
    "setweight(to_tsvector('english', coalesce(label,'')), 'A') || "
    "setweight(to_tsvector('english', coalesce(synonyms,'')), 'B')"
)
_UNWEIGHTED = (
    "to_tsvector('english', coalesce(label,'') || ' ' || coalesce(synonyms,''))"
)


def _rebuild_tsv(expression: str) -> None:
    """Swap the generated column's expression -- Postgres cannot ALTER one in place."""
    op.execute("DROP INDEX IF EXISTS idx_ncit_search_tsv")
    op.execute("ALTER TABLE ncit_search DROP COLUMN IF EXISTS tsv")
    op.execute(
        "ALTER TABLE ncit_search ADD COLUMN tsv tsvector "
        f"GENERATED ALWAYS AS ({expression}) STORED"
    )
    op.execute("CREATE INDEX idx_ncit_search_tsv ON ncit_search USING gin(tsv)")


def upgrade() -> None:
    _rebuild_tsv(_WEIGHTED)


def downgrade() -> None:
    _rebuild_tsv(_UNWEIGHTED)
