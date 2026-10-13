"""Tier-one import preserves source support and human history."""

import json

import pytest
from sqlalchemy import text
from test_support.uberon_graph import GraphClient

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.core.exceptions import StorageError
from ontolib.decomposition.evidence_ledger import EvidenceLedger
from ontolib.decomposition.evidence_models import LedgerAssessment
from ontolib.decomposition.tier_one import import_tier_one
from test_evidence_corpus_integration import seed_cohort

pytestmark = [
    pytest.mark.integration,
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings"),
]


@pytest.fixture(autouse=True)
async def clean_anatomy_fixture(isolated_postgres_settings):
    """The integration lane shares its disposable database between tests."""
    yield
    engine = make_engine(get_settings().database_url)
    try:
        async with make_sessionmaker(engine)() as session, session.begin():
            await session.execute(
                text("DELETE FROM decomp_constituent WHERE run_id='anatomy'")
            )
            await session.execute(text("DELETE FROM decomp_run WHERE id='anatomy'"))
            await session.execute(
                text(
                    "DELETE FROM assertion_evidence "
                    "WHERE concept_code IN ('C349001','C349002')"
                )
            )
    finally:
        await dispose_engine(engine)


async def seed_anatomy(sessions):
    await seed_cohort(sessions, "anatomy", ("C349001", "C349002"))
    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "UPDATE decomp_constituent SET axis='op:PrimarySite', "
                "filler_code=CASE concept_code WHEN 'C349001' THEN 'C12468' "
                "ELSE 'C12470' END WHERE run_id='anatomy'"
            )
        )
    ledger = EvidenceLedger(sessions)
    for code, filler in (("C349001", "C12468"), ("C349002", "C12470")):
        assessment = LedgerAssessment.model_validate_json(
            json.dumps(
                {
                    "concept_code": code,
                    "axis": "op:PrimarySite",
                    "filler_code": filler,
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
                                "role_code": "R101",
                                "filler_code": filler,
                                "structural_path": [],
                            }
                        ],
                        "policy_choices": [
                            {
                                "kind": "axis-assignment",
                                "policy": "D20",
                                "rationale": "Routing requires corroboration",
                                "state": "open",
                            }
                        ],
                    },
                    "evidence": [
                        {
                            "tier": 0,
                            "kind": "curated-source",
                            "source": "NCIt",
                            "source_release": "26.07d",
                            "locator": code,
                            "statement": f"R101 {filler}",
                            "context_indexes": [0],
                        }
                    ],
                }
            )
        )
        await ledger.assess(assessment)
    return ledger


async def test_import_records_partial_support_and_conflict_preserving_history():
    engine = make_engine(get_settings().database_url)
    try:
        sessions = make_sessionmaker(engine)
        ledger = await seed_anatomy(sessions)
        original = (await ledger.for_concept("C349002"))[0]
        decision = await ledger.decide(
            LedgerAssessment.model_validate_json(
                original.model_dump_json(
                    exclude={"decisions", "applicability", "updated_at"}
                )
            ),
            actor="SME",
            rationale="Historical judgement",
            lifecycle="locally-approved",
        )
        preview = await import_tier_one(
            sessions, GraphClient(), "anatomy", dry_run=True
        )
        assert (await ledger.for_concept("C349002"))[0] == decision
        report = await import_tier_one(
            sessions, GraphClient(), "anatomy", oracle_codes=("C349001",)
        )
        supported = (await ledger.for_concept("C349001"))[0]
        conflict = (await ledger.for_concept("C349002"))[0]
        assert len(supported.evidence) == 2
        item = supported.evidence[1]
        assert item.tier == 1
        assert item.source == "Uberon"
        assert item.source_release.endswith("/2026-06-19/uberon.owl")
        assert "NCIT:C12468" in item.statement
        assert "non-identity" in item.statement
        assert "supports" in item.statement
        assert "UBERON_0000062" in item.statement
        assert supported.status == "provisional"
        assert supported.support.policy_choices[0].state == "open"
        assert supported.applicability.state == "no-decision"
        assert conflict.status == "needs-SME"
        assert any(r.kind == "contradictory" for r in conflict.needs_sme_reasons)
        assert "disjointWith" in conflict.evidence[1].statement
        assert conflict.decisions == decision.decisions
        assert conflict.applicability.state == "needs-review"
        assert conflict.evidence[0] == original.evidence[0]
        assert (
            report["whole_run"]
            == preview["whole_run"]
            == {
                "op:PrimarySite": {
                    "supported": 1,
                    "contradicted": 1,
                    "no mapping": 0,
                    "mapped but unresolved": 0,
                }
            }
        )
        assert report["oracle"]["op:PrimarySite"]["supported"] == 1
        await import_tier_one(sessions, GraphClient(), "anatomy")
        assert (await ledger.for_concept("C349002"))[0] == conflict
    finally:
        await dispose_engine(engine)


async def test_source_failure_aborts_before_any_ledger_write():
    class MissingVersion(GraphClient):
        async def select(self, query):
            if "versionIRI" in query:
                return []
            return await super().select(query)

    engine = make_engine(get_settings().database_url)
    try:
        sessions = make_sessionmaker(engine)
        ledger = await seed_anatomy(sessions)
        before = await ledger.for_concept("C349001")
        with pytest.raises(StorageError, match="release version"):
            await import_tier_one(sessions, MissingVersion(), "anatomy")
        assert await ledger.for_concept("C349001") == before
    finally:
        await dispose_engine(engine)


@pytest.mark.parametrize(
    "state", ["missing-run", "running", "missing-ledger", "release", "scope"]
)
async def test_import_refuses_unavailable_or_mismatched_assessments(state):
    engine = make_engine(get_settings().database_url)
    try:
        sessions = make_sessionmaker(engine)
        ledger = await seed_anatomy(sessions)
        async with sessions() as session, session.begin():
            changes = {
                "running": "UPDATE decomp_run SET status='running',finished_at=NULL "
                "WHERE id='anatomy'",
                "missing-ledger": "DELETE FROM assertion_evidence "
                "WHERE concept_code='C349002'",
                "release": "UPDATE assertion_evidence SET source_release='other' "
                "WHERE concept_code='C349002'",
                "scope": "UPDATE assertion_evidence SET scope_root='C999' "
                "WHERE concept_code='C349002'",
            }
            if state in changes:
                await session.execute(text(changes[state]))
        before = await ledger.for_concept("C349001")
        with pytest.raises(
            ValueError, match=r"completed stored run|tier-zero|release/scope"
        ):
            await import_tier_one(
                sessions,
                GraphClient(),
                "absent" if state == "missing-run" else "anatomy",
            )
        assert await ledger.for_concept("C349001") == before
    finally:
        await dispose_engine(engine)


async def test_multi_site_report_retains_unresolved_groups_and_ignores_other_axes():
    engine = make_engine(get_settings().database_url)
    try:
        sessions = make_sessionmaker(engine)
        ledger = await seed_anatomy(sessions)
        async with sessions() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO decomp_constituent "
                    "(run_id,concept_code,axis,filler_code,axis_source,"
                    "needs_review,most_specific,source_roles,source_group_ids) VALUES "
                    "('anatomy','C349001','op:PrimarySite','C12470','role',"
                    "true,false,'[\"R101\"]','[]'), "
                    "('anatomy','C349001','op:NormalTissueOrigin','C12468','role',"
                    "true,false,'[\"R101\"]','[]')"
                )
            )
        for axis, filler in (
            ("op:PrimarySite", "C12470"),
            ("op:NormalTissueOrigin", "C12468"),
        ):
            original = (await ledger.for_concept("C349001"))[0]
            data = original.model_dump(
                mode="json", exclude={"decisions", "applicability", "updated_at"}
            )
            data.update(axis=axis, filler_code=filler)
            await ledger.assess(LedgerAssessment.model_validate_json(json.dumps(data)))
        result = await import_tier_one(sessions, GraphClient(), "anatomy")
        assert result["multi_primary_site"]["concepts"] == 1
        assert type(result["multi_primary_site"]["groups"][0]["supported"]) is int
        assert result["multi_primary_site"]["groups"] == [
            {
                "fillers": ["C12468", "C12470"],
                "concepts": 1,
                "supported": 1,
                "contradicted": 1,
                "unresolved": 1,
            }
        ]
        async with sessions() as session:
            assert (
                await session.scalar(
                    text(
                        "SELECT count(*) FROM decomp_constituent "
                        "WHERE run_id='anatomy' "
                        "AND concept_code='C349001' AND needs_review"
                    )
                )
                == 2
            )
    finally:
        await dispose_engine(engine)
