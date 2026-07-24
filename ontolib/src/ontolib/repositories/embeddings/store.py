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
    deactivate_corpus,
)

_TABLE = {Corpus.NCIT: "ncit_concepts", Corpus.CADSR: "cde_repository"}

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# cosine distance operator is ``<=>``; similarity = 1 - distance.
_SIMILAR_SQL = """
    SELECT t.doc_id, (1 - (t.embedding <=> q.embedding)) AS score
    FROM {table} t, (SELECT embedding FROM {table} WHERE doc_id = :doc_id) q
    WHERE t.doc_id <> :doc_id
      AND EXISTS (
          SELECT 1 FROM embedding_corpus_manifest manifest
          WHERE manifest.corpus = :corpus
            AND manifest.state = 'complete'
            AND manifest.is_active
      )
    ORDER BY t.embedding <=> q.embedding
    LIMIT :limit
"""
_SOURCE_EXISTS_SQL = "SELECT EXISTS (SELECT 1 FROM {table} WHERE doc_id = :doc_id)"


class EmbeddingStore:
    """Read-only nearest-neighbor queries over the embedding tables."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Wrap an async session factory bound to the pgvector database."""
        self._sf = session_factory

    async def _similar(
        self, corpus: Corpus, doc_id: str, limit: int
    ) -> list[tuple[str, float]]:
        # `table` is a fixed internal identifier (never user input); doc_id/limit bound.
        table = _TABLE[corpus]
        sql = text(_SIMILAR_SQL.format(table=table))
        async with self._sf() as session:
            available = await session.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM embedding_corpus_manifest "
                    "WHERE corpus = :corpus AND state = 'complete' AND is_active)"
                ),
                {"corpus": corpus.value},
            )
            if not available:
                raise CorpusUnavailableError(
                    f"no completed active {corpus.value} embedding corpus"
                )
            source_exists = await session.scalar(
                text(_SOURCE_EXISTS_SQL.format(table=table)), {"doc_id": doc_id}
            )
            if not source_exists:
                raise CorpusUnavailableError(
                    f"active {corpus.value} embedding corpus lacks {doc_id}"
                )
            result = await session.execute(
                sql, {"corpus": corpus.value, "doc_id": doc_id, "limit": limit}
            )
            return [(row[0], float(row[1])) for row in result.all()]

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

    async def deactivate(self, corpus: Corpus) -> None:
        """Invalidate a corpus before its official source is replaced."""
        await deactivate_corpus(self._sf, corpus)
