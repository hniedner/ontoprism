from __future__ import annotations

from pathlib import Path

import pytest
from scripts.oracle_metrics import oracle_metrics_report
from scripts.research.current_evidence import CurrentEngineEvidence
from scripts.research.golden_review import (
    load_migrated_historical_adjudication,
    load_row_decisions,
)

from ontolib.decomposition.proposal_registry import load_proposal_registry

pytestmark = pytest.mark.unit

_GOLDEN = Path(__file__).parent / "golden"


def _fraction(report: str, name: str) -> tuple[int, int]:
    line = next(line for line in report.splitlines() if line.startswith(f"{name}="))
    numerator, denominator = line.removeprefix(f"{name}=").split(" ", 1)[0].split("/")
    return int(numerator), int(denominator)


def test_dropping_one_engine_constituent_changes_the_printed_oracle_metrics() -> None:
    evidence = CurrentEngineEvidence.model_validate_json(
        (_GOLDEN / "neoplasm-current-engine-evidence.json").read_bytes()
    )
    rows = load_row_decisions(_GOLDEN / "neoplasm-row-decisions.json")
    registry = load_proposal_registry(_GOLDEN / "proposal-registry.json")
    oracle = load_migrated_historical_adjudication(
        _GOLDEN / "neoplasm-adjudicated.json",
        _GOLDEN / "proposal-registry.json",
        _GOLDEN / "proposal-registry-schema2-migration.json",
    )
    concept = evidence.concepts[0]
    assert (concept.constituents[0].axis, concept.constituents[0].filler) == (
        "op:CellType",
        "C41063",
    )
    changed = evidence.model_copy(
        update={
            "concepts": (
                concept.model_copy(update={"constituents": concept.constituents[1:]}),
                *evidence.concepts[1:],
            )
        }
    )

    baseline = oracle_metrics_report(evidence, oracle, rows, registry)
    without_constituent = oracle_metrics_report(changed, oracle, rows, registry)

    assert "historical_sme_include_rate=48/106" in baseline
    assert "historical_sme_include_rate=48/106" in without_constituent
    assert baseline != without_constituent
    baseline_precision = _fraction(baseline, "exact_pair_precision")
    changed_precision = _fraction(without_constituent, "exact_pair_precision")
    baseline_recall = _fraction(baseline, "exact_pair_recall")
    changed_recall = _fraction(without_constituent, "exact_pair_recall")
    assert changed_precision == (
        baseline_precision[0] - 1,
        baseline_precision[1] - 1,
    )
    assert changed_recall == (baseline_recall[0] - 1, baseline_recall[1])
