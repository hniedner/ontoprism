"""Contracts for the independently named M1.6 evaluation views."""

from __future__ import annotations

import pytest

from ontolib.decomposition.evaluation import M1_6_METRIC_CONTRACTS

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
