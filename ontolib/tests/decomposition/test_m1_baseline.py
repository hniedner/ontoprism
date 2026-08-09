"""Pin the M1 decomposition baseline recorded by issue #57.

These figures are *the measurement* M1 exists to produce: the SME-adjudicated oracle
(`neoplasm-adjudicated.json`), scored against the recorded engine run
(`neoplasm-engine-evidence.json`) and the #154 residual subset
(`neoplasm-corpus-comparison.json`), plus the reviewer's row-level decisions
(`neoplasm-row-decisions.json`), which carry the acceptance rate the oracle alone
cannot express. Everything the test reads is tracked in this directory, so the
baseline reproduces from git alone — no live store, no network, no `tmp/` workbook.

The two provenances answer different questions and do not reconcile pair-for-pair: a
`revise` row replaces one pair with another, so it lands in both the wrong-pair and
the never-emitted column of the pair-level score.

A change that moves any number here is not automatically a failure, but it must be
**deliberate and stated**: say which engine or scorer change moved it, and update the
expected value in the same commit with that reason.

**Never edit the oracle, the recorded evidence, the row decisions, or the corpus
comparison to make this test pass.** The golden README states the rule directly —
*"Iterate without changing the oracle merely to match the engine."* A failure here
means something upstream changed; that is a finding to report, not a test to repair.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from scripts.research.golden_review import (
    evaluate_adjudication,
    load_adjudication,
    load_row_decisions,
    read_json_without_duplicates,
)

from ontolib.decomposition.proposal_registry import load_proposal_registry

if TYPE_CHECKING:
    from scripts.research.golden_review import AdjudicationArtifact, RowDecisionExport

_GOLDEN = Path(__file__).with_name("golden")
_ADJUDICATION_PATH = _GOLDEN / "neoplasm-adjudicated.json"
_ENGINE_EVIDENCE_PATH = _GOLDEN / "neoplasm-engine-evidence.json"
_CORPUS_COMPARISON_PATH = _GOLDEN / "neoplasm-corpus-comparison.json"
_PROPOSAL_REGISTRY_PATH = _GOLDEN / "proposal-registry.json"
_ROW_DECISIONS_PATH = _GOLDEN / "neoplasm-row-decisions.json"


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


@pytest.fixture(scope="module")
def m1_row_decisions() -> RowDecisionExport:
    """Load the tracked row-level SME decisions exported from the #57 workbook."""
    return load_row_decisions(_ROW_DECISIONS_PATH)


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
    """Residual pre-coordination is 18/18 and 13/13 — a 0.0 delta, never averaged.

    Both figures are saturated: on this evidence every decomposed concept is also
    residual, so `count == denominator` and `rate == 1.0` on both sides. These
    assertions therefore pin *the tracked numbers* and cannot, on their own, prove
    the detector filter that produced the numerator ran at all — deleting it yields
    the same 18/18. The filter's discriminating behaviour is proved separately, on
    an unsaturated fixture, by
    `test_golden_review.py::test_residual_numerator_excludes_decomposed_concepts_the_detector_cleared`.

    `rates_averaged` is not asserted here: it is a hardcoded `False` literal in the
    report and no change to the code under test can move it. `absolute_rate_delta`
    carries the same claim and can fail — an averaged pair of 1.0 rates would be
    1.0, not 0.0.
    """
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


@pytest.mark.unit
def test_row_decisions_record_every_workbook_decision_row(
    m1_row_decisions: RowDecisionExport,
) -> None:
    """189 constituent rows: 106 engine suggestions and 83 SME-added candidates."""
    assert len(m1_row_decisions.rows) == 189
    assert (
        sum(row.row_type == "ENGINE SUGGESTION" for row in m1_row_decisions.rows) == 106
    )
    assert sum(row.row_type == "ADD IF MISSING" for row in m1_row_decisions.rows) == 83


@pytest.mark.unit
def test_engine_suggestion_acceptance_holds_the_m1_baseline(
    m1_row_decisions: RowDecisionExport,
) -> None:
    """The #57 headline: 48 of 106 engine suggestions kept unchanged (45%).

    42 were revised and 16 excluded. There is no `not-needed` column here: that
    action records a *candidate* row the SME never had to fill in, and on an engine
    suggestion it is a non-decision `ConstituentRowDecision` rejects outright. The
    cell is absent rather than zero, so the denominator cannot quietly grow by rows
    nobody adjudicated.
    """
    assert m1_row_decisions.cross_tab()["ENGINE SUGGESTION"] == {
        "include": 48,
        "revise": 42,
        "exclude": 16,
    }
    assert sum(m1_row_decisions.cross_tab()["ENGINE SUGGESTION"].values()) == 106


@pytest.mark.unit
def test_sme_added_constituents_hold_the_m1_baseline(
    m1_row_decisions: RowDecisionExport,
) -> None:
    """63 constituents the engine never proposed were added by the SME.

    One further candidate row was revised, 4 excluded and 15 left `not-needed`.
    """
    assert m1_row_decisions.cross_tab()["ADD IF MISSING"] == {
        "include": 63,
        "revise": 1,
        "exclude": 4,
        "not-needed": 15,
    }


@pytest.mark.unit
def test_row_decisions_and_the_oracle_agree_on_the_expected_set(
    m1_row_decisions: RowDecisionExport,
    m1_artifact: AdjudicationArtifact,
) -> None:
    """The kept rows *are* the oracle's expected set — exactly, not merely nearly.

    The relation is a clean equality on `(code, axis, filler)`: the 111 `include`
    plus 43 `revise` rows equal the 154 expected constituents of
    `neoplasm-adjudicated.json`, with no triple on either side alone. It holds only
    when keyed on the SME action — three `exclude` rows still carry the expectation
    the reviewer withdrew, so filtering on "row has an expected pair" yields 157
    triples, three of which the oracle does not contain.

    Both artifacts also name the same workbook digest, so neither can be
    regenerated from a different review without the other failing here.
    """
    expected = {
        (concept.code, item.axis, item.filler)
        for concept in m1_artifact.concepts
        if concept.expected is not None
        for item in concept.expected.constituents
    }

    assert m1_row_decisions.expected_pairs() == expected
    assert len(expected) == 154
    assert m1_row_decisions.meta.workbook_identity == m1_artifact.meta.workbook_identity
    assert m1_row_decisions.meta.ncit_version == m1_artifact.meta.ncit_version
