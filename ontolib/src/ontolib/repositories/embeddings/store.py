"""Semantic similarity over the pgvector embedding tables.

Two validated 768-dim embedding serving tables: ``ncit_concepts``
(doc_id = concept code) and ``cde_repository`` (doc_id = ``{public_id}:{version}``),
both cosine-indexed (HNSW). "Similar items" needs no runtime embedding model — it
searches from a row's own stored vector.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

from ontolib.repositories.embeddings.publication import (
    Corpus,
    CorpusUnavailableError,
)

_TABLE = {Corpus.NCIT: "ncit_concepts", Corpus.CADSR: "cde_repository"}
_MAX_SIMILAR_LIMIT = 50

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# cosine distance operator is ``<=>``; similarity = 1 - distance.
# A scalar probe permits the existing HNSW ordering path; a joined probe does not.
_SIMILAR_SQL = """
    WITH active AS (
        SELECT 1 FROM embedding_corpus_manifest
        WHERE corpus = :corpus AND state = 'complete' AND is_active
    ), source AS (
        SELECT embedding FROM {table}
        WHERE doc_id = :doc_id AND EXISTS (SELECT 1 FROM active)
    ), hits AS (
        SELECT t.doc_id,
               (1 - (t.embedding <=> (SELECT embedding FROM source))) AS score
        FROM {table} t
        WHERE t.doc_id <> :doc_id AND EXISTS (SELECT 1 FROM source)
        ORDER BY t.embedding <=> (SELECT embedding FROM source)
        LIMIT :limit
    )
    SELECT doc_id, score, true AS available, true AS source_exists FROM hits
    UNION ALL
    SELECT NULL, NULL, EXISTS (SELECT 1 FROM active), EXISTS (SELECT 1 FROM source)
    WHERE NOT EXISTS (SELECT 1 FROM hits)
    ORDER BY score DESC NULLS LAST
"""


def _search_budget(limit: int) -> str:
    if type(limit) is not int or not 1 <= limit <= _MAX_SIMILAR_LIMIT:
        raise ValueError("similarity limit must be an integer between 1 and 50")
    return str(max(40, 2 * (limit + 1)))


class EmbeddingStore:
    """Nearest-neighbor reads plus explicit source-replacement coordination."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Wrap an async session factory bound to the pgvector database."""
        self._sf = session_factory

    async def _similar(
        self, corpus: Corpus, doc_id: str, limit: int
    ) -> list[tuple[str, float]]:
        budget = _search_budget(limit)
        # `table` is a fixed internal identifier (never user input); doc_id/limit bound.
        table = _TABLE[corpus]
        sql = text(_SIMILAR_SQL.format(table=table))
        async with self._sf() as session, session.begin():
            # set_config(..., true) is parameter-bound SET LOCAL: enough HNSW
            # candidates for the requested neighbours and the excluded source.
            await session.execute(
                text("SELECT set_config('hnsw.ef_search', :budget, true)"),
                {"budget": budget},
            )
            result = await session.execute(
                sql, {"corpus": corpus.value, "doc_id": doc_id, "limit": limit}
            )
            rows = result.all()
            available = bool(rows and rows[0][2])
            if not available:
                raise CorpusUnavailableError(
                    f"no completed active {corpus.value} embedding corpus"
                )
            source_exists = bool(rows[0][3])
            if not source_exists:
                raise CorpusUnavailableError(
                    f"active {corpus.value} embedding corpus lacks {doc_id}"
                )
            return [
                (row[0], float(row[1]))
                for row in rows
                if row[0] is not None and row[1] is not None
            ]

    async def similar_ncit(
        self, code: str, *, limit: int = 10
    ) -> list[tuple[str, float]]:
        """Return (concept_code, cosine_similarity) most similar to *code*."""
        return await self._similar(Corpus.NCIT, code, limit)

    async def similar_cde(
        self, public_id: str, version: str, *, limit: int = 10
    ) -> list[tuple[str, float]]:
        """Return (``public_id:version``, similarity) most similar to a CDE."""
        return await self._similar(Corpus.CADSR, f"{public_id}:{version}", limit)

    async def active_build_id(self, corpus: Corpus) -> UUID:
        """Return the active build token used to guard cross-store response joins."""
        async with self._sf() as session:
            build_id = await session.scalar(
                text(
                    "SELECT build_id FROM embedding_corpus_manifest "
                    "WHERE corpus = :corpus AND state = 'complete' AND is_active"
                ),
                {"corpus": corpus.value},
            )
        if build_id is None:
            raise CorpusUnavailableError(
                f"no completed active {corpus.value} embedding corpus"
            )
        return build_id

    async def require_active_source(self, corpus: Corpus, source_identity: str) -> None:
        """Fail unless the active embedding manifest names the live proxy source."""
        async with self._sf() as session:
            observed = await session.scalar(
                text(
                    "SELECT source_identity FROM embedding_corpus_manifest "
                    "WHERE corpus = :corpus AND state = 'complete' AND is_active"
                ),
                {"corpus": corpus.value},
            )
        if observed != source_identity:
            raise CorpusUnavailableError(
                f"embedding source does not match active {corpus.value} proxy"
            )

    async def require_same_active_build(self, corpus: Corpus, build_id: UUID) -> None:
        """Fail if source/publication changed while an API response was assembled."""
        if await self.active_build_id(corpus) != build_id:
            raise CorpusUnavailableError(
                f"active {corpus.value} embedding corpus changed during request"
            )
