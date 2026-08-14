"""Stable names and denominator rules for decomposition evaluation metrics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


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
