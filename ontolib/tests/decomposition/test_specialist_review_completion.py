# ruff: noqa: E501
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Literal

import pytest
from pydantic import ValidationError
from scripts.research.specialist_review_packets import (
    ClinicalPairAssessment,
    ClinicalStageA,
    OntologyPairDecision,
    OntologyStageB,
    PacketIndex,
    PacketIndexEntry,
    validate_completion,
    validate_specialist_review_packet_directory,
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
    with pytest.raises(ValidationError, match="range verdict"):
        OntologyPairDecision(
            pair_id="P1",
            relation="expected-not-emitted",
            action="RE-AXIS",
            target_axis="op:StageSystem",
            target_range_verdict="invalid",
            group_assignment="G1",
            rationale="Not representable.",
        )
    with pytest.raises(ValidationError, match="group assignment"):
        OntologyPairDecision(
            pair_id="P1",
            relation="expected-not-emitted",
            action="ADD-SCOREABLE",
            target_axis=None,
            target_range_verdict=None,
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
                target_range_verdict=None,
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
                target_range_verdict=None,
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


def test_completed_validator_allows_only_response_blocks_to_change(
    tmp_path: Path,
) -> None:
    blank = b"""# packet
<!-- RESPONSE-CELLS-START A -->
| Field | Response |
|---|---|
| Reviewer name |  |
| Specialty |  |
| Review date (YYYY-MM-DD) |  |
| Conflict of interest |  |
| Source confirmation |  |
| Clinical stage (SUFFICIENT-FOR-ONTOLOGY-REVIEW or DEFERRED) |  |
| Whole-row blocker if DEFERRED |  |
| Pair | Status | Citations | Rationale |
|---|---|---|---|
| P1 |  |  |  |
<!-- RESPONSE-CELLS-END A -->
<!-- RESPONSE-CELLS-START B -->
| Field | Response |
|---|---|
| Reviewer name |  |
| Review date (YYYY-MM-DD) |  |
| Conflict of interest |  |
| Row outcome (RESOLVED or DEFERRED) |  |
| Whole-row blocker if DEFERRED |  |
| Pair | Relation | Action | Target axis | Target range | Group | Rationale |
|---|---|---|---|---|---|---|
| P1 | expected-matched-scoreable |  |  |  |  |  |
<!-- RESPONSE-CELLS-END B -->"""
    packet = tmp_path / "C27262.md"
    packet.write_bytes(blank)
    index = PacketIndex(
        schema_version=2,
        ncit_version="26.07d",
        literature_context_identity="a" * 64,
        cadsr_usage_identity="b" * 64,
        input_identities={"literature": "a" * 64},
        suppressed_candidates_by_row={"C27262": ()},
        packets=(
            PacketIndexEntry(
                code="C27262",
                path="C27262.md",
                row_sha256=hashlib.sha256(blank).hexdigest(),
                row_contract_identity="c" * 64,
                asked_pair_ids=("P1",),
                action_pair_ids=("P1",),
                engineering_pair_ids=(),
                context_pair_ids=(),
            ),
        ),
        index_identity="d" * 64,
    )
    (tmp_path / "index.json").write_text(index.model_dump_json(), encoding="utf-8")
    completed = blank.decode()
    replacements = {
        "| Reviewer name |  |": "| Reviewer name | Human Reviewer |",
        "| Specialty |  |": "| Specialty | Pathology |",
        "| Review date (YYYY-MM-DD) |  |": "| Review date (YYYY-MM-DD) | 2026-08-30 |",
        "| Conflict of interest |  |": "| Conflict of interest | None declared |",
        "| Source confirmation |  |": "| Source confirmation | Sources independently checked |",
        "| Clinical stage (SUFFICIENT-FOR-ONTOLOGY-REVIEW or DEFERRED) |  |": "| Clinical stage (SUFFICIENT-FOR-ONTOLOGY-REVIEW or DEFERRED) | SUFFICIENT-FOR-ONTOLOGY-REVIEW |",
        "| Row outcome (RESOLVED or DEFERRED) |  |": "| Row outcome (RESOLVED or DEFERRED) | RESOLVED |",
        "| P1 |  |  |  |": "| P1 | UNIVERSAL-DEFINING | PMID:1 | Independent clinical rationale. |",
        "| P1 | expected-matched-scoreable |  |  |  |  |  |": "| P1 | expected-matched-scoreable | RETAIN-SCOREABLE |  |  |  | Retain current projection. |",
    }
    for old, new in replacements.items():
        completed = completed.replace(old, new)
    packet.write_text(completed, encoding="utf-8")

    validation = validate_specialist_review_packet_directory(tmp_path)

    assert validation.model_dump() == {
        "status": "passed",
        "completed_codes": ("C27262",),
        "ontology_writes": False,
        "readiness": False,
    }

    packet.write_text(
        completed.replace("# packet", "# altered context"), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="outside response cells"):
        validate_specialist_review_packet_directory(tmp_path)
