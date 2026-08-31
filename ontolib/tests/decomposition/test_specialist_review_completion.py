from __future__ import annotations

import pytest
from pydantic import ValidationError
from scripts.research.specialist_review_packets import (
    ClinicalPairAssessment,
    ClinicalStageA,
    HumanAttestation,
    OntologyStageB,
    PairDisposition,
    PartitionDisposition,
    _normalized_utf8,
    _response_regions,
    validate_completion,
)

pytestmark = pytest.mark.unit


def _attestation(role: str) -> HumanAttestation:
    return HumanAttestation.model_validate(
        {
            "role": role,
            "attester_name": "Human Reviewer",
            "attester_capacity": "Pathologist",
            "attestation_date": "2026-08-31",
            "conflict_of_interest": "None declared",
            "source_confirmation": "Sources independently checked.",
            "human_attestation": True,
        }
    )


def _stage_a() -> ClinicalStageA:
    return ClinicalStageA(
        attestation=_attestation("clinical"),
        assessments=(
            ClinicalPairAssessment(
                pair_id="P1",
                status="CLASSIFICATION-DEPENDENT",
                citations=("SOURCE-1",),
                rationale="Applies under one named classification.",
            ),
        ),
        clinical_stage="SUFFICIENT-FOR-ONTOLOGY-REVIEW",
        blocker=None,
    )


def _resolved(*, groups: tuple[tuple[str, ...], ...]) -> OntologyStageB:
    return OntologyStageB(
        attestation=_attestation("ontology"),
        row_outcome="RESOLVED",
        dispositions=(
            PairDisposition(
                pair_id="P1",
                action="PROMOTE-SCOREABLE",
                rationale="Include this scoreable pair.",
            ),
        ),
        partition=PartitionDisposition(
            mode="CUSTOM-CURRENT-MODEL",
            groups=groups,
            rationale="Exact final cover.",
        ),
        blocker=None,
    )


def test_resolved_completion_requires_exact_post_action_partition_cover() -> None:
    assert validate_completion(
        _stage_a(),
        _resolved(groups=(("P1", "P2"),)),
        clinically_asked_pairs=("P1",),
        action_pairs=("P1",),
        allowed_actions_by_pair={"P1": ("PROMOTE-SCOREABLE",)},
        baseline_scoreable_pairs=("P2",),
    )
    with pytest.raises(ValueError, match="exactly cover"):
        validate_completion(
            _stage_a(),
            _resolved(groups=(("P1",),)),
            clinically_asked_pairs=("P1",),
            action_pairs=("P1",),
            allowed_actions_by_pair={"P1": ("PROMOTE-SCOREABLE",)},
            baseline_scoreable_pairs=("P2",),
        )


@pytest.mark.parametrize(
    "groups",
    [
        (("P1", "P2", "P3"),),
        (("P1", "P2", "P4"),),
        (("P1", "P2"), ("P2",)),
    ],
    ids=("removed-pair", "engineering-pair", "duplicate-pair"),
)
def test_custom_partition_rejects_removed_engineering_and_duplicate_ids(
    groups: tuple[tuple[str, ...], ...],
) -> None:
    with pytest.raises((ValueError, ValidationError)):
        validate_completion(
            _stage_a(),
            _resolved(groups=groups),
            clinically_asked_pairs=("P1",),
            action_pairs=("P1",),
            engineering_only_pairs=("P4",),
            allowed_actions_by_pair={"P1": ("PROMOTE-SCOREABLE",)},
            baseline_scoreable_pairs=("P2",),
        )


def test_stage_a_deferred_is_empty_and_stage_b_deferred_is_nonterminal() -> None:
    deferred_a = ClinicalStageA(
        attestation=_attestation("clinical"),
        assessments=(),
        clinical_stage="DEFERRED",
        blocker="Evidence conflict.",
    )
    deferred_b = OntologyStageB(
        attestation=_attestation("ontology"),
        row_outcome="DEFERRED",
        dispositions=(),
        partition=None,
        blocker="Stage A is nonterminal.",
        blocker_source="Stage A",
        next_action="Resolve the cited conflict.",
    )
    assert validate_completion(
        deferred_a,
        deferred_b,
        clinically_asked_pairs=("P1",),
        action_pairs=("P1",),
    )


def test_deferred_stage_b_rejects_nonempty_pair_or_partition_fields() -> None:
    with pytest.raises(ValidationError, match="cannot contain pair or partition"):
        OntologyStageB(
            attestation=_attestation("ontology"),
            row_outcome="DEFERRED",
            dispositions=(
                PairDisposition(
                    pair_id="P1",
                    action="RETAIN-SCOREABLE",
                    rationale="Impermissible terminal answer.",
                ),
            ),
            partition=None,
            blocker="Blocked.",
            blocker_source="Evidence",
            next_action="Resolve.",
        )


def test_normalization_accepts_utf8_bom_crlf_and_preserves_pipes_and_multiline() -> (
    None
):
    payload = b"\xef\xbb\xbfline | one\r\nline two\r\n"
    assert _normalized_utf8(payload) == "line | one\nline two\n"


def test_response_parser_rejects_duplicate_and_nested_visible_markers() -> None:
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
