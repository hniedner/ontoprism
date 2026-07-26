"""NCIt read-API contracts against bounded disposable and configured stores."""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
def test_concept_detail_renders_metadata_and_roles(
    isolated_api_client: TestClient,
) -> None:
    resp = isolated_api_client.get("/api/v1/ncit/concepts/C3262")
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
def test_unknown_concept_is_404(isolated_api_client: TestClient) -> None:
    assert isolated_api_client.get("/api/v1/ncit/concepts/C0").status_code == 404


@pytest.mark.integration
def test_search_returns_hits(isolated_api_client: TestClient) -> None:
    resp = isolated_api_client.get(
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
def test_neighborhood_has_center_and_role_edge(
    isolated_api_client: TestClient,
) -> None:
    resp = isolated_api_client.get("/api/v1/ncit/concepts/C3262/neighborhood")
    assert resp.status_code == 200
    body = resp.json()
    node_codes = {n["code"] for n in body["nodes"]}
    assert {"C3262", "C12922"} <= node_codes
    assert any(e["kind"] == "role" for e in body["edges"])


@pytest.mark.integration
@pytest.mark.full_build
@pytest.mark.full_store
def test_list_browses_concepts_without_a_query(live_api_client: TestClient) -> None:
    # No search term: the browse endpoint pages through all concepts in code order.
    resp = live_api_client.get("/api/v1/ncit/list", params={"limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == ""
    assert body["total"] > 100_000  # the full NCIt concept universe
    assert len(body["hits"]) == 5
    assert all(h["code"].startswith("C") for h in body["hits"])


@pytest.mark.integration
def test_list_paginates_disjointly(isolated_api_client: TestClient) -> None:
    first = isolated_api_client.get(
        "/api/v1/ncit/list", params={"limit": 5, "offset": 0}
    ).json()
    second = isolated_api_client.get(
        "/api/v1/ncit/list", params={"limit": 5, "offset": 5}
    ).json()
    first_codes = {h["code"] for h in first["hits"]}
    second_codes = {h["code"] for h in second["hits"]}
    assert first_codes.isdisjoint(second_codes)
