"""Read-only configured NCIt embedding-source shape contracts."""

import pytest

from backend.config import get_settings
from ontolib.repositories.embeddings.generate import ncit_source_fingerprint
from ontolib.terminologies.ncit.graph_store import NcitGraphStore
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

pytestmark = [pytest.mark.integration, pytest.mark.full_store, pytest.mark.full_build]


async def test_ncit_embedding_source_count_fingerprint_and_page_order_are_stable() -> (
    None
):
    settings = get_settings()
    async with OxigraphHttpClient(settings.ncit_sparql_url) as client:
        store = NcitGraphStore(client)
        count, fingerprint = await ncit_source_fingerprint(store)
        repeated_count, repeated_fingerprint = await ncit_source_fingerprint(store)
        first = await store.embedding_records(limit=2, offset=0)
        repeated = await store.embedding_records(limit=2, offset=0)
        final = await store.embedding_records(limit=2, offset=count - 2)
        sentinel = await store.get_concept("C3262")

    assert count == settings.ncit_embedding_expected_rows == 204_373
    assert fingerprint
    assert (repeated_count, repeated_fingerprint) == (count, fingerprint)
    assert first == repeated
    assert [record["code"] for record in first] == sorted(
        record["code"] for record in first
    )
    assert [record["code"] for record in final] == sorted(
        record["code"] for record in final
    )
    assert first[0]["code"] != final[-1]["code"]
    assert sentinel is not None
    assert sentinel.code == "C3262"
