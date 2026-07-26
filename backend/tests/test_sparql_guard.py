"""Guarded SPARQL endpoint rejects unsupported query forms (no store needed)."""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.security
@pytest.mark.parametrize(
    "query",
    [
        "DELETE WHERE { ?s ?p ?o }",
        "INSERT DATA { <urn:a> <urn:b> <urn:c> }",
        "DROP GRAPH <urn:g>",
        "CLEAR ALL",
        "LOAD <http://example.org/data>",
        "CREATE GRAPH <urn:g>",
        "ADD DEFAULT TO GRAPH <urn:g>",
        "MOVE GRAPH <urn:a> TO DEFAULT",
        "COPY GRAPH <urn:a> TO GRAPH <urn:b>",
        "WITH <urn:g> DELETE { ?s ?p ?o } INSERT { ?s ?p ?o } WHERE { ?s ?p ?o }",
        "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }",
        "DESCRIBE ?s WHERE { ?s ?p ?o }",
    ],
)
def test_unsupported_query_forms_are_rejected(
    app_client: TestClient,
    query: str,
) -> None:
    resp = app_client.post("/api/v1/sparql", json={"query": query})
    assert resp.status_code == 400
    assert "read-only" in resp.json()["detail"].lower()


@pytest.mark.security
def test_empty_query_is_rejected(app_client: TestClient) -> None:
    assert app_client.post("/api/v1/sparql", json={"query": ""}).status_code == 422
