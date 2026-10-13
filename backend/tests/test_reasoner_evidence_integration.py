"""Nonempty reasoner previews preserve the actual ledger and its human history."""

import json
from uuid import uuid4

import pytest

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition.evidence_ledger import EvidenceLedger
from ontolib.decomposition.evidence_models import LedgerAssessment
from ontolib.decomposition.reasoner_evidence import preview_reasoner_evidence

pytestmark = [
    pytest.mark.integration,
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings"),
]


async def test_nonempty_preview_keeps_history_and_does_not_write():
    engine = make_engine(get_settings().database_url)
    try:
        sessions = make_sessionmaker(engine)
        code = "C" + str(int(uuid4().hex[:12], 16))
        assessment = LedgerAssessment.model_validate_json(
            json.dumps(
                {
                    "concept_code": code,
                    "axis": "op:PrimarySite",
                    "filler_code": "C123",
                    "source_release": "26.07d",
                    "scope_root": "C3262",
                    "status": "provisional",
                    "support": {"axis_source": "nlp"},
                }
            )
        )
        ledger = EvidenceLedger(sessions)
        await ledger.assess(assessment)
        before = await ledger.decide(
            assessment,
            actor="SME",
            rationale="Historical",
            lifecycle="locally-approved",
        )
        report = {
            "contradictory_assertions": [
                {"concept_code": code, "axis": "op:PrimarySite", "filler_code": "C123"}
            ]
        }
        result = await preview_reasoner_evidence(sessions, report, "26.07d")
        assert result == {
            "dry_run": True,
            "affected_assertions": 1,
            "transitions": [
                {
                    "axis": "op:PrimarySite",
                    "before": "provisional",
                    "after": "needs-SME",
                    "count": 1,
                }
            ],
        }
        assert (await ledger.for_concept(code))[0] == before
        with pytest.raises(ValueError, match="ledger release"):
            await preview_reasoner_evidence(sessions, report, "other")
        assert (await ledger.for_concept(code))[0] == before
    finally:
        await dispose_engine(engine)


async def test_preview_explains_missing_ledger_assessment():
    engine = make_engine(get_settings().database_url)
    try:
        row = {
            "concept_code": "C" + str(int(uuid4().hex[:12], 16)),
            "axis": "op:PrimarySite",
            "filler_code": "C123",
        }
        with pytest.raises(ValueError, match="requires an existing ledger assessment"):
            await preview_reasoner_evidence(
                make_sessionmaker(engine), {"contradictory_assertions": [row]}, "26.07d"
            )
    finally:
        await dispose_engine(engine)
