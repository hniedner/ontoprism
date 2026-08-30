# ruff: noqa: E501
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

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


def _stage_a(*, status: str = "UNIVERSAL-DEFINING") -> ClinicalStageA:
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
        engineering_only_pairs=("P1",),
    )
    with pytest.raises(ValueError, match="inert clinical question"):
        validate_completion(stage_a, stage_b, clinically_asked_pairs=("P1",))


def test_completed_validator_allows_only_response_blocks_to_change(
    tmp_path: Path,
) -> None:
    blank = (
        b'# packet\n```STAGE-A-RESPONSE\n{"blank": true}\n```\n'
        b'```STAGE-B-RESPONSE\n{"blank": true}\n```\n'
    )
    packet = tmp_path / "C27262.md"
    packet.write_bytes(blank)
    index = PacketIndex(
        schema_version=1,
        ncit_version="26.07d",
        literature_context_identity="a" * 64,
        input_identities={"literature": "a" * 64},
        packets=(
            PacketIndexEntry(
                code="C27262",
                path="C27262.md",
                sha256=hashlib.sha256(blank).hexdigest(),
            ),
        ),
    )
    (tmp_path / "index.json").write_text(index.model_dump_json(), encoding="utf-8")
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
    completed = (
        blank.decode()
        .replace('{"blank": true}', json.dumps(stage_a.model_dump(mode="json")), 1)
        .replace('{"blank": true}', json.dumps(stage_b.model_dump(mode="json")), 1)
    )
    packet.write_text(completed, encoding="utf-8")

    validation = validate_specialist_review_packet_directory(tmp_path)

    assert validation.model_dump() == {
        "valid": True,
        "completed_codes": ("C27262",),
        "writes_performed": False,
    }
