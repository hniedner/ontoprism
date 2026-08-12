"""NCIt read-API contracts against bounded disposable and configured stores."""

import pytest
from fastapi.testclient import TestClient

_ALLOW_EMPTY_GRAPH = True
_REQUIRE_POPULATED_GRAPH = False


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
    assert body["representation_status"] == "legacy-precoordinated"
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
    statuses = {node["code"]: node["representation_status"] for node in body["nodes"]}
    assert statuses["C3262"] == "legacy-precoordinated"
    assert statuses["C12922"] is None
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


def _assert_representation_status_matches_published_graph(
    client: TestClient, *, allow_empty: bool
) -> None:
    status = "legacy-precoordinated"
    first = client.get(
        "/api/v1/ncit/list",
        params={"limit": 1, "representation_status": status},
    )
    assert first.status_code == 200, first.text
    first_page = first.json()
    if not allow_empty:
        assert first_page["total"] > 0
    if first_page["total"] == 0:
        assert first_page["hits"] == []
        unfiltered = client.get("/api/v1/ncit/list", params={"limit": 1})
        assert unfiltered.status_code == 200, unfiltered.text
        hit = unfiltered.json()["hits"][0]
        assert hit["representation_status"] is None

        detail = client.get(f"/api/v1/ncit/concepts/{hit['code']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["representation_status"] is None

        neighborhood = client.get(f"/api/v1/ncit/concepts/{hit['code']}/neighborhood")
        assert neighborhood.status_code == 200, neighborhood.text
        center = next(
            node for node in neighborhood.json()["nodes"] if node["code"] == hit["code"]
        )
        assert center["representation_status"] is None

        search = client.get(
            "/api/v1/ncit/search",
            params={"q": hit["label"], "limit": 10, "representation_status": status},
        )
        assert search.status_code == 200, search.text
        assert search.json()["total"] == 0
        assert search.json()["hits"] == []
        return

    assert first_page["hits"][0]["representation_status"] == status

    last = client.get(
        "/api/v1/ncit/list",
        params={
            "limit": 1,
            "offset": first_page["total"] - 1,
            "representation_status": status,
        },
    )
    assert last.status_code == 200, last.text
    assert last.json()["hits"][0]["representation_status"] == status

    hit = first_page["hits"][0]
    detail = client.get(f"/api/v1/ncit/concepts/{hit['code']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["representation_status"] == status

    neighborhood = client.get(f"/api/v1/ncit/concepts/{hit['code']}/neighborhood")
    assert neighborhood.status_code == 200, neighborhood.text
    center = next(
        node for node in neighborhood.json()["nodes"] if node["code"] == hit["code"]
    )
    assert center["representation_status"] == status

    search = client.get(
        "/api/v1/ncit/search",
        params={"q": hit["label"], "limit": 10, "representation_status": status},
    )
    assert search.status_code == 200, search.text
    assert any(row["code"] == hit["code"] for row in search.json()["hits"])
    assert all(row["representation_status"] == status for row in search.json()["hits"])


@pytest.mark.integration
def test_disposable_published_representation_marker_surfaces_across_ncit_reads(
    isolated_api_client: TestClient,
) -> None:
    _assert_representation_status_matches_published_graph(
        isolated_api_client, allow_empty=_REQUIRE_POPULATED_GRAPH
    )


@pytest.mark.integration
@pytest.mark.full_store
def test_configured_representation_status_matches_published_graph(
    live_api_client: TestClient,
) -> None:
    _assert_representation_status_matches_published_graph(
        live_api_client, allow_empty=_ALLOW_EMPTY_GRAPH
    )
