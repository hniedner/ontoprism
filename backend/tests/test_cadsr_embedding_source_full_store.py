"""Read-only configured caDSR embedding-source fingerprint contract."""

import pytest
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.repositories.cadsr.repository import CdeRepository

pytestmark = [pytest.mark.integration, pytest.mark.full_store, pytest.mark.full_build]

_CADSR_SOURCE_FINGERPRINT = (
    "e3e358955b9ce6b5d889d2388d6003d2dbda2a7bc3d9cdf7ee1bb0cf498a1aa3"
)


async def test_cadsr_embedding_source_count_sentinel_and_fingerprint() -> None:
    settings = get_settings()
    repository = CdeRepository(settings.cadsr_db_path)
    source, count, fingerprint = repository.certification()
    sentinel = repository.get_cde("2517527", "4")

    engine = make_engine(settings.database_url)
    try:
        async with make_sessionmaker(engine)() as session:
            embedding = (
                await session.execute(
                    text(
                        "SELECT source_identity, source_hash, actual_row_count "
                        "FROM embedding_corpus_manifest "
                        "WHERE corpus = 'cadsr' AND is_active"
                    )
                )
            ).one()
    finally:
        await dispose_engine(engine)

    assert count == settings.cadsr_embedding_expected_rows == 79_835
    assert fingerprint == _CADSR_SOURCE_FINGERPRINT
    assert sentinel is not None
    assert embedding.source_identity == source.archive_sha256
    assert embedding.source_hash == fingerprint
    assert embedding.actual_row_count == count
