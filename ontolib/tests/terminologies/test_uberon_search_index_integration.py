"""Real PostgreSQL publication contracts for the source-bound Uberon/CL cache."""

from __future__ import annotations

import pytest

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.terminologies.uberon.search_index import (
    UberonSearchIndex,
    UberonSearchPublicationError,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings"),
]

_SOURCE_IDENTITY = "a" * 64
_SOURCE_HASH = "b" * 64


async def _records():
    yield [
        {
            "code": "UBERON:0002048",
            "source": "uberon",
            "label": "lung",
            "synonyms": "pulmo",
        },
        {
            "code": "CL:0000000",
            "source": "cl",
            "label": "cell",
            "synonyms": "cellular organism",
        },
    ]


async def _duplicate_records():
    yield [
        {
            "code": "UBERON:0002048",
            "source": "uberon",
            "label": "first lung",
            "synonyms": "",
        },
        {
            "code": "UBERON:0002048",
            "source": "uberon",
            "label": "second lung",
            "synonyms": "",
        },
    ]


async def test_publication_is_identity_exact_and_failed_replacement_rolls_back() -> (
    None
):
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    index = UberonSearchIndex(sf)
    try:
        assert (
            await index.rebuild(
                _records(),
                source_identity=_SOURCE_IDENTITY,
                source_hash=_SOURCE_HASH,
                expected_row_count=2,
            )
            == 2
        )

        assert await index.is_populated(_SOURCE_IDENTITY, _SOURCE_HASH)
        assert not await index.is_populated("c" * 64, _SOURCE_HASH)
        assert not await index.is_populated(_SOURCE_IDENTITY, "d" * 64)
        page = await index.search("lung", source="uberon")
        assert [(hit.code, hit.label) for hit in page.hits] == [
            ("UBERON:0002048", "lung")
        ]

        with pytest.raises(UberonSearchPublicationError, match="duplicate code"):
            await index.rebuild(
                _duplicate_records(),
                source_identity="e" * 64,
                source_hash="f" * 64,
                expected_row_count=2,
            )

        assert await index.is_populated(_SOURCE_IDENTITY, _SOURCE_HASH)
        assert not await index.is_populated("e" * 64, "f" * 64)
        preserved = await index.search("lung", source="uberon")
        assert [(hit.code, hit.label) for hit in preserved.hits] == [
            ("UBERON:0002048", "lung")
        ]
    finally:
        await dispose_engine(engine)
