"""Behavioral tests for the CDE-centred subgraph endpoint (caDSR↔NCIt graph join)."""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api.v1.cadsr import _build_cde_neighborhood
from backend.dependencies import get_ncit_store
from ontolib.repositories.cadsr.repository import CdeRepository
from ontolib.terminologies.ncit.graph_store import NcitGraphStore
from ontolib.terminologies.ncit.models import GraphEdge, GraphNode, Neighborhood


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

    async def get_neighborhoods(self, codes: list[str]) -> list[Neighborhood]:
        return [await self.get_neighborhood(code) for code in codes]


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
def test_cde_neighborhood_against_real_qlever(
    isolated_cadsr_client: TestClient,
) -> None:
    # No store override: bounded real QLever answers for the temporary CDE mapping.
    resp = isolated_cadsr_client.get("/api/v1/cadsr/cdes/100/neighborhood")
    assert resp.status_code == 200
    body = resp.json()
    assert body["center"] == "cde:100:2.0"
    codes = {n["code"] for n in body["nodes"]}
    assert {"cde:100:2.0", "C3262"} <= codes
    # C3262's real neighborhood brings in more than just the CDE + its concept.
    assert len(body["nodes"]) > 2
    assert all(e["source"] in codes and e["target"] in codes for e in body["edges"])


@pytest.mark.mutating_integration
@pytest.mark.integration
def test_large_cde_neighborhood_matches_individual_real_store_reads(
    isolated_cadsr_client: TestClient,
    isolated_qlever_settings: None,
    isolated_postgres_settings: None,
    tmp_path: Path,
) -> None:
    """Distinct links retain serial merge precedence, missing nodes and truncation."""
    db = tmp_path / "cde_repository.db"
    codes = ["C9305", "C12922", "C2991", *[f"C999999{i}" for i in range(9)]]
    with closing(sqlite3.connect(db)) as conn:
        conn.executemany(
            "INSERT INTO cde_concepts "
            "(concept_code, concept_name, public_id, version, "
            "concept_type, is_primary) "
            "VALUES (?,?,?,?,?,?)",
            [
                (code, "Mapped concept", "100", "2.0", "object_class", 0)
                for code in codes
            ],
        )
        conn.commit()
    app: Any = isolated_cadsr_client.app
    store: NcitGraphStore = app.state.ncit_store
    cde = CdeRepository(db).get_cde("100")
    assert cde is not None
    assert len(cde.concepts) == 13

    original = store.get_neighborhood

    async def check() -> None:
        assert await store.get_neighborhoods([]) == []
        with pytest.raises(ValueError, match="at most"):
            await store.get_neighborhoods(["C3262"] * 13)
        # Read each selected neighborhood serially as the pre-change route did.
        expected = [await original(link.concept_code) for link in cde.concepts[:12]]
        center = "cde:100:2.0"
        nodes = {
            center: GraphNode(code=center, label=cde.long_name, semantic_type="CDE")
        }
        edges: dict[tuple[str, str, str, str], GraphEdge] = {}
        for link, sub in zip(cde.concepts[:12], expected, strict=True):
            for node in sub.nodes:
                nodes.setdefault(node.code, node)
            for edge in sub.edges:
                edges.setdefault(
                    (edge.source, edge.target, edge.relation, edge.kind), edge
                )
            nodes.setdefault(
                link.concept_code,
                GraphNode(code=link.concept_code, label=link.concept_name),
            )
            edge = GraphEdge(
                source=center,
                target=link.concept_code,
                relation=link.concept_type or "hasConcept",
                kind="cde-concept",
            )
            edges.setdefault((edge.source, edge.target, edge.relation, edge.kind), edge)
        grouped = await store.get_neighborhoods(
            [link.concept_code for link in cde.concepts[:12]]
        )
        assert grouped == expected
        result = await _build_cde_neighborhood(cde, store)
        assert result.truncated
        assert result.nodes == list(nodes.values())
        assert result.edges == list(edges.values())
        assert codes[-1] not in {node.code for node in result.nodes}
        assert codes[-2] in {node.code for node in result.nodes}

    assert isolated_cadsr_client.portal is not None
    isolated_cadsr_client.portal.call(check)
