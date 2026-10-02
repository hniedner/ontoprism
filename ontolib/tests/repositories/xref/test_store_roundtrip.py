"""Integration test: SSSOM record round-trips through XrefStore + Postgres."""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.repositories.xref.candidate_ingest import (
    CandidateInventory,
    generate_candidates,
)
from ontolib.repositories.xref.models import (
    CandidateContext,
    SSSOMRecord,
    StaleXrefGenerationError,
    UberonCandidateGenerationMetadata,
    UberonReadIdentity,
    XrefReadPolicy,
)
from ontolib.repositories.xref.source_versions import MappingSourceVersions
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


class _CandidateClient:
    def __init__(self, responses: dict[str, list[dict[str, str]]]) -> None:
        self._responses = responses

    async def select(self, query: str) -> list[dict[str, str]]:
        return next(
            (rows for marker, rows in self._responses.items() if marker in query), []
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


async def _clear_xref_tables(sf: object) -> None:
    async with sf() as session:  # type: ignore[operator]
        await session.execute(text("TRUNCATE xref_generation, xref_run CASCADE"))
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
        async with sf() as session:
            persisted = await session.execute(
                text(
                    "SELECT object_id, object_version FROM concept_xref "
                    "WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            )
        assert {row.object_id: row.object_version for row in persisted} == {
            "UBERON:0002107": "uberon-2026-01",
            "CL:0000057": "cl-2026-01",
        }
    finally:
        await _clear_xref_tables(sf)
        await dispose_engine(engine)


@pytest.mark.integration
async def test_generated_candidates_persist_each_source_ontology_version() -> None:
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    run_id = f"test-generated-versions-{uuid.uuid4().hex}"
    versions = MappingSourceVersions(
        ncit="26.07d",
        uberon="http://purl.obolibrary.org/obo/uberon/releases/2026-06-19/uberon.owl",
        cl="http://purl.obolibrary.org/obo/cl/releases/2026-06-08/cl.owl",
    )
    contexts = (
        CandidateContext("R101", "C3262", "op:PrimarySite"),
        CandidateContext("R104", "C12345", "op:CellOrigin"),
    )
    try:
        records, _ = await generate_candidates(
            _CandidateClient(
                {
                    "SELECT DISTINCT ?fillerCode": [
                        {"fillerCode": "C3262"},
                        {"fillerCode": "C12345"},
                    ],
                    "SELECT ?code ?label WHERE": [],
                }
            ),
            _CandidateClient(
                {
                    "hasDbXref": [
                        {
                            "upstream": (
                                "http://purl.obolibrary.org/obo/UBERON_0002107"
                            ),
                            "xref": "NCIT:C3262",
                        },
                        {
                            "upstream": "http://purl.obolibrary.org/obo/CL_0000057",
                            "xref": "NCIT:C12345",
                        },
                    ]
                }
            ),
            versions,
            inventory=CandidateInventory(contexts=contexts, excluded_counts=()),
        )
        store = XrefStore(sf)
        await store.upsert_run(
            run_id=run_id,
            source="uberon-cl",
            ncit_version=versions.ncit,
            source_version=versions.uberon,
        )
        await activate_records(
            store, source="uberon-cl", run_id=run_id, records=records
        )
        async with sf() as session:
            persisted = await session.execute(
                text(
                    "SELECT object_id, object_version FROM concept_xref "
                    "WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            )
        assert {row.object_id: row.object_version for row in persisted} == {
            "UBERON:0002107": versions.uberon,
            "CL:0000057": versions.cl,
        }
        loaded = await store.proposed_candidates(
            expected=UberonReadIdentity(
                ncit_source_identity="a" * 64,
                uberon_source_identity="b" * 64,
                uberon_serving_identity="c" * 64,
            )
        )
        assert {
            context for record in loaded for context in record.candidate_contexts
        } == set(contexts)
    finally:
        await _clear_xref_tables(sf)
        await dispose_engine(engine)


@pytest.mark.integration
async def test_run_lifecycle_is_terminal_and_exact_retry_is_idempotent() -> None:
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

        metrics = {"count": 1}
        await store.update_run_metrics(run_id, metrics, status="completed")
        async with sf() as session:
            retried = (
                (
                    await session.execute(
                        text(
                            "SELECT source, ncit_version, source_version, started_at, "
                            "status, finished_at, metrics "
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
            "finished_at": retried["finished_at"],
            "metrics": metrics,
        }
        finished_at = retried["finished_at"]

        await store.update_run_metrics(run_id, metrics, status="completed")
        async with sf() as session:
            assert (
                await session.scalar(
                    text("SELECT finished_at FROM xref_run WHERE id = :id"),
                    {"id": run_id},
                )
                == finished_at
            )

        with pytest.raises(ValueError, match="terminal"):
            await store.upsert_run(run_id, "uberon-cl", "26.02d", "uberon-2026-01")
        with pytest.raises(ValueError, match="terminal"):
            await store.update_run_metrics(run_id, {"count": 2}, status="completed")
        with pytest.raises(ValueError, match="terminal"):
            await store.update_run_metrics(run_id, metrics, status="failed")

        for source, ncit_version, source_version in (
            ("uberon-cl-promotion", "26.02d", "uberon-2026-01"),
            ("uberon-cl", "26.03a", "uberon-2026-01"),
            ("uberon-cl", "26.02d", "uberon-2026-02"),
        ):
            with pytest.raises(ValueError, match="different provenance"):
                await store.upsert_run(run_id, source, ncit_version, source_version)
    finally:
        async with sf() as session:
            await session.execute(
                text("DELETE FROM xref_run WHERE id = :id"), {"id": run_id}
            )
            await session.commit()
        await dispose_engine(engine)


@pytest.mark.integration
async def test_failed_run_cannot_be_reset_or_overwritten() -> None:
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    run_id = f"test-failed-run-{uuid.uuid4().hex}"
    try:
        store = XrefStore(sf)
        await store.upsert_run(run_id, "uberon-cl", "26.02d", "uberon-2026-01")
        await store.update_run_metrics(run_id, {"errors": 1}, status="failed")

        with pytest.raises(ValueError, match="terminal"):
            await store.upsert_run(run_id, "uberon-cl", "26.02d", "uberon-2026-01")
        with pytest.raises(ValueError, match="terminal"):
            await store.update_run_metrics(run_id, {"errors": 0}, status="completed")

        async with sf() as session:
            row = (
                (
                    await session.execute(
                        text("SELECT status, metrics FROM xref_run WHERE id = :id"),
                        {"id": run_id},
                    )
                )
                .mappings()
                .one()
            )
        assert row == {"status": "failed", "metrics": {"errors": 1}}
    finally:
        async with sf() as session:
            await session.execute(
                text("DELETE FROM xref_run WHERE id = :id"), {"id": run_id}
            )
            await session.commit()
        await dispose_engine(engine)


@pytest.mark.integration
async def test_mapping_strength_applies_serving_lifecycle_policy() -> None:
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
        strength = await store.mapping_strength_by_subject(expected=_READ_POLICY)
        assert strength == {
            "C3262": {(EXACT_MATCH, "validated"), (CLOSE_MATCH, "proposed")},
            "C12345": {(CLOSE_MATCH, "proposed")},
        }
    finally:
        await _clear_xref_tables(sf)
        await dispose_engine(engine)


@pytest.mark.integration
async def test_mapping_strength_rejects_stale_active_generation() -> None:
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    run_id = f"test-stale-strength-{uuid.uuid4().hex}"
    try:
        store = XrefStore(sf)
        await store.upsert_run(run_id, "uberon-cl", "26.02d", "test-1")
        await activate_records(
            store,
            source="uberon-cl",
            run_id=run_id,
            records=[
                SSSOMRecord(
                    subject_id="C3262",
                    predicate_id=EXACT_MATCH,
                    object_id="UBERON:0002107",
                    mapping_justification="semapv:ManualMappingCuration",
                    confidence=1.0,
                    subject_source_version="26.02d",
                    object_source_version="uberon-2026-01",
                    lifecycle_state="validated",
                )
            ],
        )

        stale_policy = XrefReadPolicy(
            uberon=UberonReadIdentity(
                ncit_source_identity="d" * 64,
                uberon_source_identity="b" * 64,
                uberon_serving_identity="c" * 64,
            )
        )
        with pytest.raises(StaleXrefGenerationError, match="ncit_source_identity"):
            await store.mapping_strength_by_subject(expected=stale_policy)
    finally:
        await _clear_xref_tables(sf)
        await dispose_engine(engine)


@pytest.mark.integration
async def test_served_mappings_filter_by_subject_identifier() -> None:
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

        result = await store.mappings_for_identifiers({"C3262"}, expected=_READ_POLICY)
        assert "C3262" in result
        assert len(result["C3262"]) == 1
        mapping = result["C3262"][0]
        assert mapping.object.identifier == "UBERON:0002107"
        assert mapping.predicate == EXACT_MATCH
        assert mapping.lifecycle == "validated"
        assert mapping.confidence == 1.0
        assert "C12400" not in result
    finally:
        await _clear_xref_tables(sf)
        await dispose_engine(engine)


@pytest.mark.integration
async def test_served_mappings_empty_lookup_returns_empty() -> None:
    engine = make_engine(get_settings().database_url)
    try:
        sf = make_sessionmaker(engine)
        store = XrefStore(sf)
        result = await store.mappings_for_identifiers(set(), expected=_READ_POLICY)
        assert result == {}
    finally:
        await dispose_engine(engine)


@pytest.mark.integration
async def test_served_mappings_reverse_lookup() -> None:
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

        result = await store.mappings_for_identifiers(
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
        await _clear_xref_tables(sf)
        await dispose_engine(engine)
