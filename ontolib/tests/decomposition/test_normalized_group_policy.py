from __future__ import annotations

from pathlib import Path

import pytest
from scripts.research.normalized_group_policy import (
    generate_active_normalized_group_policy,
)

from ontolib.decomposition.models import Constituent, Decomposition
from ontolib.decomposition.normalized_group_policy import (
    DETERMINISTIC_SOURCE_CODES,
    PAIR_ONLY_CODES,
    REVIEWED_STAGE_CODES,
    ActiveNormalizedGroupPolicy,
    CurrentDecision,
    NormalizedGroupPolicyRow,
    PolicyBlock,
    apply_normalized_group_policy,
    load_packaged_normalized_group_policy,
)

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).parents[3]
_GOLDEN = Path(__file__).with_name("golden")


def _generate(tmp_path: Path):
    return generate_active_normalized_group_policy(
        evidence_path=_GOLDEN / "neoplasm-current-engine-evidence.json",
        comparison_path=_GOLDEN / "neoplasm-current-comparison.json",
        packet_path=_ROOT / "evidence/group-review-packet-26.07d-schema3.json",
        historical_packet_path=(
            _ROOT / "evidence/group-review-packet-26.07d-schema3.json"
        ),
        rationale_markdown_path=(_ROOT / "evidence/group-review-rationale-26.07d.md"),
        rationale_sidecar_path=(_ROOT / "evidence/group-review-rationale-26.07d.json"),
        output=tmp_path / "normalized-group-policy.json",
    )


def test_c27262_source_evidence_partitions_each_final_axis_without_conflation(
    tmp_path: Path,
) -> None:
    policy = _generate(tmp_path)
    row = policy.by_code["C27262"]

    assert row.rule_kind == "source-evidence-grouping"
    assert "C27262" in DETERMINISTIC_SOURCE_CODES
    assert row.output_partition == (
        (("op:CellType", "C41063"),),
        (("op:ClinicalFinding", "C36220"), ("op:ClinicalFinding", "C41397")),
        (("op:Morphology", "C35501"), ("op:Morphology", "C9290")),
        (("op:NormalTissueOrigin", "C13051"),),
        (("op:PrimarySite", "C12431"),),
    )
    clinical = row.block_for(("op:ClinicalFinding", "C36220"))
    morphology = row.block_for(("op:Morphology", "C35501"))
    primary_site = row.block_for(("op:PrimarySite", "C12431"))
    assert clinical.normalized_group_id != morphology.normalized_group_id
    assert primary_site.normalized_group_id is None
    assert clinical.source_group_ids == (
        "031618fa9a721f3a35606fd9875ffffc92f2c9b8c7ed315e819ed1c58a7061b6",
    )
    assert morphology.occurrence_availability == "not-applicable-genus-fact"
    assert morphology.source_occurrence_ids == ()
    assert morphology.source_fact_ids


def test_c102870_groups_the_exact_genus_morphologies_without_occurrences(
    tmp_path: Path,
) -> None:
    row = _generate(tmp_path).by_code["C102870"]

    morphology = row.block_for(("op:Morphology", "C121619"))
    assert morphology.pairs == (
        ("op:Morphology", "C121619"),
        ("op:Morphology", "C39986"),
    )
    assert morphology.occurrence_availability == "not-applicable-genus-fact"
    assert morphology.source_occurrence_ids == ()
    assert len(morphology.source_fact_ids) == 2
    assert morphology.source_coordinates
    assert all(
        block.normalized_group_id != morphology.normalized_group_id
        for block in row.blocks
        if block is not morphology and block.normalized_group_id is not None
    )


def test_c100051_and_c4791_apply_source_evidence_overmerge_corrections(
    tmp_path: Path,
) -> None:
    policy = _generate(tmp_path)

    expected = {
        "C100051": {
            ("op:AssociatedRegion", "C12413"),
            ("op:AssociatedRegion", "C49274"),
        },
        "C4791": {
            ("op:AssociatedRegion", "C12905"),
            ("op:AssociatedRegion", "C13004"),
        },
    }
    for code, pairs in expected.items():
        row = policy.by_code[code]
        assert row.input_diagnosis == "over-merge"
        assert row.rule_kind == "source-evidence-grouping"
        assert all(row.block_for(pair).normalized_group_id is None for pair in pairs)
        assert len({row.block_for(pair).pairs for pair in pairs}) == 2


def test_active_policy_has_exact_15_rows_and_preserves_decision_history(
    tmp_path: Path,
) -> None:
    policy = _generate(tmp_path)

    assert set(policy.by_code) == DETERMINISTIC_SOURCE_CODES | REVIEWED_STAGE_CODES
    assert len(policy.rows) == 15
    assert set(policy.pair_only_codes) == PAIR_ONLY_CODES
    assert not (set(policy.by_code) & PAIR_ONLY_CODES)
    assert {
        row.concept_code
        for row in policy.rows
        if row.rule_kind == "source-evidence-grouping"
    } == DETERMINISTIC_SOURCE_CODES
    assert {
        row.concept_code
        for row in policy.rows
        if row.rule_kind == "reviewed-regrouping"
    } == REVIEWED_STAGE_CODES
    assert {
        row.concept_code
        for row in policy.rows
        if row.historical_decision.decision == "Approve intentional normalization"
    } == {"C181564", "C186620", "C162226"}
    assert {
        row.concept_code
        for row in policy.rows
        if row.current_decision is not None
        and row.current_decision.decision == "activate-reviewed-normalized-group-policy"
    } == REVIEWED_STAGE_CODES - {"C181564", "C186620", "C162226"}
    assert all(
        row.current_decision is not None
        and row.current_decision.authority_identifier
        == "project-owner-current-conversation"
        and row.current_decision.decision_date == "2026-09-14"
        for row in policy.rows
        if row.concept_code in {"C27262", "C102870"}
    )
    assert all(
        row.historical_decision.rationale
        and row.historical_decision.reviewer == "R. Hannes Niedner, M.D."
        and row.historical_decision.review_date == "2026-08-28"
        for row in policy.rows
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("blocks", "blocks differ"),
        ("pair-set", "changes the pair set"),
        ("diagnosis", "diagnosis differs"),
        ("rule-kind", "rule kind differs"),
        ("identity", "row identity differs"),
    ],
)
def test_policy_rows_fail_closed_when_governed_invariants_drift(
    mutation: str, message: str
) -> None:
    row = load_packaged_normalized_group_policy().rows[0]
    payload = row.model_dump()
    if mutation == "blocks":
        payload["blocks"] = payload["blocks"][1:]
    elif mutation == "pair-set":
        payload["input_partition"] = payload["input_partition"][1:]
    elif mutation == "diagnosis":
        payload["input_diagnosis"] = (
            "over-split" if payload["input_diagnosis"] == "over-merge" else "over-merge"
        )
    elif mutation == "rule-kind":
        payload["rule_kind"] = (
            "reviewed-regrouping"
            if payload["rule_kind"] == "source-evidence-grouping"
            else "source-evidence-grouping"
        )
    else:
        payload["row_identity"] = "0" * 64

    with pytest.raises(ValueError, match=message):
        NormalizedGroupPolicyRow.model_validate(payload)


def test_policy_block_and_decision_identities_fail_closed() -> None:
    policy = load_packaged_normalized_group_policy()
    block = next(
        block for row in policy.rows for block in row.blocks if len(block.pairs) > 1
    )
    block_payload = block.model_dump()
    block_payload["normalized_group_id"] = None
    with pytest.raises(ValueError, match="presence differs"):
        PolicyBlock.model_validate(block_payload)

    singleton = next(
        block for row in policy.rows for block in row.blocks if len(block.pairs) == 1
    )
    singleton_payload = singleton.model_dump()
    singleton_payload["normalized_group_id"] = "0" * 64
    with pytest.raises(ValueError, match="presence differs"):
        PolicyBlock.model_validate(singleton_payload)

    decision = next(row.current_decision for row in policy.rows if row.current_decision)
    decision_payload = decision.model_dump()
    decision_payload["decision_identity"] = "0" * 64
    with pytest.raises(ValueError, match="decision identity differs"):
        CurrentDecision.model_validate(decision_payload)


def test_genus_blocks_require_fact_evidence_without_occurrences() -> None:
    policy = load_packaged_normalized_group_policy()
    block = next(
        item
        for item in policy.by_code["C102870"].blocks
        if item.occurrence_availability == "not-applicable-genus-fact"
    )

    with_occurrence = block.model_dump()
    with_occurrence["source_occurrence_ids"] = ("occurrence",)
    with pytest.raises(ValueError, match="cannot carry occurrence IDs"):
        PolicyBlock.model_validate(with_occurrence)

    without_fact = block.model_dump()
    without_fact["source_fact_ids"] = ()
    with pytest.raises(ValueError, match="requires fact evidence"):
        PolicyBlock.model_validate(without_fact)


def test_policy_pair_lookup_and_source_axis_grouping_fail_closed() -> None:
    policy = load_packaged_normalized_group_policy()
    source_row = policy.by_code["C27262"]
    with pytest.raises(ValueError, match="has 0 blocks"):
        source_row.block_for(("op:UnknownAxis", "C1"))

    payload = source_row.model_dump()
    block = next(item for item in payload["blocks"] if len(item["pairs"]) > 1)
    original = block["pairs"][0]
    changed = ("op:DifferentAxis", original[1])
    block["pairs"] = (changed, *block["pairs"][1:])
    payload["output_partition"] = tuple(
        tuple(changed if pair == original else pair for pair in item)
        for item in payload["output_partition"]
    )
    payload["input_partition"] = tuple(
        tuple(changed if pair == original else pair for pair in item)
        for item in payload["input_partition"]
    )
    payload["diagnosis_pairs"] = tuple(
        changed if pair == original else pair for pair in payload["diagnosis_pairs"]
    )
    with pytest.raises(ValueError, match="spans final axes"):
        NormalizedGroupPolicyRow.model_validate(payload)


def test_runtime_policy_refuses_pair_and_evidence_drift() -> None:
    policy = load_packaged_normalized_group_policy()
    unknown = Decomposition(code="C1", semantic_type="Neoplastic Process")
    assert apply_normalized_group_policy(unknown, policy) is unknown

    wrong_pairs = Decomposition(
        code="C27262",
        semantic_type="Neoplastic Process",
        constituents=(
            Constituent(axis="op:UnknownAxis", filler_code="C1", axis_source="nlp"),
        ),
    )
    with pytest.raises(ValueError, match="pair set differs"):
        apply_normalized_group_policy(wrong_pairs, policy)

    row = policy.by_code["C27262"]
    wrong_evidence = Decomposition(
        code=row.concept_code,
        semantic_type="Neoplastic Process",
        constituents=tuple(
            Constituent(axis=axis, filler_code=filler, axis_source="nlp")
            for block in row.input_partition
            for axis, filler in block
        ),
    )
    with pytest.raises(ValueError, match="source evidence differs"):
        apply_normalized_group_policy(wrong_evidence, policy)


def test_policy_rejects_duplicate_concepts_and_wrong_identity() -> None:
    policy = load_packaged_normalized_group_policy()
    duplicate = policy.model_dump()
    duplicate["rows"] = (
        duplicate["rows"][0],
        duplicate["rows"][0],
        *duplicate["rows"][2:],
    )
    with pytest.raises(ValueError, match="concept set differs"):
        ActiveNormalizedGroupPolicy.model_validate(duplicate)

    wrong_exclusions = policy.model_dump()
    wrong_exclusions["pair_only_codes"] = ()
    with pytest.raises(ValueError, match="pair-only exclusion set differs"):
        ActiveNormalizedGroupPolicy.model_validate(wrong_exclusions)

    wrong_identity = policy.model_dump()
    wrong_identity["policy_identity"] = "0" * 64
    with pytest.raises(ValueError, match="policy identity differs"):
        ActiveNormalizedGroupPolicy.model_validate(wrong_identity)
