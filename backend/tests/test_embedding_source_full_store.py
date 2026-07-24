"""Read-only configured NCIt embedding-source shape contracts."""

import pytest

from backend.config import get_settings
from ontolib.repositories.embeddings.generate import ncit_source_fingerprint
from ontolib.terminologies.ncit.graph_store import NcitGraphStore
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

pytestmark = [pytest.mark.integration, pytest.mark.full_store, pytest.mark.full_build]
_NCIT_2602D_EMBEDDING_FINGERPRINT = (
    "56d86424bcb413ba97bb9b79ad29b90037c5e2f1457f577afc62fce8b287e41a"
)


async def test_ncit_embedding_source_count_fingerprint_and_page_order_are_stable() -> (
    None
):
    settings = get_settings()
    async with OxigraphHttpClient(settings.ncit_sparql_url) as client:
        store = NcitGraphStore(client)
        count, fingerprint = await ncit_source_fingerprint(store, batch_size=1000)
        first = await store.embedding_records(limit=2)
        repeated = await store.embedding_records(limit=2)
        second = await store.embedding_records(limit=2, after=first[-1]["iri"])
        sentinel = await store.get_concept("C3262")

    assert count == settings.ncit_embedding_expected_rows == 204_373
    assert fingerprint == _NCIT_2602D_EMBEDDING_FINGERPRINT
    assert first == repeated
    assert [record["code"] for record in first] == sorted(
        record["code"] for record in first
    )
    assert first[-1]["iri"] < second[0]["iri"]
    assert sentinel is not None
    assert sentinel.code == "C3262"
