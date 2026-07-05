"""NcitGraphStore assembly logic against a real local stub server (CI-runnable)."""

import pytest

from ontolib.terminologies.ncit.graph_store import (
    _MAX_NEIGHBORHOOD_NODES,
    NcitGraphStore,
)
from ontolib.terminologies.ncit.models import ConceptDetail, ConceptRef
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient


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
async def test_labels_for_batch(ncit_stub_url: str) -> None:
    async with OxigraphHttpClient(ncit_stub_url) as client:
        labels = await NcitGraphStore(client).labels_for(["C9305", "C2991"])
    assert labels == {"C9305": "Malignant Neoplasm", "C2991": "Disease or Disorder"}


@pytest.mark.unit
async def test_labels_for_empty_is_noop(ncit_stub_url: str) -> None:
    async with OxigraphHttpClient(ncit_stub_url) as client:
        assert await NcitGraphStore(client).labels_for([]) == {}


@pytest.mark.unit
async def test_neighborhood_builds_typed_edges(ncit_stub_url: str) -> None:
    async with OxigraphHttpClient(ncit_stub_url) as client:
        graph = await NcitGraphStore(client).get_neighborhood("C3262")

    node_codes = {n.code for n in graph.nodes}
    assert {"C3262", "C12922", "C2991", "C9305"} <= node_codes
    kinds = {e.kind for e in graph.edges}
    assert {"subClassOf", "role", "association"} <= kinds


@pytest.mark.unit
async def test_neighborhood_depth_expands_beyond_one_hop(ncit_stub_url: str) -> None:
    # depth=2 expands each depth-1 neighbor, so more edges are discovered than at
    # depth=1 (regression: `depth` used to be ignored). Node set stays deduped.
    async with OxigraphHttpClient(ncit_stub_url) as client:
        store = NcitGraphStore(client)
        one = await store.get_neighborhood("C3262", depth=1)
        two = await store.get_neighborhood("C3262", depth=2)

    assert len(two.edges) > len(one.edges)
    assert {n.code for n in one.nodes} <= {n.code for n in two.nodes}


@pytest.mark.unit
async def test_neighborhood_node_count_is_hard_capped(ncit_stub_url: str) -> None:
    # A single concept with far more neighbors than the cap must not blow past it:
    # the bound is enforced while adding nodes, not only between concepts. Every
    # surviving edge must still connect two surviving nodes (no dangling endpoints).
    over_cap = _MAX_NEIGHBORHOOD_NODES + 200
    dense = ConceptDetail(
        code="C1",
        label="Dense",
        parents=[ConceptRef(code=f"P{i}", label=f"p{i}") for i in range(over_cap)],
    )

    async def only_center(code: str) -> ConceptDetail | None:
        return dense if code == "C1" else None

    async with OxigraphHttpClient(ncit_stub_url) as client:
        store = NcitGraphStore(client)
        store.get_concept_detail = only_center  # type: ignore[method-assign]
        graph = await store.get_neighborhood("C1", depth=1)

    assert len(graph.nodes) == _MAX_NEIGHBORHOOD_NODES
    assert graph.truncated is True  # dropped neighbors are signalled, not silent
    node_codes = {n.code for n in graph.nodes}
    assert all(e.source in node_codes and e.target in node_codes for e in graph.edges)


@pytest.mark.unit
async def test_neighborhood_not_truncated_when_under_cap(ncit_stub_url: str) -> None:
    # A small neighborhood that fits under the cap must report truncated=False.
    async with OxigraphHttpClient(ncit_stub_url) as client:
        graph = await NcitGraphStore(client).get_neighborhood("C3262")
    assert graph.truncated is False
