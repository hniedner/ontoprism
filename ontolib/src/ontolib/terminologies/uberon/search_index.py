"""Source-bound PostgreSQL full-text cache for Uberon/CL concepts."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from sqlalchemy import text

from ontolib.terminologies.uberon.models import (
    UberonSearchHit,
    UberonSearchPage,
    UberonSource,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterable, AsyncIterator, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ontolib.terminologies.uberon.graph_store import UberonGraphStore

_SEARCH_SQL = r"""
SELECT code, source, label, COUNT(*) OVER () AS total
FROM uberon_search, websearch_to_tsquery('english', :q) AS q
WHERE tsv @@ q
  AND (CAST(:source AS text) IS NULL OR source = CAST(:source AS text))
ORDER BY (lower(label) = lower(btrim(:q, E' \t\r\n"'))) DESC,
         ts_rank(tsv, q) DESC, length(label), label, code
LIMIT :limit OFFSET :offset
"""
_READY_SQL = """
SELECT EXISTS(
  SELECT 1 FROM uberon_search_manifest manifest
  WHERE manifest.singleton = true
    AND manifest.source_identity = :source_identity
    AND manifest.row_count > 0
    AND manifest.row_count = (SELECT COUNT(*) FROM uberon_search)
)
"""
_UPSERT_SQL = """
INSERT INTO uberon_search (code, source, label, synonyms)
VALUES (:code, :source, :label, :synonyms)
ON CONFLICT (code) DO UPDATE SET source = EXCLUDED.source, label = EXCLUDED.label,
  synonyms = EXCLUDED.synonyms
"""
_PUBLISH_SQL = """
INSERT INTO uberon_search_manifest
  (singleton, source_identity, source_hash, row_count, built_at)
VALUES (true, :source_identity, :source_hash, :row_count, now())
"""
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _require_digest(name: str, value: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


class UberonSearchIndex:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def is_populated(self, source_identity: str) -> bool:
        _require_digest("source_identity", source_identity)
        async with self._sf() as session:
            result = await session.execute(
                text(_READY_SQL), {"source_identity": source_identity}
            )
            return bool(result.scalar_one())

    async def search(
        self,
        query: str,
        *,
        source: UberonSource | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> UberonSearchPage:
        async with self._sf() as session:
            result = await session.execute(
                text(_SEARCH_SQL),
                {"q": query, "source": source, "limit": limit, "offset": offset},
            )
            rows = result.all()
        return UberonSearchPage(
            query=query,
            total=int(rows[0].total) if rows else 0,
            limit=limit,
            offset=offset,
            hits=[
                UberonSearchHit(code=row.code, source=row.source, label=row.label)
                for row in rows
            ],
        )

    async def rebuild(
        self,
        batches: AsyncIterable[Sequence[dict[str, str | None]]],
        *,
        source_identity: str,
        source_hash: str,
    ) -> int:
        _require_digest("source_identity", source_identity)
        _require_digest("source_hash", source_hash)
        total = 0
        async with self._sf() as session, session.begin():
            await session.execute(text("DELETE FROM uberon_search_manifest"))
            await session.execute(text("DELETE FROM uberon_search"))
            async for records in batches:
                if records:
                    await session.execute(text(_UPSERT_SQL), list(records))
                    total += len(records)
            if total <= 0:
                raise ValueError("Uberon/CL search source produced no records")
            await session.execute(
                text(_PUBLISH_SQL),
                {
                    "source_identity": source_identity,
                    "source_hash": source_hash,
                    "row_count": total,
                },
            )
        return total


async def populate_from_store(
    store: UberonGraphStore,
    index: UberonSearchIndex,
    *,
    source_identity: str,
    source_hash: str,
    batch_size: int = 5000,
) -> int:
    """Atomically rebuild the FTS cache from deterministic QLever pages."""

    async def pages() -> AsyncIterator[Sequence[dict[str, str | None]]]:
        offset = 0
        while True:
            records = await store.search_records(limit=batch_size, offset=offset)
            if not records:
                return
            yield records
            offset += batch_size

    return await index.rebuild(
        pages(), source_identity=source_identity, source_hash=source_hash
    )
