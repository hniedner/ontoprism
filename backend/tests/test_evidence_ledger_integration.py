"""Ledger history survives re-derived assertions in disposable Postgres."""

import asyncio
import json
from importlib import import_module

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition.evidence_ledger import EvidenceLedger
from ontolib.decomposition.evidence_models import LedgerAssessment

pytestmark = [
    pytest.mark.integration,
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings"),
]


async def test_rerun_retains_decision_and_changed_support_requires_review() -> None:
    engine = make_engine(get_settings().database_url)
    try:
        ledger = EvidenceLedger(make_sessionmaker(engine))
        assessment = LedgerAssessment.model_validate_json("""{
            "concept_code":"C3262", "axis":"op:Morphology", "filler_code":"C123",
            "source_release":"26.07d", "scope_root":"C3262",
            "support":{"axis_source":"role", "contexts":[{
                "kind":"restriction", "anchor_code":"C3262", "depth":0,
                "role_code":"R108", "filler_code":"C123", "structural_path":[0],
                "siblings":[{"role_code":"R108", "filler_code":"C123"}]
            }]}, "status":"provisional"
        }""")
        first = await ledger.assess(assessment)
        approved = await ledger.decide(
            assessment,
            actor="reviewer",
            rationale="examined both contexts",
            lifecycle="locally-approved",
        )
        rerun = await ledger.assess(assessment)
        assert rerun == approved
        assert first.decisions == ()
        assert rerun.applicability.state == "applicable"
        changed = assessment.model_dump(mode="json")
        changed["support"]["contexts"][0]["siblings"].append(
            {"role_code": "R108", "filler_code": "C456"}
        )
        revised = await ledger.assess(
            LedgerAssessment.model_validate_json(json.dumps(changed))
        )
        assert revised.applicability.state == "needs-review"
        assert revised.status == "needs-SME"
        assert revised.decisions == approved.decisions
        assert revised.decisions[0].rationale == "examined both contexts"
        assert await ledger.for_concept("C3262") == [revised]
    finally:
        await dispose_engine(engine)


def assessment_for(code: str = "C100") -> LedgerAssessment:
    return LedgerAssessment.model_validate_json(
        json.dumps(
            {
                "concept_code": code,
                "axis": "op:Morphology",
                "filler_code": "C123",
                "source_release": "26.07d",
                "scope_root": "C3262",
                "status": "provisional",
                "support": {
                    "axis_source": "role",
                    "contexts": [
                        {
                            "kind": "restriction",
                            "anchor_code": code,
                            "depth": 0,
                            "role_code": "R108",
                            "filler_code": "C123",
                            "structural_path": [position],
                            "siblings": [{"role_code": "R108", "filler_code": "C123"}],
                        }
                        for position in (0, 1)
                    ],
                },
            }
        )
    )


async def test_multiple_contexts_history_absence_and_concurrent_decisions() -> None:
    engine = make_engine(get_settings().database_url)
    try:
        ledger = EvidenceLedger(make_sessionmaker(engine))
        assessment = assessment_for()
        first, duplicate = await asyncio.gather(
            ledger.assess(assessment), ledger.assess(assessment)
        )
        assert first == duplicate
        assert len(first.support.contexts) == 2
        await asyncio.gather(
            *(
                ledger.decide(
                    assessment,
                    actor=actor,
                    rationale="Reviewed the two occurrences",
                    lifecycle="locally-approved",
                )
                for actor in ("Alice", "Bob")
            )
        )
        entries = await ledger.for_concept("C100")
        assert len(entries) == 1
        assert {d.actor for d in entries[0].decisions} == {"Alice", "Bob"}
        absent = await ledger.assess(
            assessment, absent_reason="Concept exited Neoplasm scope"
        )
        assert absent.applicability.state == "inapplicable"
        assert absent.applicability.reason == "Concept exited Neoplasm scope"
        assert absent.decisions == entries[0].decisions
        assert (await ledger.for_concept("C100"))[0] == absent
        assert await ledger.for_concept("C999") == []
        with pytest.raises(ValueError, match="nonblank"):
            await ledger.assess(assessment, absent_reason=" ")
    finally:
        await dispose_engine(engine)


async def test_changed_evidence_invalidates_and_stale_decision_cannot_overwrite() -> (
    None
):
    engine = make_engine(get_settings().database_url)
    try:
        ledger = EvidenceLedger(make_sessionmaker(engine))
        assessment = assessment_for("C200")
        original = await ledger.decide(
            assessment,
            actor="Alice",
            rationale="Source examined",
            lifecycle="locally-approved",
        )
        payload = assessment.model_dump(mode="json")
        payload["evidence"] = [
            {
                "tier": 0,
                "kind": "curated-source",
                "source": "NCIt",
                "source_release": "26.07d",
                "locator": "R108",
                "statement": "New support",
                "context_indexes": [1],
            }
        ]
        changed = LedgerAssessment.model_validate_json(json.dumps(payload))
        revised = await ledger.assess(changed)
        assert revised.applicability.state == "needs-review"
        assert revised.decisions == original.decisions
        with pytest.raises(ValueError, match="stale"):
            await ledger.decide(
                assessment,
                actor="Bob",
                rationale="Old view",
                lifecycle="locally-approved",
            )
        current = LedgerAssessment.model_validate_json(
            revised.model_dump_json(
                exclude={"decisions", "applicability", "updated_at"}
            )
        )
        approved = await ledger.decide(
            current,
            actor="Bob",
            rationale="New support checked",
            lifecycle="locally-approved",
        )
        assert approved.applicability.state == "applicable"
        assert len(approved.decisions) == 2
    finally:
        await dispose_engine(engine)


async def test_invalid_write_and_invalid_persisted_history_fail_closed() -> None:
    engine = make_engine(get_settings().database_url)
    try:
        ledger = EvidenceLedger(make_sessionmaker(engine))
        assessment = assessment_for("C300")
        original = await ledger.assess(assessment)
        with pytest.raises(ValidationError, match="evidence-supported"):
            await ledger.assess(
                assessment.model_copy(update={"status": "evidence-supported"})
            )
        assert await ledger.for_concept("C300") == [original]
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE assertion_evidence SET applicability = "
                    "CAST(:value AS jsonb) WHERE concept_code='C300'"
                ),
                {"value": json.dumps({"state": "applicable", "decision_index": 4})},
            )
        with pytest.raises(ValidationError, match="outside history"):
            await ledger.for_concept("C300")
    finally:
        await dispose_engine(engine)


async def test_downgrade_refuses_history_and_empty_ledger_can_be_recreated() -> None:
    migration = import_module("migrations.versions.0035_assertion_evidence")

    def migrate(connection, operation):
        with Operations.context(MigrationContext.configure(connection)):
            operation()

    engine = make_engine(get_settings().database_url)
    try:
        ledger = EvidenceLedger(make_sessionmaker(engine))
        original = await ledger.assess(assessment_for("C400"))
        with pytest.raises(DBAPIError, match="refusing to drop nonempty"):
            async with engine.begin() as connection:
                await connection.run_sync(migrate, migration.downgrade)
        assert await ledger.for_concept("C400") == [original]
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM assertion_evidence"))
            await connection.run_sync(migrate, migration.downgrade)
            await connection.run_sync(migrate, migration.upgrade)
            await connection.run_sync(
                migrate,
                import_module("migrations.versions.0036_ledger_mint_fillers").upgrade,
            )
        assert await ledger.for_concept("C400") == []
    finally:
        await dispose_engine(engine)
