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
    make_is_ancestor,
    roles_from_rows,
)
from ontolib.decomposition.filler_selection import select_constituents
from ontolib.decomposition.stated_queries import (
    build_ancestor_pairs_query,
    build_role_restrictions_query,
    build_semantic_type_query,
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
        # Each builder must produce SPARQL the engine accepts (0 rows is fine when the
        # stated graph is empty — we are checking well-formedness, not data).
        assert isinstance(
            await client.select(build_role_restrictions_query("C6135")), list
        )
        assert isinstance(await client.select(build_semantic_type_query("C6135")), list)
        assert isinstance(
            await client.select(build_ancestor_pairs_query(["C12400", "C12401"])), list
        )


@pytest.mark.integration
async def test_c6135_roles_first_extraction() -> None:
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt Oxigraph not reachable at {url}")
    if not _stated_loaded(url):
        pytest.skip("stated NCIt graph not loaded (run owl_load with include_stated)")

    async with OxigraphHttpClient(url) as client:
        roles = roles_from_rows(
            await client.select(build_role_restrictions_query("C6135"))
        )
        filler_codes = {r.filler_code for r in roles}
        pairs = ancestor_pairs_from_rows(
            await client.select(build_ancestor_pairs_query(filler_codes))
        )
        constituents = select_constituents(roles, make_is_ancestor(pairs))

    fillers = {c.filler_code for c in constituents}
    # C6135 — Stage III Thyroid Gland Medullary Carcinoma AJCC v7 (design §4.2).
    assert {"C27970", "C90530", "C12400", "C36761"} <= fillers
    # Every constituent is an existing NCIt concept code (roles-path coverage ~100%).
    assert all(c.filler_code.startswith("C") for c in constituents)
