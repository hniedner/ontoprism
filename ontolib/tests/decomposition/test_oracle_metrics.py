"""Stored-run scores retain flagged pairs in the plain view, not the official view."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.oracle_metrics import (
    ExpectedConcept,
    StoredPair,
    _conservation_counts,
    read_oracle,
    score_pairs,
)

pytestmark = pytest.mark.unit
_GOLDEN = Path(__file__).parent / "golden"


def _fraction(report: str, name: str) -> tuple[int, int]:
    line = next(line for line in report.splitlines() if line.startswith(f"{name}="))
    numerator, denominator = line.split("=", 1)[1].split(" ", 1)[0].split("/")
    return int(numerator), int(denominator)


def test_scores_distinguish_flagged_true_positive_false_positive_and_missed_pair():
    oracle = [
        ExpectedConcept.model_validate(
            {
                "code": "C1",
                "adjudication": {"status": "accepted"},
                "expected": {
                    "constituents": [
                        {
                            "axis": "op:CellType",
                            "filler": code,
                            "needs_review": False,
                            "provenance_status": "ncit-26.07d",
                            "relationship_group": None,
                        }
                        for code in ("C2", "C3", "C4")
                    ]
                },
            }
        )
    ]
    actual = [
        StoredPair(
            concept_code="C1",
            axis="op:CellType",
            filler_code=code,
            needs_review=code == "C3",
            normalized_group_id=None,
        )
        for code in ("C2", "C3", "C5")
    ]
    report = score_pairs(oracle, actual)
    assert "official (flagged unscoreable, NCIt-bound)" in report
    assert "exact_pair_precision=1/2 (0.500000)" in report
    assert "exact_pair_recall=1/3 (0.333333)" in report
    assert "plain exact pairs" in report
    assert "plain_exact_pair_precision=2/3 (0.666667)" in report
    assert "plain_exact_pair_recall=2/3 (0.666667)" in report
    assert "full_partition_agreement=0/1" in report
    assert "common_pair_partition_agreement_decided_only=0/0 (not-computed)" in report


def test_report_conservation_categories_stay_distinct() -> None:
    result = _conservation_counts(
        {
            "projected": 2,
            "unchanged-unprojected": 3,
            "one-step-r82": 4,
            "closure-only-r82": 5,
            "unresolved": 6,
        }
    )

    assert result.model_dump() == {
        "total": 20,
        "projected": 2,
        "unchanged_unprojected": 3,
        "one_step_r82": 4,
        "closure_only_r82": 5,
        "unresolved": 6,
    }


def test_command_import_does_not_load_r103_review_chain():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import scripts.oracle_metrics; "
            "assert not [m for m in sys.modules "
            "if any(p.startswith('r103_') for p in m.split('.'))]",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_partition_metrics_preserve_abstentions_and_pair_removal_effect():
    oracle = read_oracle(_GOLDEN / "neoplasm-adjudicated.json")
    evidence = json.loads(
        (_GOLDEN / "neoplasm-current-engine-evidence.json").read_text()
    )
    actual = [
        StoredPair(
            concept_code=c["code"],
            axis=r["axis"],
            filler_code=r["filler"],
            needs_review=r["needs_review"],
            normalized_group_id=r["normalized_group_id"],
        )
        for c in evidence["concepts"]
        for r in c["constituents"]
    ]
    unresolved = {"C27262": {"C35501", "C9290"}, "C102870": {"C121619", "C39986"}}
    abstaining = [
        r.model_copy(update={"normalized_group_id": None})
        if r.axis == "op:Morphology"
        and r.filler_code in unresolved.get(r.concept_code, set())
        else r
        for r in actual
    ]
    report = score_pairs(oracle, abstaining)
    assert "[decided-only; agrees=13; disagrees=3; abstains=2; ineligible=2]" in report
    assert "common_pair_decision_coverage=16/18" in report
    assert "partition_abstentions=C102870,C27262" in report
    assert "full_partition_agreement=3/20" in report
    without = score_pairs(oracle, abstaining[1:])
    precision = _fraction(report, "exact_pair_precision")
    recall = _fraction(report, "exact_pair_recall")
    assert _fraction(without, "exact_pair_precision") == (
        precision[0] - 1,
        precision[1] - 1,
    )
    assert _fraction(without, "exact_pair_recall") == (recall[0] - 1, recall[1])
    original = score_pairs(oracle, actual)
    for metric in ("exact_pair_precision", "exact_pair_recall"):
        assert _fraction(report, metric) == _fraction(original, metric)
    sparse = [
        next(r for r in actual if r.concept_code == code)
        for code in sorted({r.concept_code for r in actual})
    ]
    empty = score_pairs(oracle, sparse)
    assert "common_pair_partition_agreement_decided_only=0/0 (not-computed)" in empty
    assert "common_pair_decision_coverage=0/0 (not-computed)" in empty
    assert "partition_abstentions=none" in empty
    resolved = [
        r.model_copy(update={"normalized_group_id": "resolved"})
        if r.concept_code == "C27262" and r.axis == "op:Morphology"
        else r
        for r in abstaining
    ]
    assert "common_pair_partition_agreement_decided_only=14/17" in score_pairs(
        oracle, resolved
    )
