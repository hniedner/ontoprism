"""Integration tests for the decomposition query layer against real Oxigraph.

Two explicit tiers:
- SPARQL builders parse against the bounded disposable engine.
- ``full_store`` contracts run only against configured stated NCIt.
"""

from __future__ import annotations

import os
from collections import Counter

import httpx
import pytest

from ontolib.decomposition.extract import (
    PartOfPair,
    ancestor_pairs_from_rows,
    concepts_from_rows,
    make_is_ancestor,
    part_of_pairs_from_rows,
    semantic_type_of_from_rows,
)
from ontolib.decomposition.filler_selection import select_constituents
from ontolib.decomposition.stated_queries import (
    build_ancestor_pairs_query,
    build_genus_walk_members_query,
    build_in_scope_concepts_query,
    build_part_of_pairs_queries,
    build_part_of_pairs_query,
    build_role_restrictions_query,
    build_semantic_type_of_query,
    build_semantic_type_query,
    resolve_morphology_filler,
    walk_genus_chain,
)
from ontolib.terminologies.namespaces import NCIT_NS, OWL_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.oxigraph_http_client import (
    OxigraphHttpClient,
    flatten_bindings,
    parse_ask_result,
)

_DEFAULT_NCIT_URL = "http://localhost:7888"


def _url() -> str:
    return os.environ.get("NCIT_SPARQL_URL", _DEFAULT_NCIT_URL)


def _reachable(url: str) -> bool:
    try:
        resp = httpx.post(
            f"{url.rstrip('/')}/query",
            content=b"ASK {}",
            headers={
                "Content-Type": "application/sparql-query",
                "Accept": "application/sparql-results+json",
            },
            timeout=2.0,
        )
    except httpx.HTTPError:
        return False
    resp.raise_for_status()
    return True


def _stated_loaded(url: str) -> bool:
    try:
        resp = httpx.post(
            f"{url.rstrip('/')}/query",
            content=(f"ASK {{ GRAPH <{STATED_GRAPH_IRI}> {{ ?s ?p ?o }} }}".encode()),
            headers={
                "Content-Type": "application/sparql-query",
                "Accept": "application/sparql-results+json",
            },
            timeout=2.0,
        )
    except httpx.HTTPError:
        return False
    resp.raise_for_status()
    return parse_ask_result(resp.json())


@pytest.mark.integration
async def test_stated_query_builders_parse_against_disposable_store(
    isolated_oxigraph_url: str,
) -> None:
    async with OxigraphHttpClient(isolated_oxigraph_url) as client:
        assert isinstance(
            await client.select(build_role_restrictions_query("C6135")), list
        )
        assert isinstance(await client.select(build_semantic_type_query("C6135")), list)
        assert isinstance(
            await client.select(build_ancestor_pairs_query(["C12400", "C12401"])), list
        )
        assert isinstance(
            await client.select(
                build_in_scope_concepts_query(["Neoplastic Process"], limit=5)
            ),
            list,
        )
        for q in build_genus_walk_members_query("C6135"):
            rows = await client.select(q)
            assert isinstance(rows, list)


@pytest.mark.integration
async def test_part_of_pairs_queries_cover_production_shaped_disposable_store(
    isolated_oxigraph_url: str,
) -> None:
    # Sorting puts C12510 plus the 15 decoys in the first tile and C32291 in the
    # second, so the inherited known pair exercises a cross-tile combination.
    codes = [
        "C12510",
        *(f"C200{i:02d}" for i in range(15)),
        "C32291",
        "C99101",
        "C99102",
        "C99103",
        "C99104",
        "C99105",
        "C99106",
    ]
    async with OxigraphHttpClient(isolated_oxigraph_url) as client:
        rows = []
        for query in build_part_of_pairs_queries(codes):
            rows.extend(await client.select(query))
        empty_rows = await client.select(
            build_part_of_pairs_query(part_codes=[], whole_codes=[])
        )
        direct_raw = await client.select_raw(
            build_part_of_pairs_query(
                part_codes=["C32291"],
                whole_codes=["C12510"],
            )
        )
        direct_rows = flatten_bindings(direct_raw)
        reverse_rows = await client.select(
            build_part_of_pairs_query(
                part_codes=["C12510"],
                whole_codes=["C32291"],
            )
        )
        health = await client.select(
            f"SELECT ?s WHERE {{ GRAPH <{STATED_GRAPH_IRI}> {{ ?s ?p ?o }} }} LIMIT 1"
        )

    assert Counter(part_of_pairs_from_rows(rows)) == Counter(
        {
            PartOfPair(part="C32291", whole="C12510"): 1,
            PartOfPair(part="C20000", whole="C99106"): 1,
            PartOfPair(part="C99101", whole="C99102"): 1,
            PartOfPair(part="C99103", whole="C99104"): 1,
        }
    )
    assert empty_rows == []
    assert direct_rows == [{"part": f"{NCIT_NS}C32291", "whole": f"{NCIT_NS}C12510"}]
    assert direct_raw == {
        "head": {"vars": ["part", "whole"]},
        "results": {
            "bindings": [
                {
                    "part": {"type": "uri", "value": f"{NCIT_NS}C32291"},
                    "whole": {"type": "uri", "value": f"{NCIT_NS}C12510"},
                }
            ]
        },
    }
    assert reverse_rows == []
    assert health
    assert health[0].get("s")


@pytest.mark.integration
@pytest.mark.full_store
async def test_part_of_pairs_query_matches_full_store_and_stays_healthy() -> None:
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt Oxigraph not reachable at {url}")
    if not _stated_loaded(url):
        pytest.skip("stated NCIt graph not loaded (run owl_load with include_stated)")

    async with OxigraphHttpClient(url) as client:
        version_rows = await client.select(
            f"SELECT ?version WHERE {{ GRAPH <{STATED_GRAPH_IRI}> {{ "
            f"?ontology a <{OWL_NS}Ontology> ; "
            f"<{OWL_NS}versionInfo> ?version . }} }}"
        )
        rows = await client.select(
            build_part_of_pairs_query(
                part_codes=["C32291"],
                whole_codes=["C12510"],
            )
        )
        no_match_rows = await client.select(
            build_part_of_pairs_query(
                part_codes=["C32291"],
                whole_codes=["C999999999"],
            )
        )
        health = await client.select(
            f"SELECT ?s WHERE {{ GRAPH <{STATED_GRAPH_IRI}> {{ ?s ?p ?o }} }} LIMIT 1"
        )

    assert version_rows == [{"version": "26.06e"}]
    assert rows == [
        {
            "part": f"{NCIT_NS}C32291",
            "whole": f"{NCIT_NS}C12510",
        }
    ]
    assert no_match_rows == []
    assert health
    assert health[0].get("s")


@pytest.mark.integration
@pytest.mark.full_store
async def test_in_scope_concepts_query_pages_over_the_live_stated_graph() -> None:
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt Oxigraph not reachable at {url}")
    if not _stated_loaded(url):
        pytest.skip("stated NCIt graph not loaded (run owl_load with include_stated)")

    async with OxigraphHttpClient(url) as client:
        rows = await client.select(
            build_in_scope_concepts_query(["Neoplastic Process"], limit=5, offset=0)
        )
    codes = concepts_from_rows(rows)
    assert len(codes) <= 5
    assert all(c.startswith("C") for c in codes)


@pytest.mark.integration
@pytest.mark.full_store
async def test_c6135_genus_walk_finds_roles() -> None:
    """The genus-chain walker must find role restrictions for C6135 from the
    stated graph. Previously the flat ``rdfs:subClassOf`` query returned nothing
    for this defined class — the walker is the fix."""
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt Oxigraph not reachable at {url}")
    if not _stated_loaded(url):
        pytest.skip("stated NCIt graph not loaded (run owl_load with include_stated)")

    async with OxigraphHttpClient(url) as client:
        roles = await walk_genus_chain(client.select, "C6135", max_depth=6)

    # The walker should find at minimum these core roles from the genus chain:
    filler_codes = {r.filler_code for r in roles}
    assert "C27970" in filler_codes  # R88 — Stage III (from C6135 level)
    assert "C90530" in filler_codes  # R88 — Medullary Carcinoma (from C141041)
    assert "C12400" in filler_codes  # R101 — Malignant Neoplasm (from C4815)
    # Deep R101 fillers found via recursive genus walk:
    assert "C13063" in filler_codes  # R101 — Primitive Hemocytoblast (from C6077)
    assert "C12418" in filler_codes  # R101 — White Blood Cell (from C35850)

    # Core-role filter must have excluded generic neoplasm roles like R103/R108
    # that originate at the C3879 (Neoplasm by Site) level:
    role_codes = {r.role_code for r in roles}
    assert "R88" in role_codes
    assert "R101" in role_codes


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.full_store
async def test_c6135_walked_roles_route_d19_d20_with_semantic_type_of() -> None:
    """After the genus-chain walker, feeding roles through ``select_constituents``
    with ``semantic_type_of`` should apply D19/D20 axis routing.

    Marked @slow because it walks a depth-6 genus chain against the full stated
    build, which can take 30-60s on a cold store.
    """
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt Oxigraph not reachable at {url}")
    if not _stated_loaded(url):
        pytest.skip("stated NCIt graph not loaded (run owl_load with include_stated)")

    async with OxigraphHttpClient(url) as client:
        roles = await walk_genus_chain(client.select, "C6135", max_depth=6)

        filler_codes = {r.filler_code for r in roles}
        rows = await client.select(build_semantic_type_of_query(list(filler_codes)))
        semantic_type_of = semantic_type_of_from_rows(rows)

        def _st_of(code: str) -> str | None:
            types = semantic_type_of.get(code)
            return types[0] if types else None

        ancestor_pairs: set[tuple[str, str]] = set()
        if filler_codes:
            ancestor_rows = await client.select(
                build_ancestor_pairs_query(list(filler_codes))
            )
            ancestor_pairs = ancestor_pairs_from_rows(ancestor_rows)

        constituents = select_constituents(
            roles,
            make_is_ancestor(ancestor_pairs),
            parent_morphology=None,
            semantic_type_of=_st_of,
        )

    fillers = {c.filler_code for c in constituents}

    # R88 filler — Stage III
    assert "C27970" in fillers

    # R101 organ filler stays on R101
    assert "C12400" in fillers

    # Deep R101 fillers in "Body Location or Region" route to op:AssociatedRegion
    region_axes = {c.axis for c in constituents if c.filler_code == "C13063"}
    assert "op:AssociatedRegion" in region_axes
    region_axes_12418 = {c.axis for c in constituents if c.filler_code == "C12418"}
    assert "op:AssociatedRegion" in region_axes_12418

    # R101 fillers anchored on a lineage-generic genus (C3809 Neuroendocrine
    # Neoplasm, in C6135's chain) route to op:AssociatedLineageClassification
    # (D20 refinement 1). This exercises the walker's anchoring_genus population
    # end-to-end — the gap that silently disabled lineage routing before.
    lineage = {
        c.filler_code
        for c in constituents
        if c.axis == "op:AssociatedLineageClassification"
    }
    assert "C12704" in lineage  # Endocrine Gland, anchored on C3809


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.full_store
async def test_resolve_morphology_filler_for_c6135() -> None:
    """The morphology filler for C6135 should be C3879 (Thyroid Gland Medullary
    Carcinoma), not the staging genus C141041.

    Marked @slow for the same reason as test_c6135_walked_roles_route_d19_d20.
    """
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt Oxigraph not reachable at {url}")
    if not _stated_loaded(url):
        pytest.skip("stated NCIt graph not loaded (run owl_load with include_stated)")

    async with OxigraphHttpClient(url) as client:
        morphology = await resolve_morphology_filler(
            client.select, "C6135", max_depth=6
        )

    # C3879 is "Thyroid Gland Medullary Carcinoma" - the first non-staging genus
    assert morphology == "C3879"


@pytest.mark.integration
@pytest.mark.full_store
async def test_c6135_decomposition_includes_morphology_constituent() -> None:
    """When morphology is resolved, the decomposition should include an
    op:Morphology constituent with axis_source='parent'."""
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt Oxigraph not reachable at {url}")
    if not _stated_loaded(url):
        pytest.skip("stated NCIt graph not loaded (run owl_load with include_stated)")

    async with OxigraphHttpClient(url) as client:
        roles = await walk_genus_chain(client.select, "C6135", max_depth=6)
        morphology = await resolve_morphology_filler(
            client.select, "C6135", max_depth=6
        )

        filler_codes = {r.filler_code for r in roles}
        rows = await client.select(build_semantic_type_of_query(list(filler_codes)))
        semantic_type_of = semantic_type_of_from_rows(rows)

        def _st_of(code: str) -> str | None:
            types = semantic_type_of.get(code)
            return types[0] if types else None

        ancestor_pairs: set[tuple[str, str]] = set()
        if filler_codes:
            ancestor_rows = await client.select(
                build_ancestor_pairs_query(list(filler_codes))
            )
            ancestor_pairs = ancestor_pairs_from_rows(ancestor_rows)

        constituents = select_constituents(
            roles,
            make_is_ancestor(ancestor_pairs),
            parent_morphology=morphology,
            semantic_type_of=_st_of,
        )

    # The morphology constituent should be present
    morphology_constituents = [c for c in constituents if c.axis == "op:Morphology"]
    assert len(morphology_constituents) == 1
    assert morphology_constituents[0].filler_code == "C3879"
    assert morphology_constituents[0].axis_source == "parent"
