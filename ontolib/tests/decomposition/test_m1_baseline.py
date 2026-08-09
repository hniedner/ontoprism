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

from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from scripts.research.golden_review import (
    ExcludedRow,
    ExpectedTriple,
    KeptRow,
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


def _sequence(value: object, label: str) -> list[Any]:
    """Narrow one evidence node to a list, failing loudly if its shape changed."""
    assert isinstance(value, list), f"{label} must be a list, got {type(value)}"
    return value


def _engine_emitted_triples() -> set[ExpectedTriple]:
    """Every `(code, axis, filler)` the recorded engine run actually emitted."""
    evidence = _mapping(
        read_json_without_duplicates(_ENGINE_EVIDENCE_PATH), "engine evidence"
    )
    return {
        ExpectedTriple(
            code=_mapping(concept, "engine concept")["code"],
            axis=_mapping(constituent, "engine constituent")["axis"],
            filler=_mapping(constituent, "engine constituent")["filler"],
        )
        for concept in _sequence(evidence["concepts"], "engine concepts")
        for constituent in _sequence(
            _mapping(concept, "engine concept")["constituents"],
            "engine constituents",
        )
    }


def _engine_constituents_per_concept() -> Counter[str]:
    """How many constituents the run emitted per concept, zeros omitted.

    Zeros are dropped rather than relying on `Counter.__eq__` eliding them: two
    adjudicated concepts have no engine constituents at all, and a comparison that
    depended on that stdlib nicety would be a false assumption waiting to break.
    The row-side counter never contains a zero by construction.
    """
    evidence = _mapping(
        read_json_without_duplicates(_ENGINE_EVIDENCE_PATH), "engine evidence"
    )
    counts: Counter[str] = Counter()
    for concept in _sequence(evidence["concepts"], "engine concepts"):
        node = _mapping(concept, "engine concept")
        emitted = len(_sequence(node["constituents"], "engine constituents"))
        if emitted:
            counts[node["code"]] = emitted
    return counts


def _row_triples(
    export: RowDecisionExport, row_type: str, sme_action: str
) -> set[ExpectedTriple]:
    """The `(code, expected axis, expected filler)` triples of one cross-tab cell.

    Only kept rows have a triple at all: `ExcludedRow` may carry a withdrawn one
    and `UnusedCandidateRow` carries none, so the filter is on the variant.
    """
    return {
        row.expected_triple
        for row in export.rows
        if isinstance(row, KeptRow)
        and row.row_type == row_type
        and row.sme_action == sme_action
    }


def _oracle_triples(artifact: AdjudicationArtifact) -> set[ExpectedTriple]:
    """Every `(code, axis, filler)` the adjudicated oracle expects."""
    return {
        ExpectedTriple(code=concept.code, axis=item.axis, filler=item.filler)
        for concept in artifact.concepts
        if concept.expected is not None
        for item in concept.expected.constituents
    }


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
    """The #57 headline: 48 of 106 engine suggestions kept as offered (45%).

    42 were revised and 16 excluded. There is no `not-needed` column here: that
    action records a *candidate* row the SME never had to fill in, and on an engine
    suggestion it is a non-decision no row can express. The cell is absent rather
    than zero, so the denominator cannot quietly grow by rows nobody adjudicated.

    `include` counts a label the reviewer wrote, and a label can be rewritten:
    relabelling the 32 `revise` rows whose expected pair the engine had in fact
    emitted, and re-signing the payload, moved this figure from 0.4528 to 0.7547
    with every assertion in this file still passing. `pair_preserved` counts an
    equality between two recorded pairs instead, so that edit cannot move it —
    which is why it is asserted here beside the labels. The two numbers differ (48
    against 80) precisely because "revised" does not mean "the pair changed".
    """
    engine_suggestion = m1_row_decisions.cross_tab().engine_suggestion

    assert engine_suggestion.include == 48
    assert engine_suggestion.revise == 42
    assert engine_suggestion.exclude == 16
    assert engine_suggestion.adjudicated == 106
    assert engine_suggestion.pair_preserved == 80

    rate = engine_suggestion.included_rate
    assert rate is not None
    assert round(rate, 4) == 0.4528


@pytest.mark.unit
def test_sme_added_constituents_hold_the_m1_baseline(
    m1_row_decisions: RowDecisionExport,
) -> None:
    """63 constituents the engine never proposed were added by the SME.

    One further candidate row was revised, 4 excluded and 15 left `not-needed`.
    """
    add_if_missing = m1_row_decisions.cross_tab().add_if_missing

    assert add_if_missing.include == 63
    assert add_if_missing.revise == 1
    assert add_if_missing.exclude == 4
    assert add_if_missing.not_needed == 15


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
    expected = _oracle_triples(m1_artifact)

    assert m1_row_decisions.expected_pairs() == expected
    assert len(expected) == 154
    assert m1_row_decisions.meta.workbook_identity == m1_artifact.meta.workbook_identity
    assert m1_row_decisions.meta.ncit_version == m1_artifact.meta.ncit_version


@pytest.mark.unit
def test_the_acceptance_denominator_is_this_engine_run_s_output(
    m1_row_decisions: RowDecisionExport,
    m1_artifact: AdjudicationArtifact,
) -> None:
    """106 is what the recorded run emitted, pair for pair — not a row count.

    The published headline divides by the number of `ENGINE SUGGESTION` rows, and
    nothing tied that number to any engine output: relabelling the 63
    `ADD IF MISSING` / `include` rows as suggestions would have moved it to 121 and
    the rate to 40%. The export now records `Engine Axis` / `Engine Filler` on every
    suggestion row, so the binding is an equality between two sets of triples rather
    than a comparison of counts: the pairs the rows say were suggested must be
    exactly the pairs `neoplasm-engine-evidence.json` says were emitted. A
    relabelled row has to invent an engine pair, and any invented pair breaks that
    equality.

    The 48 accepted suggestions must further be triples that run actually emitted,
    and the SME-added constituents must be disjoint from them — 0 of the 63 appear
    in the run's output, which is what makes the relabelling check bite rather than
    pass vacuously. `revise` rows sit in between by construction: 32 of 42 kept the
    engine's pair and 10 replaced it, so `include` is not merely "the rows whose
    pair survives".

    Every structural assertion precedes the count literal it supports. Ordered the
    other way, a `len(...) == 48` failing first hides whether the set relation still
    holds, and the relation is the claim; the literal is only its size.
    """
    emitted = _engine_emitted_triples()
    suggested = {
        ExpectedTriple(code=row.code, axis=row.engine.axis, filler=row.engine.filler)
        for row in m1_row_decisions.rows
        if row.engine is not None
    }
    suggestions_per_concept = Counter(
        row.code for row in m1_row_decisions.rows if row.row_type == "ENGINE SUGGESTION"
    )
    emitted_per_concept = _engine_constituents_per_concept()

    assert suggested == emitted
    assert suggestions_per_concept == emitted_per_concept
    assert len(emitted_per_concept) == 18
    assert sum(suggestions_per_concept.values()) == 106
    assert len(emitted) == 106

    accepted = _row_triples(m1_row_decisions, "ENGINE SUGGESTION", "include")
    assert accepted <= emitted
    assert _row_triples(m1_row_decisions, "ADD IF MISSING", "include").isdisjoint(
        emitted
    )
    assert len(_row_triples(m1_row_decisions, "ENGINE SUGGESTION", "revise") & emitted)
    assert len(accepted) == 48


@pytest.mark.unit
def test_three_withdrawn_expectations_sit_outside_the_oracle(
    m1_row_decisions: RowDecisionExport,
    m1_artifact: AdjudicationArtifact,
) -> None:
    """The rows that prove the SME *action* decides what was kept, pinned.

    Three `exclude` rows still name the pair the reviewer withdrew, and they are the
    entire evidence for the rule repeated in `ExcludedRow`, `expected_pairs()` and
    `_KEPT_SME_ACTIONS`: the presence of an expected pair never decides membership.
    Nothing pinned them. Blanking all three, or rewriting one to a fabricated pair,
    survived the whole suite once the payload was re-signed — so the docstrings
    asserted a design the data no longer had to satisfy.

    Pinned four ways: the triples themselves, so a fabricated pair fails; which of
    them the engine had suggested, so a row type cannot be swapped between an
    excluded suggestion and an excluded candidate on one concept — an edit that
    moves no count and is invisible to a per-concept cross-tab; their disjointness
    from the oracle, so the rule they demonstrate is the rule tested; and the 157
    they make with the kept set, which is the number a pair-presence filter would
    return and the reason 154 is not that number.
    """
    expected = _oracle_triples(m1_artifact)
    withdrawn = {
        triple
        for row in m1_row_decisions.rows
        if isinstance(row, ExcludedRow) and (triple := row.withdrawn_triple) is not None
    }
    withdrawn_suggestions = {
        triple
        for row in m1_row_decisions.rows
        if isinstance(row, ExcludedRow)
        and row.engine is not None
        and (triple := row.withdrawn_triple) is not None
    }

    assert withdrawn == {
        ExpectedTriple(code="C6135", axis="op:AssociatedRegion", filler="C12418"),
        ExpectedTriple(code="C4791", axis="op:AssociatedRegion", filler="C12727"),
        ExpectedTriple(code="C101539", axis="op:AssociatedRegion", filler="C12418"),
    }
    assert withdrawn_suggestions == {
        ExpectedTriple(code="C6135", axis="op:AssociatedRegion", filler="C12418")
    }
    assert withdrawn_suggestions <= _engine_emitted_triples()
    assert len(withdrawn - withdrawn_suggestions) == 2
    assert withdrawn.isdisjoint(expected)
    assert len(withdrawn) == 3
    assert len(expected | withdrawn) == 157


@pytest.mark.unit
def test_row_decisions_name_the_run_and_source_the_oracle_names(
    m1_row_decisions: RowDecisionExport,
    m1_artifact: AdjudicationArtifact,
) -> None:
    """Acceptance is a statement about one engine run, so the export names it.

    `_required_evidence` already demanded `Engine evidence identity`, `Source
    identity` and `Engine run` and then discarded all but `NCIt release`, so the
    tracked rows could be read beside any run at all.

    The reviewer block is compared too. Both artifacts read it from the same sheet
    of the same workbook, so a disagreement about who signed the review, in what
    capacity, or on what date is a forgery in one of the two files and provable as
    such without consulting the workbook. Rewriting `_meta.reviewer` in the export
    and re-signing the payload was otherwise undetectable.
    """
    assert (
        m1_row_decisions.meta.engine_evidence_identity
        == m1_artifact.meta.engine_evidence_identity
    )
    assert m1_row_decisions.meta.source_identity == m1_artifact.meta.source_identity
    assert m1_row_decisions.meta.run_id == m1_artifact.meta.run_id
    assert m1_row_decisions.meta.reviewer == m1_artifact.meta.reviewer
