"""Hermetic behavioral tests for the guarded SPARQL endpoint's success + error paths.

The write-query rejection lives in ``test_sparql_guard.py``; here we drive the happy
path (a fake read-only client), the row-cap truncation flag, and the 502 mapping when
the upstream store errors — all without a live Oxigraph.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.dependencies import get_ncit_client
from backend.main import create_app
from ontolib.core.exceptions import StorageError


def _results(n: int) -> dict[str, Any]:
    return {
        "head": {"vars": ["s"]},
        "results": {"bindings": [{"s": {"value": f"urn:{i}"}} for i in range(n)]},
    }


class _FakeClient:
    def __init__(self, *, rows: int = 3, fail: bool = False) -> None:
        self._rows = rows
        self._fail = fail
        self.queries: list[str] = []

    async def select_raw(self, query: str) -> dict[str, Any]:
        self.queries.append(query)
        if self._fail:
            raise StorageError("upstream boom")
        return _results(self._rows)


def _client(fake: _FakeClient) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_ncit_client] = lambda: fake
    with TestClient(app) as client:
        yield client


@pytest.mark.api
def test_select_runs_and_returns_rows() -> None:
    fake = _FakeClient(rows=2)
    client = next(_client(fake))
    resp = client.post("/api/v1/sparql", json={"query": "SELECT * WHERE { ?s ?p ?o }"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["truncated"] is False
    assert len(body["result"]["results"]["bindings"]) == 2
    assert fake.queries == ["SELECT * WHERE { ?s ?p ?o }"]


@pytest.mark.api
def test_result_is_capped_and_flagged_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPARQL_ROW_CAP", "2")
    get_settings.cache_clear()
    try:
        fake = _FakeClient(rows=5)
        client = next(_client(fake))
        resp = client.post(
            "/api/v1/sparql", json={"query": "SELECT * WHERE { ?s ?p ?o }"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["truncated"] is True
        assert len(body["result"]["results"]["bindings"]) == 2
    finally:
        get_settings.cache_clear()


@pytest.mark.api
def test_upstream_error_is_502() -> None:
    fake = _FakeClient(fail=True)
    client = next(_client(fake))
    resp = client.post("/api/v1/sparql", json={"query": "SELECT * WHERE { ?s ?p ?o }"})
    assert resp.status_code == 502
