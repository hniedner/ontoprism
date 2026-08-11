"""Read-only configured NCIt embedding-source shape contracts."""

from pathlib import Path

import pytest
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.repositories.embeddings.generate import ncit_source_fingerprint
from ontolib.terminologies.ncit.client import ncit_sparql_client
from ontolib.terminologies.ncit.graph_store import NcitGraphStore
from ontolib.terminologies.ncit.sibling_store import (
    CANDIDATE_MANIFEST_FILENAME,
    validate_ncit_sibling_manifest,
)

pytestmark = [pytest.mark.integration, pytest.mark.full_store, pytest.mark.full_build]
_NCIT_2607D_EMBEDDING_FINGERPRINT = (
    "bfe0ee7596a7025997a148508a3d9d6ef00db9b9be1564aed74b6b4687fc95fb"
)


async def test_ncit_embedding_source_count_fingerprint_and_page_order_are_stable() -> (
    None
):
    settings = get_settings()
    async with ncit_sparql_client(settings.ncit_sparql_url) as client:
        store = NcitGraphStore(client)
        count, fingerprint = await ncit_source_fingerprint(store, batch_size=1000)
        first = await store.embedding_records(limit=2)
        repeated = await store.embedding_records(limit=2)
        second = await store.embedding_records(limit=2, after=first[-1]["iri"])
        sentinel = await store.get_concept_detail("C3262")

    assert count == settings.ncit_embedding_expected_rows == 206_860
    assert fingerprint == _NCIT_2607D_EMBEDDING_FINGERPRINT
    assert first == repeated
    assert [record["code"] for record in first] == sorted(
        record["code"] for record in first
    )
    assert first[-1]["iri"] < second[0]["iri"]
    assert sentinel is not None
    assert sentinel.code == "C3262"


async def test_ncit_publications_bind_the_active_proxy_and_content() -> None:
    settings = get_settings()
    active = Path(settings.ncit_store_dir)
    manifest = validate_ncit_sibling_manifest(active / CANDIDATE_MANIFEST_FILENAME)
    async with ncit_sparql_client(settings.ncit_sparql_url) as client:
        count, fingerprint = await ncit_source_fingerprint(
            NcitGraphStore(client), batch_size=1000
        )

    engine = make_engine(settings.database_url)
    try:
        async with make_sessionmaker(engine)() as session:
            search = (
                await session.execute(
                    text(
                        "SELECT source_identity, source_hash, row_count "
                        "FROM ncit_search_manifest WHERE singleton = true"
                    )
                )
            ).one()
            embedding = (
                await session.execute(
                    text(
                        "SELECT source_identity, source_hash, actual_row_count "
                        "FROM embedding_corpus_manifest "
                        "WHERE corpus = 'ncit' AND is_active"
                    )
                )
            ).one()
    finally:
        await dispose_engine(engine)

    assert (
        search.source_identity == embedding.source_identity == manifest.source_identity
    )
    assert search.source_hash == embedding.source_hash == fingerprint
    assert search.row_count == embedding.actual_row_count == count
