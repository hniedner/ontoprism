# ruff: noqa: E501
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Literal

import pytest
from pydantic import ValidationError
from scripts.research.specialist_review_packets import (
    ClinicalPairAssessment,
    ClinicalStageA,
    IndexedPairContract,
    OntologyPairDecision,
    OntologyStageB,
    PacketIndexEntry,
    _response_regions,
    validate_completion,
    validate_specialist_review_row,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _stage_a(
    *,
    status: Literal[
        "UNIVERSAL-DEFINING",
        "UNIVERSAL-NONDEFINING",
        "CHARACTERISTIC-NONUNIVERSAL",
        "CLASSIFICATION-DEPENDENT",
        "INAPPLICABLE",
        "UNRESOLVED",
    ] = "UNIVERSAL-DEFINING",
) -> ClinicalStageA:
    return ClinicalStageA(
        reviewer_name="Human Reviewer",
        specialty="Pathology",
        review_date="2026-08-30",
        conflict_of_interest="None declared",
        source_confirmation="Sources independently checked",
        assessments=(
            ClinicalPairAssessment(
                pair_id="P1",
                status=status,
                citations=("PMID:1",),
                rationale="Independent clinical rationale.",
            ),
        ),
        clinical_stage="SUFFICIENT-FOR-ONTOLOGY-REVIEW",
        blocker=None,
    )


def test_unresolved_stage_a_forces_whole_row_deferred() -> None:
    with pytest.raises(ValidationError, match="DEFERRED"):
        _stage_a(status="UNRESOLVED")


def test_stage_b_rejects_range_invalid_actions_and_requires_group_for_addition() -> (
    None
):
    stage_a = _stage_a()
    with pytest.raises(ValidationError, match="Extra inputs"):
        OntologyPairDecision.model_validate(
            {
                "pair_id": "P1",
                "relation": "expected-not-emitted",
                "action": "RE-AXIS",
                "target_axis": "op:StageSystem",
                "target_range_verdict": "invalid",
                "group_assignment": "G1",
                "rationale": "Not representable.",
            }
        )
    with pytest.raises(ValidationError, match="group assignment"):
        OntologyPairDecision(
            pair_id="P1",
            relation="expected-not-emitted",
            action="ADD-SCOREABLE",
            target_axis=None,
            group_assignment=None,
            rationale="Would alter the projection.",
        )
    stage_b = OntologyStageB(
        reviewer_name="Ontology Reviewer",
        review_date="2026-08-30",
        conflict_of_interest="None declared",
        row_outcome="RESOLVED",
        decisions=(
            OntologyPairDecision(
                pair_id="P1",
                relation="expected-matched-scoreable",
                action="RETAIN-SCOREABLE",
                target_axis=None,
                group_assignment=None,
                rationale="Retain current projection.",
            ),
        ),
        blocker=None,
    )
    assert validate_completion(stage_a, stage_b, clinically_asked_pairs=("P1",))


def test_engineering_only_questions_have_no_ontology_action_but_no_question_is_inert() -> (
    None
):
    stage_a = _stage_a()
    stage_b = OntologyStageB(
        reviewer_name="Ontology Reviewer",
        review_date="2026-08-30",
        conflict_of_interest="None declared",
        row_outcome="DEFERRED",
        decisions=(),
        blocker="Selector repair required before ontology action.",
        blocker_source="Issue 274",
        next_action="Regenerate after selector repair.",
    )
    assert validate_completion(
        stage_a,
        stage_b,
        clinically_asked_pairs=("P1",),
        action_pairs=(),
        engineering_only_pairs=("P1",),
    )
    with pytest.raises(ValueError, match="action-pair set"):
        validate_completion(stage_a, stage_b, clinically_asked_pairs=("P1",))


def test_whole_row_defer_is_nonterminal_with_real_action_inventory() -> None:
    deferred_a = _stage_a().model_copy(
        update={
            "clinical_stage": "DEFERRED",
            "blocker": "Controlling evidence conflicts and needs resolution.",
        }
    )
    deferred_b = OntologyStageB(
        reviewer_name="Ontology Reviewer",
        review_date="2026-08-31",
        conflict_of_interest="None declared",
        row_outcome="DEFERRED",
        decisions=(),
        blocker="Stage A is nonterminal.",
        blocker_source="Stage A review",
        next_action="Resolve the clinical evidence conflict.",
    )

    assert validate_completion(
        deferred_a,
        deferred_b,
        clinically_asked_pairs=("P1",),
        action_pairs=("P1", "P2"),
    )

    sufficient_a = _stage_a()
    assert validate_completion(
        sufficient_a,
        deferred_b.model_copy(update={"blocker": "Ontology representation blocked."}),
        clinically_asked_pairs=("P1",),
        action_pairs=("P1", "P2"),
    )


def test_completion_rejects_missing_asked_pair_and_action_on_engineering_pair() -> None:
    stage_a = _stage_a()
    stage_b = OntologyStageB(
        reviewer_name="Ontology Reviewer",
        review_date="2026-08-30",
        conflict_of_interest="None declared",
        row_outcome="RESOLVED",
        decisions=(
            OntologyPairDecision(
                pair_id="P1",
                relation="expected-matched-scoreable",
                action="RETAIN-SCOREABLE",
                target_axis=None,
                group_assignment=None,
                rationale="Retain current projection.",
            ),
        ),
        blocker=None,
    )
    with pytest.raises(ValueError, match="asked-pair set"):
        validate_completion(
            stage_a,
            stage_b,
            clinically_asked_pairs=("P1", "P2"),
            action_pairs=("P1",),
        )
    with pytest.raises(ValueError, match="engineering-only"):
        validate_completion(
            stage_a,
            stage_b,
            clinically_asked_pairs=("P1",),
            action_pairs=("P1",),
            engineering_only_pairs=("P1",),
        )


def test_indexed_reaxis_contract_rejects_reviewer_range_and_disallowed_target() -> None:
    decision = OntologyPairDecision(
        pair_id="P1",
        relation="expected-matched-scoreable",
        action="RE-AXIS",
        target_axis="op:StageSystem",
        group_assignment="G1",
        rationale="Stored target requested.",
    )
    with pytest.raises(ValueError, match="indexed allowed re-axis target"):
        validate_completion(
            _stage_a(),
            OntologyStageB(
                reviewer_name="Ontology Reviewer",
                review_date="2026-08-31",
                conflict_of_interest="None",
                row_outcome="RESOLVED",
                decisions=(decision,),
                blocker=None,
            ),
            clinically_asked_pairs=("P1",),
            action_pairs=("P1",),
            allowed_actions_by_pair={"P1": ("RE-AXIS",)},
            allowed_reaxis_targets_by_pair={"P1": ()},
        )


def test_external_return_accepts_bom_newlines_pipes_and_deferred_blank_decisions(
    tmp_path: Path,
) -> None:
    blank = """# packet
[[ONTOPRISM:STAGE-A:START]]
Reviewer name:
Specialty:
Review date (YYYY-MM-DD):
Conflict of interest:
Source confirmation:
Clinical stage (SUFFICIENT-FOR-ONTOLOGY-REVIEW, CLINICAL-COMPLETE-ENGINEERING-PENDING, or DEFERRED):
Whole-row blocker if DEFERRED:
[[ONTOPRISM:STAGE-A:END]]
[[ONTOPRISM:STAGE-A-PAIR:P1:START]]
Status:
Citations:
Rationale:
[[ONTOPRISM:STAGE-A-PAIR:P1:END]]
[[ONTOPRISM:STAGE-B:START]]
Reviewer name:
Review date (YYYY-MM-DD):
Conflict of interest:
ROW-OUTCOME (RESOLVED or DEFERRED):
Whole-row blocker if DEFERRED:
Blocker source if DEFERRED:
Next action if DEFERRED:
[[ONTOPRISM:STAGE-B:END]]
[[ONTOPRISM:STAGE-B-PAIR:P1:START]]
Action:
Target axis:
Group:
Rationale:
[[ONTOPRISM:STAGE-B-PAIR:P1:END]]
"""
    canonical = tmp_path / "dispatch"
    returns = tmp_path / "outside-workspace"
    canonical.mkdir()
    returns.mkdir()
    canonical_file = canonical / "C27262.md"
    canonical_file.write_text(blank, encoding="utf-8", newline="\n")
    entry = PacketIndexEntry(
        code="C27262",
        path="C27262.md",
        row_sha256=hashlib.sha256(blank.encode()).hexdigest(),
        row_contract_identity="c" * 64,
        asked_pair_ids=("P1",),
        action_pair_ids=("P1",),
        engineering_pair_ids=(),
        context_pair_ids=(),
        pair_contracts=(
            IndexedPairContract(
                pair_id="P1",
                relation="expected-matched-scoreable",
                review_scope="stage-a-and-stage-b",
                source_evidence_status="available",
                axis_range_verdict="valid",
                allowed_actions=("RETAIN-SCOREABLE",),
                allowed_reaxis_targets=(),
                consequence_by_action={},
            ),
        ),
        stage_a_mode="clinical-review",
        stage_b_mode="ontology-review",
        dispatch_status="dispatchable",
        withholding_reasons=(),
        row_validation_path="C27262.validation.json",
    )
    index_values = {
        "schema_version": 3,
        "ncit_version": "26.07d",
        "input_identities": {"literature.json": "a" * 64},
        "literature_context_identity": "a" * 64,
        "cadsr_usage_identity": "b" * 64,
        "suppressed_candidates_by_row": {"C27262": []},
        "packets": [entry.model_dump(mode="json")],
        "unavailable_action_classes": {},
        "registered_mint_expected_set": [],
        "index_identity": "d" * 64,
    }
    (canonical / "index.json").write_text(json.dumps(index_values), encoding="utf-8")
    completed = blank
    replacements = {
        "Reviewer name:\nSpecialty:": "Reviewer name: Human Reviewer\nSpecialty: Pathology",
        "Review date (YYYY-MM-DD):\nConflict": "Review date (YYYY-MM-DD): 2026-08-31\nConflict",
        "Conflict of interest:\nSource": "Conflict of interest: None\nSource",
        "Source confirmation:\nClinical": "Source confirmation: Sources checked | independently\nClinical",
        "Clinical stage (SUFFICIENT-FOR-ONTOLOGY-REVIEW, CLINICAL-COMPLETE-ENGINEERING-PENDING, or DEFERRED):": "Clinical stage (SUFFICIENT-FOR-ONTOLOGY-REVIEW, CLINICAL-COMPLETE-ENGINEERING-PENDING, or DEFERRED): SUFFICIENT-FOR-ONTOLOGY-REVIEW",
        "Status:\nCitations:\nRationale:": "Status: CLASSIFICATION-DEPENDENT\nCitations: SOURCE-1\nRationale: Applies under system A | not system B.\n\nSecond paragraph.",
        "[[ONTOPRISM:STAGE-B:START]]\nReviewer name:\n": "[[ONTOPRISM:STAGE-B:START]]\nReviewer name: Ontology Reviewer\n",
        "ROW-OUTCOME (RESOLVED or DEFERRED):\nWhole-row blocker if DEFERRED:": "ROW-OUTCOME (RESOLVED or DEFERRED): DEFERRED\nWhole-row blocker if DEFERRED: Representation blocked",
        "Blocker source if DEFERRED:": "Blocker source if DEFERRED: issue 274",
        "Next action if DEFERRED:": "Next action if DEFERRED: regenerate after repair",
    }
    for old, new in replacements.items():
        completed = completed.replace(old, new)
    stage_b_start = "[[ONTOPRISM:STAGE-B:START]]\n"
    stage_b_end = "[[ONTOPRISM:STAGE-B:END]]"
    before, remainder = completed.split(stage_b_start, 1)
    _old_stage_b, after = remainder.split(stage_b_end, 1)
    completed = (
        before
        + stage_b_start
        + """Reviewer name: Ontology Reviewer
Review date (YYYY-MM-DD): 2026-08-31
Conflict of interest: None declared
ROW-OUTCOME (RESOLVED or DEFERRED): DEFERRED
Whole-row blocker if DEFERRED: Representation blocked
Blocker source if DEFERRED: issue 274
Next action if DEFERRED: regenerate after repair
"""
        + stage_b_end
        + after
    )
    returned = returns / "C27262.md"
    returned.write_bytes(b"\xef\xbb\xbf" + completed.replace("\n", "\r\n").encode())
    validation = validate_specialist_review_row(
        code="C27262",
        return_path=returned,
        index_path=canonical / "index.json",
        validation_output=returns / "C27262.validation.json",
    )
    assert validation.status == "passed"
    assert validation.deferred_valid is True


def test_response_parser_rejects_duplicate_and_nested_visible_markers(
    tmp_path: Path,
) -> None:
    # Marker ambiguity must fail before semantic response parsing.
    duplicate = "[[ONTOPRISM:STAGE-A:START]]\n[[ONTOPRISM:STAGE-A:END]]\n" * 2
    with pytest.raises(ValueError, match="duplicate"):
        _response_regions(duplicate)
    nested = """[[ONTOPRISM:STAGE-A:START]]
[[ONTOPRISM:STAGE-A-PAIR:P1:START]]
[[ONTOPRISM:STAGE-A-PAIR:P1:END]]
[[ONTOPRISM:STAGE-A:END]]
"""
    with pytest.raises(ValueError, match="nested"):
        _response_regions(nested)
