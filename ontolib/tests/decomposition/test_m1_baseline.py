"""Pin the M1 decomposition baseline recorded by issue #57.

These figures are *the measurement* M1 exists to produce: the SME-adjudicated oracle
(`neoplasm-adjudicated.json`), scored against the recorded engine run
(`neoplasm-engine-evidence.json`) and the #154 residual subset
(`neoplasm-corpus-comparison.json`). Everything the test reads is tracked in this
directory, so the baseline reproduces from git alone — no live store, no network, no
`tmp/` workbook.

A change that moves any number here is not automatically a failure, but it must be
**deliberate and stated**: say which engine or scorer change moved it, and update the
expected value in the same commit with that reason.

**Never edit the oracle, the recorded evidence, or the corpus comparison to make this
test pass.** The golden README states the rule directly — *"Iterate without changing the
oracle merely to match the engine."* A failure here means something upstream changed;
that is a finding to report, not a test to repair.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from scripts.research.golden_review import (
    evaluate_adjudication,
    load_adjudication,
    read_json_without_duplicates,
)

from ontolib.decomposition.proposal_registry import load_proposal_registry

if TYPE_CHECKING:
    from scripts.research.golden_review import AdjudicationArtifact

_GOLDEN = Path(__file__).with_name("golden")
_ADJUDICATION_PATH = _GOLDEN / "neoplasm-adjudicated.json"
_ENGINE_EVIDENCE_PATH = _GOLDEN / "neoplasm-engine-evidence.json"
_CORPUS_COMPARISON_PATH = _GOLDEN / "neoplasm-corpus-comparison.json"
_PROPOSAL_REGISTRY_PATH = _GOLDEN / "proposal-registry.json"


def _mapping(value: object, label: str) -> dict[str, Any]:
    """Narrow one report node to a mapping, failing loudly if its shape changed."""
    assert isinstance(value, dict), f"{label} must be a mapping, got {type(value)}"
    return value


@pytest.fixture(scope="module")
def m1_artifact() -> AdjudicationArtifact:
    """Load the tracked oracle bound to its tracked proposal registry."""
    return load_adjudication(
        _ADJUDICATION_PATH,
        load_proposal_registry(_PROPOSAL_REGISTRY_PATH),
    )


@pytest.fixture(scope="module")
def m1_report(m1_artifact: AdjudicationArtifact) -> dict[str, object]:
    """Score the tracked oracle against the tracked engine and corpus evidence."""
    return evaluate_adjudication(
        m1_artifact,
        read_json_without_duplicates(_ENGINE_EVIDENCE_PATH),
        read_json_without_duplicates(_CORPUS_COMPARISON_PATH),
    )


@pytest.mark.unit
def test_tracked_oracle_declares_sme_adjudicated_status(
    m1_artifact: AdjudicationArtifact,
) -> None:
    """The tracked artifact is human truth, not an automated draft."""
    raw = _mapping(read_json_without_duplicates(_ADJUDICATION_PATH), "artifact")
    assert _mapping(raw["_meta"], "_meta")["status"] == "SME-ADJUDICATED"
    assert m1_artifact.meta.status == "SME-ADJUDICATED"


@pytest.mark.unit
def test_tracked_oracle_adjudicates_twenty_concepts(
    m1_artifact: AdjudicationArtifact,
) -> None:
    """The M1 cohort is 20 adjudicated concepts."""
    assert len(m1_artifact.concepts) == 20


@pytest.mark.unit
def test_tracked_oracle_records_154_expected_constituents(
    m1_artifact: AdjudicationArtifact,
) -> None:
    """The SME expected 154 constituents across the concepts carrying expectations."""
    total = sum(
        len(concept.expected.constituents)
        for concept in m1_artifact.concepts
        if concept.expected is not None
    )
    assert total == 154


@pytest.mark.unit
def test_ncit_bound_precision_and_recall_hold_the_m1_baseline(
    m1_report: dict[str, object],
) -> None:
    """The D59 strict-denominator view scores 0.7547 precision / 0.5229 recall."""
    ncit_bound = _mapping(
        _mapping(m1_report["pair_micro"], "pair_micro")["ncit_bound"],
        "pair_micro.ncit_bound",
    )
    assert round(ncit_bound["precision"], 4) == 0.7547
    assert round(ncit_bound["recall"], 4) == 0.5229


@pytest.mark.unit
def test_expected_pair_provenance_holds_the_m1_baseline(
    m1_report: dict[str, object],
) -> None:
    """153 expected pairs are NCIt 26.07d-bound; exactly one is locally approved."""
    assert m1_report["expected_pair_provenance"] == {
        "locally-approved": 1,
        "ncit-26.07d": 153,
    }


@pytest.mark.unit
def test_group_partition_agreement_holds_the_m1_baseline(
    m1_report: dict[str, object],
) -> None:
    """Relationship-group partitions agree on 2 of the 20 concepts (see #274)."""
    assert m1_report["group_partition_agreement"] == {
        "concepts_agree": 2,
        "concepts_disagree": 18,
    }


@pytest.mark.unit
def test_residual_comparison_holds_the_m1_baseline(
    m1_report: dict[str, object],
) -> None:
    """Residual pre-coordination is 18/18 and 13/13 — a 0.0 delta, never averaged."""
    residual = _mapping(m1_report["residual_comparison"], "residual_comparison")
    adjudication = _mapping(
        residual["adjudication"], "residual_comparison.adjudication"
    )
    assert adjudication["count"] == 18
    assert adjudication["denominator"] == 18
    assert adjudication["rate"] == 1.0

    corpus_sample = _mapping(
        residual["corpus_sample"], "residual_comparison.corpus_sample"
    )
    assert corpus_sample["count"] == 13
    assert corpus_sample["denominator"] == 13
    assert corpus_sample["rate"] == 1.0

    assert residual["absolute_rate_delta"] == 0.0
    assert residual["rates_averaged"] is False
