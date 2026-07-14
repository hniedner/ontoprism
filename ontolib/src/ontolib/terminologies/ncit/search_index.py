"""Materialized full-text search over NCIt concepts (Postgres tsvector + GIN).

Serves NCIt search/browse from an index rather than a live SPARQL ``CONTAINS`` scan
over ~204k classes per request. The Oxigraph store stays the source of truth: this
cache is (re)populated from it via :func:`populate_from_store`, and callers fall back
to the store's SPARQL search when the cache is empty (see the NCIt search endpoint).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

from ontolib.terminologies.ncit.models import SearchHit, SearchPage

if TYPE_CHECKING:
    from collections.abc import AsyncIterable, AsyncIterator, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ontolib.terminologies.ncit.graph_store import NcitGraphStore

# websearch_to_tsquery gives users familiar query syntax (quoted phrases, OR, -term)
# while being injection-safe. COUNT(*) OVER () returns the full total in one query.
#
# The ORDER BY has four tiers, and every one of them is load-bearing:
#
# 1. An EXACT NAME MATCH wins. `ts_rank` scores by weighted term frequency, so every
#    concept whose label contains the term once scores *identically* -- searching
#    "neoplasm" tied C3262 (whose label IS "Neoplasm") with hundreds of "... Neoplasm"
#    concepts. `btrim` because `:q` is the raw user string: a trailing space from a
#    paste made this tier miss and dropped C3262 straight back to rank ~224.
#    The tier only fires for a bare term -- a websearch DSL query (`OR`, `-x`, quotes)
#    is compared literally against the label and never matches, by design. Tier 3 is
#    what carries those.
# 2. THEN WEIGHTED RELEVANCE. `tsv` is `setweight`ed at the index (migration
#    0005_search_weights): label 'A', synonyms 'B'. Without it, a concept listing the
#    term across thirty synonyms outranked the concept *named* for it, because raw
#    frequency was all that counted. This is the tier that governs every *partial*
#    query ("breast neoplasm"), which is most real searches -- tier 1 cannot help there.
# 3. THEN THE SHORTER LABEL. Equal relevance breaks toward the more specific name, not
#    toward the alphabet. Without this, a `ts_rank` tie fell through to alphabetical
#    order, which is not relevance -- it is what buried "Neoplasm" behind hundreds of
#    equally-scored "... Neoplasm" concepts, and it is what a quoted or operator query
#    (which tier 1 cannot rescue) would still hit.
# 4. `label`, then `code`, purely to make the order TOTAL. `label` is not unique in
#    NCIt (204,373 rows, 204,238 distinct labels), so without the primary key the sort
#    is underdetermined and a tied row can appear on two pages of a LIMIT/OFFSET walk,
#    or on none.
_SEARCH_SQL = """
    SELECT code, label, semantic_type, COUNT(*) OVER () AS total
    FROM ncit_search, websearch_to_tsquery('english', :q) AS q
    WHERE tsv @@ q
    ORDER BY (lower(label) = lower(btrim(:q))) DESC, ts_rank(tsv, q) DESC,
             length(label), label, code
    LIMIT :limit OFFSET :offset
"""

_UPSERT_SQL = """
    INSERT INTO ncit_search (code, label, semantic_type, synonyms)
    VALUES (:code, :label, :semantic_type, :synonyms)
    ON CONFLICT (code) DO UPDATE SET
        label = EXCLUDED.label,
        semantic_type = EXCLUDED.semantic_type,
        synonyms = EXCLUDED.synonyms
"""


class NcitSearchIndex:
    """Read/write access to the ``ncit_search`` FTS cache."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Wrap an async session factory bound to the Postgres database."""
        self._sf = session_factory

    async def count(self) -> int:
        """Number of cached concepts (0 ⇒ not populated ⇒ callers use SPARQL)."""
        async with self._sf() as session:
            result = await session.execute(text("SELECT COUNT(*) FROM ncit_search"))
            return int(result.scalar_one())

    async def is_populated(self) -> bool:
        """True if the cache has any rows (cheap existence probe)."""
        async with self._sf() as session:
            result = await session.execute(
                text("SELECT EXISTS(SELECT 1 FROM ncit_search)")
            )
            return bool(result.scalar_one())

    async def search(
        self, query: str, *, limit: int = 25, offset: int = 0
    ) -> SearchPage:
        """Full-text search the cache; total is the full match count (one query)."""
        async with self._sf() as session:
            result = await session.execute(
                text(_SEARCH_SQL),
                {"q": query, "limit": limit, "offset": offset},
            )
            rows = result.all()
        total = int(rows[0].total) if rows else 0
        hits = [
            SearchHit(
                code=row.code,
                label=row.label,
                semantic_type=row.semantic_type,
                matched_synonym=None,
            )
            for row in rows
        ]
        return SearchPage(
            query=query, total=total, limit=limit, offset=offset, hits=hits
        )

    async def rebuild(
        self, batches: AsyncIterable[Sequence[dict[str, str | None]]]
    ) -> int:
        """Atomically replace the whole cache from an async stream of record batches.

        DELETE + all inserts run in ONE transaction: concurrent readers keep seeing the
        previous complete snapshot (MVCC) until commit, and a mid-rebuild failure rolls
        back to it — preserving the invariant the fallback relies on (a non-empty cache
        is always a complete cache). DELETE (not TRUNCATE) so readers aren't blocked.
        """
        total = 0
        async with self._sf() as session, session.begin():
            await session.execute(text("DELETE FROM ncit_search"))
            async for records in batches:
                if records:
                    await session.execute(text(_UPSERT_SQL), list(records))
                    total += len(records)
        return total


async def populate_from_store(
    store: NcitGraphStore, index: NcitSearchIndex, *, batch_size: int = 5000
) -> int:
    """Rebuild the FTS cache from the live store; returns the number of concepts cached.

    Pages the store's search records and hands them to :meth:`NcitSearchIndex.rebuild`,
    which applies them atomically.
    """

    async def _pages() -> AsyncIterator[Sequence[dict[str, str | None]]]:
        offset = 0
        while True:
            records = await store.search_records(limit=batch_size, offset=offset)
            if not records:
                return
            yield records
            offset += batch_size

    return await index.rebuild(_pages())
