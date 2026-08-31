from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError
from scripts.research.specialist_literature_context import (
    LiteratureCitation,
    LiteratureEvidenceClaim,
    LiteraturePairKey,
    citation_supports_pair,
)
from scripts.research.specialist_review_packets import (
    ActionConsequence,
    ClinicalStageA,
    HumanAttestation,
    OntologyStageB,
    PairDisposition,
    PairScopeInput,
    PairScopeVerdict,
    PartitionDisposition,
    ReturnChannel,
    classify_pair_scope,
)

pytestmark = pytest.mark.unit


def _attestation(role: str, *, human: bool = True) -> HumanAttestation:
    return HumanAttestation.model_validate(
        {
            "role": role,
            "attester_name": "Dr Human",
            "attester_capacity": "Pathologist",
            "attestation_date": "2026-08-31",
            "conflict_of_interest": "None",
            "source_confirmation": "I checked the cited passages.",
            "human_attestation": human,
        }
    )


def _scope(**updates: object) -> PairScopeInput:
    values: dict[str, object] = {
        "relation": "expected-emitted-review-bearing",
        "range_verdict": "valid",
        "source_evidence_status": "available",
        "diagnostic_classification": "emitted-review-bearing",
        "has_clinical_claim": True,
        "claim_contests_projection": True,
        "action_representable": True,
        "governance_status": "eligible",
    }
    values.update(updates)
    return PairScopeInput.model_validate(values)


def test_b1_stage_roles_use_separate_human_attestations() -> None:
    assert ClinicalStageA.model_fields["attestation"].annotation == HumanAttestation
    assert OntologyStageB.model_fields["attestation"].annotation == HumanAttestation
    with pytest.raises(ValidationError, match="human"):
        _attestation("clinical", human=False)


def test_b2_pair_disposition_has_only_three_actions_and_no_axis_or_group_fields() -> (
    None
):
    assert set(PairDisposition.model_fields) == {"pair_id", "action", "rationale"}
    annotation = str(PairDisposition.model_fields["action"].annotation)
    assert {"RETAIN-SCOREABLE", "PROMOTE-SCOREABLE", "REMOVE-FROM-PROJECTION"} <= set(
        annotation.split("'")
    )
    assert {"ADD-SCOREABLE", "OMIT", "RE-AXIS", "GROUP-TOGETHER"}.isdisjoint(
        annotation.split("'")
    )


def test_b3_partition_disposition_is_independent_and_typed() -> None:
    assert set(PartitionDisposition.model_fields) == {"mode", "groups", "rationale"}
    assert "partition" in OntologyStageB.model_fields
    assert "partition" not in PairDisposition.model_fields


def test_b4_stage_a_defer_requires_empty_assessments_blocker_and_attestation() -> None:
    with pytest.raises(
        ValidationError, match="DEFERRED Stage A requires empty assessments"
    ):
        ClinicalStageA.model_validate(
            {
                "attestation": _attestation("clinical"),
                "assessments": (
                    {
                        "pair_id": "P1",
                        "status": "UNRESOLVED",
                        "citations": ("S1",),
                        "rationale": "Blocked.",
                    },
                ),
                "clinical_stage": "DEFERRED",
                "blocker": "Evidence conflict.",
            }
        )


def test_b5_stage_b_defer_rejects_every_pair_and_partition_response() -> None:
    with pytest.raises(ValidationError, match="DEFERRED Stage B cannot contain"):
        OntologyStageB.model_validate(
            {
                "attestation": _attestation("ontology"),
                "row_outcome": "DEFERRED",
                "dispositions": (),
                "partition": {"mode": "EMPTY", "groups": (), "rationale": "No pairs."},
                "blocker": "Blocked.",
                "blocker_source": "Stage A",
                "next_action": "Resolve evidence.",
            }
        )


def test_b6_return_channel_has_exact_instruction_and_deadline() -> None:
    channel = ReturnChannel()
    assert (
        channel.instruction
        == "Return the completed file, with the same filename, as a file attachment "
        "to the OntoPrism project coordinator through the same secure channel by "
        "which this packet was received."
    )
    assert (
        channel.deadline
        == "No deadline assigned; coordinator will communicate changes."
    )


@pytest.mark.parametrize(
    ("updates", "status"),
    [
        ({"governance_status": "suppressed"}, "suppressed"),
        ({"range_verdict": "invalid"}, "engineering-only"),
        ({"source_evidence_status": "unavailable"}, "engineering-only"),
        ({"diagnostic_classification": "selection-miss"}, "clinical-only"),
        (
            {
                "has_clinical_claim": False,
                "diagnostic_classification": "selection-miss",
            },
            "engineering-only",
        ),
        ({}, "actionable"),
        ({"has_clinical_claim": False}, "context"),
        ({"action_representable": False}, "refused-invalid"),
    ],
)
def test_b7_scope_classifier_is_total_with_fail_closed_precedence(
    updates: dict[str, object], status: str
) -> None:
    assert classify_pair_scope(_scope(**updates)).status == status


def test_b8_scope_classifier_returns_exact_typed_verdict() -> None:
    assert isinstance(classify_pair_scope(_scope()), PairScopeVerdict)
    assert set(inspect.signature(classify_pair_scope).parameters) == {"scope_input"}


def test_b9_consequence_names_comparison_relative_deltas_and_readiness() -> None:
    assert set(ActionConsequence.model_fields) == {
        "comparison_tp_delta",
        "comparison_fp_delta",
        "comparison_fn_delta",
        "scoreable_emitted_delta",
        "source_preserved",
        "pair_after",
        "needs_review_after",
        "group_effect",
        "row_readiness",
        "publication",
    }


def test_b10_valid_responses_cannot_authorize_writes_or_publication() -> None:
    assert OntologyStageB.model_fields["ontology_writes"].default is False
    assert OntologyStageB.model_fields["readiness"].default is False
    assert OntologyStageB.model_fields["publication"].default is False


def _citation(
    *,
    status: str = "cited",
    passage: str = "The tumor contains spindle cells.",
    does_not: str = "No ontology action.",
) -> LiteratureCitation:
    return LiteratureCitation.model_validate(
        {
            "citation_id": "S1",
            "status": status,
            "authority_class": "peer-reviewed open-access pathology review",
            "authority_order": 1,
            "bibliography": "Source.",
            "url": "https://example.test/source",
            "doi": None,
            "pmid": None,
            "verified_on": "2026-08-31",
            "exact_locator": "Results",
            "exact_passage": passage,
            "supports": "Spindle-cell morphology is described.",
            "does_not_support": does_not,
            "limitations": "Review.",
            "conflicts_or_supersession": "None.",
        }
    )


def _claim(*, filler: str = "C1", citation_id: str = "S1") -> LiteratureEvidenceClaim:
    return LiteratureEvidenceClaim(
        question_id="Q1",
        pair_key=LiteraturePairKey(axis="op:CellType", filler=filler),
        citation_id=citation_id,
        source_fact="The passage describes spindle cells.",
    )


def test_d1_shared_citation_predicate_accepts_exact_accessible_pair_claim() -> None:
    assert citation_supports_pair(
        question_id="Q1",
        pair_key=LiteraturePairKey(axis="op:CellType", filler="C1"),
        claim=_claim(),
        citation=_citation(),
    )


def test_d2_shared_citation_predicate_rejects_wrong_pair() -> None:
    assert not citation_supports_pair(
        question_id="Q1",
        pair_key=LiteraturePairKey(axis="op:CellType", filler="C2"),
        claim=_claim(),
        citation=_citation(),
    )


def test_d3_shared_citation_predicate_rejects_restricted_source() -> None:
    assert not citation_supports_pair(
        question_id="Q1",
        pair_key=LiteraturePairKey(axis="op:CellType", filler="C1"),
        claim=_claim(),
        citation=_citation(status="access-restricted"),
    )


def test_d4_shared_citation_predicate_rejects_contradiction() -> None:
    assert not citation_supports_pair(
        question_id="Q1",
        pair_key=LiteraturePairKey(axis="op:CellType", filler="C1"),
        claim=_claim(),
        citation=_citation(does_not="Does not support spindle cells."),
    )


def test_d5_empty_partition_is_only_for_no_scoreable_pairs() -> None:
    with pytest.raises(ValidationError, match="EMPTY"):
        PartitionDisposition(mode="EMPTY", groups=(("P1",),), rationale="Wrong.")


def test_d6_custom_partition_requires_an_exact_nonempty_cover() -> None:
    with pytest.raises(ValidationError, match="groups"):
        PartitionDisposition(
            mode="CUSTOM-CURRENT-MODEL", groups=(), rationale="Missing."
        )


def test_d7_attestation_roles_are_distinct_even_for_same_person() -> None:
    clinical = _attestation("clinical")
    ontology = _attestation("ontology")
    assert clinical.attester_name == ontology.attester_name
    assert clinical.role != ontology.role


def test_d8_ai_role_name_cannot_attest() -> None:
    with pytest.raises(ValidationError, match="human"):
        _attestation("ontology").model_copy(
            update={"attester_name": "AI agent"}
        ).model_validate(
            _attestation("ontology")
            .model_copy(update={"attester_name": "AI agent"})
            .model_dump()
        )


def test_d9_unknown_scope_product_fails_instead_of_defaulting() -> None:
    with pytest.raises(ValidationError):
        PairScopeInput.model_validate(
            {**_scope().model_dump(), "diagnostic_classification": "mystery"}
        )


def test_d10_pair_action_and_partition_are_not_interchangeable() -> None:
    with pytest.raises(ValidationError):
        PairDisposition.model_validate(
            {
                "pair_id": "P1",
                "action": "GROUP-SPECIFIED-PAIRS-TOGETHER",
                "rationale": "Wrong layer.",
            }
        )
