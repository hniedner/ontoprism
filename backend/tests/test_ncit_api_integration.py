"""Integration tests for the NCIt read API against the live store (no mocks)."""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
def test_concept_detail_renders_metadata_and_roles(live_api_client: TestClient) -> None:
    resp = live_api_client.get("/api/v1/ncit/concepts/C3262")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == "C3262"
    assert body["label"] == "Neoplasm"
    assert "Neoplastic Process" in body["semantic_types"]
    assert body["definition"]
    assert "Neoplasia" in body["synonyms"]
    # Roles must render (restriction traversal): C3262 -> R105 -> C12922.
    role_targets = {(r["relation"], r["target"]["code"]) for r in body["roles"]}
    assert ("R105", "C12922") in role_targets


@pytest.mark.integration
def test_unknown_concept_is_404(live_api_client: TestClient) -> None:
    assert live_api_client.get("/api/v1/ncit/concepts/C0").status_code == 404


@pytest.mark.integration
def test_search_returns_hits(live_api_client: TestClient) -> None:
    resp = live_api_client.get(
        "/api/v1/ncit/search", params={"q": "neoplasm", "limit": 10}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] > 0
    assert body["hits"]
    assert all(
        "neoplasm" in (h["label"] or "").lower() or h["matched_synonym"]
        for h in body["hits"]
    )


@pytest.mark.integration
def test_neighborhood_has_center_and_role_edge(live_api_client: TestClient) -> None:
    resp = live_api_client.get("/api/v1/ncit/concepts/C3262/neighborhood")
    assert resp.status_code == 200
    body = resp.json()
    node_codes = {n["code"] for n in body["nodes"]}
    assert {"C3262", "C12922"} <= node_codes
    assert any(e["kind"] == "role" for e in body["edges"])


@pytest.mark.integration
def test_guarded_sparql_select_runs(live_api_client: TestClient) -> None:
    resp = live_api_client.post(
        "/api/v1/sparql",
        json={"query": "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }"},
    )
    assert resp.status_code == 200
    bindings = resp.json()["result"]["results"]["bindings"]
    assert bindings[0]["n"]["value"] == "12836426"
