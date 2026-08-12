"""Integration test: loading the writer's output is additive (design §11).

Proves ``test_additive_no_deletions`` against a REAL store: writing decomposition
triples into a named graph via the same mechanism the engine uses
(``client.load(..., graph_iri=..., replace=True)``) never changes the default graph's
triple count. Uses a dedicated *test-only* graph IRI (never the real
``DECOMPOSED_GRAPH_IRI``) and cleans it up unconditionally, so it never leaves data
behind in a disposable store.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from ontolib.decomposition.legacy_writer import write_ttl
from ontolib.decomposition.models import Constituent, Decomposition
from ontolib.terminologies.sparql_http_client import SparqlHttpClient

if TYPE_CHECKING:
    from pathlib import Path

_DEFAULT_NCIT_URL = "http://localhost:7888"
_TEST_GRAPH_IRI = "http://ontoprism.invalid/test-additivity-guarantee"

pytestmark = [
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_qlever_settings"),
]


def _url() -> str:
    return os.environ.get("NCIT_SPARQL_URL", _DEFAULT_NCIT_URL)


@pytest.mark.integration
async def test_loading_writer_output_leaves_default_graph_unchanged(
    tmp_path: Path,
) -> None:
    url = _url()
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

    async with SparqlHttpClient.for_qlever(url, named_graphs=()) as client:
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
            await client.update(f"DROP SILENT GRAPH <{_TEST_GRAPH_IRI}>")
