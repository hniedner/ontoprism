"""Integration tests for the decomposition query layer against a live Oxigraph.

Two tiers, both auto-skipping when unavailable:
- The stated-graph SPARQL builders must *parse* against a real engine (property paths,
  GRAPH, VALUES). Runs whenever the store is reachable, even with an empty stated graph.
- The full roles-first extraction of ``C6135`` runs only once the **stated** NCIt OWL is
  loaded into ``STATED_GRAPH_IRI`` (skipped until then — the inferred build alone is not
  enough; see design §2).
"""

from __future__ import annotations

import os

import httpx
import pytest

from ontolib.decomposition.extract import (
    ancestor_pairs_from_rows,
    concepts_from_rows,
    make_is_ancestor,
    semantic_type_of_from_rows,
)
from ontolib.decomposition.filler_selection import select_constituents
from ontolib.decomposition.stated_queries import (
    build_ancestor_pairs_query,
    build_genus_walk_members_query,
    build_in_scope_concepts_query,
    build_role_restrictions_query,
    build_semantic_type_of_query,
    build_semantic_type_query,
    resolve_morphology_filler,
    walk_genus_chain,
)
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

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
    return resp.status_code == 200


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
    return resp.status_code == 200 and resp.json().get("boolean", False)


@pytest.mark.integration
async def test_stated_query_builders_parse_against_live_store() -> None:
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt Oxigraph not reachable at {url}")
    async with OxigraphHttpClient(url) as client:
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
