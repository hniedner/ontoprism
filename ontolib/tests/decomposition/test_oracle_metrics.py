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


def test_oracle_report_separates_explicit_abstentions_from_decided_partitions() -> None:
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
    unresolved = {
        "C27262": {"C35501", "C9290"},
        "C102870": {"C121619", "C39986"},
    }
    concepts = tuple(
        concept.model_copy(
            update={
                "constituents": tuple(
                    item.model_copy(
                        update={
                            "normalized_group_id": None,
                            "normalized_group_label": None,
                        }
                    )
                    if item.axis == "op:Morphology"
                    and item.filler in unresolved.get(concept.code, set())
                    else item
                    for item in concept.constituents
                )
            }
        )
        for concept in evidence.concepts
    )
    abstaining = evidence.model_copy(update={"concepts": concepts})
    report = oracle_metrics_report(abstaining, oracle, rows, registry)
    assert "[decided-only; agrees=13; disagrees=3; abstains=2; ineligible=2]" in report
    assert _fraction(report, "common_pair_partition_agreement_decided_only") == (13, 16)
    assert _fraction(report, "common_pair_decision_coverage") == (16, 18)
    assert "partition_abstentions=C102870,C27262" in report
    assert _fraction(report, "full_partition_agreement")[1] == 20
    assert "[full-cohort; includes 2 common-pair abstentions]" in report
    # Null groups on non-abstaining concepts remain ordinary singleton partitions.
    assert "partition_abstentions=C100054" not in report
    original = oracle_metrics_report(evidence, oracle, rows, registry)
    for metric in ("exact_pair_precision", "exact_pair_recall"):
        assert _fraction(report, metric) == _fraction(original, metric)

    # Fewer than two common pairs is ineligible, not an abstention or a success.
    sparse = abstaining.model_copy(
        update={
            "concepts": tuple(
                c.model_copy(update={"constituents": c.constituents[:1]})
                for c in abstaining.concepts
            )
        }
    )
    empty = oracle_metrics_report(sparse, oracle, rows, registry)
    assert "common_pair_partition_agreement_decided_only=0/0 (not-computed)" in empty
    assert "common_pair_decision_coverage=0/0 (not-computed)" in empty
    assert "partition_abstentions=none" in empty

    # A decided group on a historically abstaining concept is not scored as unknown.
    resolved = abstaining.model_copy(
        update={
            "concepts": tuple(
                c.model_copy(
                    update={
                        "constituents": tuple(
                            item.model_copy(
                                update={
                                    "normalized_group_id": "a" * 64,
                                    "normalized_group_label": "test-resolved",
                                }
                            )
                            if item.axis == "op:Morphology"
                            else item
                            for item in c.constituents
                        )
                    }
                )
                if c.code == "C27262"
                else c
                for c in abstaining.concepts
            )
        }
    )
    decided = oracle_metrics_report(resolved, oracle, rows, registry)
    assert _fraction(decided, "common_pair_partition_agreement_decided_only") == (
        14,
        17,
    )
    assert _fraction(decided, "common_pair_decision_coverage") == (17, 18)
    assert decided.endswith("partition_abstentions=C102870")
