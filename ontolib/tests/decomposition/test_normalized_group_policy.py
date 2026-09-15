from __future__ import annotations

from pathlib import Path

import pytest
from scripts.research.normalized_group_policy import (
    derive_normalized_group_identity,
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
    SourcePairEvidence,
    UnavailableParentBinding,
    apply_normalized_group_policy,
    canonical_identity,
    load_packaged_normalized_group_policy,
)
from ontolib.decomposition.run_artifacts import (
    ArtifactUnavailableRecord,
    write_unavailable_record,
)

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).parents[3]
_GOLDEN = Path(__file__).with_name("golden")


def _generate(tmp_path: Path):
    unavailable_path = tmp_path / (
        "tmp/artifacts/v1/unavailable/"
        "neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997.json"
    )
    write_unavailable_record(
        unavailable_path,
        ArtifactUnavailableRecord(
            schema_version=1,
            record_type="unavailable-artifact",
            family="m1-6-current-replay",
            run_id="neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997",
            expected_sha256=(
                "4febb77cb0e0b91418a22a08c19d9fa05d65529f00af30e85afe53a8d716424d"
            ),
            last_known_path="tmp/m1-6-current-replay.ttl",
            reason="overwritten-before-immutable-retention",
            references=("normalized-group-policy",),
        ),
    )
    return generate_active_normalized_group_policy(
        evidence_path=_GOLDEN / "neoplasm-current-engine-evidence.json",
        comparison_path=_GOLDEN / "neoplasm-current-comparison.json",
        packet_path=_ROOT / "evidence/group-review-packet-26.07d-schema3.json",
        historical_packet_path=(
            _ROOT / "evidence/group-review-packet-26.07d-schema3.json"
        ),
        rationale_markdown_path=(_ROOT / "evidence/group-review-rationale-26.07d.md"),
        rationale_sidecar_path=(_ROOT / "evidence/group-review-rationale-26.07d.json"),
        unavailable_prechange_record_path=unavailable_path,
        output=tmp_path / "normalized-group-policy.json",
    )


def test_unavailable_prechange_record_is_not_an_observed_manifest_parent(
    tmp_path: Path,
) -> None:
    policy = _generate(tmp_path)

    assert isinstance(policy.unavailable_prechange_parent, UnavailableParentBinding)
    assert policy.unavailable_prechange_parent.expected_artifact_sha256 == (
        "4febb77cb0e0b91418a22a08c19d9fa05d65529f00af30e85afe53a8d716424d"
    )
    assert policy.unavailable_prechange_parent.reason == (
        "overwritten-before-immutable-retention"
    )
    assert "artifact_records" not in policy.unavailable_prechange_parent.model_dump()

    with pytest.raises(ValueError, match="unavailable prechange record"):
        generate_active_normalized_group_policy(
            evidence_path=_GOLDEN / "neoplasm-current-engine-evidence.json",
            comparison_path=_GOLDEN / "neoplasm-current-comparison.json",
            packet_path=_ROOT / "evidence/group-review-packet-26.07d-schema3.json",
            historical_packet_path=(
                _ROOT / "evidence/group-review-packet-26.07d-schema3.json"
            ),
            rationale_markdown_path=(
                _ROOT / "evidence/group-review-rationale-26.07d.md"
            ),
            rationale_sidecar_path=(
                _ROOT / "evidence/group-review-rationale-26.07d.json"
            ),
            unavailable_prechange_record_path=tmp_path / "missing-unavailable.json",
            output=tmp_path / "must-not-exist.json",
        )


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
        ("pair-set", "unknown policy pairs"),
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
        payload["historical_observed_partition"]["partition"] = (
            (("op:UnknownAxis", "C1"),),
            *payload["historical_observed_partition"]["partition"],
        )
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
    decision_identity = row.historical_decision.review_row_identity
    normalized_id = derive_normalized_group_identity(
        concept_code=row.concept_code,
        canonical_block_members=merged_pairs,
        rule_kind=row.rule_kind,
        decision_identity=decision_identity,
        source_evidence_identity=row.source_evidence_identity,
        bootstrap_group=None,
    )
    source = [item for item in row.source_pair_evidence if item.pair in affected]
    merged = {
        "pairs": merged_pairs,
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


def test_normalized_group_identity_ignores_bootstrap_group_labels() -> None:
    members = (("op:Morphology", "C35501"), ("op:Morphology", "C9290"))
    inputs = {
        "concept_code": "C27262",
        "canonical_block_members": members,
        "rule_kind": "source-evidence-grouping",
        "decision_identity": "1" * 64,
        "source_evidence_identity": "2" * 64,
    }
    assert derive_normalized_group_identity(**inputs, bootstrap_group="old-a") == (
        derive_normalized_group_identity(**inputs, bootstrap_group="mutated-old-b")
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
            for block in row.output_partition
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


def test_policy_rejects_drift_in_exact_current_decision_sets() -> None:
    payload = load_packaged_normalized_group_policy().model_dump()
    row = next(item for item in payload["rows"] if item["concept_code"] == "C100051")
    decision = {
        "authority_identifier": "project-owner-current-conversation",
        "decision_date": "2026-09-14",
        "decision": "activate-reviewed-normalized-group-policy",
        "basis_packet_identity": payload["basis_packet_identity"],
        "basis_source_evidence_identity": row["source_evidence_identity"],
        "supersedes_review_row_identity": row["historical_decision"][
            "review_row_identity"
        ],
    }
    decision["grouping_decision_identity"] = canonical_identity(
        {
            key: decision[key]
            for key in (
                "authority_identifier",
                "decision_date",
                "decision",
                "supersedes_review_row_identity",
            )
        }
    )
    row["current_decision"] = {
        **decision,
        "decision_identity": canonical_identity(decision),
    }
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
