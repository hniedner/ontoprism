from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.research.current_evidence import (
    CurrentComparison,
    CurrentEngineEvidence,
    regenerate_current_comparison,
)
from scripts.research.group_review_packet import generate_group_review_packet
from scripts.research.normalized_group_policy import (
    generate_active_normalized_group_policy,
    validate_evidence_policy_group_map,
    validate_promotion_bundle,
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
    NormalizedGroupPolicyRow,
    NotApplicable,
    PolicyBlock,
    SourceCoordinate,
    SourcePairEvidence,
    _constituent_evidence_identity,
    apply_normalized_group_policy,
    canonical_identity,
    load_normalized_group_policy,
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


def _evidence_with_policy_groups(
    policy: ActiveNormalizedGroupPolicy,
    evidence: CurrentEngineEvidence | None = None,
) -> CurrentEngineEvidence:
    evidence = evidence or CurrentEngineEvidence.model_validate_json(
        (_GOLDEN / "neoplasm-current-engine-evidence.json").read_bytes()
    )
    concepts = []
    for concept in evidence.concepts:
        row = policy.by_code.get(concept.code)
        if row is None:
            concepts.append(concept)
            continue
        concepts.append(
            concept.model_copy(
                update={
                    "constituents": tuple(
                        item.model_copy(
                            update={
                                "normalized_group_id": row.block_for(
                                    (item.axis, item.filler)
                                ).normalized_group_id,
                                "normalized_group_label": row.block_for(
                                    (item.axis, item.filler)
                                ).normalized_group_label,
                            }
                        )
                        for item in concept.constituents
                    )
                }
            )
        )
    payload = evidence.model_dump(mode="python", exclude={"evidence_identity"})
    payload["concepts"] = tuple(
        concept.model_dump(mode="python") for concept in concepts
    )
    return CurrentEngineEvidence.model_validate(
        {**payload, "evidence_identity": canonical_identity(payload)}
    )


def _write_json_model(path: Path, model: object) -> None:
    path.write_text(
        json.dumps(model.model_dump(mode="json"), indent=2) + "\n",  # type: ignore[union-attr]
        encoding="utf-8",
    )


def _regenerate_comparison(evidence_path: Path, output: Path) -> CurrentComparison:
    return regenerate_current_comparison(
        evidence_path=evidence_path,
        oracle_path=_GOLDEN / "neoplasm-adjudicated.json",
        row_decisions_path=_GOLDEN / "neoplasm-row-decisions.json",
        proposal_registry_path=_GOLDEN / "proposal-registry.json",
        proposal_registry_migration_path=(
            _GOLDEN / "proposal-registry-schema2-migration.json"
        ),
        output=output,
    )


def _fresh_promotion_bundle(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    current_path = _GOLDEN / "neoplasm-current-engine-evidence.json"
    current = CurrentEngineEvidence.model_validate_json(current_path.read_bytes())
    separating = {
        "C181564": {
            ("op:StageSystem", "C180901"),
            ("op:StageValue", "C27966"),
        },
        "C186620": {
            ("op:StageSystem", "C186618"),
            ("op:StageValue", "C27966"),
        },
        "C162226": {
            ("op:StageSystem", "C186617"),
            ("op:StageValue", "C96244"),
        },
    }
    abstaining = {
        "C27262": {
            ("op:Morphology", "C35501"),
            ("op:Morphology", "C9290"),
        },
        "C102870": {
            ("op:Morphology", "C121619"),
            ("op:Morphology", "C39986"),
        },
    }
    concepts = []
    for concept in current.concepts:
        constituents = []
        for item in concept.constituents:
            pair = (item.axis, item.filler)
            candidate_item = item
            if pair in separating.get(concept.code, set()):
                group_id = canonical_identity((concept.code, pair))
                candidate_item = item.model_copy(
                    update={
                        "normalized_group_id": group_id,
                        "normalized_group_label": f"test:{group_id[:12]}",
                    }
                )
            elif pair in abstaining.get(concept.code, set()):
                candidate_item = item.model_copy(
                    update={
                        "normalized_group_id": None,
                        "normalized_group_label": None,
                    }
                )
            constituents.append(candidate_item)
        concepts.append(
            concept.model_copy(update={"constituents": tuple(constituents)})
        )
    seed_payload = current.model_dump(mode="python", exclude={"evidence_identity"})
    seed_payload["concepts"] = tuple(
        concept.model_dump(mode="python") for concept in concepts
    )
    seed = CurrentEngineEvidence.model_validate(
        {**seed_payload, "evidence_identity": canonical_identity(seed_payload)}
    )
    evidence_path = tmp_path / "candidate-evidence.json"
    comparison_path = tmp_path / "candidate-comparison.json"
    packet_path = tmp_path / "candidate-group-review.json"
    policy_path = tmp_path / "candidate-policy.json"
    _write_json_model(evidence_path, seed)
    comparison = _regenerate_comparison(evidence_path, comparison_path)
    generate_group_review_packet(
        evidence_path=evidence_path,
        comparison_path=comparison_path,
        output=packet_path,
    )
    policy = generate_active_normalized_group_policy(
        evidence_path=evidence_path,
        comparison_path=comparison_path,
        packet_path=packet_path,
        historical_packet_path=(
            _ROOT / "evidence/group-review-packet-26.07d-schema3.json"
        ),
        rationale_markdown_path=(_ROOT / "evidence/group-review-rationale-26.07d.md"),
        rationale_sidecar_path=(_ROOT / "evidence/group-review-rationale-26.07d.json"),
        output=policy_path,
    )
    candidate = _evidence_with_policy_groups(policy, seed)
    _write_json_model(evidence_path, candidate)
    comparison = _regenerate_comparison(evidence_path, comparison_path)
    generate_group_review_packet(
        evidence_path=evidence_path,
        comparison_path=comparison_path,
        output=packet_path,
    )
    generate_active_normalized_group_policy(
        evidence_path=evidence_path,
        comparison_path=comparison_path,
        packet_path=packet_path,
        historical_packet_path=(
            _ROOT / "evidence/group-review-packet-26.07d-schema3.json"
        ),
        rationale_markdown_path=(_ROOT / "evidence/group-review-rationale-26.07d.md"),
        rationale_sidecar_path=(_ROOT / "evidence/group-review-rationale-26.07d.json"),
        output=policy_path,
    )
    assert comparison.metrics.common_pair_partition_agreement.model_dump() == {
        "numerator": 13,
        "denominator": 18,
        "rate": 13 / 18,
        "ineligible": 2,
    }
    return evidence_path, comparison_path, policy_path, current_path


def _write_comparison_mutation(path: Path, comparison: CurrentComparison) -> None:
    payload = comparison.model_dump(mode="python", exclude={"comparison_identity"})
    _write_json_model(
        path,
        CurrentComparison.model_validate(
            {**payload, "comparison_identity": canonical_identity(payload)}
        ),
    )


def _rebind_policy_comparison(policy_path: Path, comparison_path: Path) -> None:
    comparison = CurrentComparison.model_validate_json(comparison_path.read_bytes())
    payload = json.loads(policy_path.read_bytes())
    payload["basis_comparison_identity"] = comparison.comparison_identity
    payload["policy_identity"] = canonical_identity(
        {key: value for key, value in payload.items() if key != "policy_identity"}
    )
    policy_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    load_normalized_group_policy(policy_path)


def test_promotion_accepts_policy_accounted_thirteen_of_eighteen_bundle(
    tmp_path: Path,
) -> None:
    evidence, comparison, policy, current = _fresh_promotion_bundle(tmp_path)

    assert (
        validate_promotion_bundle(
            evidence_path=evidence,
            comparison_path=comparison,
            policy_path=policy,
            current_evidence_path=current,
        )
        is None
    )


@pytest.mark.parametrize(("denominator", "ineligible"), [(17, 3), (19, 1)])
def test_promotion_rejects_common_pair_eligibility_boundary_drift(
    tmp_path: Path, denominator: int, ineligible: int
) -> None:
    evidence, comparison_path, policy, current = _fresh_promotion_bundle(tmp_path)
    comparison = CurrentComparison.model_validate_json(comparison_path.read_bytes())
    payload = comparison.model_dump(mode="python")
    common = payload["metrics"]["common_pair_partition_agreement"]
    common.update(
        numerator=min(common["numerator"], denominator),
        denominator=denominator,
        rate=min(common["numerator"], denominator) / denominator,
        ineligible=ineligible,
    )
    full = payload["metrics"]["full_partition_agreement"]
    full["denominator"] = denominator + ineligible
    full["rate"] = full["numerator"] / full["denominator"]
    mutated = CurrentComparison.model_validate(
        {
            **{
                key: value
                for key, value in payload.items()
                if key != "comparison_identity"
            },
            "comparison_identity": canonical_identity(
                {
                    key: value
                    for key, value in payload.items()
                    if key != "comparison_identity"
                }
            ),
        }
    )
    _write_json_model(comparison_path, mutated)
    _rebind_policy_comparison(policy, comparison_path)

    with pytest.raises(ValueError, match="eligibility boundary"):
        validate_promotion_bundle(
            evidence_path=evidence,
            comparison_path=comparison_path,
            policy_path=policy,
            current_evidence_path=current,
        )


def test_promotion_rejects_unaccounted_sixth_disagreement_without_writes(
    tmp_path: Path,
) -> None:
    evidence, comparison_path, policy, current = _fresh_promotion_bundle(tmp_path)
    comparison = CurrentComparison.model_validate_json(comparison_path.read_bytes())
    agreeing = next(
        concept
        for concept in comparison.concepts
        if concept.common_pair_partition.eligible
        and concept.common_pair_partition.agrees
    )
    common = agreeing.common_pair_partition
    altered_partition = (
        (*common.actual_partition[0], *common.actual_partition[1]),
        *common.actual_partition[2:],
    )
    altered = agreeing.model_copy(
        update={
            "common_pair_partition": common.model_copy(
                update={"actual_partition": altered_partition, "agrees": False}
            )
        }
    )
    metrics = comparison.metrics
    common_metric = metrics.common_pair_partition_agreement.model_copy(
        update={"numerator": 12, "rate": 12 / 18}
    )
    _write_comparison_mutation(
        comparison_path,
        comparison.model_copy(
            update={
                "concepts": tuple(
                    altered if item.code == altered.code else item
                    for item in comparison.concepts
                ),
                "metrics": metrics.model_copy(
                    update={"common_pair_partition_agreement": common_metric}
                ),
            }
        ),
    )
    _rebind_policy_comparison(policy, comparison_path)
    before = {path: path.read_bytes() for path in (evidence, comparison_path, policy)}

    with pytest.raises(ValueError, match="unaccounted reviewed disagreement"):
        validate_promotion_bundle(
            evidence_path=evidence,
            comparison_path=comparison_path,
            policy_path=policy,
            current_evidence_path=current,
        )
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("code", ["C181564", "C186620", "C162226"])
def test_promotion_rejects_altered_historical_approval_partition(
    tmp_path: Path, code: str
) -> None:
    evidence, comparison_path, policy, current = _fresh_promotion_bundle(tmp_path)
    comparison = CurrentComparison.model_validate_json(comparison_path.read_bytes())
    concept = next(item for item in comparison.concepts if item.code == code)
    common = concept.common_pair_partition
    affected = set(common.primary_diagnosis.affected_pairs)  # type: ignore[union-attr]
    unrelated = next(
        block for block in common.actual_partition if not set(block) & affected
    )
    target_blocks = tuple(
        block for block in common.actual_partition if set(block) & affected
    )
    altered_partition = (
        *(
            block
            for block in common.actual_partition
            if block not in (*target_blocks, unrelated)
        ),
        tuple(sorted((*unrelated, *target_blocks[0]))),
        *target_blocks[1:],
    )
    altered = concept.model_copy(
        update={
            "common_pair_partition": common.model_copy(
                update={"actual_partition": tuple(sorted(altered_partition))}
            )
        }
    )
    _write_comparison_mutation(
        comparison_path,
        comparison.model_copy(
            update={
                "concepts": tuple(
                    altered if item.code == code else item
                    for item in comparison.concepts
                )
            }
        ),
    )
    _rebind_policy_comparison(policy, comparison_path)

    with pytest.raises(ValueError, match="altered reviewed disagreement"):
        validate_promotion_bundle(
            evidence_path=evidence,
            comparison_path=comparison_path,
            policy_path=policy,
            current_evidence_path=current,
        )


@pytest.mark.parametrize("code", ["C27262", "C102870"])
def test_promotion_rejects_resolved_abstention_without_new_decision(
    tmp_path: Path, code: str
) -> None:
    evidence, comparison_path, policy, current = _fresh_promotion_bundle(tmp_path)
    comparison = CurrentComparison.model_validate_json(comparison_path.read_bytes())
    concept = next(item for item in comparison.concepts if item.code == code)
    common = concept.common_pair_partition
    resolved = concept.model_copy(
        update={
            "common_pair_partition": common.model_copy(
                update={
                    "actual_partition": common.expected_partition,
                    "agrees": True,
                    "primary_diagnosis": None,
                }
            )
        }
    )
    metric = comparison.metrics.common_pair_partition_agreement.model_copy(
        update={"numerator": 14, "rate": 14 / 18}
    )
    _write_comparison_mutation(
        comparison_path,
        comparison.model_copy(
            update={
                "concepts": tuple(
                    resolved if item.code == code else item
                    for item in comparison.concepts
                ),
                "metrics": comparison.metrics.model_copy(
                    update={"common_pair_partition_agreement": metric}
                ),
            }
        ),
    )
    _rebind_policy_comparison(policy, comparison_path)

    with pytest.raises(ValueError, match="altered reviewed disagreement"):
        validate_promotion_bundle(
            evidence_path=evidence,
            comparison_path=comparison_path,
            policy_path=policy,
            current_evidence_path=current,
        )


@pytest.mark.parametrize(
    "identity_fields",
    [
        {"human_decision_identity": "1" * 64},
        {"machine_policy_identity": "2" * 64},
        {
            "normalized_group_id": "3" * 64,
            "normalized_group_label": "forged-group",
        },
    ],
)
def test_promotion_rejects_abstention_identity_without_new_decision(
    tmp_path: Path, identity_fields: dict[str, str]
) -> None:
    evidence, comparison, policy_path, current = _fresh_promotion_bundle(tmp_path)
    policy = json.loads(policy_path.read_bytes())
    row = next(item for item in policy["rows"] if item["concept_code"] == "C27262")
    block = next(
        item for item in row["blocks"] if item["grouping_status"] == "unresolved"
    )
    block.update(identity_fields)
    policy_path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unresolved grouping cannot carry"):
        validate_promotion_bundle(
            evidence_path=evidence,
            comparison_path=comparison,
            policy_path=policy_path,
            current_evidence_path=current,
        )


def test_promotion_still_rejects_pair_inventory_and_exact_metric_drift(
    tmp_path: Path,
) -> None:
    evidence, comparison_path, policy, current = _fresh_promotion_bundle(tmp_path)
    comparison = CurrentComparison.model_validate_json(comparison_path.read_bytes())
    metrics = comparison.metrics
    precision = metrics.exact_pair_precision.model_copy(
        update={"numerator": 110, "rate": 110 / 132}
    )
    recall = metrics.exact_pair_recall.model_copy(
        update={"numerator": 110, "rate": 110 / 153}
    )
    _write_comparison_mutation(
        comparison_path,
        comparison.model_copy(
            update={
                "metrics": metrics.model_copy(
                    update={
                        "exact_pair_precision": precision,
                        "exact_pair_recall": recall,
                    }
                )
            }
        ),
    )
    _rebind_policy_comparison(policy, comparison_path)
    with pytest.raises(ValueError, match="approved pair metrics"):
        validate_promotion_bundle(
            evidence_path=evidence,
            comparison_path=comparison_path,
            policy_path=policy,
            current_evidence_path=current,
        )

    current_model = CurrentEngineEvidence.model_validate_json(current.read_bytes())
    first = current_model.concepts[0]
    altered = first.model_copy(update={"constituents": first.constituents[1:]})
    current_payload = current_model.model_dump(
        mode="python", exclude={"evidence_identity"}
    )
    current_payload["concepts"] = tuple(
        (altered if concept.code == first.code else concept).model_dump(mode="python")
        for concept in current_model.concepts
    )
    drifted_current = CurrentEngineEvidence.model_validate(
        {
            **current_payload,
            "evidence_identity": canonical_identity(current_payload),
        }
    )
    drifted_current_path = tmp_path / "drifted-current.json"
    _write_json_model(drifted_current_path, drifted_current)
    _regenerate_comparison(evidence, comparison_path)
    _rebind_policy_comparison(policy, comparison_path)
    with pytest.raises(ValueError, match="constituent pair inventory"):
        validate_promotion_bundle(
            evidence_path=evidence,
            comparison_path=comparison_path,
            policy_path=policy,
            current_evidence_path=drifted_current_path,
        )


@pytest.mark.parametrize("mutation", ["swapped", "extra", "truncated", "duplicate"])
def test_evidence_policy_group_map_requires_exact_symmetric_unique_pairs(
    tmp_path: Path, mutation: str
) -> None:
    policy = _generate(tmp_path)
    evidence = _evidence_with_policy_groups(policy)
    validate_evidence_policy_group_map(evidence, policy)
    concept = next(item for item in evidence.concepts if item.code == "C115057")
    constituents = list(concept.constituents)
    if mutation == "swapped":
        first, second = constituents[:2]
        constituents[:2] = [
            first.model_copy(
                update={
                    "normalized_group_id": second.normalized_group_id,
                    "normalized_group_label": second.normalized_group_label,
                }
            ),
            second.model_copy(
                update={
                    "normalized_group_id": first.normalized_group_id,
                    "normalized_group_label": first.normalized_group_label,
                }
            ),
        ]
    elif mutation == "extra":
        constituents.append(
            constituents[0].model_copy(
                update={"axis": "op:Unrelated", "filler": "C999"}
            )
        )
    elif mutation == "truncated":
        constituents.pop()
    else:
        constituents.append(constituents[0])
    changed = concept.model_copy(update={"constituents": tuple(constituents)})
    mutated = evidence.model_copy(
        update={
            "concepts": tuple(
                changed if item.code == changed.code else item
                for item in evidence.concepts
            )
        }
    )

    with pytest.raises(ValueError, match="evidence normalized-group map differs"):
        validate_evidence_policy_group_map(mutated, policy)


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


@pytest.mark.parametrize("claimed_digest", ["0" * 64, "4febb77c" + "0" * 56])
def test_unavailable_prechange_record_rejects_any_present_artifact_claim(
    claimed_digest: str,
) -> None:
    payload = load_packaged_normalized_group_policy().model_dump()
    payload["unavailable_historical_artifact"]["present_artifact"] = {
        "path": "bounded/replay.ttl",
        "sha256": claimed_digest,
    }

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
    assert morphology.grouping_status == "unresolved"
    assert morphology.normalized_group_id is None
    assert morphology.normalized_group_label is None
    assert morphology.human_decision_identity is None
    assert morphology.machine_policy_identity is None
    assert "current_decision" not in row.model_dump()


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
    assert morphology.grouping_status == "unresolved"
    assert morphology.normalized_group_id is None
    assert morphology.normalized_group_label is None
    assert morphology.human_decision_identity is None
    assert morphology.machine_policy_identity is None
    assert "current_decision" not in row.model_dump()
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
    assert all("current_decision" not in row.model_dump() for row in policy.rows)
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
            block.decision_regime == "historical-approval" for block in target_blocks
        )
        assert all(
            block.decision_regime in {"source-evidence", "current-pair-preservation"}
            and block.human_decision_identity is None
            for block in non_target_blocks
        )


def test_historical_stage_review_preserves_exact_separate_and_together_partitions(
    tmp_path: Path,
) -> None:
    policy = _generate(tmp_path)
    separating = {"C181564", "C186620", "C162226"}
    together = {
        "C115057",
        "C101539",
        "C132677",
        "C206219",
        "C6135",
        "C89995",
        "C27787",
        "C115118",
    }

    for code in separating:
        row = policy.by_code[code]
        assert row.historical_decision.decision == "Approve intentional normalization"
        assert row.reviewed_partition == tuple(
            (pair,) for pair in row.decision_target_pair_set
        )
        assert all(
            row.block_for(pair).pairs == (pair,)
            and row.block_for(pair).decision_regime == "historical-approval"
            and row.block_for(pair).human_decision_identity
            == row.historical_decision.review_row_identity
            for pair in row.decision_target_pair_set
        )

    for code in together:
        row = policy.by_code[code]
        assert (
            row.historical_decision.decision == "Require source-reproducible correction"
        )
        assert row.reviewed_partition == (row.decision_target_pair_set,)
        shared_ids = {
            row.block_for(pair).normalized_group_id
            for pair in row.decision_target_pair_set
        }
        assert len(shared_ids) == 1
        assert None not in shared_ids
        assert all(
            row.block_for(pair).human_decision_identity
            == row.historical_decision.review_row_identity
            for pair in row.decision_target_pair_set
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
    with pytest.raises(ValueError, match="decided policy block requires an identity"):
        PolicyBlock.model_validate(singleton_payload)

    machine_block = next(
        item
        for row in policy.rows
        for item in row.blocks
        if item.machine_policy_identity is not None
    )
    missing_human = machine_block.model_dump()
    missing_human["decision_regime"] = "historical-approval"
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
        "grouping_status": "decided",
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
        normalized_group_identity(**inputs, decision_regime="historical-approval")
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


def test_abstention_status_cannot_escape_the_exact_disputed_morphology_pairs() -> None:
    row = load_packaged_normalized_group_policy().by_code["C27262"]
    payload = row.model_dump()
    unrelated = next(
        block
        for block in payload["blocks"]
        if block["pairs"]
        != (
            ("op:Morphology", "C35501"),
            ("op:Morphology", "C9290"),
        )
    )
    unrelated.update(
        grouping_status="unresolved",
        decision_regime="unresolved-abstention",
        human_decision_identity=None,
        machine_policy_identity=None,
        normalized_group_id=None,
        normalized_group_label=None,
    )
    payload["row_identity"] = canonical_identity(
        {key: value for key, value in payload.items() if key != "row_identity"}
    )

    with pytest.raises(ValueError, match="unresolved abstention target differs"):
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


def test_policy_rejects_removed_current_decision_schema() -> None:
    payload = load_packaged_normalized_group_policy().model_dump()
    payload["rows"][0]["current_decision"] = {
        "authority_identifier": "project-owner-current-conversation"
    }

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
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
