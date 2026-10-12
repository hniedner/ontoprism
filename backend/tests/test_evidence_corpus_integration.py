"""Current-ledger report counts are scoped to stored run cohorts."""

import json
from datetime import UTC, datetime

import pytest
from scripts.corpus_shape import corpus_shape_counts, render_report
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition.evidence_ledger import EvidenceLedger
from ontolib.decomposition.evidence_models import LedgerAssessment
from ontolib.decomposition.models import Constituent, Decomposition
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.decomposition.provenance_models import (
    RUN_STAGE_SEQUENCE_IDENTITY,
    RunFingerprint,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings"),
]


async def seed_cohort(sessions, run_id: str, codes: tuple[str, ...]) -> None:
    store = ProvenanceStore(sessions)
    fingerprint = RunFingerprint(
        source_identity="a" * 64,
        collapse_policy_identity="0" * 64,
        routing_implementation_identity="1" * 64,
        stage_sequence_identity=RUN_STAGE_SEQUENCE_IDENTITY,
        branch="neoplasm",
        scope_root="C3262",
        scope_version="stated-genus-subclass-v1",
        semantic_types=("Neoplastic Process",),
        worklist=codes,
        total_limit=None,
        algorithm_version="decomposition-v1",
        config_version="axes-v1",
        walker_max_depth=5,
        output_mode="none",
        load_mode="none",
        emitted_at=datetime(2026, 10, 12, tzinfo=UTC),
    )
    await store.create_run(run_id, "26.07d", fingerprint)
    for code in codes:
        claim = await store.claim_work_item(run_id, code)
        assert claim is not None
        await store.complete_work_item(
            run_id,
            code,
            claim,
            decomposition=Decomposition(
                code=code,
                semantic_type="Neoplastic Process",
                constituents=(
                    Constituent(
                        axis="op:Morphology", filler_code="C123", axis_source="nlp"
                    ),
                ),
            ),
            semantic_types=("Neoplastic Process",),
            minted=(),
        )
    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "UPDATE decomp_run SET status='complete',finished_at=now() "
                "WHERE id=:run"
            ),
            {"run": run_id},
        )


def assessment(code: str, **changes) -> LedgerAssessment:
    value = {
        "concept_code": code,
        "axis": "op:Morphology",
        "filler_code": "C123",
        "source_release": "26.07d",
        "scope_root": "C3262",
        "status": "provisional",
        "support": {"axis_source": "nlp"},
    }
    return LedgerAssessment.model_validate_json(json.dumps(value | changes))


async def test_evidence_cohorts_separate_unassessed_mismatches_and_statuses():
    engine = make_engine(get_settings().database_url)
    try:
        sessions = make_sessionmaker(engine)
        codes = tuple(f"C47600{i}" for i in range(1, 8))
        await seed_cohort(sessions, "evidence-selected", codes)
        await seed_cohort(sessions, "evidence-against", codes[:2])
        ledger = EvidenceLedger(sessions)
        evidence = [
            {
                "tier": 0,
                "kind": "curated-source",
                "source": "NCIt",
                "source_release": "26.07d",
                "locator": "C123",
                "statement": "Reviewed source",
            }
        ]
        await ledger.assess(
            assessment(codes[0], status="evidence-supported", evidence=evidence)
        )
        await ledger.assess(
            assessment(
                codes[1],
                support={
                    "axis_source": "nlp",
                    "policy_choices": [
                        {
                            "kind": "nlp-derivation",
                            "policy": "nlp-v1",
                            "rationale": "Needs corroboration",
                            "state": "open",
                        }
                    ],
                },
            )
        )
        await ledger.assess(
            assessment(
                codes[2],
                status="needs-SME",
                needs_sme_reasons=[
                    {"kind": kind, "rationale": "Review required"}
                    for kind in ("no-evidence", "contradictory", "ambiguous")
                ],
            )
        )
        # codes[3] is intentionally unassessed. Mismatches must not count as support.
        await ledger.assess(
            assessment(
                codes[4],
                source_release="26.09d",
                status="evidence-supported",
                evidence=evidence,
            )
        )
        await ledger.assess(
            assessment(
                codes[5],
                scope_root="C999",
                status="evidence-supported",
                evidence=evidence,
            )
        )
        await ledger.assess(
            assessment(
                codes[6],
                source_release="26.09d",
                scope_root="C999",
                status="evidence-supported",
                evidence=evidence,
            )
        )
        # Unrelated ledger row must not enlarge either cohort.
        await ledger.assess(
            assessment("C476999", status="evidence-supported", evidence=evidence)
        )
        selected = await corpus_shape_counts("evidence-selected")
        assert selected["evidence.assertions.total"] == 7
        assert selected["evidence.unassessed"] == 1
        assert selected["evidence.status.provisional"] == 1
        assert selected["evidence.status.evidence-supported"] == 1
        assert selected["evidence.status.needs-SME"] == 1
        assert selected["evidence.mismatch.total"] == 3
        assert selected["evidence.mismatch.release"] == 2
        assert selected["evidence.mismatch.scope"] == 2
        for reason in ("no-evidence", "contradictory", "ambiguous"):
            assert (
                selected[f"evidence.needs-SME.axis.op:Morphology.reason.{reason}"] == 1
            )
        assert selected["evidence.open-policy.nlp-derivation"] == 1
        against = await corpus_shape_counts("evidence-against")
        report = render_report(
            "evidence-selected", selected, against=("evidence-against", against)
        )
        assert "evidence.supported-share=1/7 (14.285714%)" in report
        assert "not a historical tier-state comparison" in report
        assert "difference.selected-minus-against.evidence.unassessed=+1" in report
        assert (
            "difference.selected-minus-against.evidence.status.evidence-supported=+0"
            in report
        )
    finally:
        await dispose_engine(engine)
