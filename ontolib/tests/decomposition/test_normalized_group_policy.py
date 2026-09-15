from __future__ import annotations

from pathlib import Path

import pytest
from scripts.research.group_review_packet import generate_group_review_packet
from scripts.research.normalized_group_policy import (
    generate_active_normalized_group_policy,
)

from ontolib.decomposition.models import (
    CompleteDefinition,
    Constituent,
    Decomposition,
    DefinitionGroup,
    GenusDefinitionFact,
    canonical_definition_fact_id,
    canonical_definition_group_id,
)
from ontolib.decomposition.normalized_group_policy import (
    DETERMINISTIC_SOURCE_CODES,
    PAIR_ONLY_CODES,
    REVIEWED_STAGE_CODES,
    ActiveNormalizedGroupPolicy,
    CurrentDecision,
    NormalizedGroupPolicyRow,
    NotApplicable,
    PolicyBlock,
    SourceCoordinate,
    SourcePairEvidence,
    _constituent_evidence_identity,
    apply_normalized_group_policy,
    canonical_identity,
    load_packaged_normalized_group_policy,
    normalized_group_identity,
)

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).parents[3]
_GOLDEN = Path(__file__).with_name("golden")


def _generate(tmp_path: Path):
    current_packet = tmp_path / "current-group-review.json"
    generate_group_review_packet(
        evidence_path=_GOLDEN / "neoplasm-current-engine-evidence.json",
        comparison_path=_GOLDEN / "neoplasm-current-comparison.json",
        r101_report_path=_GOLDEN / "neoplasm-r101-v4-conservation.json.gz",
        output=current_packet,
    )
    return generate_active_normalized_group_policy(
        evidence_path=_GOLDEN / "neoplasm-current-engine-evidence.json",
        comparison_path=_GOLDEN / "neoplasm-current-comparison.json",
        packet_path=current_packet,
        historical_packet_path=(
            _ROOT / "evidence/group-review-packet-26.07d-schema3.json"
        ),
        rationale_markdown_path=(_ROOT / "evidence/group-review-rationale-26.07d.md"),
        rationale_sidecar_path=(_ROOT / "evidence/group-review-rationale-26.07d.json"),
        output=tmp_path / "normalized-group-policy.json",
    )


def test_unavailable_prechange_metadata_is_self_contained(
    tmp_path: Path,
) -> None:
    policy = _generate(tmp_path)

    assert policy.unavailable_historical_artifact.status == "not-retained"
    assert policy.unavailable_historical_artifact.expected_artifact_sha256 == (
        "4febb77cb0e0b91418a22a08c19d9fa05d65529f00af30e85afe53a8d716424d"
    )
    assert policy.unavailable_historical_artifact.reason == (
        "overwritten-before-immutable-retention"
    )
    dumped = policy.unavailable_historical_artifact.model_dump()
    assert dumped["evidentiary_use"] == "none"
    assert "durable_record_path" not in dumped
    assert "record_sha256" not in dumped
    assert "last_known_path" not in dumped


def test_policy_refuses_unavailable_prechange_output_identity_literals() -> None:
    payload = load_packaged_normalized_group_policy().model_dump()
    payload["prechange_evidence_identity"] = "4475" + "0" * 60

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        ActiveNormalizedGroupPolicy.model_validate(payload)


def test_active_rows_use_tracked_historical_observations_not_counterfactuals(
    tmp_path: Path,
) -> None:
    policy = _generate(tmp_path)
    assert all(
        row.historical_observed_partition.packet_identity
        == policy.historical_packet_identity
        for row in policy.rows
    )

    payload = policy.rows[0].model_dump()
    payload["historical_observed_partition"]["kind"] = (
        "counterfactual_diagnostic_partition"
    )
    payload["row_identity"] = canonical_identity(
        {key: value for key, value in payload.items() if key != "row_identity"}
    )
    with pytest.raises(ValueError, match="historical_observed_partition"):
        NormalizedGroupPolicyRow.model_validate(payload)


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
    assert primary_site.normalized_group_id is not None
    assert primary_site.normalized_group_label is not None
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
        assert all(
            row.block_for(pair).normalized_group_id is not None for pair in pairs
        )
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


def test_reviewed_decisions_cover_only_exact_stage_targets(tmp_path: Path) -> None:
    policy = _generate(tmp_path)
    c101539 = policy.by_code["C101539"]
    c115057 = policy.by_code["C115057"]

    assert c101539.decision_target_pair_set == (
        ("op:StageSystem", "C140961"),
        ("op:StageValue", "C27966"),
    )
    assert c115057.decision_target_pair_set == (
        ("op:StageSystem", "C90529"),
        ("op:StageSystem", "C90530"),
        ("op:StageValue", "C27966"),
    )
    assert ("op:AssociatedRegion", "C12418") not in c115057.decision_target_pair_set

    for row in (c101539, c115057):
        assert {pair for block in row.reviewed_partition for pair in block} == set(
            row.decision_target_pair_set
        )
        assert {pair for block in row.output_partition for pair in block} == {
            item.pair for item in row.source_pair_evidence
        }
        target_blocks = [
            block
            for block in row.blocks
            if set(block.pairs) <= set(row.decision_target_pair_set)
        ]
        non_target_blocks = [
            block for block in row.blocks if block not in target_blocks
        ]
        assert all(
            block.decision_regime in {"historical-approval", "current-owner-decision"}
            for block in target_blocks
        )
        assert all(
            block.decision_regime in {"source-evidence", "current-pair-preservation"}
            and block.human_decision_identity is None
            for block in non_target_blocks
        )


@pytest.mark.parametrize("mutation", ["added", "removed"])
def test_reviewed_stage_target_drift_refuses(mutation: str) -> None:
    row = load_packaged_normalized_group_policy().by_code["C101539"]
    payload = row.model_dump()
    if mutation == "added":
        payload["decision_target_pair_set"] = (
            *payload["decision_target_pair_set"],
            ("op:StageSystem", "C999"),
        )
    else:
        payload["decision_target_pair_set"] = payload["decision_target_pair_set"][:-1]
    payload["row_identity"] = canonical_identity(
        {key: value for key, value in payload.items() if key != "row_identity"}
    )
    with pytest.raises(ValueError, match="decision target pair set"):
        NormalizedGroupPolicyRow.model_validate(payload)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("blocks", "blocks differ"),
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
    singleton_payload["normalized_group_id"] = None
    singleton_payload["normalized_group_label"] = None
    with pytest.raises(ValueError, match="requires an identity"):
        PolicyBlock.model_validate(singleton_payload)

    decision = next(row.current_decision for row in policy.rows if row.current_decision)
    decision_payload = decision.model_dump()
    decision_payload["decision_identity"] = "0" * 64
    with pytest.raises(ValueError, match="decision identity differs"):
        CurrentDecision.model_validate(decision_payload)

    grouping_payload = decision.model_dump()
    grouping_payload["grouping_decision_identity"] = "0" * 64
    with pytest.raises(ValueError, match="grouping decision identity differs"):
        CurrentDecision.model_validate(grouping_payload)

    machine_block = next(
        item
        for row in policy.rows
        for item in row.blocks
        if item.machine_policy_identity is not None
    )
    missing_human = machine_block.model_dump()
    missing_human["decision_regime"] = "current-owner-decision"
    with pytest.raises(ValueError, match="human decision identity differs"):
        PolicyBlock.model_validate(missing_human)

    competing_identities = missing_human | {"human_decision_identity": "0" * 64}
    with pytest.raises(ValueError, match="machine policy identity differs"):
        PolicyBlock.model_validate(competing_identities)


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


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "pair": ("op:PrimarySite", "C1"),
                "source_fact_ids": ("fact",),
                "source_occurrence_ids": (),
                "source_coordinates": (),
                "occurrence_availability": "unavailable-current-source-coordinate",
            },
            "unavailable source evidence cannot carry citations",
        ),
        (
            {
                "pair": ("op:PrimarySite", "C1"),
                "source_fact_ids": (),
                "source_occurrence_ids": ("occurrence",),
                "source_coordinates": (),
                "occurrence_availability": "available",
            },
            "available source evidence requires facts and coordinates",
        ),
        (
            {
                "pair": ("op:Morphology", "C1"),
                "source_fact_ids": ("fact",),
                "source_occurrence_ids": ("invented-occurrence",),
                "source_coordinates": (
                    {"source_group_id": "0" * 64, "anchor_code": "C1", "depth": 0},
                ),
                "occurrence_availability": "not-applicable-genus-fact",
            },
            "genus fact grouping cannot carry occurrence IDs",
        ),
        (
            {
                "pair": ("op:PrimarySite", "C1"),
                "source_fact_ids": ("fact",),
                "source_occurrence_ids": (),
                "source_coordinates": (
                    {"source_group_id": "0" * 64, "anchor_code": "C1", "depth": 0},
                ),
                "occurrence_availability": "available",
            },
            "restriction source evidence requires occurrence IDs",
        ),
        (
            {
                "pair": ("op:PrimarySite", "C1"),
                "source_fact_ids": ("fact",),
                "source_occurrence_ids": ("invented-occurrence",),
                "source_coordinates": (
                    {"source_group_id": "0" * 64, "anchor_code": "C1", "depth": 0},
                ),
                "occurrence_availability": "available-source-fact",
            },
            "source-fact-only evidence cannot carry occurrence IDs",
        ),
    ],
)
def test_source_pair_evidence_rejects_internally_inconsistent_availability(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        SourcePairEvidence.model_validate(payload)


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
    payload["historical_observed_partition"]["partition"] = tuple(
        tuple(changed if pair == original else pair for pair in item)
        for item in payload["historical_observed_partition"]["partition"]
    )
    payload["diagnosis_pairs"] = tuple(
        changed if pair == original else pair for pair in payload["diagnosis_pairs"]
    )
    with pytest.raises(ValueError, match="spans final axes"):
        NormalizedGroupPolicyRow.model_validate(payload)


def test_source_rule_rejects_output_partition_not_computed_from_coordinates() -> None:
    row = load_packaged_normalized_group_policy().by_code["C100051"]
    payload = row.model_dump()
    affected = {
        ("op:AssociatedRegion", "C12413"),
        ("op:AssociatedRegion", "C49274"),
    }
    remaining_blocks = [
        block for block in payload["blocks"] if tuple(block["pairs"][0]) not in affected
    ]
    merged_pairs = tuple(sorted(affected))
    transformation_rules = ("specificity-collapse",)
    machine_policy_identity = canonical_identity(
        {
            "kind": "normalized-group-machine-policy",
            "concept_code": row.concept_code,
            "pairs": merged_pairs,
            "decision_regime": "source-evidence",
            "transformation_rules": transformation_rules,
            "source_evidence_identity": row.source_evidence_identity,
        }
    )
    normalized_id = normalized_group_identity(
        concept_code=row.concept_code,
        canonical_block_members=merged_pairs,
        rule_kind=row.rule_kind,
        transformation_rules=transformation_rules,
        decision_identity=machine_policy_identity,
        source_evidence_identity=row.source_evidence_identity,
        decision_regime="source-evidence",
    )
    source = [item for item in row.source_pair_evidence if item.pair in affected]
    merged = {
        "pairs": merged_pairs,
        "transformation_rules": transformation_rules,
        "decision_regime": "source-evidence",
        "human_decision_identity": None,
        "machine_policy_identity": machine_policy_identity,
        "normalized_group_id": normalized_id,
        "normalized_group_label": (
            f"source-evidence-grouping:C100051:{normalized_id[:12]}"
        ),
        "source_fact_ids": tuple(
            sorted({value for item in source for value in item.source_fact_ids})
        ),
        "source_occurrence_ids": tuple(
            sorted({value for item in source for value in item.source_occurrence_ids})
        ),
        "source_group_ids": tuple(
            sorted(
                {
                    coordinate.source_group_id
                    for item in source
                    for coordinate in item.source_coordinates
                }
            )
        ),
        "source_coordinates": tuple(
            {
                "source_group_id": group,
                "anchor_code": anchor,
                "depth": depth,
            }
            for group, anchor, depth in sorted(
                {
                    (
                        coordinate.source_group_id,
                        coordinate.anchor_code,
                        coordinate.depth,
                    )
                    for item in source
                    for coordinate in item.source_coordinates
                }
            )
        ),
        "occurrence_availability": "available",
    }
    payload["blocks"] = tuple(
        sorted((*remaining_blocks, merged), key=lambda block: block["pairs"])
    )
    payload["output_partition"] = tuple(block["pairs"] for block in payload["blocks"])
    payload["row_identity"] = canonical_identity(
        {key: value for key, value in payload.items() if key != "row_identity"}
    )

    with pytest.raises(ValueError, match="computed source partition"):
        NormalizedGroupPolicyRow.model_validate(payload)


def test_normalized_group_identity_binds_decision_regime() -> None:
    members = (("op:Morphology", "C35501"), ("op:Morphology", "C9290"))
    inputs = {
        "concept_code": "C27262",
        "canonical_block_members": members,
        "rule_kind": "source-evidence-grouping",
        "transformation_rules": ("co-assertion-preservation",),
        "decision_identity": "1" * 64,
        "source_evidence_identity": "2" * 64,
    }
    assert normalized_group_identity(**inputs, decision_regime="source-evidence") != (
        normalized_group_identity(**inputs, decision_regime="current-owner-decision")
    )


def test_policy_row_rejects_duplicate_normalized_group_id() -> None:
    row = load_packaged_normalized_group_policy().by_code["C27262"]
    payload = row.model_dump()
    grouped = [block for block in payload["blocks"] if block["normalized_group_id"]]
    assert len(grouped) >= 2
    grouped[1]["normalized_group_id"] = grouped[0]["normalized_group_id"]
    grouped[1]["normalized_group_label"] = grouped[0]["normalized_group_label"]
    payload["row_identity"] = canonical_identity(
        {key: value for key, value in payload.items() if key != "row_identity"}
    )

    with pytest.raises(ValueError, match="normalized group identities are not unique"):
        NormalizedGroupPolicyRow.model_validate(payload)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("source-identity", "source evidence identity differs"),
        ("group-identity", "group identity differs from canonical derivation"),
        ("group-label", "group label differs from canonical derivation"),
    ],
)
def test_policy_row_rejects_evidence_and_group_derivation_drift(
    mutation: str, message: str
) -> None:
    row = load_packaged_normalized_group_policy().by_code["C27262"]
    payload = row.model_dump()
    grouped = next(block for block in payload["blocks"] if len(block["pairs"]) > 1)
    if mutation == "source-identity":
        payload["source_evidence_identity"] = "f" * 64
    elif mutation == "group-identity":
        grouped["normalized_group_id"] = "f" * 64
        grouped["normalized_group_label"] = (
            f"source-evidence-grouping:C27262:{'f' * 12}"
        )
    else:
        grouped["normalized_group_label"] = "source-evidence-grouping:C27262:wrong"
    payload["row_identity"] = canonical_identity(
        {key: value for key, value in payload.items() if key != "row_identity"}
    )

    with pytest.raises(ValueError, match=message):
        NormalizedGroupPolicyRow.model_validate(payload)


def test_policy_row_rejects_duplicate_pair_evidence_and_inexact_block_union() -> None:
    policy = load_packaged_normalized_group_policy()
    reviewed_row = policy.by_code["C101539"]
    duplicate_evidence = reviewed_row.model_dump()
    duplicate_evidence["source_pair_evidence"] = (
        *duplicate_evidence["source_pair_evidence"],
        duplicate_evidence["source_pair_evidence"][0],
    )
    with pytest.raises(ValueError, match="source pair evidence differs"):
        NormalizedGroupPolicyRow.model_validate(duplicate_evidence)

    row = policy.by_code["C27262"]
    inexact_union = row.model_dump()
    inexact_union["blocks"][0]["source_fact_ids"] = (
        *inexact_union["blocks"][0]["source_fact_ids"],
        "invented-fact",
    )
    with pytest.raises(
        ValueError, match="block evidence differs from exact member union"
    ):
        NormalizedGroupPolicyRow.model_validate(inexact_union)


def test_runtime_policy_refuses_pair_and_evidence_drift() -> None:
    policy = load_packaged_normalized_group_policy()
    unknown = Decomposition(code="C1", semantic_type="Neoplastic Process")
    not_applicable = apply_normalized_group_policy(unknown, policy)
    assert isinstance(not_applicable, NotApplicable)
    assert not_applicable.decomposition is unknown

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
            for block in row.output_partition
            for axis, filler in block
        ),
    )
    with pytest.raises(ValueError, match="source evidence differs"):
        apply_normalized_group_policy(wrong_evidence, policy)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("added", "reviewed stage target added current pair"),
        ("removed", "reviewed stage target removed current pair"),
    ],
)
def test_runtime_reviewed_policy_refuses_stage_target_drift(
    mutation: str, message: str
) -> None:
    policy = load_packaged_normalized_group_policy()
    row = policy.by_code["C101539"]
    if mutation == "added":
        output_partition = (*row.output_partition, (("op:StageSystem", "C999"),))
    else:
        removed = row.decision_target_pair_set[0]
        output_partition = tuple(
            tuple(pair for pair in block if pair != removed)
            for block in row.output_partition
        )
        output_partition = tuple(block for block in output_partition if block)
    drifted_row = row.model_copy(update={"output_partition": output_partition})
    drifted_policy = policy.model_copy(
        update={
            "rows": tuple(
                drifted_row if item.concept_code == row.concept_code else item
                for item in policy.rows
            )
        }
    )
    decomposition = Decomposition(
        code=row.concept_code,
        semantic_type="Neoplastic Process",
        constituents=tuple(
            Constituent(axis=axis, filler_code=filler, axis_source="nlp")
            for block in output_partition
            for axis, filler in block
        ),
    )

    with pytest.raises(ValueError, match=message):
        apply_normalized_group_policy(decomposition, drifted_policy)


def test_runtime_policy_applies_exact_source_and_normalized_groups() -> None:
    policy = load_packaged_normalized_group_policy()
    pair = ("op:Morphology", "C2")
    group_id = canonical_definition_group_id("C1", ("genus:C2:primitive",))
    fact_id = canonical_definition_fact_id("C1", group_id, "genus", "C2", "primitive")
    constituent = Constituent(
        axis=pair[0],
        filler_code=pair[1],
        axis_source="parent",
        source_definition_ids=(fact_id,),
    )
    coordinate = SourceCoordinate(
        source_group_id=group_id,
        anchor_code="C1",
        depth=0,
    )
    source_evidence = SourcePairEvidence(
        pair=pair,
        source_fact_ids=(fact_id,),
        source_occurrence_ids=(),
        source_coordinates=(coordinate,),
        occurrence_availability="not-applicable-genus-fact",
    )
    template_row = policy.rows[0]
    transformation_block = template_row.blocks[0].model_copy(
        update={
            "pairs": (pair,),
            "source_fact_ids": (fact_id,),
            "source_occurrence_ids": (),
            "source_group_ids": (group_id,),
            "source_coordinates": (coordinate,),
            "occurrence_availability": "not-applicable-genus-fact",
        }
    )
    transformation_row = template_row.model_copy(
        update={
            "concept_code": "C1",
            "rule_kind": "source-evidence-grouping",
            "output_partition": ((pair,),),
            "blocks": (transformation_block,),
            "source_pair_evidence": (source_evidence,),
            "input_pair_evidence_identity": _constituent_evidence_identity(
                (constituent,)
            ),
        }
    )
    transformation_policy = policy.model_copy(update={"rows": (transformation_row,)})
    decomposition = Decomposition(
        code="C1",
        semantic_type="Neoplastic Process",
        constituents=(constituent,),
        complete_definition=CompleteDefinition(
            root_code="C1",
            facts=(
                GenusDefinitionFact(
                    fact_id=fact_id,
                    anchor_code="C1",
                    group_id=group_id,
                    depth=0,
                    genus_code="C2",
                    is_defined=False,
                ),
            ),
            groups=(DefinitionGroup(group_id=group_id, anchor_code="C1", depth=0),),
            root_group_ids=(group_id,),
        ),
    )

    applied = apply_normalized_group_policy(decomposition, transformation_policy)

    assert not isinstance(applied, NotApplicable)
    assert {
        (item.axis, item.filler_code): (
            item.source_group_ids,
            item.normalized_group_id,
            item.normalized_group_label,
        )
        for item in applied.decomposition.constituents
    } == {
        pair: (
            (group_id,),
            transformation_block.normalized_group_id,
            transformation_block.normalized_group_label,
        )
    }


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


def test_policy_rejects_drift_in_exact_current_decision_sets() -> None:
    payload = load_packaged_normalized_group_policy().model_dump()
    row = next(item for item in payload["rows"] if item["concept_code"] == "C27262")
    row["current_decision"] = None
    row["row_identity"] = canonical_identity(
        {key: value for key, value in row.items() if key != "row_identity"}
    )
    payload["policy_identity"] = canonical_identity(
        {key: value for key, value in payload.items() if key != "policy_identity"}
    )

    with pytest.raises(ValueError, match="current decision concept sets differ"):
        ActiveNormalizedGroupPolicy.model_validate(payload)


def test_policy_rejects_historical_packet_provenance_drift() -> None:
    payload = load_packaged_normalized_group_policy().model_dump()
    row = payload["rows"][0]
    row["historical_observed_partition"]["packet_identity"] = "f" * 64
    row["row_identity"] = canonical_identity(
        {key: value for key, value in row.items() if key != "row_identity"}
    )
    payload["policy_identity"] = canonical_identity(
        {key: value for key, value in payload.items() if key != "policy_identity"}
    )

    with pytest.raises(ValueError, match="provenance bindings differ"):
        ActiveNormalizedGroupPolicy.model_validate(payload)
