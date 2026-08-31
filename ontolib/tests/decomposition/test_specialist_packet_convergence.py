from __future__ import annotations

from importlib import import_module
from typing import cast

import pytest
from scripts.research.specialist_review_packets import (
    ActionConsequence,
    IndexedPairContract,
    PacketIndex,
    PacketIndexEntry,
    PairKey,
    PairScopeStatus,
    Relation,
    ReviewScope,
    SpecialistPair,
    SpecialistRowPacket,
    filter_governed_pairs,
)

pytestmark = pytest.mark.unit
module = import_module("scripts.research.specialist_review_packets")


def _pair(identifier: str, scope: ReviewScope) -> SpecialistPair:
    verdict = {
        "stage-a-clinical-only": "clinical-only",
        "stage-a-and-stage-b": "actionable",
        "engineering-only": "engineering-only",
        "context-not-under-review": "context",
    }[scope]
    return SpecialistPair(
        pair_id=identifier,
        key=PairKey(axis="op:Morphology", filler=f"C{identifier[1:] or '1'}"),
        relation="expected-matched-scoreable",
        scope_verdict=cast("PairScopeStatus", verdict),
        review_scope=scope,
        scope_reason="Explicit contract reason.",
        contested=True,
        filler_label="Exact filler label",
        filler_definition="Exact P97 definition",
        source_role_code="R105",
        source_role_label="Disease Has Normal Cell Origin",
        source_role_definition="Exact role definition",
        source_occurrences=(),
        current_projection_status="scoreable-release-bound",
        axis_range_verdict="valid",
        modality="inherited",
        governance="active axis contract",
        fallback="No fallback used.",
    )


def test_row_contract_partitions_every_pair_once_and_resolves_semantic_questions() -> (
    None
):
    row = SpecialistRowPacket(
        code="C102870",
        label="Ovarian Non-Dysgerminomatous Germ Cell Tumor",
        definition="Definition",
        pairs=(
            _pair("P1", "stage-a-clinical-only"),
            _pair("P2", "stage-a-and-stage-b"),
            _pair("P3", "engineering-only"),
            _pair("P4", "context-not-under-review"),
        ),
        question_pair_keys=(
            (PairKey(axis="op:Morphology", filler="C1"),),
            (PairKey(axis="op:Morphology", filler="C2"),),
        ),
        engineering_blockers={
            "P1": "#274 selector owner; queued; regenerate after repair",
            "P3": "#271 range owner; blocked; rerun range gate",
        },
    )
    assert row.asked_pair_ids == ("P1", "P2")
    assert row.action_pair_ids == ("P2",)
    assert row.engineering_pair_ids == ("P1", "P3")
    assert row.context_pair_ids == ("P4",)
    assert row.resolved_question_pair_ids == (("P1",), ("P2",))

    with pytest.raises(ValueError, match="unknown semantic pair"):
        row.model_copy(
            update={
                "question_pair_keys": ((PairKey(axis="op:StageValue", filler="C999"),),)
            }
        ).model_validate(
            row.model_copy(
                update={
                    "question_pair_keys": (
                        (PairKey(axis="op:StageValue", filler="C999"),),
                    )
                }
            ).model_dump()
        )


def test_index_schema_three_binds_complete_pair_contracts_and_dispatch() -> None:
    assert PacketIndex.model_fields["schema_version"].default == 3
    assert {
        "row_contract_identity",
        "asked_pair_ids",
        "action_pair_ids",
        "engineering_pair_ids",
        "context_pair_ids",
        "row_sha256",
    } <= set(PacketIndexEntry.model_fields)
    assert "index_identity" in PacketIndex.model_fields
    assert "cadsr_usage_identity" in PacketIndex.model_fields
    assert {
        "pair_contracts",
        "dispatch_status",
        "withholding_reasons",
        "row_validation_path",
    } <= set(PacketIndexEntry.model_fields)
    assert {
        "pair_id",
        "relation",
        "review_scope",
        "source_evidence_status",
        "axis_range_verdict",
        "allowed_actions",
        "citation_ids",
        "consequence_by_action",
    } <= set(IndexedPairContract.model_fields)
    assert {
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
    } <= set(ActionConsequence.model_fields)


def test_no_legacy_dead_packet_models_remain() -> None:
    assert not hasattr(module, "RowPacket")
    assert not hasattr(module, "PairEvidence")
    assert not hasattr(module, "OntologyPairDecision")


def test_mint_gate_suppresses_unregistered_and_ineligible_before_numbering() -> None:
    relations: tuple[tuple[tuple[str, str], Relation], ...] = (
        (("op:Morphology", "MINT-111111111111"), "current-only-proposed"),
        (("op:Morphology", "MINT-222222222222"), "current-only-proposed"),
        (("op:Morphology", "MINT-333333333333"), "current-only-proposed"),
        (("op:Morphology", "C39986"), "expected-matched-scoreable"),
    )
    visible, suppressed, registered_visible = filter_governed_pairs(
        relations=relations,
        registered_mints={"MINT-222222222222", "MINT-333333333333"},
        range_status={
            ("op:Morphology", "MINT-222222222222"): "valid",
            ("op:Morphology", "MINT-333333333333"): "invalid",
        },
    )
    assert tuple(pair for pair, _relation in visible) == (
        ("op:Morphology", "MINT-222222222222"),
        ("op:Morphology", "C39986"),
    )
    suppressed_contract = tuple(
        (item.axis, item.generated_id, item.reason) for item in suppressed
    )
    assert suppressed_contract == (
        ("op:Morphology", "MINT-111111111111", "unregistered"),
        ("op:Morphology", "MINT-333333333333", "range-ineligible"),
    )
    assert registered_visible == ("MINT-222222222222",)


def test_row_rejects_an_asked_pair_without_a_curated_question() -> None:
    with pytest.raises(ValueError, match="curated question set"):
        SpecialistRowPacket(
            code="C102870",
            label="Ovarian Non-Dysgerminomatous Germ Cell Tumor",
            definition="Definition",
            pairs=(_pair("P1", "stage-a-and-stage-b"),),
            question_pair_keys=(),
            engineering_blockers={},
        )


@pytest.mark.parametrize(
    ("relation", "expected_actions", "forbidden"),
    [
        (
            "expected-not-emitted",
            set(),
            {"RETAIN-SCOREABLE", "PROMOTE-SCOREABLE"},
        ),
        (
            "expected-emitted-review-bearing",
            {"PROMOTE-SCOREABLE", "REMOVE-FROM-PROJECTION"},
            {"ADD-SCOREABLE", "OMIT", "RETAIN-SCOREABLE"},
        ),
    ],
)
def test_pair_consequences_render_only_relation_valid_actions(
    relation: Relation, expected_actions: set[str], forbidden: set[str]
) -> None:
    pair = _pair("P1", "stage-a-and-stage-b").model_copy(update={"relation": relation})
    rendered = module.pair_consequences(pair)
    assert expected_actions <= set(rendered)
    assert forbidden.isdisjoint(rendered)
    assert all(value.source_preserved for value in rendered.values())
    assert all(
        isinstance(value.comparison_tp_delta, int) for value in rendered.values()
    )
