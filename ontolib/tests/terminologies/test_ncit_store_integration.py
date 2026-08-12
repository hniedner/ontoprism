"""Integration tests against real QLever (no mocks).

Behavioral contracts use the bounded disposable fixture. Separate ``full_store``
contracts pin the configured inferred build's count, version, and canonical role shape.
"""

import pytest

from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.client import ncit_sparql_client
from ontolib.terminologies.ncit.graph_store import NcitGraphStore
from ontolib.terminologies.ncit.role_queries import build_role_relationships_query

_PINNED_TRIPLE_COUNT = 12_980_813
_PINNED_VERSION = "26.07d"


@pytest.mark.integration
@pytest.mark.full_build
@pytest.mark.full_store
async def test_triple_count_matches_pinned_build(ncit_url: str) -> None:
    async with ncit_sparql_client(ncit_url) as client:
        assert await client.count() == _PINNED_TRIPLE_COUNT


@pytest.mark.integration
async def test_seeded_version_info_is_pinned(isolated_qlever_url: str) -> None:
    async with ncit_sparql_client(isolated_qlever_url) as client:
        assert await client.version() == _PINNED_VERSION


@pytest.mark.integration
async def test_seeded_c3262_role_traversal_yields_abnormal_cell(
    isolated_qlever_url: str,
) -> None:
    # C3262 (Neoplasm) -> R105 (Disease_Has_Abnormal_Cell) -> C12922. This is the
    # restriction-traversal path that makes NCIt roles queryable at all.
    async with ncit_sparql_client(isolated_qlever_url) as client:
        rows = await client.select(build_role_relationships_query("C3262", NCIT_NS))
    pairs = {
        (r["rel"].rsplit("#", 1)[-1], r["target"].rsplit("#", 1)[-1])
        for r in rows
        if r.get("rel") and r.get("target")
    }
    assert ("R105", "C12922") in pairs


@pytest.mark.integration
@pytest.mark.full_store
async def test_configured_version_info_is_pinned(ncit_url: str) -> None:
    async with ncit_sparql_client(ncit_url) as client:
        assert await client.version() == _PINNED_VERSION


@pytest.mark.integration
@pytest.mark.full_store
async def test_configured_c3262_role_traversal_yields_abnormal_cell(
    ncit_url: str,
) -> None:
    async with ncit_sparql_client(ncit_url) as client:
        rows = await client.select(build_role_relationships_query("C3262", NCIT_NS))
    pairs = {
        (r["rel"].rsplit("#", 1)[-1], r["target"].rsplit("#", 1)[-1])
        for r in rows
        if r.get("rel") and r.get("target")
    }
    assert ("R105", "C12922") in pairs


@pytest.mark.integration
@pytest.mark.full_build
@pytest.mark.full_store
async def test_defined_disease_hierarchy_and_anatomy_control(
    ncit_url: str,
) -> None:
    async with ncit_sparql_client(ncit_url) as client:
        store = NcitGraphStore(client)
        childhood_cancer = await store.get_concept_detail("C4005")
        tier_one = await store.get_concept_detail("C198031")
        lung = await store.get_concept_detail("C12468")
        tier_one_graph = await store.get_neighborhood("C198031", depth=3)

    assert childhood_cancer is not None
    assert {parent.code for parent in childhood_cancer.parents} == {"C6283", "C9305"}
    assert "C198027" in {child.code for child in childhood_cancer.children}
    assert tier_one is not None
    assert {parent.code for parent in tier_one.parents} == {"C198030"}
    assert {"C198032", "C198034"} <= {child.code for child in tier_one.children}
    hierarchy_edges = {
        (edge.source, edge.target)
        for edge in tier_one_graph.edges
        if edge.kind == "subClassOf"
    }
    assert {
        ("C198031", "C198030"),
        ("C198030", "C198027"),
        ("C198027", "C4005"),
    } <= hierarchy_edges
    assert lung is not None
    assert {parent.code for parent in lung.parents} == {"C13018"}
    assert {child.code for child in lung.children} == {"C32967", "C33483"}


@pytest.mark.integration
async def test_neighborhood_depth_two_pulls_more_than_one_hop(
    isolated_qlever_url: str,
) -> None:
    # depth is honored: a 2-hop expansion of C3262 reaches strictly more concepts than
    # a single hop, and stays within the node bound.
    async with ncit_sparql_client(isolated_qlever_url) as client:
        store = NcitGraphStore(client)
        one = await store.get_neighborhood("C3262", depth=1)
        two = await store.get_neighborhood("C3262", depth=2)
    assert len(two.nodes) > len(one.nodes)
    assert len(two.nodes) <= 400
