"""NcitGraphStore assembly logic against a real local stub server (CI-runnable)."""

import pytest

from fairlib.terminologies.ncit.graph_store import NcitGraphStore
from fairlib.terminologies.oxigraph_http_client import OxigraphHttpClient


@pytest.mark.unit
async def test_concept_detail_assembles_all_sections(ncit_stub_url: str) -> None:
    async with OxigraphHttpClient(ncit_stub_url) as client:
        detail = await NcitGraphStore(client).get_concept_detail("C3262")

    assert detail is not None
    assert detail.label == "Neoplasm"
    assert detail.definition
    assert detail.semantic_types == ["Neoplastic Process"]
    assert detail.synonyms == ["Neoplasia", "Neoplasm"]
    assert [p.code for p in detail.parents] == ["C2991"]
    assert [c.code for c in detail.children] == ["C9305"]
    # Roles surface the restriction target with its human-readable relation name.
    assert detail.roles[0].relation == "R105"
    assert detail.roles[0].target.code == "C12922"
    assert detail.associations[0].relation == "A8"
    assert detail.incoming_roles[0].target.code == "C4"


@pytest.mark.unit
async def test_search_returns_hits_and_total(ncit_stub_url: str) -> None:
    async with OxigraphHttpClient(ncit_stub_url) as client:
        page = await NcitGraphStore(client).search("neoplasm", limit=10)

    assert page.total == 2
    assert [h.code for h in page.hits] == ["C3262", "C9305"]
    assert page.hits[0].semantic_type == "Neoplastic Process"


@pytest.mark.unit
async def test_neighborhood_builds_typed_edges(ncit_stub_url: str) -> None:
    async with OxigraphHttpClient(ncit_stub_url) as client:
        graph = await NcitGraphStore(client).get_neighborhood("C3262")

    node_codes = {n.code for n in graph.nodes}
    assert {"C3262", "C12922", "C2991", "C9305"} <= node_codes
    kinds = {e.kind for e in graph.edges}
    assert {"subClassOf", "role", "association"} <= kinds
