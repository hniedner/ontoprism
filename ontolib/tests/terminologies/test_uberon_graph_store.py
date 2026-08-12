"""Uberon/CL graph read-model contracts."""

import pytest
from pydantic import ValidationError

from ontolib.core.exceptions import StorageError
from ontolib.terminologies.uberon.graph_store import UberonGraphStore
from ontolib.terminologies.uberon.models import (
    UberonConceptRef,
    UberonGraphEdge,
    UberonRelationship,
    UberonSearchPage,
)

pytestmark = pytest.mark.unit


class _ShapeClient:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def select(self, query: str) -> list[dict[str, str]]:
        self.queries.append(query)
        if "SELECT ?label ?definition" in query:
            return [{"label": "lung", "definition": "Respiratory organ."}]
        if "?node rdfs:subClassOf <" in query:
            return [
                {
                    "node": "http://purl.obolibrary.org/obo/UBERON_0000104",
                    "label": "life cycle",
                }
            ]
        if (
            "<http://purl.obolibrary.org/obo/UBERON_0002048> "
            "rdfs:subClassOf ?node" in query
        ):
            return [
                {
                    "node": "http://purl.obolibrary.org/obo/UBERON_0001004",
                    "label": "respiratory system",
                }
            ]
        if "owl:onProperty" in query and "someValuesFrom" in query:
            return [
                {
                    "restriction": "http://example.test/r1",
                    "rel": "http://purl.obolibrary.org/obo/BFO_0000050",
                    "rellabel": "part of",
                    "target": "http://purl.obolibrary.org/obo/UBERON_0001004",
                    "tlabel": "respiratory system",
                },
                {
                    "restriction": "http://example.test/r2",
                    "rel": "http://purl.obolibrary.org/obo/RO_0002202",
                    "rellabel": "develops from",
                    "target": "http://purl.obolibrary.org/obo/CL_0000000",
                    "tlabel": "cell",
                },
            ]
        return []


@pytest.mark.asyncio
async def test_detail_carries_source_and_reads_part_of_as_restriction() -> None:
    client = _ShapeClient()

    detail = await UberonGraphStore(client).get_concept_detail("UBERON:0002048")  # type: ignore[arg-type]

    assert detail is not None
    assert detail.source == "uberon"
    assert detail.code == "UBERON:0002048"
    assert detail.label == "lung"
    observed = [
        (edge.kind, edge.target.code, edge.target.source) for edge in detail.relations
    ]
    assert observed == [
        ("part_of", "UBERON:0001004", "uberon"),
        ("other-restriction", "CL:0000000", "cl"),
    ]
    restriction_query = next(
        query for query in client.queries if "owl:onProperty" in query
    )
    assert "rdfs:subClassOf ?restriction" in restriction_query
    assert "owl:someValuesFrom ?target" in restriction_query
    direct_pattern = "?concept <http://purl.obolibrary.org/obo/BFO_0000050> ?target"
    assert direct_pattern not in restriction_query


@pytest.mark.asyncio
async def test_list_filters_source_before_page_and_memoizes_total() -> None:
    class _ListClient:
        def __init__(self) -> None:
            self.queries: list[str] = []

        async def select(self, query: str) -> list[dict[str, str]]:
            self.queries.append(query)
            if "COUNT(DISTINCT ?concept)" in query:
                return [{"count": "1"}]
            return [
                {
                    "concept": "http://purl.obolibrary.org/obo/CL_0000000",
                    "label": "cell",
                }
            ]

    client = _ListClient()
    store = UberonGraphStore(client)  # type: ignore[arg-type]

    first = await store.list_concepts(source="cl", limit=25, offset=0)
    second = await store.list_concepts(source="cl", limit=25, offset=25)

    assert first.total == second.total == 1
    assert first.hits[0].source == "cl"
    page_query = next(query for query in client.queries if "LIMIT 25 OFFSET 0" in query)
    assert page_query.index("CL_") < page_query.index("LIMIT 25")
    assert len([q for q in client.queries if "COUNT(DISTINCT ?concept)" in q]) == 1


@pytest.mark.asyncio
async def test_neighborhood_drops_edges_whose_capped_endpoint_was_dropped() -> None:
    store = UberonGraphStore(_ShapeClient())  # type: ignore[arg-type]

    graph = await store.get_neighborhood("UBERON:0002048", depth=1, node_limit=2)

    assert graph.truncated is True
    assert len(graph.nodes) == 2
    node_codes = {node.code for node in graph.nodes}
    assert all(
        edge.source in node_codes and edge.target in node_codes for edge in graph.edges
    )


@pytest.mark.asyncio
async def test_invalid_code_and_unknown_concept_fail_with_distinct_contracts() -> None:
    store = UberonGraphStore(_ShapeClient())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="CURIE"):
        await store.get_concept_detail("not-a-curie")

    class _EmptyClient:
        async def select(self, _query: str) -> list[dict[str, str]]:
            return []

    unknown_store = UberonGraphStore(_EmptyClient())  # type: ignore[arg-type]
    assert await unknown_store.get_concept_detail("CL:9999999") is None


@pytest.mark.asyncio
async def test_search_and_cache_records_preserve_cl_source_and_synonyms() -> None:
    class _SearchClient:
        async def select(self, query: str) -> list[dict[str, str]]:
            if "COUNT(DISTINCT ?concept)" in query:
                return [{"count": "1"}]
            return [
                {
                    "concept": "http://purl.obolibrary.org/obo/CL_0000000",
                    "label": "cell",
                    "matched": "native cell",
                    "synonyms": "native cell||cellular unit",
                }
            ]

    store = UberonGraphStore(_SearchClient())  # type: ignore[arg-type]

    page = await store.search("cell", source="cl", limit=5, offset=10)
    records = await store.search_records(limit=5, offset=0)

    assert page.total == 1
    assert page.hits[0].model_dump() == {
        "code": "CL:0000000",
        "source": "cl",
        "label": "cell",
        "matched_synonym": "native cell",
    }
    assert records == [
        {
            "code": "CL:0000000",
            "source": "cl",
            "label": "cell",
            "synonyms": "native cell||cellular unit",
        }
    ]


@pytest.mark.asyncio
async def test_neighborhood_rejects_node_limit_outside_supported_range() -> None:
    store = UberonGraphStore(_ShapeClient())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="depth=1"):
        await store.get_neighborhood("UBERON:0002048", depth=2)
    with pytest.raises(ValueError, match="node_limit"):
        await store.get_neighborhood("UBERON:0002048", node_limit=0)


@pytest.mark.asyncio
async def test_detail_reports_edge_truncation_instead_of_silent_limit() -> None:
    class _DenseClient:
        async def select(self, query: str) -> list[dict[str, str]]:
            if "SELECT ?label ?definition" in query:
                return [{"label": "dense concept"}]
            if "?node rdfs:subClassOf <" in query:
                return [
                    {
                        "node": f"http://purl.obolibrary.org/obo/UBERON_{index:07}",
                        "label": f"child {index}",
                    }
                    for index in range(201)
                ]
            return []

    detail = await UberonGraphStore(_DenseClient()).get_concept_detail(  # type: ignore[arg-type]
        "UBERON:0000064"
    )

    assert detail is not None
    assert len(detail.children) == 200
    assert detail.truncated is True


@pytest.mark.asyncio
async def test_unknown_neighborhood_is_not_a_successful_empty_graph() -> None:
    class _EmptyClient:
        async def select(self, _query: str) -> list[dict[str, str]]:
            return []

    store = UberonGraphStore(_EmptyClient())  # type: ignore[arg-type]

    with pytest.raises(LookupError, match="CL:9999999"):
        await store.get_neighborhood("CL:9999999")


@pytest.mark.asyncio
async def test_missing_required_sparql_binding_fails_closed() -> None:
    class _MalformedClient:
        async def select(self, query: str) -> list[dict[str, str]]:
            if "SELECT ?label ?definition" in query:
                return [{"label": "malformed"}]
            if "?node rdfs:subClassOf <" in query:
                return [{"label": "missing node"}]
            return []

    with pytest.raises(StorageError, match="required bindings"):
        await UberonGraphStore(_MalformedClient()).get_concept_detail(  # type: ignore[arg-type]
            "UBERON:0002048"
        )


@pytest.mark.asyncio
async def test_missing_list_and_search_counts_fail_closed() -> None:
    class _MissingCountClient:
        async def select(self, query: str) -> list[dict[str, str]]:
            if "COUNT(DISTINCT ?concept)" in query:
                return []
            return []

    store = UberonGraphStore(_MissingCountClient())  # type: ignore[arg-type]

    with pytest.raises(StorageError, match="list count"):
        await store.list_concepts(source="uberon")
    with pytest.raises(StorageError, match="search count"):
        await store.search("lung", source="uberon")


@pytest.mark.unit
def test_models_enforce_curie_source_edge_and_pagination_invariants() -> None:
    with pytest.raises(ValidationError, match="UBERON:digits"):
        UberonConceptRef(code="not-a-curie", source="uberon")
    with pytest.raises(ValidationError):
        UberonConceptRef(code="CL:0000000", source="uberon")
    with pytest.raises(ValidationError):
        UberonGraphEdge(
            source="UBERON:1",
            target="UBERON:2",
            relation="BFO_0000050",
            kind="other-restriction",
        )
    with pytest.raises(ValidationError, match="canonical edge kind"):
        UberonRelationship(
            relation="RO_0002202",
            kind="subClassOf",
            target=UberonConceptRef(code="CL:0000000", source="cl"),
        )
    with pytest.raises(ValidationError):
        UberonSearchPage(query="x", total=-1, limit=0, offset=-1)
