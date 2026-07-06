"""Unit tests for the pgvector similarity store (fake async session, no real DB).

These pin the KNN contract: the right table is queried, ``doc_id``/``limit`` are
bound (never interpolated), and rows are returned as ``(id, float score)`` pairs.
"""

from typing import Any

import pytest

from ontolib.repositories.embeddings.store import EmbeddingStore


class _FakeResult:
    def __init__(self, rows: list[tuple[str, float]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[str, float]]:
        return self._rows


class _FakeSession:
    """Records executed (sql, params) and returns a fixed result set."""

    def __init__(
        self, calls: list[tuple[str, dict[str, Any]]], rows: list[Any]
    ) -> None:
        self._calls = calls
        self._rows = rows

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def execute(self, sql: Any, params: dict[str, Any]) -> _FakeResult:
        self._calls.append((str(sql), params))
        return _FakeResult(self._rows)


class _FakeSessionFactory:
    def __init__(self, rows: list[Any]) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._rows = rows

    def __call__(self) -> _FakeSession:
        return _FakeSession(self.calls, self._rows)


@pytest.mark.unit
async def test_similar_ncit_queries_concept_table_and_coerces_scores() -> None:
    sf = _FakeSessionFactory(rows=[("C9305", 0.91), ("C12345", 0)])
    store = EmbeddingStore(sf)  # type: ignore[arg-type]

    hits = await store.similar_ncit("C3262", limit=5)

    assert hits == [("C9305", 0.91), ("C12345", 0.0)]
    assert all(isinstance(score, float) for _, score in hits)
    sql, params = sf.calls[0]
    assert "ncit_concepts" in sql
    assert params == {"doc_id": "C3262", "limit": 5}


@pytest.mark.unit
async def test_similar_cde_builds_composite_doc_id_for_cde_table() -> None:
    sf = _FakeSessionFactory(rows=[("200:1.0", 0.8)])
    store = EmbeddingStore(sf)  # type: ignore[arg-type]

    hits = await store.similar_cde("100", "2.0", limit=3)

    assert hits == [("200:1.0", 0.8)]
    sql, params = sf.calls[0]
    assert "cde_repository" in sql
    # doc_id is the composite {public_id}:{version} key, not the bare public_id.
    assert params == {"doc_id": "100:2.0", "limit": 3}


@pytest.mark.unit
async def test_similar_returns_empty_when_no_neighbors() -> None:
    sf = _FakeSessionFactory(rows=[])
    store = EmbeddingStore(sf)  # type: ignore[arg-type]

    assert await store.similar_ncit("C3262") == []
