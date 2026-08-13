"""Integration test: SSSOM record round-trips through XrefStore + Postgres."""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.repositories.xref.models import (
    SSSOMRecord,
    UberonCandidateGenerationMetadata,
    UberonReadIdentity,
    XrefReadPolicy,
)
from ontolib.repositories.xref.store import XrefStore
from ontolib.repositories.xref.vocab import CLOSE_MATCH, EXACT_MATCH

from .conftest import activate_records

pytestmark = [
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings"),
]
_SOURCE_METADATA = UberonCandidateGenerationMetadata(
    ncit_source_identity="a" * 64,
    uberon_source_identity="b" * 64,
    uberon_serving_identity="c" * 64,
)
_READ_POLICY = XrefReadPolicy(
    uberon=UberonReadIdentity(
        ncit_source_identity="a" * 64,
        uberon_source_identity="b" * 64,
        uberon_serving_identity="c" * 64,
    )
)


@pytest.fixture(autouse=True)
async def _isolate_xref_tables(isolated_postgres_settings: None) -> None:
    del isolated_postgres_settings
    engine = make_engine(get_settings().database_url)
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE xref_generation, xref_run CASCADE"))
    await dispose_engine(engine)


async def _retain_only_active_source(sf: object, source: str) -> None:
    async with sf() as session:  # type: ignore[operator]
        await session.execute(
            text("DELETE FROM xref_active_generation WHERE source <> :source"),
            {"source": source},
        )
        await session.commit()


@pytest.mark.integration
async def test_store_roundtrip() -> None:
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    run_id = f"test-roundtrip-{uuid.uuid4().hex}"
    try:
        store = XrefStore(sf)

        count = await store.upsert_run(
            run_id=run_id,
            source="uberon-cl",
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
        assert await activate_records(
            store, source="uberon-cl", run_id=run_id, records=records
        )

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
async def test_upsert_run_only_reuses_the_exact_run_provenance() -> None:
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    run_id = f"test-run-retry-{uuid.uuid4().hex}"
    try:
        store = XrefStore(sf)
        await store.upsert_run(run_id, "uberon-cl", "26.02d", "uberon-2026-01")
        async with sf() as session:
            started_at = await session.scalar(
                text("SELECT started_at FROM xref_run WHERE id = :id"), {"id": run_id}
            )
        assert isinstance(started_at, datetime.datetime)

        await store.upsert_run(
            run_id,
            "uberon-cl",
            "26.02d",
            "uberon-2026-01",
            status="completed",
        )
        async with sf() as session:
            retried = (
                (
                    await session.execute(
                        text(
                            "SELECT source, ncit_version, source_version, started_at, "
                            "status "
                            "FROM xref_run WHERE id = :id"
                        ),
                        {"id": run_id},
                    )
                )
                .mappings()
                .one()
            )
        assert retried == {
            "source": "uberon-cl",
            "ncit_version": "26.02d",
            "source_version": "uberon-2026-01",
            "started_at": started_at,
            "status": "completed",
        }

        for source, ncit_version, source_version in (
            ("uberon-cl-promotion", "26.02d", "uberon-2026-01"),
            ("uberon-cl", "26.03a", "uberon-2026-01"),
            ("uberon-cl", "26.02d", "uberon-2026-02"),
        ):
            with pytest.raises(ValueError, match="different provenance"):
                await store.upsert_run(
                    run_id, source, ncit_version, source_version, status="failed"
                )
    finally:
        async with sf() as session:
            await session.execute(
                text("DELETE FROM xref_run WHERE id = :id"), {"id": run_id}
            )
            await session.commit()
        await dispose_engine(engine)


@pytest.mark.integration
async def test_mapping_strength_by_subject() -> None:
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    run_id = f"test-strength-{uuid.uuid4().hex}"
    try:
        store = XrefStore(sf)
        await store.upsert_run(run_id, "uberon-cl", "26.02d", "test-1")
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
        await activate_records(
            store, source="uberon-cl", run_id=run_id, records=records
        )
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
        await store.upsert_run(run_id, "uberon-cl", "26.02d", "test-1")
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
        await activate_records(
            store, source="uberon-cl", run_id=run_id, records=records
        )
        await _retain_only_active_source(sf, "uberon-cl")

        result = await store.mappings_by_subjects({"C3262"}, expected=_READ_POLICY)
        assert "C3262" in result
        assert len(result["C3262"]) == 1
        mapping = result["C3262"][0]
        assert mapping.object.identifier == "UBERON:0002107"
        assert mapping.predicate == EXACT_MATCH
        assert mapping.lifecycle == "validated"
        assert mapping.confidence == 1.0
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
        result = await store.mappings_by_subjects(set(), expected=_READ_POLICY)
        assert result == {}
    finally:
        await dispose_engine(engine)


@pytest.mark.integration
async def test_mappings_by_objects_reverse_lookup() -> None:
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    run_id = f"test-mbo-{uuid.uuid4().hex}"
    try:
        store = XrefStore(sf)
        await store.upsert_run(run_id, "uberon-cl", "26.02d", "test-1")
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
        await activate_records(
            store, source="uberon-cl", run_id=run_id, records=records
        )
        await _retain_only_active_source(sf, "uberon-cl")

        result = await store.mappings_by_objects(
            {"UBERON:0002107"}, expected=_READ_POLICY
        )
        assert "UBERON:0002107" in result
        assert len(result["UBERON:0002107"]) == 1
        mapping = result["UBERON:0002107"][0]
        assert mapping.subject.identifier == "C3262"
        assert mapping.predicate == EXACT_MATCH
        assert mapping.lifecycle == "validated"
        assert mapping.confidence == 1.0
        assert "UBERON:0002046" not in result
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
async def test_mappings_by_objects_empty_returns_empty() -> None:
    engine = make_engine(get_settings().database_url)
    try:
        sf = make_sessionmaker(engine)
        store = XrefStore(sf)
        result = await store.mappings_by_objects(set(), expected=_READ_POLICY)
        assert result == {}
    finally:
        await dispose_engine(engine)
