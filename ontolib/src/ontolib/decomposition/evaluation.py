"""Stable names and denominator rules for decomposition evaluation metrics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations
from typing import Literal

type EvaluationPair = tuple[str, str]
type GroupedEvaluationPair = tuple[EvaluationPair, str | None]
type PartitionBlock = tuple[EvaluationPair, ...]
type PairPartition = tuple[PartitionBlock, ...]

_MIN_COMMON_PAIR_COUNT = 2


class EvaluationMetricName(StrEnum):
    """Independent views reported by decomposition evaluations."""

    SME_INCLUDE_RATE = "sme_include_rate"
    EXACT_PAIR_PRECISION = "exact_pair_precision"
    EXACT_PAIR_RECALL = "exact_pair_recall"
    FULL_PARTITION_AGREEMENT = "full_partition_agreement"
    COMMON_PAIR_PARTITION_AGREEMENT = "common_pair_partition_agreement"


class MetricDenominatorRule(StrEnum):
    """The population that governs each evaluation metric."""

    HISTORICAL_ENGINE_SUGGESTIONS = "historical_engine_suggestion_rows"
    CURRENT_SCOREABLE_PAIRS = "current_emitted_ncit_bound_scoreable_pairs"
    NCIT_BOUND_ORACLE_EXPECTATIONS = "ncit_bound_non_deferred_oracle_expectations"
    ACCEPTED_COHORT = "accepted_20_concept_cohort"
    COMMON_PAIRS = "concepts_with_at_least_two_shared_pairs"


class PartitionDiagnosis(StrEnum):
    """One mutually exclusive primary explanation for a partition disagreement."""

    OVER_MERGE = "over-merge"
    OVER_SPLIT = "over-split"
    MISASSIGNMENT = "misassignment"


@dataclass(frozen=True, slots=True)
class PartitionComparison:
    """Canonical full- or common-pair partition comparison."""

    eligible: bool
    agrees: bool | None
    expected_partition: PairPartition
    actual_partition: PairPartition
    missing_pairs: tuple[EvaluationPair, ...]
    extra_pairs: tuple[EvaluationPair, ...]
    shared_pair_count: int
    ineligibility_reason: Literal["zero-shared-pairs", "one-shared-pair"] | None
    primary_diagnosis: PartitionDiagnosis | None


@dataclass(frozen=True, slots=True)
class EvaluationMetricContract:
    """Bind a public metric name to its governing denominator population."""

    name: EvaluationMetricName
    denominator: MetricDenominatorRule


M1_6_METRIC_CONTRACTS: tuple[EvaluationMetricContract, ...] = (
    EvaluationMetricContract(
        EvaluationMetricName.SME_INCLUDE_RATE,
        MetricDenominatorRule.HISTORICAL_ENGINE_SUGGESTIONS,
    ),
    EvaluationMetricContract(
        EvaluationMetricName.EXACT_PAIR_PRECISION,
        MetricDenominatorRule.CURRENT_SCOREABLE_PAIRS,
    ),
    EvaluationMetricContract(
        EvaluationMetricName.EXACT_PAIR_RECALL,
        MetricDenominatorRule.NCIT_BOUND_ORACLE_EXPECTATIONS,
    ),
    EvaluationMetricContract(
        EvaluationMetricName.FULL_PARTITION_AGREEMENT,
        MetricDenominatorRule.ACCEPTED_COHORT,
    ),
    EvaluationMetricContract(
        EvaluationMetricName.COMMON_PAIR_PARTITION_AGREEMENT,
        MetricDenominatorRule.COMMON_PAIRS,
    ),
)


def normalized_partition(rows: tuple[GroupedEvaluationPair, ...]) -> PairPartition:
    """Return group-name-independent blocks with ungrouped pairs as singletons."""
    pairs = tuple(pair for pair, _group in rows)
    if len(pairs) != len(set(pairs)):
        raise ValueError("partition rows must contain unique pairs")
    if any(not axis or not filler for (axis, filler), _group in rows):
        raise ValueError("partition pairs require non-empty axis and filler")
    return _partition_blocks(rows)


def _partition_blocks(rows: tuple[GroupedEvaluationPair, ...]) -> PairPartition:
    grouped: dict[str, list[EvaluationPair]] = {}
    blocks: list[PartitionBlock] = []
    for pair, group in rows:
        if group is None:
            blocks.append((pair,))
        else:
            grouped.setdefault(group, []).append(pair)
    blocks.extend(tuple(sorted(members)) for members in grouped.values())
    return tuple(sorted(blocks))


def _induce_partition(
    partition: PairPartition, allowed: frozenset[EvaluationPair]
) -> PairPartition:
    blocks = {tuple(pair for pair in block if pair in allowed) for block in partition}
    return tuple(sorted(block for block in blocks if block))


def _co_membership(partition: PairPartition) -> frozenset[frozenset[EvaluationPair]]:
    return frozenset(
        frozenset(pair_pair)
        for block in partition
        for pair_pair in combinations(block, 2)
    )


def _diagnosis(
    expected: PairPartition, actual: PairPartition
) -> PartitionDiagnosis | None:
    expected_together = _co_membership(expected)
    actual_together = _co_membership(actual)
    over_merge = actual_together - expected_together
    over_split = expected_together - actual_together
    if over_merge and over_split:
        return PartitionDiagnosis.MISASSIGNMENT
    if over_merge:
        return PartitionDiagnosis.OVER_MERGE
    if over_split:
        return PartitionDiagnosis.OVER_SPLIT
    return None


def _ineligible_common_comparison(
    expected: PairPartition,
    actual: PairPartition,
    shared: frozenset[EvaluationPair],
    missing: tuple[EvaluationPair, ...],
    extra: tuple[EvaluationPair, ...],
) -> PartitionComparison:
    reason: Literal["zero-shared-pairs", "one-shared-pair"] = (
        "zero-shared-pairs" if not shared else "one-shared-pair"
    )
    return PartitionComparison(
        eligible=False,
        agrees=None,
        expected_partition=expected,
        actual_partition=actual,
        missing_pairs=missing,
        extra_pairs=extra,
        shared_pair_count=len(shared),
        ineligibility_reason=reason,
        primary_diagnosis=None,
    )


def _eligible_comparison(
    expected: PairPartition,
    actual: PairPartition,
    shared: frozenset[EvaluationPair],
    missing: tuple[EvaluationPair, ...],
    extra: tuple[EvaluationPair, ...],
    *,
    common_only: bool,
) -> PartitionComparison:
    agrees = expected == actual and (common_only or (not missing and not extra))
    return PartitionComparison(
        eligible=True,
        agrees=agrees,
        expected_partition=expected,
        actual_partition=actual,
        missing_pairs=missing,
        extra_pairs=extra,
        shared_pair_count=len(shared),
        ineligibility_reason=None,
        primary_diagnosis=_diagnosis(expected, actual) if not agrees else None,
    )


def _comparison(
    expected_rows: tuple[GroupedEvaluationPair, ...],
    actual_rows: tuple[GroupedEvaluationPair, ...],
    *,
    common_only: bool,
) -> PartitionComparison:
    expected = normalized_partition(expected_rows)
    actual = normalized_partition(actual_rows)
    expected_pairs = frozenset(pair for block in expected for pair in block)
    actual_pairs = frozenset(pair for block in actual for pair in block)
    shared = expected_pairs & actual_pairs
    missing = tuple(sorted(expected_pairs - actual_pairs))
    extra = tuple(sorted(actual_pairs - expected_pairs))
    if common_only:
        expected = _induce_partition(expected, shared)
        actual = _induce_partition(actual, shared)
        if len(shared) < _MIN_COMMON_PAIR_COUNT:
            return _ineligible_common_comparison(
                expected, actual, shared, missing, extra
            )
    return _eligible_comparison(
        expected,
        actual,
        shared,
        missing,
        extra,
        common_only=common_only,
    )


def compare_full_partition(
    expected: tuple[GroupedEvaluationPair, ...],
    actual: tuple[GroupedEvaluationPair, ...],
) -> PartitionComparison:
    """Compare exact pair sets and their complete normalized partitions."""
    return _comparison(expected, actual, common_only=False)


def compare_common_pair_partition(
    expected: tuple[GroupedEvaluationPair, ...],
    actual: tuple[GroupedEvaluationPair, ...],
) -> PartitionComparison:
    """Compare partitions induced onto at least two shared pairs."""
    return _comparison(expected, actual, common_only=True)
