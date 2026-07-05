"""Behavioral tests for the CDE-centred subgraph endpoint (caDSR↔NCIt graph join)."""

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.dependencies import get_ncit_store
from ontolib.terminologies.ncit.models import GraphEdge, GraphNode, Neighborhood


def _ncit_reachable() -> bool:
    url = get_settings().ncit_sparql_url.rstrip("/")
    try:
        resp = httpx.post(
            f"{url}/query",
            content=b"ASK {}",
            headers={
                "Content-Type": "application/sparql-query",
                "Accept": "application/sparql-results+json",
            },
            timeout=2.0,
        )
    except httpx.HTTPError:
        return False
    return resp.status_code == 200


class _FakeStore:
    """A hand-written NCIt store returning a fixed neighborhood (the join boundary)."""

    async def get_neighborhood(self, code: str, *, depth: int = 1) -> Neighborhood:
        _ = depth
        return Neighborhood(
            center=code,
            nodes=[
                GraphNode(code=code, label="Neoplasm", semantic_type="Neoplastic"),
                GraphNode(code="C9305", label="Malignant Neoplasm"),
            ],
            edges=[
                GraphEdge(
                    source="C9305",
                    target=code,
                    relation="subClassOf",
                    kind="subClassOf",
                )
            ],
        )


@pytest.mark.api
def test_cde_neighborhood_joins_into_ncit(cadsr_client: TestClient) -> None:
    app: Any = cadsr_client.app
    app.dependency_overrides[get_ncit_store] = _FakeStore
    resp = cadsr_client.get("/api/v1/cadsr/cdes/100/neighborhood")
    assert resp.status_code == 200
    body = resp.json()
    assert body["center"] == "cde:100:2.0"
    codes = {n["code"] for n in body["nodes"]}
    # the CDE pseudo-node, its mapped concept (C3262), and that concept's neighbor
    assert {"cde:100:2.0", "C3262", "C9305"} <= codes
    kinds = {e["kind"] for e in body["edges"]}
    assert "cde-concept" in kinds  # the CDE→concept join edge
    assert "subClassOf" in kinds  # the concept's own NCIt neighborhood
    # no dangling edges: every endpoint is a real node
    assert all(e["source"] in codes and e["target"] in codes for e in body["edges"])


@pytest.mark.api
def test_cde_neighborhood_unknown_cde_is_404(cadsr_client: TestClient) -> None:
    app: Any = cadsr_client.app
    app.dependency_overrides[get_ncit_store] = _FakeStore
    resp = cadsr_client.get("/api/v1/cadsr/cdes/999999/neighborhood")
    assert resp.status_code == 404


@pytest.mark.integration
def test_cde_neighborhood_against_live_ncit(cadsr_client: TestClient) -> None:
    # No store override → the real NCIt store answers; the temp CDE 100 maps to the
    # real concept C3262 (Neoplasm), which has a genuine NCIt neighborhood.
    if not _ncit_reachable():
        pytest.skip("NCIt Oxigraph not reachable")
    resp = cadsr_client.get("/api/v1/cadsr/cdes/100/neighborhood")
    assert resp.status_code == 200
    body = resp.json()
    assert body["center"] == "cde:100:2.0"
    codes = {n["code"] for n in body["nodes"]}
    assert {"cde:100:2.0", "C3262"} <= codes
    # C3262's real neighborhood brings in more than just the CDE + its concept.
    assert len(body["nodes"]) > 2
    assert all(e["source"] in codes and e["target"] in codes for e in body["edges"])
