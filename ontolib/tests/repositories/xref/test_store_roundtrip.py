"""Integration test: SSSOM record round-trips through XrefStore + Postgres."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.repositories.xref.models import SSSOMRecord
from ontolib.repositories.xref.store import XrefStore
from ontolib.repositories.xref.vocab import CLOSE_MATCH


@pytest.mark.integration
async def test_store_roundtrip() -> None:
    engine = make_engine(get_settings().database_url)
    try:
        sf = make_sessionmaker(engine)
        store = XrefStore(sf)

        count = await store.upsert_run(
            run_id="test-roundtrip",
            source="uberon",
            ncit_version="26.02d",
            source_version="uberon-2026-01",
        )
        assert count > 0

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
            SSSOMRecord(
                subject_id="C12345",
                predicate_id=CLOSE_MATCH,
                object_id="CL:0000057",
                mapping_justification="semapv:LexicalMatching",
                confidence=0.7,
                subject_source_version="26.02d",
                object_source_version="cl-2026-01",
            ),
        ]
        rows_written = await store.upsert_records("test-roundtrip", records)
        assert rows_written == 2

        read_back = await store.records_for_run("test-roundtrip")
        assert len(read_back) == 2
        assert {r["subject_id"] for r in read_back} == {"C3262", "C12345"}
        assert all(r["predicate_id"] == CLOSE_MATCH for r in read_back)
        assert all(r["confidence"] in (0.7, 0.8) for r in read_back)

        # Clean up test data
        async with sf() as s:
            await s.execute(
                text("DELETE FROM concept_xref WHERE run_id = 'test-roundtrip'")
            )
            await s.execute(text("DELETE FROM xref_run WHERE id = 'test-roundtrip'"))
            await s.commit()
    finally:
        await dispose_engine(engine)


@pytest.mark.integration
async def test_upsert_records_empty_is_noop() -> None:
    engine = make_engine(get_settings().database_url)
    try:
        sf = make_sessionmaker(engine)
        store = XrefStore(sf)
        count = await store.upsert_records("nonexistent-run", [])
        assert count == 0
    finally:
        await dispose_engine(engine)
