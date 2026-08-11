"""Hermetic tests for caDSR error mapping and the semantic-similar CDE endpoint.

The happy-path detail/search/list/join tests (real temp DB) live in
``test_cadsr_api.py``. Here we pin: SQLite operational failures map to 503 (not 500),
and the similar-CDE endpoint's embedding join / 404 / 503 behaviour.
"""

import sqlite3
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from backend.dependencies import (
    get_cadsr_repo,
    get_embedding_store,
    get_repository_metadata,
)
from backend.main import create_app
from ontolib.repositories.cadsr.models import CdeSearchPage, CdeSummary
from ontolib.repositories.embeddings.publication import Corpus, CorpusUnavailableError


class _BrokenRepo:
    """Every read raises the sqlite error a corrupt/missing DB would raise."""

    _boom = sqlite3.OperationalError("no such table: cdes")

    def search(self, *_a: Any, **_k: Any) -> CdeSearchPage:
        raise self._boom

    def list_cdes(self, *_a: Any, **_k: Any) -> CdeSearchPage:
        raise self._boom

    def get_cde(self, *_a: Any, **_k: Any) -> Any:
        raise self._boom

    def find_cdes_by_concept(self, *_a: Any, **_k: Any) -> list[CdeSummary]:
        raise self._boom


@pytest.fixture
def broken_client() -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_cadsr_repo] = _BrokenRepo
    with TestClient(app) as client:
        yield client


@pytest.mark.api
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/cadsr/search?q=x",
        "/api/v1/cadsr/list",
        "/api/v1/cadsr/cdes/100",
        "/api/v1/cadsr/concepts/C3262/cdes",
        "/api/v1/cadsr/cdes/100/neighborhood",
    ],
)
def test_sqlite_failure_maps_to_503(broken_client: TestClient, path: str) -> None:
    assert broken_client.get(path).status_code == 503


# --- similar CDEs (embedding join) -----------------------------------------------


class _FakeEmbeddings:
    def __init__(self, hits: list[tuple[str, float]], *, fail: bool = False) -> None:
        self._hits = hits
        self._fail = fail
        self.build_id = UUID("00000000-0000-0000-0000-000000000001")
        self.source_checks: list[tuple[Corpus, str]] = []

    async def require_active_source(self, corpus: Corpus, source_identity: str) -> None:
        self.source_checks.append((corpus, source_identity))

    async def active_build_id(self, _corpus: Corpus) -> UUID:
        return self.build_id

    async def require_same_active_build(self, _corpus: Corpus, build_id: UUID) -> None:
        if build_id != self.build_id:
            raise CorpusUnavailableError("corpus changed")

    async def similar_cde(
        self, public_id: str, version: str, *, limit: int = 10
    ) -> list[tuple[str, float]]:
        if self._fail:
            raise OperationalError("pgvector down", None, Exception())
        return self._hits


def _with_embeddings(client: TestClient, emb: _FakeEmbeddings) -> None:
    client.app.dependency_overrides[get_embedding_store] = lambda: emb  # type: ignore[attr-defined]
    client.app.dependency_overrides[get_repository_metadata] = lambda: SimpleNamespace(  # type: ignore[attr-defined]
        cadsr=lambda: SimpleNamespace(source_identity="f" * 64)
    )


@pytest.mark.api
def test_similar_cdes_joins_summaries(cadsr_client: TestClient) -> None:
    # The only CDE in the temp DB is 100:2.0; a hit on it resolves to its summary,
    # Every active hit must resolve against the source used by the API.
    embeddings = _FakeEmbeddings([("100:2.0", 0.95)])
    _with_embeddings(cadsr_client, embeddings)
    resp = cadsr_client.get("/api/v1/cadsr/cdes/100/similar")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["public_id"] == "100"
    assert body[0]["score"] == 0.95
    assert embeddings.source_checks == [(Corpus.CADSR, "f" * 64)]


@pytest.mark.api
def test_similar_cdes_source_mismatch_is_503(cadsr_client: TestClient) -> None:
    _with_embeddings(cadsr_client, _FakeEmbeddings([("999:1.0", 0.4)]))

    response = cadsr_client.get("/api/v1/cadsr/cdes/100/similar")

    assert response.status_code == 503
    assert "do not match" in response.json()["detail"]


@pytest.mark.api
def test_similar_cdes_unknown_cde_is_404(cadsr_client: TestClient) -> None:
    _with_embeddings(cadsr_client, _FakeEmbeddings([]))
    assert cadsr_client.get("/api/v1/cadsr/cdes/999999/similar").status_code == 404


@pytest.mark.api
def test_similar_cdes_backend_down_is_503(cadsr_client: TestClient) -> None:
    _with_embeddings(cadsr_client, _FakeEmbeddings([], fail=True))
    assert cadsr_client.get("/api/v1/cadsr/cdes/100/similar").status_code == 503


@pytest.mark.api
def test_similar_cdes_without_active_corpus_is_503(cadsr_client: TestClient) -> None:
    class _UnavailableEmbeddings(_FakeEmbeddings):
        async def similar_cde(
            self, public_id: str, version: str, *, limit: int = 10
        ) -> list[tuple[str, float]]:
            del public_id, version, limit
            raise CorpusUnavailableError("no completed active cadsr embedding corpus")

    _with_embeddings(cadsr_client, _UnavailableEmbeddings([]))

    assert cadsr_client.get("/api/v1/cadsr/cdes/100/similar").status_code == 503


@pytest.mark.api
def test_similar_cdes_rejects_build_change_during_source_join(
    cadsr_client: TestClient,
) -> None:
    class _ChangingEmbeddings(_FakeEmbeddings):
        async def similar_cde(
            self, public_id: str, version: str, *, limit: int = 10
        ) -> list[tuple[str, float]]:
            hits = await super().similar_cde(public_id, version, limit=limit)
            self.build_id = UUID("00000000-0000-0000-0000-000000000002")
            return hits

    _with_embeddings(cadsr_client, _ChangingEmbeddings([("100:2.0", 0.95)]))

    assert cadsr_client.get("/api/v1/cadsr/cdes/100/similar").status_code == 503
