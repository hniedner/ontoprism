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
