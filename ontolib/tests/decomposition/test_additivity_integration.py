"""Integration test: loading the writer's output is additive (design §11).

Proves ``test_additive_no_deletions`` against a REAL store: writing decomposition
triples into a named graph via the same mechanism the engine uses
(``client.load(..., graph_iri=..., replace=True)``) never changes the default graph's
triple count. Uses a dedicated *test-only* graph IRI (never the real
``DECOMPOSED_GRAPH_IRI``) and cleans it up unconditionally, so it never leaves data
behind in a developer's live store.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import httpx
import pytest

from ontolib.decomposition.legacy_writer import write_ttl
from ontolib.decomposition.models import Constituent, Decomposition
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

if TYPE_CHECKING:
    from pathlib import Path

_DEFAULT_NCIT_URL = "http://localhost:7888"
_TEST_GRAPH_IRI = "http://ontoprism.invalid/test-additivity-guarantee"


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
async def test_loading_writer_output_leaves_default_graph_unchanged(
    tmp_path: Path,
) -> None:
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt Oxigraph not reachable at {url}")

    decs = [
        Decomposition(
            code="C6135",
            semantic_type="Neoplastic Process",
            constituents=[
                Constituent(axis="R88", filler_code="C27970", axis_source="role"),
            ],
        )
    ]
    out = tmp_path / "additivity.ttl"
    await write_ttl(decs, dest=out, run_id="additivity-test")

    async with OxigraphHttpClient(url) as client:
        count_before = await client.count()
        try:
            await client.load(
                out.read_bytes(),
                content_type="text/turtle",
                graph_iri=_TEST_GRAPH_IRI,
                replace=True,
            )
            count_after = await client.count()
            assert count_after == count_before  # default graph untouched

            loaded = await client.ask(
                f"ASK {{ GRAPH <{_TEST_GRAPH_IRI}> {{ ?s ?p ?o }} }}"
            )
            assert loaded  # the writer's triples really did land, just not here
        finally:
            async with httpx.AsyncClient() as http:
                await http.delete(
                    f"{url.rstrip('/')}/store", params={"graph": _TEST_GRAPH_IRI}
                )
