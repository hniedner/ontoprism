"""Integration test for the decomposition read path against a live Oxigraph.

Seeds a tiny ``op:`` graph into DECOMPOSED_GRAPH_IRI and reads it back through the real
query + assembly — validating the read layer end-to-end without the (not-yet-built)
writer. Skips when the store is unreachable.
"""

from __future__ import annotations

import os

import httpx
import pytest

from ontolib.decomposition import vocab
from ontolib.decomposition.read import decomposition_from_rows
from ontolib.decomposition.read_queries import build_decomposition_query
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

_DEFAULT_NCIT_URL = "http://localhost:7888"
_NCIT = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#"

# A hand-written decomposed-graph fixture (what the engine will emit for C6135).
_SEED_TTL = f"""
@prefix op: <{vocab.ONTOPRISM_NS}> .
@prefix ncit: <{_NCIT}> .
ncit:C6135 op:representationStatus "{vocab.LEGACY_PRECOORDINATED}" ;
    op:decomposedOn "2026-07-06" ;
    op:hasConstituent
        [ op:axis ncit:R88 ; op:filler ncit:C27970 ;
          op:axisSource "role" ; op:mostSpecific false ] ,
        [ op:axis ncit:R101 ; op:filler ncit:C12400 ;
          op:axisSource "role" ; op:mostSpecific true ] .
""".encode()


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


@pytest.mark.integration
async def test_decomposition_round_trips_through_the_decomposed_graph() -> None:
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt Oxigraph not reachable at {url}")

    async with OxigraphHttpClient(url) as client:
        # Seed the decomposed graph (replace=True isolates the test to its own graph).
        await client.load(
            _SEED_TTL,
            content_type="text/turtle",
            graph_iri=vocab.DECOMPOSED_GRAPH_IRI,
            replace=True,
        )
        rows = await client.select(build_decomposition_query("C6135"))

    decomposition = decomposition_from_rows("C6135", rows)
    assert decomposition.is_legacy_precoordinated is True
    assert decomposition.decomposed_on == "2026-07-06"
    by_axis = {c.axis: c for c in decomposition.constituents}
    assert set(by_axis) == {"R88", "R101"}
    assert by_axis["R101"].filler == "C12400"
    assert by_axis["R101"].most_specific is True
    assert by_axis["R88"].most_specific is False
