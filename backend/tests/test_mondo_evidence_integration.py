"""Mondo import previews and writes the same assessments, retaining human history."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition.evidence_ledger import EvidenceLedger
from ontolib.decomposition.evidence_models import LedgerAssessment
from ontolib.decomposition.mondo_evidence import import_mondo_evidence
from ontolib.terminologies.mondo import MONDO, MondoHierarchy
from test_evidence_corpus_integration import seed_cohort

pytestmark = [
    pytest.mark.integration,
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings"),
]


@pytest.fixture
async def cohort(isolated_postgres_settings):
    engine = make_engine(get_settings().database_url)
    sessions = make_sessionmaker(engine)
    run_id = "mondo-" + uuid4().hex
    codes = tuple("C" + str(uuid4().int) for _ in range(3))
    ledger = EvidenceLedger(sessions)
    try:
        await seed_cohort(sessions, run_id, codes)
        async with sessions() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE decomp_constituent SET axis='op:ToldGenus',"
                    "axis_source='parent' "
                    "WHERE run_id=:run"
                ),
                {"run": run_id},
            )
        for code in codes:
            await ledger.assess(
                LedgerAssessment.model_validate_json(
                    json.dumps(
                        {
                            "concept_code": code,
                            "axis": "op:ToldGenus",
                            "filler_code": "C123",
                            "source_release": "26.07d",
                            "scope_root": "C3262",
                            "status": "provisional",
                            "support": {
                                "axis_source": "parent",
                                "policy_choices": [
                                    {
                                        "kind": "axis-assignment",
                                        "policy": "nearest-frontier",
                                        "rationale": "Nearest non-qualifier selection",
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
                                    "statement": "Stated genus C123",
                                }
                            ],
                        }
                    )
                )
            )
        source = MondoHierarchy(
            parents={
                MONDO + "1": {MONDO + "3"},
                MONDO + "2": {MONDO + "3"},
                MONDO + "3": set(),
            },
            mappings={
                codes[0]: {MONDO + "1"},
                codes[1]: {MONDO + "2"},
                "C123": {MONDO + "3"},
            },
            reverse={
                MONDO + "1": {"NCIT:" + codes[0]},
                MONDO + "2": {"NCIT:" + codes[1]},
                MONDO + "3": {"NCIT:C123"},
            },
            exclusions={},
        )
        yield sessions, ledger, run_id, codes, source
    finally:
        async with sessions() as session, session.begin():
            await session.execute(
                text("DELETE FROM decomp_constituent WHERE run_id=:run"),
                {"run": run_id},
            )
            await session.execute(
                text("DELETE FROM decomp_run WHERE id=:run"), {"run": run_id}
            )
            await session.execute(
                text("DELETE FROM assertion_evidence WHERE concept_code=ANY(:codes)"),
                {"codes": list(codes)},
            )
        await dispose_engine(engine)


async def test_import_preserves_frontier_choices_and_previews_human_reassessment(
    cohort,
):
    sessions, ledger, run_id, codes, source = cohort
    original = (await ledger.for_concept(codes[1]))[0]
    decision = await ledger.decide(
        LedgerAssessment.model_validate_json(
            original.model_dump_json(
                exclude={"decisions", "applicability", "updated_at"}
            )
        ),
        actor="SME",
        rationale="Previous judgement",
        lifecycle="locally-approved",
    )
    untouched = await ledger.for_concept(codes[2])
    preview = await import_mondo_evidence(sessions, run_id, source, dry_run=True)
    assert preview["buckets"] == {
        "supported": 2,
        "unmapped": 1,
        "multi-mapped": 0,
        "disagreement": 0,
    }
    assert (await ledger.for_concept(codes[1]))[0] == decision
    report = await import_mondo_evidence(sessions, run_id, source)
    assert report == preview | {"dry_run": False}
    supported = (await ledger.for_concept(codes[0]))[0]
    assert len(supported.evidence) == 2
    assert supported.evidence[0].source == "NCIt"
    assert supported.status == "provisional"
    assert supported.support.policy_choices[0].state == "open"
    historical = (await ledger.for_concept(codes[1]))[0]
    assert historical.decisions == decision.decisions
    assert historical.applicability.state == "needs-review"
    assert historical.status == "needs-SME"
    assert report["promotions"] == 0
    assert report["by_status"] == {"provisional": 2, "needs-SME": 1}
    assert report["by_reason"] == {"ambiguous": 1}
    assert await ledger.for_concept(codes[2]) == untouched
    await import_mondo_evidence(sessions, run_id, source)
    assert (await ledger.for_concept(codes[0]))[0] == supported
    assert (await ledger.for_concept(codes[1]))[0] == historical


async def test_support_can_promote_only_when_no_policy_choice_remains_open(cohort):
    sessions, ledger, run_id, codes, source = cohort
    original = (await ledger.for_concept(codes[0]))[0]
    payload = original.model_dump(
        mode="json", include=set(LedgerAssessment.model_fields)
    )
    payload["support"]["policy_choices"][0]["state"] = "corroborated"
    await ledger.assess(LedgerAssessment.model_validate_json(json.dumps(payload)))
    report = await import_mondo_evidence(sessions, run_id, source, dry_run=True)
    assert report["promotions"] == 1
    assert (await ledger.for_concept(codes[0]))[0].status == "provisional"


@pytest.mark.parametrize(
    "problem", ["missing-run", "incomplete", "missing-ledger", "release", "scope"]
)
async def test_bad_cohort_aborts_before_attaching_any_evidence(cohort, problem):
    sessions, ledger, run_id, codes, source = cohort
    async with sessions() as session, session.begin():
        statements = {
            "incomplete": "UPDATE decomp_run SET status='running',finished_at=NULL "
            "WHERE id=:run",
            "missing-ledger": "DELETE FROM assertion_evidence WHERE concept_code=:code",
            "release": "UPDATE assertion_evidence SET source_release='other' "
            "WHERE concept_code=:code",
            "scope": "UPDATE assertion_evidence SET scope_root='C999' "
            "WHERE concept_code=:code",
        }
        if problem in statements:
            await session.execute(
                text(statements[problem]), {"run": run_id, "code": codes[-1]}
            )
    before = await ledger.for_concept(codes[0])
    with pytest.raises(ValueError, match=r"completed|existing ledger|release/scope"):
        await import_mondo_evidence(
            sessions, "absent" if problem == "missing-run" else run_id, source
        )
    assert await ledger.for_concept(codes[0]) == before


async def test_non_parent_rows_are_not_a_genus_cohort(cohort):
    sessions, _ledger, run_id, _codes, source = cohort
    async with sessions() as session, session.begin():
        await session.execute(
            text("UPDATE decomp_constituent SET axis_source='nlp' WHERE run_id=:run"),
            {"run": run_id},
        )
    with pytest.raises(ValueError, match="no parent-derived genus assertions"):
        await import_mondo_evidence(sessions, run_id, source, dry_run=True)


@pytest.mark.parametrize(
    "status", ["provisional", "evidence-supported", "ambiguous", "no-evidence"]
)
async def test_stored_morphology_keys_keep_p334_evidence_and_reasons(cohort, status):
    sessions, ledger, run_id, codes, source = cohort
    previous = (await ledger.for_concept(codes[0]))[0]
    payload = previous.model_dump(
        mode="json", include=set(LedgerAssessment.model_fields)
    )
    payload["axis"] = "op:Morphology"
    payload["evidence"].append(
        {
            "tier": 1,
            "kind": "curated-source",
            "source": "NCIt",
            "source_release": "26.07d",
            "locator": "C123/P334",
            "statement": "P334 histology carrier C123 supports "
            "morphology axis-assignment",
        }
    )
    if status == "evidence-supported":
        payload["status"] = status
        payload["support"]["policy_choices"][0]["state"] = "corroborated"
    elif status != "provisional":
        payload["status"] = "needs-SME"
        payload["needs_sme_reasons"] = [
            {"kind": status, "rationale": "Existing morphology assessment"}
        ]
    original = await ledger.assess(
        LedgerAssessment.model_validate_json(json.dumps(payload))
    )
    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "UPDATE decomp_constituent SET axis='op:Morphology' "
                "WHERE run_id=:run AND concept_code=:code"
            ),
            {"run": run_id, "code": codes[0]},
        )
    preview = await import_mondo_evidence(sessions, run_id, source, dry_run=True)
    assert preview["assertions"] == 3
    assert {
        "before": original.status,
        "after": original.status,
        "p334_evidence": True,
        "count": 1,
    } in preview["transitions"]
    assert (await ledger.for_concept(codes[0]))[0] == original
    report = await import_mondo_evidence(sessions, run_id, source)
    assert report == preview | {"dry_run": False}
    current = (await ledger.for_concept(codes[0]))[0]
    assert current.axis == "op:Morphology"
    assert current.status == original.status
    assert current.needs_sme_reasons == original.needs_sme_reasons
    assert current.support == original.support
    assert current.evidence[:-1] == original.evidence
    assert current.evidence[-1].source == "Mondo"
