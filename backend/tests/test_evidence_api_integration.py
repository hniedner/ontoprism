"""HTTP ledger reads use real disposable Postgres; graph inputs are controlled."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from backend.dependencies import (
    get_decomposition_reader,
    get_ncit_store,
    get_provenance_store,
    get_repository_metadata,
    get_xref_store,
)
from backend.main import create_app
from ontolib.decomposition.evidence_ledger import EvidenceLedger
from ontolib.decomposition.evidence_models import LedgerAssessment
from ontolib.decomposition.provenance import RunStateError
from ontolib.decomposition.read import decomposition_from_rows
from ontolib.decomposition.vocab import ONTOPRISM_NS
from ontolib.terminologies.namespaces import NCIT_NS

pytestmark = [
    pytest.mark.integration,
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings"),
]


async def test_evidence_endpoint_retains_history_without_current_decomposition() -> (
    None
):
    engine = make_engine(get_settings().database_url)
    try:
        ledger = EvidenceLedger(make_sessionmaker(engine))
        assessment = LedgerAssessment.model_validate_json(
            json.dumps(
                {
                    "concept_code": "C500",
                    "axis": "op:Morphology",
                    "filler_code": "C123",
                    "source_release": "26.07d",
                    "scope_root": "C3262",
                    "support": {"axis_source": "nlp"},
                    "status": "provisional",
                }
            )
        )
        await ledger.decide(
            assessment,
            actor="Alice",
            rationale="Historical review",
            lifecycle="locally-approved",
        )
        await ledger.assess(assessment, absent_reason="No longer in current scope")
        app = create_app()
        app.state.evidence_ledger = ledger
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/ncit/concepts/C500/evidence")
            assert response.status_code == 200
            body = response.json()
            assert body["concept_code"] == "C500"
            assert len(body["assertions"]) == 1
            assertion = body["assertions"][0]
            assert assertion["applicability"]["state"] == "inapplicable"
            assert assertion["decisions"][0]["rationale"] == "Historical review"
            missing = await client.get("/api/v1/ncit/concepts/C999/evidence")
            assert missing.json() == {"concept_code": "C999", "assertions": []}
    finally:
        await dispose_engine(engine)


async def test_decomposition_join_preserves_contract_and_fails_closed() -> None:
    engine = make_engine(get_settings().database_url)
    try:
        ledger = EvidenceLedger(make_sessionmaker(engine))
        app = create_app()
        app.state.evidence_ledger = ledger
        rows = [
            {
                "axis": ONTOPRISM_NS + "Morphology",
                "filler": NCIT_NS + "C123",
                "axisSource": "nlp",
                "run": "published-run",
                "publicationStatus": "provisional",
                "publicationNotice": "expert review, not an NCIt release",
                "outcome": "decomposed",
                "outcomeReason": "Fixture decomposition",
            }
        ]
        app.dependency_overrides[get_decomposition_reader] = lambda: SimpleNamespace(
            rows_for=AsyncMock(return_value=rows)
        )
        app.dependency_overrides[get_ncit_store] = lambda: SimpleNamespace(
            labels_for=AsyncMock(return_value={})
        )
        app.dependency_overrides[get_xref_store] = lambda: SimpleNamespace(
            mappings_for_identifiers=AsyncMock(return_value={})
        )
        metadata = SimpleNamespace(
            ncit=AsyncMock(
                return_value=SimpleNamespace(source_identity="a" * 64, release="26.07d")
            ),
            uberon=AsyncMock(
                return_value=SimpleNamespace(
                    source_identity="b" * 64,
                    observation=SimpleNamespace(
                        serving=SimpleNamespace(sha256="c" * 64)
                    ),
                )
            ),
        )
        run = SimpleNamespace(
            ncit_version="26.07d",
            fingerprint=SimpleNamespace(source_identity="a" * 64, scope_root="C3262"),
        )
        app.dependency_overrides[get_repository_metadata] = lambda: metadata
        provenance = SimpleNamespace(
            completed_run_for_evidence=AsyncMock(return_value=run)
        )
        app.dependency_overrides[get_provenance_store] = lambda: provenance
        assessment = LedgerAssessment.model_validate_json(
            json.dumps(
                {
                    "concept_code": "C530001",
                    "axis": "op:Morphology",
                    "filler_code": "C123",
                    "source_release": "26.07d",
                    "scope_root": "C3262",
                    "status": "provisional",
                    "support": {"axis_source": "nlp"},
                }
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            url = "/api/v1/ncit/concepts/C530001/decomposition"
            unassessed = await client.get(url)
            assert unassessed.status_code == 200
            constituent = unassessed.json()["constituents"][0]
            assert constituent["evidence_status"] == "provisional"
            assert constituent["decision_applicability"]["state"] == "no-decision"
            expected = decomposition_from_rows("C530001", rows).model_dump(mode="json")
            assert all(
                unassessed.json()[key] == value
                for key, value in expected.items()
                if key != "constituents"
            )
            assert all(
                constituent[key] == value
                for key, value in expected["constituents"][0].items()
            )
            await ledger.decide(
                assessment,
                actor="Alice",
                rationale="Original rationale",
                lifecycle="locally-approved",
            )
            matched = await client.get(url)
            assert matched.status_code == 200
            assert (
                matched.json()["constituents"][0]["decision_applicability"]["state"]
                == "applicable"
            )
            changed = assessment.model_dump(mode="json")
            changed["support"]["policy_choices"] = [
                {
                    "kind": "nlp-derivation",
                    "policy": "nlp-v2",
                    "rationale": "Changed policy",
                    "state": "open",
                }
            ]
            await ledger.assess(
                LedgerAssessment.model_validate_json(json.dumps(changed))
            )
            revised = (await client.get(url)).json()["constituents"][0]
            assert revised["decision_applicability"]["state"] == "needs-review"
            assert revised["decision_history"][0]["rationale"] == "Original rationale"
            await assert_context_failures(client, url, run, metadata, provenance, rows)
            await assert_database_failure(engine, client, url)
    finally:
        await dispose_engine(engine)


async def assert_context_failures(client, url, run, metadata, provenance, rows):
    run.fingerprint.scope_root = "C999"
    assert (await client.get(url)).status_code == 503
    run.fingerprint.scope_root = "C3262"
    metadata.ncit.return_value.release = "26.09d"
    assert (await client.get(url)).status_code == 503
    metadata.ncit.return_value.release = "26.07d"
    provenance.completed_run_for_evidence.side_effect = RunStateError("missing")
    assert (await client.get(url)).status_code == 503
    provenance.completed_run_for_evidence.side_effect = None
    rows[0].pop("run")
    assert (await client.get(url)).status_code == 503
    rows[0]["run"] = "published-run"


async def test_ledger_database_failure_returns_503_not_empty_evidence() -> None:
    engine = make_engine(get_settings().database_url)
    try:
        app = create_app()
        app.state.evidence_ledger = EvidenceLedger(make_sessionmaker(engine))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await assert_database_failure(
                engine, client, "/api/v1/ncit/concepts/C3262/evidence"
            )
    finally:
        await dispose_engine(engine)


async def assert_database_failure(engine, client, url):
    async with engine.begin() as connection:
        await connection.execute(
            text("ALTER TABLE assertion_evidence RENAME TO unavailable_ledger")
        )
    try:
        response = await client.get(url)
        assert response.status_code == 503
        assert "assertions" not in response.json()
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("ALTER TABLE unavailable_ledger RENAME TO assertion_evidence")
            )
