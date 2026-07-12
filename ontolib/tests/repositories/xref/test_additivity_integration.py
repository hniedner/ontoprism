"""Integration test: loading xref Turtle into a named graph is additive (issue #71).

Proves that loading SSSOM triples into a test-only named graph never changes the
default graph's triple count. Uses a dedicated test-only graph IRI and cleans up.
"""

from __future__ import annotations

import os

import httpx
import pytest

from ontolib.repositories.xref.models import SSSOMRecord
from ontolib.repositories.xref.ttl_writer import render_ttl
from ontolib.repositories.xref.vocab import CLOSE_MATCH
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

_DEFAULT_NCIT_URL = "http://localhost:7888"
_TEST_GRAPH_IRI = "http://ontoprism.invalid/test-xref-additivity"


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
async def test_loading_xref_graph_leaves_default_graph_unchanged() -> None:
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt Oxigraph not reachable at {url}")

    records = [
        SSSOMRecord(
            subject_id="C3262",
            predicate_id=CLOSE_MATCH,
            object_id="UBERON:0002107",
            mapping_justification="semapv:LexicalMatching",
            confidence=0.8,
            subject_source_version="26.02d",
            object_source_version="uberon-2026-01",
        ),
    ]
    ttl = render_ttl(records)

    async with OxigraphHttpClient(url) as client:
        count_before = await client.count()
        try:
            await client.load(
                ttl.encode("utf-8"),
                content_type="text/turtle",
                graph_iri=_TEST_GRAPH_IRI,
                replace=True,
            )
            count_after = await client.count()
            assert count_after == count_before

            loaded = await client.ask(
                f"ASK {{ GRAPH <{_TEST_GRAPH_IRI}> {{ ?s ?p ?o }} }}"
            )
            assert loaded
        finally:
            async with httpx.AsyncClient() as http:
                await http.delete(
                    f"{url.rstrip('/')}/store",
                    params={"graph": _TEST_GRAPH_IRI},
                )
