"""Contracts for the independently named M1.6 evaluation views."""

from __future__ import annotations

import pytest

from ontolib.decomposition.evaluation import (
    M1_6_METRIC_CONTRACTS,
    PartitionDiagnosis,
    compare_common_pair_partition,
    compare_full_partition,
    normalized_partition,
)

pytestmark = pytest.mark.unit


def test_m1_6_metric_names_pin_their_denominator_populations() -> None:
    assert tuple(
        (contract.name.value, contract.denominator.value)
        for contract in M1_6_METRIC_CONTRACTS
    ) == (
        ("sme_include_rate", "historical_engine_suggestion_rows"),
        ("exact_pair_precision", "current_emitted_ncit_bound_scoreable_pairs"),
        ("exact_pair_recall", "ncit_bound_non_deferred_oracle_expectations"),
        ("full_partition_agreement", "accepted_20_concept_cohort"),
        (
            "common_pair_partition_agreement",
            "concepts_with_at_least_two_shared_pairs",
        ),
    )


def test_partition_normalization_uses_singletons_for_ungrouped_pairs() -> None:
    assert normalized_partition(
        (
            (("op:Morphology", "C1"), "g1"),
            (("op:PrimarySite", "C2"), None),
            (("op:Laterality", "C3"), None),
            (("op:Morphology", "C4"), "g1"),
        )
    ) == (
        (("op:Laterality", "C3"),),
        (("op:Morphology", "C1"), ("op:Morphology", "C4")),
        (("op:PrimarySite", "C2"),),
    )


def test_full_partition_requires_exact_pairs_and_partition() -> None:
    expected = (
        (("op:Morphology", "C1"), "g1"),
        (("op:PrimarySite", "C2"), "g1"),
    )
    actual = (
        (("op:Morphology", "C1"), "different-name"),
        (("op:PrimarySite", "C2"), "different-name"),
        (("op:Laterality", "C3"), None),
    )

    comparison = compare_full_partition(expected, actual)

    assert comparison.eligible is True
    assert comparison.agrees is False
    assert comparison.missing_pairs == ()
    assert comparison.extra_pairs == (("op:Laterality", "C3"),)
    assert comparison.primary_diagnosis is None


@pytest.mark.parametrize(
    ("expected", "actual", "diagnosis"),
    [
        (
            ((("a", "1"), "x"), (("a", "2"), "y")),
            ((("a", "1"), "z"), (("a", "2"), "z")),
            PartitionDiagnosis.OVER_MERGE,
        ),
        (
            ((("a", "1"), "x"), (("a", "2"), "x")),
            ((("a", "1"), "y"), (("a", "2"), "z")),
            PartitionDiagnosis.OVER_SPLIT,
        ),
        (
            (
                (("a", "1"), "x"),
                (("a", "2"), "x"),
                (("a", "3"), "y"),
                (("a", "4"), "y"),
            ),
            (
                (("a", "1"), "x"),
                (("a", "3"), "x"),
                (("a", "2"), "y"),
                (("a", "4"), "y"),
            ),
            PartitionDiagnosis.MISASSIGNMENT,
        ),
    ],
)
def test_common_pair_partition_has_one_primary_diagnosis(
    expected: tuple[tuple[tuple[str, str], str], ...],
    actual: tuple[tuple[tuple[str, str], str], ...],
    diagnosis: PartitionDiagnosis,
) -> None:
    comparison = compare_common_pair_partition(expected, actual)

    assert comparison.eligible is True
    assert comparison.agrees is False
    assert comparison.primary_diagnosis is diagnosis


@pytest.mark.parametrize(
    ("actual", "reason", "shared_count"),
    [
        (((("a", "9"), None),), "zero-shared-pairs", 0),
        (((("a", "1"), None),), "one-shared-pair", 1),
    ],
)
def test_common_pair_partition_reports_ineligible_denominators(
    actual: tuple[tuple[tuple[str, str], None], ...],
    reason: str,
    shared_count: int,
) -> None:
    expected = ((("a", "1"), "g"), (("a", "2"), "g"))

    comparison = compare_common_pair_partition(expected, actual)

    assert comparison.eligible is False
    assert comparison.agrees is None
    assert comparison.ineligibility_reason == reason
    assert comparison.shared_pair_count == shared_count
    assert comparison.primary_diagnosis is None
