"""Semantic similarity over the pgvector embedding tables.

Two 768-dim embedding tables (mirrored from the fairdata build): ``ncit_concepts``
(doc_id = concept code) and ``cde_repository`` (doc_id = ``{public_id}:{version}``),
both cosine-indexed (HNSW). "Similar items" needs no runtime embedding model — it
searches from a row's own stored vector.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# cosine distance operator is ``<=>``; similarity = 1 - distance.
_SIMILAR_SQL = """
    SELECT t.doc_id, (1 - (t.embedding <=> q.embedding)) AS score
    FROM {table} t, (SELECT embedding FROM {table} WHERE doc_id = :doc_id) q
    WHERE t.doc_id <> :doc_id
    ORDER BY t.embedding <=> q.embedding
    LIMIT :limit
"""


class EmbeddingStore:
    """Read-only nearest-neighbor queries over the embedding tables."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Wrap an async session factory bound to the pgvector database."""
        self._sf = session_factory

    async def _similar(
        self, table: str, doc_id: str, limit: int
    ) -> list[tuple[str, float]]:
        # `table` is a fixed internal identifier (never user input); doc_id/limit bound.
        sql = text(_SIMILAR_SQL.format(table=table))
        async with self._sf() as session:
            result = await session.execute(sql, {"doc_id": doc_id, "limit": limit})
            return [(row[0], float(row[1])) for row in result.all()]

    async def similar_ncit(
        self, code: str, *, limit: int = 10
    ) -> list[tuple[str, float]]:
        """Return (concept_code, cosine_similarity) most similar to *code*."""
        return await self._similar("ncit_concepts", code, limit)

    async def similar_cde(
        self, public_id: str, version: str, *, limit: int = 10
    ) -> list[tuple[str, float]]:
        """Return (``public_id:version``, similarity) most similar to a CDE."""
        return await self._similar("cde_repository", f"{public_id}:{version}", limit)
