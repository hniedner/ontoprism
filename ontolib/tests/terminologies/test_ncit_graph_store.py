"""NcitGraphStore assembly logic against a real local stub server (CI-runnable)."""

import pytest

from ontolib.terminologies.ncit.graph_store import (
    _MAX_NEIGHBORHOOD_NODES,
    NcitGraphStore,
    _rel,
)
from ontolib.terminologies.ncit.models import ConceptDetail, ConceptRef, Relationship
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient


@pytest.mark.unit
def test_rel_returns_none_when_rel_or_target_missing() -> None:
    assert _rel(None, "label", "http://ncit#C1", "Target") is None
    assert _rel("http://ncit#R1", "rel", None, "Target") is None
    assert _rel("http://ncit#R1", "rel", "http://ncit#C1", "Target") is not None


@pytest.mark.unit
async def test_get_concept_detail_unknown_returns_none(
    ncit_stub_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with OxigraphHttpClient(ncit_stub_url) as client:
        store = NcitGraphStore(client)

        async def _empty_select(_query: str) -> list[dict[str, str]]:
            return []

        monkeypatch.setattr(store._client, "select", _empty_select)
        detail = await store.get_concept_detail("C999999")
    assert detail is None


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
async def test_neighborhood_unknown_center_returns_empty(
    ncit_stub_url: str,
) -> None:
    async def _none_for_any(_code: str) -> None:
        return None

    async with OxigraphHttpClient(ncit_stub_url) as client:
        store = NcitGraphStore(client)
        store.get_concept_detail = _none_for_any  # type: ignore[method-assign]
        graph = await store.get_neighborhood("C999999")
    assert graph.center == "C999999"
    assert graph.nodes == []
    assert graph.edges == []


@pytest.mark.unit
async def test_neighborhood_skips_already_expanded(ncit_stub_url: str) -> None:
    center = ConceptDetail(
        code="C1",
        label="Center",
        parents=[ConceptRef(code="P1", label="Parent")],
        roles=[
            Relationship(
                relation="R1",
                relation_label="rel",
                target=ConceptRef(code="P1", label="Parent"),
            )
        ],
    )

    async def detail(code: str) -> ConceptDetail | None:
        if code == "C1":
            return center
        return ConceptDetail(code=code, label=code, parents=[])

    async with OxigraphHttpClient(ncit_stub_url) as client:
        store = NcitGraphStore(client)
        store.get_concept_detail = detail  # type: ignore[method-assign]
        graph = await store.get_neighborhood("C1", depth=2)
    assert graph.center == "C1"
    assert len(graph.nodes) == 2  # C1 + P1
    assert graph.truncated is False


@pytest.mark.unit
async def test_neighborhood_skips_missing_neighbor(ncit_stub_url: str) -> None:
    center = ConceptDetail(
        code="C1",
        label="Center",
        parents=[
            ConceptRef(code="P1", label="Present"),
            ConceptRef(code="P2", label="Missing"),
        ],
        children=[ConceptRef(code="C2", label="Child")],
    )

    async def detail(code: str) -> ConceptDetail | None:
        if code == "C1":
            return center
        if code == "P2":
            return None
        return ConceptDetail(code=code, label=code, parents=[])

    async with OxigraphHttpClient(ncit_stub_url) as client:
        store = NcitGraphStore(client)
        store.get_concept_detail = detail  # type: ignore[method-assign]
        graph = await store.get_neighborhood("C1", depth=2)
    node_codes = {n.code for n in graph.nodes}
    assert "P1" in node_codes
    assert "P2" in node_codes  # added as neighbor at hop 1, but not expanded further
    assert "C2" in node_codes
    assert graph.truncated is False


@pytest.mark.unit
async def test_list_concepts_returns_ordered_page(ncit_stub_url: str) -> None:
    async with OxigraphHttpClient(ncit_stub_url) as client:
        page = await NcitGraphStore(client).list_concepts(limit=25, offset=0)

    assert page.total == 2
    assert [h.code for h in page.hits] == ["C3262", "C9305"]
    assert page.hits[0].semantic_type == "Neoplastic Process"
    assert page.hits[0].matched_synonym is None


@pytest.mark.unit
async def test_search_records_returns_records_with_synonyms(
    ncit_stub_url: str,
) -> None:
    async with OxigraphHttpClient(ncit_stub_url) as client:
        records = await NcitGraphStore(client).search_records(limit=100, offset=0)

    assert len(records) == 1
    assert records[0]["code"] == "C3262"
    assert records[0]["label"] == "Neoplasm"
    assert records[0]["semantic_type"] == "Neoplastic Process"
    assert records[0]["synonyms"] == "Neoplasia||Neoplasm"


@pytest.mark.unit
async def test_embedding_records_returns_records_with_all_fields(
    ncit_stub_url: str,
) -> None:
    async with OxigraphHttpClient(ncit_stub_url) as client:
        records = await NcitGraphStore(client).embedding_records(limit=200, offset=0)

    assert len(records) == 1
    assert records[0]["code"] == "C3262"
    assert records[0]["preferred_name"] == "Neoplasm"
    assert records[0]["definition"] == "A benign or malignant tissue growth."
    assert records[0]["semantic_type"] == "Neoplastic Process"
    assert records[0]["synonyms"] == "Neoplasia | Neoplasm"


@pytest.mark.unit
async def test_list_concepts_memoizes_total(ncit_stub_url: str) -> None:
    async with OxigraphHttpClient(ncit_stub_url) as client:
        store = NcitGraphStore(client)
        page1 = await store.list_concepts(limit=25, offset=0)
        assert page1.total == 2
        page2 = await store.list_concepts(limit=25, offset=1)
        assert page2.total == 2  # memoized after first call


@pytest.mark.unit
async def test_neighborhood_not_truncated_when_under_cap(ncit_stub_url: str) -> None:
    # A small neighborhood that fits under the cap must report truncated=False.
    async with OxigraphHttpClient(ncit_stub_url) as client:
        graph = await NcitGraphStore(client).get_neighborhood("C3262")
    assert graph.truncated is False


@pytest.mark.unit
async def test_neighborhood_truncated_when_cap_filled_exactly(
    ncit_stub_url: str,
) -> None:
    # Exact-fill boundary: the first hop lands nodes on the cap with NO per-node drop,
    # so expansion is cut short before its neighbors are expanded at hop 2. That is a
    # partial result and must report truncated=True (regression: the flag under-reported
    # when the cap was filled exactly rather than overshot).
    exact = _MAX_NEIGHBORHOOD_NODES - 1  # center node + these parents == cap
    center = ConceptDetail(
        code="C1",
        label="Center",
        parents=[ConceptRef(code=f"P{i}", label=f"p{i}") for i in range(exact)],
    )

    async def detail(code: str) -> ConceptDetail:
        return center if code == "C1" else ConceptDetail(code=code, label=code)

    async with OxigraphHttpClient(ncit_stub_url) as client:
        store = NcitGraphStore(client)
        store.get_concept_detail = detail  # type: ignore[method-assign]
        graph = await store.get_neighborhood("C1", depth=2)

    assert len(graph.nodes) == _MAX_NEIGHBORHOOD_NODES
    assert graph.truncated is True
