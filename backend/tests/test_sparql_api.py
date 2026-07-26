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
        "results": {
            "bindings": [{"s": {"type": "uri", "value": f"urn:{i}"}} for i in range(n)]
        },
    }


class _FakeClient:
    def __init__(
        self,
        *,
        rows: int = 3,
        fail: bool = False,
        result: dict[str, Any] | None = None,
    ) -> None:
        self._rows = rows
        self._fail = fail
        self._result = result
        self.queries: list[str] = []

    async def select_raw(self, query: str) -> dict[str, Any]:
        self.queries.append(query)
        if self._fail:
            raise StorageError("upstream boom")
        return self._result if self._result is not None else _results(self._rows)


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
def test_ask_runs_and_returns_boolean() -> None:
    result = {"head": {}, "boolean": True}
    fake = _FakeClient(result=result)
    client = next(_client(fake))

    resp = client.post("/api/v1/sparql", json={"query": "ASK { ?s ?p ?o }"})

    assert resp.status_code == 200
    assert resp.json() == {"result": result, "truncated": False}


@pytest.mark.api
@pytest.mark.parametrize(
    ("query", "result"),
    [
        (
            "PREFIX ex: <http://example.org/> SELECT ?s WHERE { ?s ex:p ?o }",
            _results(1),
        ),
        (
            "# standard prologue\nBASE <http://example.org/>\nASK { <x> <p> <o> }",
            {"head": {}, "boolean": False},
        ),
    ],
)
def test_standard_prologues_are_supported(
    query: str,
    result: dict[str, Any],
) -> None:
    fake = _FakeClient(result=result)
    client = next(_client(fake))

    resp = client.post("/api/v1/sparql", json={"query": query})

    assert resp.status_code == 200
    assert resp.json() == {"result": result, "truncated": False}
    assert fake.queries == [query]


@pytest.mark.security
@pytest.mark.parametrize(
    "query",
    [
        "SELECT * WHERE { SERVICE <http://example.org/sparql> { ?s ?p ?o } }",
        "SELECT * WHERE { SERVICE SILENT <http://example.org/sparql> { ?s ?p ?o } }",
        (
            "SELECT * WHERE { VALUES ?endpoint { <http://example.org/sparql> } "
            "SERVICE ?endpoint { ?s ?p ?o } }"
        ),
        (
            "SELECT * WHERE { OPTIONAL { SERVICE <http://example.org/sparql> "
            "{ ?s ?p ?o } } }"
        ),
        (
            "SELECT * WHERE { { SELECT * WHERE { "
            "SERVICE <http://example.org/sparql> { ?s ?p ?o } } } }"
        ),
    ],
)
def test_federated_service_patterns_are_rejected_before_query(query: str) -> None:
    fake = _FakeClient()
    client = next(_client(fake))

    resp = client.post("/api/v1/sparql", json={"query": query})

    assert resp.status_code == 400
    assert "service" in resp.json()["detail"].lower()
    assert fake.queries == []


@pytest.mark.security
def test_service_text_outside_graph_pattern_is_allowed() -> None:
    query = (
        'SELECT ("SERVICE <http://example.org/sparql>" AS ?label) WHERE {} '
        "# SERVICE <http://example.org/sparql>"
    )
    result = {
        "head": {"vars": ["label"]},
        "results": {
            "bindings": [
                {"label": {"type": "literal", "value": "SERVICE"}},
            ]
        },
    }
    fake = _FakeClient(result=result)
    client = next(_client(fake))

    resp = client.post("/api/v1/sparql", json={"query": query})

    assert resp.status_code == 200
    assert fake.queries == [query]


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


@pytest.mark.api
@pytest.mark.parametrize(
    "result",
    [
        {},
        {"head": {"vars": []}, "results": {"bindings": []}, "boolean": False},
        {
            "head": {"vars": ["s"]},
            "results": {"bindings": [{"s": {"type": "uri"}}]},
        },
        {"head": {}, "boolean": "false"},
    ],
)
def test_malformed_upstream_result_is_502(result: dict[str, Any]) -> None:
    client = next(_client(_FakeClient(result=result)))

    resp = client.post("/api/v1/sparql", json={"query": "SELECT * WHERE { ?s ?p ?o }"})

    assert resp.status_code == 502


@pytest.mark.api
def test_named_select_rejects_missing_projected_variable() -> None:
    result: dict[str, Any] = {
        "head": {"vars": ["s"]},
        "results": {"bindings": []},
    }
    client = next(_client(_FakeClient(result=result)))

    resp = client.post(
        "/api/v1/sparql",
        json={"query": "SELECT ?s ?label WHERE { ?s ?p ?o }"},
    )

    assert resp.status_code == 502
    assert "label" in resp.json()["detail"]


@pytest.mark.api
def test_aliased_select_rejects_missing_projected_variable() -> None:
    result: dict[str, Any] = {
        "head": {"vars": []},
        "results": {"bindings": []},
    }
    client = next(_client(_FakeClient(result=result)))

    resp = client.post(
        "/api/v1/sparql",
        json={"query": 'SELECT ("value" AS ?label) WHERE {}'},
    )

    assert resp.status_code == 502
    assert "label" in resp.json()["detail"]


@pytest.mark.api
@pytest.mark.parametrize(
    ("query", "result"),
    [
        ("SELECT * WHERE { ?s ?p ?o }", {"head": {}, "boolean": False}),
        (
            "ASK { ?s ?p ?o }",
            {"head": {"vars": []}, "results": {"bindings": []}},
        ),
    ],
)
def test_mismatched_upstream_result_form_is_502(
    query: str,
    result: dict[str, Any],
) -> None:
    client = next(_client(_FakeClient(result=result)))

    resp = client.post("/api/v1/sparql", json={"query": query})

    assert resp.status_code == 502
