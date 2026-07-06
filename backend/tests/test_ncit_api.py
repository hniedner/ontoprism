"""Hermetic behavioral tests for the NCIt read API (fake store / index / embeddings).

These pin the endpoint contracts without a live Oxigraph: the FTS-cache-vs-SPARQL
fallback, 404 mapping for unknown/malformed codes, the similar-concepts label join,
and the 503 mapping when the embedding backend is unavailable. The live-store
variants live in ``test_ncit_api_integration.py``.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from backend.dependencies import (
    get_embedding_store,
    get_ncit_search_index,
    get_ncit_store,
)
from backend.main import create_app
from ontolib.terminologies.ncit.models import (
    ConceptDetail,
    GraphEdge,
    GraphNode,
    Neighborhood,
    SearchHit,
    SearchPage,
)


class _FakeStore:
    """A hand-written NCIt store with a single known concept, C3262."""

    def __init__(self) -> None:
        self.search_calls: list[tuple[str, int, int]] = []

    async def search(self, q: str, *, limit: int, offset: int) -> SearchPage:
        self.search_calls.append((q, limit, offset))
        return SearchPage(
            query=q,
            total=1,
            limit=limit,
            offset=offset,
            hits=[SearchHit(code="C3262", label="Neoplasm", matched_synonym="tumor")],
        )

    async def list_concepts(self, *, limit: int, offset: int) -> SearchPage:
        return SearchPage(
            query="",
            total=2,
            limit=limit,
            offset=offset,
            hits=[SearchHit(code="C3262", label="Neoplasm")],
        )

    async def get_concept_detail(self, code: str) -> ConceptDetail | None:
        if code == "bad code":
            raise ValueError("malformed IRI")
        if code != "C3262":
            return None
        return ConceptDetail(code="C3262", label="Neoplasm", definition="A growth.")

    async def get_neighborhood(self, code: str, *, depth: int = 1) -> Neighborhood:
        if code == "bad code":
            raise ValueError("malformed IRI")
        return Neighborhood(
            center=code,
            nodes=[GraphNode(code=code, label="Neoplasm"), GraphNode(code="C12922")],
            edges=[
                GraphEdge(source=code, target="C12922", relation="R105", kind="role")
            ],
        )

    async def labels_for(self, codes: list[str]) -> dict[str, str]:
        known = {"C3262": "Neoplasm", "C9305": "Malignant Neoplasm"}
        return {c: known[c] for c in codes if c in known}


class _FakeIndex:
    """FTS cache double; ``populated`` and ``fail`` control the fallback branches."""

    def __init__(self, *, populated: bool = True, fail: bool = False) -> None:
        self._populated = populated
        self._fail = fail
        self.searched = False

    async def is_populated(self) -> bool:
        if self._fail:
            raise OperationalError("cache down", None, Exception())
        return self._populated

    async def search(self, q: str, *, limit: int, offset: int) -> SearchPage:
        self.searched = True
        return SearchPage(
            query=q,
            total=1,
            limit=limit,
            offset=offset,
            hits=[SearchHit(code="C3262", label="Neoplasm (from cache)")],
        )


class _FakeEmbeddings:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    async def similar_ncit(
        self, code: str, *, limit: int = 10
    ) -> list[tuple[str, float]]:
        if self._fail:
            raise OperationalError("pgvector down", None, Exception())
        return [("C9305", 0.92), ("C99999", 0.5)]


def _client(
    *,
    store: _FakeStore | None = None,
    index: _FakeIndex | None = None,
    embeddings: _FakeEmbeddings | None = None,
) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_ncit_store] = lambda: store or _FakeStore()
    app.dependency_overrides[get_ncit_search_index] = lambda: index or _FakeIndex()
    app.dependency_overrides[get_embedding_store] = lambda: (
        embeddings or _FakeEmbeddings()
    )
    with TestClient(app) as client:
        yield client


@pytest.fixture
def ncit_client() -> Iterator[TestClient]:
    yield from _client()


@pytest.mark.api
def test_search_served_from_populated_cache(ncit_client: TestClient) -> None:
    resp = ncit_client.get("/api/v1/ncit/search", params={"q": "neoplasm"})
    assert resp.status_code == 200
    # A populated cache answers directly (label carries the cache marker).
    assert resp.json()["hits"][0]["label"] == "Neoplasm (from cache)"


@pytest.mark.api
def test_search_falls_back_to_store_when_cache_empty() -> None:
    store = _FakeStore()
    gen = _client(store=store, index=_FakeIndex(populated=False))
    client = next(gen)
    resp = client.get("/api/v1/ncit/search", params={"q": "neoplasm"})
    assert resp.status_code == 200
    # Empty cache -> the store (source of truth) answered.
    assert store.search_calls == [("neoplasm", 25, 0)]
    assert resp.json()["hits"][0]["label"] == "Neoplasm"


@pytest.mark.api
def test_search_falls_back_to_store_when_cache_errors() -> None:
    store = _FakeStore()
    gen = _client(store=store, index=_FakeIndex(fail=True))
    client = next(gen)
    resp = client.get("/api/v1/ncit/search", params={"q": "neoplasm"})
    assert resp.status_code == 200
    # A cache failure degrades gracefully to the store rather than 500-ing.
    assert store.search_calls == [("neoplasm", 25, 0)]


@pytest.mark.api
def test_search_requires_nonempty_query(ncit_client: TestClient) -> None:
    assert ncit_client.get("/api/v1/ncit/search", params={"q": ""}).status_code == 422


@pytest.mark.api
def test_list_browses_without_query(ncit_client: TestClient) -> None:
    resp = ncit_client.get("/api/v1/ncit/list", params={"limit": 5})
    assert resp.status_code == 200
    assert resp.json()["query"] == ""


@pytest.mark.api
def test_concept_detail_returns_concept(ncit_client: TestClient) -> None:
    resp = ncit_client.get("/api/v1/ncit/concepts/C3262")
    assert resp.status_code == 200
    assert resp.json()["label"] == "Neoplasm"


@pytest.mark.api
def test_concept_detail_unknown_is_404(ncit_client: TestClient) -> None:
    assert ncit_client.get("/api/v1/ncit/concepts/C0").status_code == 404


@pytest.mark.api
def test_concept_detail_malformed_code_is_404(ncit_client: TestClient) -> None:
    resp = ncit_client.get("/api/v1/ncit/concepts/bad code")
    assert resp.status_code == 404
    assert "Invalid code" in resp.json()["detail"]


@pytest.mark.api
def test_similar_concepts_joins_labels(ncit_client: TestClient) -> None:
    resp = ncit_client.get("/api/v1/ncit/concepts/C3262/similar")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0] == {"code": "C9305", "label": "Malignant Neoplasm", "score": 0.92}
    # A hit with no resolvable label still appears (label is null, not dropped).
    assert body[1]["code"] == "C99999"
    assert body[1]["label"] is None


@pytest.mark.api
def test_similar_concepts_backend_down_is_503() -> None:
    gen = _client(embeddings=_FakeEmbeddings(fail=True))
    client = next(gen)
    resp = client.get("/api/v1/ncit/concepts/C3262/similar")
    assert resp.status_code == 503


@pytest.mark.api
def test_neighborhood_returns_subgraph(ncit_client: TestClient) -> None:
    resp = ncit_client.get("/api/v1/ncit/concepts/C3262/neighborhood")
    assert resp.status_code == 200
    body = resp.json()
    assert body["center"] == "C3262"
    assert any(e["kind"] == "role" for e in body["edges"])


@pytest.mark.api
def test_neighborhood_malformed_code_is_404(ncit_client: TestClient) -> None:
    resp = ncit_client.get("/api/v1/ncit/concepts/bad code/neighborhood")
    assert resp.status_code == 404
