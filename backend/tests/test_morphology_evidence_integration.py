"""P334 morphology evidence through the ledger importer, without ICD-O leakage."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition.axis_diagnostics import AxisHierarchyEvidence, HierarchyEdge
from ontolib.decomposition.evidence_ledger import EvidenceLedger
from ontolib.decomposition.evidence_models import LedgerAssessment
from ontolib.decomposition.histology_anchor import HistologyAnchors
from ontolib.decomposition.morphology_evidence import import_morphology_evidence
from test_evidence_corpus_integration import seed_cohort

pytestmark = [
    pytest.mark.integration,
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings"),
]


@pytest.mark.parametrize(
    ("edges", "values", "status", "matched", "reason", "unmatched"),
    [
        ([], {"C475001": ("8000/3",)}, "evidence-supported", ["C475001"], None, None),
        (
            [("C475001", "C475002")],
            {"C475002": ("8000/3",)},
            "evidence-supported",
            ["C475002"],
            None,
            None,
        ),
        (
            [("C475001", "C475002"), ("C475001", "C475003")],
            {"C475002": ("8000/3",), "C475003": ("8001/3",)},
            "needs-SME",
            ["C475002", "C475003"],
            "ambiguous",
            None,
        ),
        ([], {}, "needs-SME", [], "no-evidence", None),
        ([], {"C475001": ("8000/6",)}, "needs-SME", [], "no-evidence", None),
        (
            [("C475001", "C475002"), ("C475002", "C123")],
            {"C475002": ("8000/3",)},
            "provisional",
            [],
            None,
            "filler-ancestor-of-anchor",
        ),
        (
            [("C475001", "C475002")],
            {"C475002": ("8000/3",)},
            "provisional",
            [],
            None,
            "incomparable",
        ),
        (
            [("C475001", "C475002"), ("C475001", "C475003"), ("C123", "C475002")],
            {"C475002": ("8000/3",), "C475003": ("8001/3",)},
            "needs-SME",
            ["C475002"],
            "ambiguous",
            "mixed",
        ),
    ],
)
async def test_disease_anchors_control_morphology_assessment(
    edges, values, status, matched, reason, unmatched
):
    engine = make_engine(get_settings().database_url)
    run_id = "morphology-" + uuid4().hex
    # Unique concept keys avoid cross-test history in the shared disposable database.
    suffix = str(int(uuid4().hex[:12], 16))
    codes = {c: "C" + suffix + c[1:] for c in ("C475001", "C475002", "C475003", "C123")}
    disease = codes["C475001"]
    filler = codes["C123"] if unmatched else disease
    source = AxisHierarchyEvidence(
        source_identity="a" * 64,
        edges=tuple(HierarchyEdge(child=codes[a], parent=codes[b]) for a, b in edges),
        disjoint_pairs=(),
    )
    anchors = HistologyAnchors(source, {codes[c]: v for c, v in values.items()})
    try:
        sessions = make_sessionmaker(engine)
        await seed_cohort(sessions, run_id, (disease,))
        async with sessions() as session, session.begin():
            await session.execute(
                text("UPDATE decomp_constituent SET filler_code=:f WHERE run_id=:r"),
                {"f": filler, "r": run_id},
            )
        assessment = LedgerAssessment.model_validate_json(
            json.dumps(
                {
                    "concept_code": disease,
                    "axis": "op:Morphology",
                    "filler_code": filler,
                    "source_release": "26.07d",
                    "scope_root": "C3262",
                    "status": "provisional",
                    "support": {
                        "axis_source": "parent",
                        "policy_choices": [
                            {
                                "kind": "axis-assignment",
                                "policy": "stored-engine-choice",
                                "rationale": "Needs corroboration",
                                "state": "open",
                            }
                        ],
                    },
                }
            )
        )
        ledger = EvidenceLedger(sessions)
        before = await ledger.assess(assessment)
        preview = await import_morphology_evidence(
            sessions, run_id, anchors, dry_run=True
        )
        assert (await ledger.for_concept(disease))[0] == before
        report = await import_morphology_evidence(sessions, run_id, anchors)
        entry = (await ledger.for_concept(disease))[0]
        assert entry.status == status
        assert len(entry.evidence) == len(matched)
        for item, carrier in zip(entry.evidence, matched, strict=True):
            assert item.tier == 1
            assert item.source == "NCIt"
            assert item.source_release == "26.07d"
            assert codes[carrier] in item.locator
            assert "P334" in item.statement
        assert "8000/" not in entry.model_dump_json()
        assert "8001/" not in entry.model_dump_json()
        assert [r.kind for r in entry.needs_sme_reasons] == ([reason] if reason else [])
        if reason == "ambiguous":
            assert all(codes[c] in entry.needs_sme_reasons[0].rationale for c in values)
        assert entry.support.policy_choices[0].state == (
            "corroborated" if status == "evidence-supported" else "open"
        )
        if unmatched in {"incomparable", "filler-ancestor-of-anchor"}:
            assert report["unique_nonmatching"][unmatched] == 1
            assert entry == before
        assert preview["by_status"] == report["by_status"]
        await import_morphology_evidence(sessions, run_id, anchors)
        assert (await ledger.for_concept(disease))[0] == entry
    finally:
        await dispose_engine(engine)


@pytest.mark.parametrize("invalid", ["run", "missing-ledger", "release", "source"])
async def test_morphology_import_rejects_unavailable_or_mismatched_inputs(invalid):
    engine = make_engine(get_settings().database_url)
    run_id = "morphology-" + uuid4().hex
    code = "C" + str(int(uuid4().hex[:12], 16))
    try:
        sessions = make_sessionmaker(engine)
        await seed_cohort(sessions, run_id, (code,))
        ledger = EvidenceLedger(sessions)
        if invalid != "missing-ledger":
            await ledger.assess(
                LedgerAssessment.model_validate_json(
                    json.dumps(
                        {
                            "concept_code": code,
                            "axis": "op:Morphology",
                            "filler_code": "C123",
                            "source_release": "other"
                            if invalid == "release"
                            else "26.07d",
                            "scope_root": "C3262",
                            "status": "provisional",
                            "support": {"axis_source": "nlp"},
                        }
                    )
                )
            )
        anchors = HistologyAnchors(
            AxisHierarchyEvidence(
                source_identity=("b" if invalid == "source" else "a") * 64,
                edges=(),
                disjoint_pairs=(),
            ),
            {},
        )
        before = await ledger.for_concept(code)
        with pytest.raises(
            ValueError,
            match=r"completed|existing ledger|release/scope|hierarchy source",
        ):
            await import_morphology_evidence(
                sessions, "absent" if invalid == "run" else run_id, anchors
            )
        assert await ledger.for_concept(code) == before
    finally:
        await dispose_engine(engine)


@pytest.mark.parametrize(
    "case", ["history", "other-choices", "tier-zero-closed", "prior-conflict"]
)
async def test_morphology_import_preserves_independent_choices_and_history(case):
    engine = make_engine(get_settings().database_url)
    run_id = "morphology-" + uuid4().hex
    code = "C" + str(int(uuid4().hex[:12], 16))
    source = AxisHierarchyEvidence(
        source_identity="a" * 64,
        edges=(HierarchyEdge(child="C123", parent=code),),
        disjoint_pairs=(),
    )
    values = {} if case == "tier-zero-closed" else {code: ("8000/3",)}
    anchors = HistologyAnchors(source, values)
    try:
        sessions = make_sessionmaker(engine)
        await seed_cohort(sessions, run_id, (code,))
        choices = (
            []
            if case == "tier-zero-closed"
            else [
                {
                    "kind": "axis-assignment",
                    "state": "open",
                    "policy": "stored-engine-choice",
                    "rationale": "Needs corroboration",
                }
            ]
        )
        if case == "other-choices":
            choices += [
                {
                    "kind": k,
                    "state": "open",
                    "policy": "stored-engine-choice",
                    "rationale": "Independent choice",
                }
                for k in ("collapse", "grouping", "nlp-derivation")
            ]
        reasons = (
            [{"kind": "contradictory", "rationale": "Previous independent conflict"}]
            if case == "prior-conflict"
            else []
        )
        assessment = LedgerAssessment.model_validate_json(
            json.dumps(
                {
                    "concept_code": code,
                    "axis": "op:Morphology",
                    "filler_code": "C123",
                    "source_release": "26.07d",
                    "scope_root": "C3262",
                    "status": "evidence-supported"
                    if case == "tier-zero-closed"
                    else "needs-SME"
                    if reasons
                    else "provisional",
                    "support": {"axis_source": "parent", "policy_choices": choices},
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
                    "needs_sme_reasons": reasons,
                }
            )
        )
        ledger = EvidenceLedger(sessions)
        before = await ledger.assess(assessment)
        if case == "history":
            before = await ledger.decide(
                assessment,
                actor="SME",
                rationale="Historical review",
                lifecycle="locally-approved",
            )
        preview = await import_morphology_evidence(
            sessions, run_id, anchors, dry_run=True
        )
        assert (await ledger.for_concept(code))[0] == before
        report = await import_morphology_evidence(sessions, run_id, anchors)
        after = (await ledger.for_concept(code))[0]
        assert preview["by_status"] == report["by_status"]
        assert preview["by_reason"] == report["by_reason"]
        assert after.evidence[0] == before.evidence[0]
        if case == "history":
            assert after.decisions == before.decisions
            assert after.applicability.state == "needs-review"
            assert after.status == "needs-SME"
            assert report["by_reason"] == {"ambiguous": 1}
        elif case == "tier-zero-closed":
            assert after == before
        elif case == "other-choices":
            assert after.status == "provisional"
            assert {p.kind: p.state for p in after.support.policy_choices} == {
                "axis-assignment": "corroborated",
                "collapse": "open",
                "grouping": "open",
                "nlp-derivation": "open",
            }
        else:
            assert after.status == "needs-SME"
            assert after.needs_sme_reasons == before.needs_sme_reasons
    finally:
        await dispose_engine(engine)
