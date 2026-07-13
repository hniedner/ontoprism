"""Integration test: SSSOM record round-trips through XrefStore + Postgres."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.repositories.xref.models import SSSOMRecord
from ontolib.repositories.xref.store import XrefStore
from ontolib.repositories.xref.vocab import CLOSE_MATCH, EXACT_MATCH


@pytest.mark.integration
async def test_store_roundtrip() -> None:
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    run_id = f"test-roundtrip-{uuid.uuid4().hex}"
    try:
        store = XrefStore(sf)

        count = await store.upsert_run(
            run_id=run_id,
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
                mapping_justification="semapv:ManualMappingCuration",
                confidence=1.0,
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
        rows_written = await store.upsert_records(run_id, records)
        assert rows_written == 2

        read_back = await store.records_for_run(run_id)
        assert len(read_back) == 2
        assert {r["subject_id"] for r in read_back} == {"C3262", "C12345"}
        assert all(r["predicate_id"] == CLOSE_MATCH for r in read_back)
        assert all(r["confidence"] in (0.7, 1.0) for r in read_back)
    finally:
        async with sf() as s:
            await s.execute(
                text("DELETE FROM concept_xref WHERE run_id = :rid"),
                {"rid": run_id},
            )
            await s.execute(
                text("DELETE FROM xref_run WHERE id = :rid"),
                {"rid": run_id},
            )
            await s.commit()
        await dispose_engine(engine)


@pytest.mark.integration
async def test_mapping_strength_by_subject() -> None:
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    run_id = f"test-strength-{uuid.uuid4().hex}"
    try:
        store = XrefStore(sf)
        await store.upsert_run(run_id, "test", "26.02d", "test-1")
        records = [
            SSSOMRecord(
                subject_id="C3262",
                predicate_id=EXACT_MATCH,
                object_id="UBERON:0002107",
                mapping_justification="semapv:ManualMappingCuration",
                confidence=1.0,
                subject_source_version="26.02d",
                object_source_version="uberon-2026-01",
                lifecycle_state="validated",
            ),
            SSSOMRecord(
                subject_id="C3262",
                predicate_id=CLOSE_MATCH,
                object_id="CL:0000057",
                mapping_justification="semapv:LexicalMatching",
                confidence=0.7,
                subject_source_version="26.02d",
                object_source_version="cl-2026-01",
            ),
            SSSOMRecord(
                subject_id="C12345",
                predicate_id=CLOSE_MATCH,
                object_id="UBERON:0002048",
                mapping_justification="semapv:LexicalMatching",
                confidence=0.5,
                subject_source_version="26.02d",
                object_source_version="uberon-2026-01",
            ),
        ]
        await store.upsert_records(run_id, records)
        strength = await store.mapping_strength_by_subject()
        assert "C3262" in strength
        assert (EXACT_MATCH, "validated") in strength["C3262"]
        assert (CLOSE_MATCH, "proposed") in strength["C3262"]
        assert "C12345" in strength
        assert (CLOSE_MATCH, "proposed") in strength["C12345"]
    finally:
        async with sf() as s:
            await s.execute(
                text("DELETE FROM concept_xref WHERE run_id = :rid"), {"rid": run_id}
            )
            await s.execute(
                text("DELETE FROM xref_run WHERE id = :rid"), {"rid": run_id}
            )
            await s.commit()
        await dispose_engine(engine)


@pytest.mark.integration
async def test_mappings_by_subjects_filters_by_codes() -> None:
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    run_id = f"test-mbs-{uuid.uuid4().hex}"
    try:
        store = XrefStore(sf)
        await store.upsert_run(run_id, "test", "26.02d", "test-1")
        records = [
            SSSOMRecord(
                subject_id="C3262",
                predicate_id=EXACT_MATCH,
                object_id="UBERON:0002107",
                mapping_justification="semapv:ManualMappingCuration",
                confidence=1.0,
                subject_source_version="26.02d",
                object_source_version="uberon-2026-01",
                lifecycle_state="validated",
            ),
            SSSOMRecord(
                subject_id="C12400",
                predicate_id=CLOSE_MATCH,
                object_id="UBERON:0002046",
                mapping_justification="semapv:LexicalMatching",
                confidence=0.7,
                subject_source_version="26.02d",
                object_source_version="uberon-2026-01",
            ),
        ]
        await store.upsert_records(run_id, records)

        result = await store.mappings_by_subjects({"C3262"})
        assert "C3262" in result
        assert len(result["C3262"]) == 1
        obj, pred, lifecycle = result["C3262"][0]
        assert obj == "UBERON:0002107"
        assert pred == EXACT_MATCH
        assert lifecycle == "validated"
        assert "C12400" not in result
    finally:
        async with sf() as s:
            await s.execute(
                text("DELETE FROM concept_xref WHERE run_id = :rid"), {"rid": run_id}
            )
            await s.execute(
                text("DELETE FROM xref_run WHERE id = :rid"), {"rid": run_id}
            )
            await s.commit()
        await dispose_engine(engine)


@pytest.mark.integration
async def test_mappings_by_subjects_empty_returns_empty() -> None:
    engine = make_engine(get_settings().database_url)
    try:
        sf = make_sessionmaker(engine)
        store = XrefStore(sf)
        result = await store.mappings_by_subjects(set())
        assert result == {}
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
