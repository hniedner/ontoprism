"""Guarded SPARQL endpoint rejects write/management queries (no store needed)."""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.api
@pytest.mark.parametrize(
    "query",
    [
        "DELETE WHERE { ?s ?p ?o }",
        "INSERT DATA { <urn:a> <urn:b> <urn:c> }",
        "DROP GRAPH <urn:g>",
        "CLEAR ALL",
        "LOAD <http://example.org/data>",
    ],
)
def test_write_queries_are_rejected(app_client: TestClient, query: str) -> None:
    resp = app_client.post("/api/v1/sparql", json={"query": query})
    assert resp.status_code == 400
    assert "read-only" in resp.json()["detail"].lower()


@pytest.mark.api
def test_empty_query_is_rejected(app_client: TestClient) -> None:
    assert app_client.post("/api/v1/sparql", json={"query": ""}).status_code == 422
