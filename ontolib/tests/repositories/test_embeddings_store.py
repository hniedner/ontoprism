"""Unit tests for the pgvector similarity store (fake async session, no real DB).

These pin the KNN contract: the right table is queried, ``doc_id``/``limit`` are
bound (never interpolated), and rows are returned as ``(id, float score)`` pairs.
"""

from typing import Any

import pytest

from ontolib.repositories.embeddings.publication import CorpusUnavailableError
from ontolib.repositories.embeddings.store import EmbeddingStore


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """Records executed (sql, params) and returns a fixed result set."""

    def __init__(
        self,
        calls: list[tuple[str, dict[str, Any]]],
        rows: list[Any],
        *,
        available: bool,
        source_exists: bool,
    ) -> None:
        self._calls = calls
        self._rows = rows
        self._available = available
        self._source_exists = source_exists
        self._scalar_calls = 0

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def execute(self, sql: Any, params: dict[str, Any]) -> _FakeResult:
        self._calls.append((str(sql), params))
        if "WITH active AS" in str(sql):
            rows = [
                (doc_id, score, self._available, self._source_exists)
                for doc_id, score in self._rows
            ]
            if not rows:
                rows = [(None, None, self._available, self._source_exists)]
            return _FakeResult(rows)
        return _FakeResult(self._rows)

    async def scalar(self, sql: Any, params: dict[str, Any]) -> bool:
        self._calls.append((str(sql), params))
        self._scalar_calls += 1
        return self._available if self._scalar_calls == 1 else self._source_exists


class _FakeSessionFactory:
    def __init__(
        self,
        rows: list[Any],
        *,
        available: bool = True,
        source_exists: bool = True,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._rows = rows
        self.available = available
        self.source_exists = source_exists

    def __call__(self) -> _FakeSession:
        return _FakeSession(
            self.calls,
            self._rows,
            available=self.available,
            source_exists=self.source_exists,
        )


@pytest.mark.unit
async def test_similar_ncit_queries_concept_table_and_coerces_scores() -> None:
    sf = _FakeSessionFactory(rows=[("C9305", 0.91), ("C12345", 0)])
    store = EmbeddingStore(sf)  # type: ignore[arg-type]

    hits = await store.similar_ncit("C3262", limit=5)

    assert hits == [("C9305", 0.91), ("C12345", 0.0)]
    assert all(isinstance(score, float) for _, score in hits)
    sql, params = sf.calls[0]
    assert "ncit_concepts" in sql
    assert "state = 'complete'" in sql
    assert "is_active" in sql
    assert params == {"corpus": "ncit", "doc_id": "C3262", "limit": 5}


@pytest.mark.unit
async def test_similar_cde_builds_composite_doc_id_for_cde_table() -> None:
    sf = _FakeSessionFactory(rows=[("200:1.0", 0.8)])
    store = EmbeddingStore(sf)  # type: ignore[arg-type]

    hits = await store.similar_cde("100", "2.0", limit=3)

    assert hits == [("200:1.0", 0.8)]
    sql, params = sf.calls[0]
    assert "cde_repository" in sql
    assert "state = 'complete'" in sql
    # doc_id is the composite {public_id}:{version} key, not the bare public_id.
    assert params == {"corpus": "cadsr", "doc_id": "100:2.0", "limit": 3}


@pytest.mark.unit
async def test_similar_returns_empty_when_no_neighbors() -> None:
    sf = _FakeSessionFactory(rows=[])
    store = EmbeddingStore(sf)  # type: ignore[arg-type]

    assert await store.similar_ncit("C3262") == []


@pytest.mark.unit
async def test_similar_rejects_uncertified_corpus() -> None:
    store = EmbeddingStore(_FakeSessionFactory(rows=[], available=False))  # type: ignore[arg-type]

    with pytest.raises(CorpusUnavailableError, match="no completed active ncit"):
        await store.similar_ncit("C3262")


@pytest.mark.unit
async def test_similar_rejects_missing_source_vector_in_active_corpus() -> None:
    store = EmbeddingStore(
        _FakeSessionFactory(rows=[], source_exists=False)  # type: ignore[arg-type]
    )

    with pytest.raises(CorpusUnavailableError, match="lacks C3262"):
        await store.similar_ncit("C3262")
