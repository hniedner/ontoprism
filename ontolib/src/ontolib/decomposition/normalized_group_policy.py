"""Strict active policy for normalized decomposition relationship groups."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from importlib.resources import files
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from ontolib.common.boundary_models import StrictFrozenBoundaryModel
from ontolib.decomposition.evaluation import compare_common_pair_partition
from ontolib.decomposition.models import Constituent, Decomposition, GenusDefinitionFact

Pair = tuple[str, str]
Partition = tuple[tuple[Pair, ...], ...]
TransformationName = Literal[
    "co-assertion-preservation",
    "routing",
    "specificity-collapse",
    "repeated-pairs",
    "reviewed-regrouping",
]
DecisionRegime = Literal[
    "source-evidence",
    "historical-approval",
    "current-pair-preservation",
    "unresolved-abstention",
]

DETERMINISTIC_SOURCE_CODES = frozenset({"C27262", "C102870", "C100051", "C4791"})
REVIEWED_STAGE_CODES = frozenset(
    {
        "C115057",
        "C101539",
        "C132677",
        "C181564",
        "C186620",
        "C162226",
        "C206219",
        "C6135",
        "C89995",
        "C27787",
        "C115118",
    }
)
PAIR_ONLY_CODES = frozenset({"C198031", "C100054", "C35756"})
ACTIVE_GROUP_CODES = DETERMINISTIC_SOURCE_CODES | REVIEWED_STAGE_CODES
UNRESOLVED_ABSTENTION_BLOCKS = {
    "C27262": frozenset({("op:Morphology", "C35501"), ("op:Morphology", "C9290")}),
    "C102870": frozenset({("op:Morphology", "C121619"), ("op:Morphology", "C39986")}),
}
CERVICAL_STAGE_APPROVALS = {
    "C181564": (("op:StageSystem", "C180901"), ("op:StageValue", "C27966")),
    "C186620": (("op:StageSystem", "C186618"), ("op:StageValue", "C27966")),
    "C162226": (("op:StageSystem", "C186617"), ("op:StageValue", "C96244")),
}
CERVICAL_STAGE_RATIONALE = (
    "Owner/SME approval 2026-09-25, #355: retain distinct StageSystem and StageValue "
    "axes in one concept-local group, expressing this value under this edition. "
    "Supersedes the 2026-08-28 singleton approval, retained in "
    "evidence/group-review-rationale-26.07d.md. Stage meaning is edition-dependent "
    "(doi:10.1016/j.ygyno.2020.03.027; doi:10.3322/caac.21663); mCODE represents "
    "method and value within one assessment. No filler or cross-edition equivalence. "
    "Rationale: docs/evidence/cervical-stage-grouping-355.md."
)


def canonical_identity(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=lambda item: item.model_dump(mode="json"),
        ).encode()
    ).hexdigest()


class SourceCoordinate(StrictFrozenBoundaryModel):
    source_group_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    anchor_code: str = Field(pattern=r"^C[0-9]+$")
    depth: int = Field(ge=0)


class SourcePairEvidence(StrictFrozenBoundaryModel):
    pair: Pair
    source_fact_ids: tuple[str, ...]
    source_occurrence_ids: tuple[str, ...]
    source_coordinates: tuple[SourceCoordinate, ...]
    occurrence_availability: Literal[
        "available",
        "available-source-fact",
        "not-applicable-genus-fact",
        "unavailable-current-source-coordinate",
    ]

    @model_validator(mode="after")
    def _validate_availability(self) -> Self:
        if self.occurrence_availability == "unavailable-current-source-coordinate":
            _validate_unavailable_source_evidence(self)
        else:
            _validate_available_source_evidence(self)
        return self


class HistoricalObservedPartition(StrictFrozenBoundaryModel):
    kind: Literal["historical_observed_partition"]
    packet_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    partition: Partition


class UnavailableHistoricalArtifact(StrictFrozenBoundaryModel):
    status: Literal["not-retained"]
    family: Literal["m1-6-current-replay"]
    run_id: Literal["neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997"]
    expected_artifact_sha256: Literal[
        "4febb77cb0e0b91418a22a08c19d9fa05d65529f00af30e85afe53a8d716424d"
    ]
    reason: Literal["overwritten-before-immutable-retention"]
    evidentiary_use: Literal["none"]


class PolicyBlock(StrictFrozenBoundaryModel):
    pairs: tuple[Pair, ...] = Field(min_length=1)
    grouping_status: Literal["decided", "unresolved"]
    transformation_rules: tuple[TransformationName, ...] = Field(min_length=1)
    decision_regime: DecisionRegime
    human_decision_identity: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    machine_policy_identity: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    normalized_group_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    normalized_group_label: str | None
    source_fact_ids: tuple[str, ...]
    source_occurrence_ids: tuple[str, ...]
    source_group_ids: tuple[str, ...]
    source_coordinates: tuple[SourceCoordinate, ...]
    occurrence_availability: Literal[
        "available",
        "available-source-fact",
        "not-applicable-genus-fact",
        "unavailable-current-source-coordinate",
        "mixed",
    ]

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if (self.normalized_group_id is None) != (self.normalized_group_label is None):
            raise ValueError("normalized group label presence differs from identity")
        _validate_genus_block_evidence(self)
        _validate_block_decision_identity(self)
        return self


def _validate_genus_block_evidence(block: PolicyBlock) -> None:
    if block.occurrence_availability != "not-applicable-genus-fact":
        return
    if block.source_occurrence_ids:
        raise ValueError("genus fact grouping cannot carry occurrence IDs")
    if not block.source_fact_ids:
        raise ValueError("genus fact grouping requires fact evidence")


def _validate_block_decision_identity(block: PolicyBlock) -> None:
    if block.grouping_status == "unresolved":
        _validate_unresolved_block(block)
        return
    _validate_decided_block(block)


def _validate_unresolved_block(block: PolicyBlock) -> None:
    if block.decision_regime != "unresolved-abstention":
        raise ValueError("unresolved grouping has a decided regime")
    identities = (
        block.human_decision_identity,
        block.machine_policy_identity,
        block.normalized_group_id,
        block.normalized_group_label,
    )
    if any(value is not None for value in identities):
        raise ValueError("unresolved grouping cannot carry decision identities")


def _validate_decided_block(block: PolicyBlock) -> None:
    if block.normalized_group_id is None:
        raise ValueError("every decided policy block requires an identity")
    if block.decision_regime == "unresolved-abstention":
        raise ValueError("decided grouping has an unresolved regime")
    human = block.decision_regime == "historical-approval"
    if human != (block.human_decision_identity is not None):
        raise ValueError("human decision identity differs from decision regime")
    if human == (block.machine_policy_identity is not None):
        raise ValueError("machine policy identity differs from decision regime")


class HistoricalDecision(StrictFrozenBoundaryModel):
    review_row_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: str
    pair_decision: str | None
    reviewer: str
    review_date: str
    rationale: str
    rationale_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class NormalizedGroupPolicyRow(StrictFrozenBoundaryModel):
    concept_code: str = Field(pattern=r"^C[0-9]+$")
    rule_kind: Literal["source-evidence-grouping", "reviewed-regrouping"]
    input_diagnosis: Literal["over-merge", "over-split", "misassignment"]
    diagnosis_pairs: tuple[Pair, ...] = Field(min_length=2)
    decision_target_pair_set: tuple[Pair, ...]
    reviewed_partition: Partition
    historical_expected_partition: Partition
    historical_observed_partition: HistoricalObservedPartition
    output_partition: Partition
    input_pair_evidence_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_evidence_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_pair_evidence: tuple[SourcePairEvidence, ...] = Field(min_length=1)
    blocks: tuple[PolicyBlock, ...] = Field(min_length=1)
    historical_decision: HistoricalDecision
    limitations: tuple[str, ...] = Field(min_length=1)
    row_identity: str = Field(pattern=r"^[0-9a-f]{64}$")

    def block_for(self, pair: Pair) -> PolicyBlock:
        matches = tuple(block for block in self.blocks if pair in block.pairs)
        if len(matches) != 1:
            raise ValueError(f"policy pair has {len(matches)} blocks: {pair!r}")
        return matches[0]

    @model_validator(mode="after")
    def _validate_row(self) -> Self:
        _validate_axis_blocks(self)
        _validate_rule_kind(self)
        _validate_partition_shape(self)
        _validate_diagnosis(self)
        expected = canonical_identity(
            self.model_dump(mode="json", exclude={"row_identity"})
        )
        if self.row_identity != expected:
            raise ValueError("normalized group policy row identity differs")
        return self


def _validate_partition_shape(row: NormalizedGroupPolicyRow) -> None:
    if tuple(block.pairs for block in row.blocks) != row.output_partition:
        raise ValueError("policy blocks differ from output partition")
    _validate_partition_evidence(row)
    _validate_normalized_group_identifiers(row)
    unresolved = {
        frozenset(block.pairs)
        for block in row.blocks
        if block.grouping_status == "unresolved"
    }
    expected = (
        {UNRESOLVED_ABSTENTION_BLOCKS[row.concept_code]}
        if row.concept_code in UNRESOLVED_ABSTENTION_BLOCKS
        else set()
    )
    if unresolved != expected:
        raise ValueError("unresolved abstention target differs")


def _validate_partition_evidence(row: NormalizedGroupPolicyRow) -> None:
    if row.rule_kind == "reviewed-regrouping":
        _validate_reviewed_target(row)
    _validate_source_pair_inventory(row)
    evidence_by_pair = {item.pair: item for item in row.source_pair_evidence}
    for block in row.blocks:
        members = tuple(evidence_by_pair[pair] for pair in block.pairs)
        _validate_block_evidence_union(block, members)


def _validate_source_pair_inventory(row: NormalizedGroupPolicyRow) -> None:
    evidence_pairs = tuple(item.pair for item in row.source_pair_evidence)
    if len(evidence_pairs) != len(set(evidence_pairs)) or set(evidence_pairs) != (
        _partition_pairs(row.output_partition)
    ):
        raise ValueError("source pair evidence differs from policy pair inventory")
    expected_source_identity = canonical_identity(row.source_pair_evidence)
    if row.source_evidence_identity != expected_source_identity:
        raise ValueError("source evidence identity differs")


def _member_fact_ids(members: tuple[SourcePairEvidence, ...]) -> tuple[str, ...]:
    return tuple(sorted({value for item in members for value in item.source_fact_ids}))


def _member_occurrence_ids(members: tuple[SourcePairEvidence, ...]) -> tuple[str, ...]:
    return tuple(
        sorted({value for item in members for value in item.source_occurrence_ids})
    )


def _member_coordinates(
    members: tuple[SourcePairEvidence, ...],
) -> tuple[SourceCoordinate, ...]:
    coordinates = {
        (coordinate.source_group_id, coordinate.anchor_code, coordinate.depth)
        for item in members
        for coordinate in item.source_coordinates
    }
    return tuple(
        SourceCoordinate(source_group_id=group, anchor_code=anchor, depth=depth)
        for group, anchor, depth in sorted(coordinates)
    )


def _validate_block_evidence_union(
    block: PolicyBlock, members: tuple[SourcePairEvidence, ...]
) -> None:
    expected_facts = _member_fact_ids(members)
    expected_occurrences = _member_occurrence_ids(members)
    expected_coordinates = _member_coordinates(members)
    expected_groups = tuple(
        sorted({item.source_group_id for item in expected_coordinates})
    )
    statuses = {item.occurrence_availability for item in members}
    expected_availability = next(iter(statuses)) if len(statuses) == 1 else "mixed"
    actual = (
        block.source_fact_ids,
        block.source_occurrence_ids,
        block.source_coordinates,
        block.source_group_ids,
        block.occurrence_availability,
    )
    expected = (
        expected_facts,
        expected_occurrences,
        expected_coordinates,
        expected_groups,
        expected_availability,
    )
    if actual != expected:
        raise ValueError("policy block evidence differs from exact member union")


def _validate_reviewed_target(row: NormalizedGroupPolicyRow) -> None:
    targets = set(row.decision_target_pair_set)
    if _partition_pairs(row.reviewed_partition) != targets:
        raise ValueError("reviewed partition differs from decision target pair set")
    if not targets <= _partition_pairs(row.output_partition):
        raise ValueError("decision target pair set differs from current policy pairs")
    target_blocks = set(row.reviewed_partition)
    for block in row.blocks:
        is_target = block.pairs in target_blocks
        human = block.decision_regime == "historical-approval"
        if is_target != human:
            raise ValueError("human decision identity escaped reviewed target blocks")


def _validate_normalized_group_identifiers(row: NormalizedGroupPolicyRow) -> None:
    declared_group_ids = [
        block.normalized_group_id
        for block in row.blocks
        if block.normalized_group_id is not None
    ]
    if len(declared_group_ids) != len(set(declared_group_ids)):
        raise ValueError("normalized group identities are not unique within policy row")
    for block in row.blocks:
        if block.grouping_status == "unresolved":
            continue
        _validate_normalized_group_block(row, block)


def _validate_normalized_group_block(
    row: NormalizedGroupPolicyRow,
    block: PolicyBlock,
) -> None:
    decision_identity = block.human_decision_identity or block.machine_policy_identity
    if decision_identity is None:
        raise ValueError("normalized group block lacks decision identity")
    expected_id = normalized_group_identity(
        concept_code=row.concept_code,
        canonical_block_members=block.pairs,
        rule_kind=row.rule_kind,
        transformation_rules=block.transformation_rules,
        decision_regime=block.decision_regime,
        decision_identity=decision_identity,
        source_evidence_identity=row.source_evidence_identity,
    )
    if block.normalized_group_id != expected_id:
        raise ValueError("normalized group identity differs from canonical derivation")
    if block.normalized_group_label != normalized_group_label(
        row.concept_code, row.rule_kind, expected_id
    ):
        raise ValueError("normalized group label differs from canonical derivation")


def _validate_unavailable_source_evidence(item: SourcePairEvidence) -> None:
    if item.source_fact_ids or item.source_occurrence_ids or item.source_coordinates:
        raise ValueError("unavailable source evidence cannot carry citations")


def _validate_available_source_evidence(item: SourcePairEvidence) -> None:
    if not item.source_fact_ids or not item.source_coordinates:
        raise ValueError("available source evidence requires facts and coordinates")
    availability = item.occurrence_availability
    if availability == "not-applicable-genus-fact":
        _reject_genus_occurrences(item.source_occurrence_ids)
        return
    if availability == "available-source-fact":
        _reject_source_fact_occurrences(item.source_occurrence_ids)
        return
    _require_restriction_occurrences(item.source_occurrence_ids)


def _reject_genus_occurrences(source_occurrence_ids: tuple[str, ...]) -> None:
    if source_occurrence_ids:
        raise ValueError("genus fact grouping cannot carry occurrence IDs")


def _reject_source_fact_occurrences(source_occurrence_ids: tuple[str, ...]) -> None:
    if source_occurrence_ids:
        raise ValueError("source-fact-only evidence cannot carry occurrence IDs")


def _require_restriction_occurrences(source_occurrence_ids: tuple[str, ...]) -> None:
    if not source_occurrence_ids:
        raise ValueError("restriction source evidence requires occurrence IDs")


def _partition_pairs(partition: Partition) -> set[Pair]:
    return {pair for block in partition for pair in block}


def _diagnosis_rows(
    partition: Partition, prefix: str, included: set[Pair]
) -> tuple[tuple[Pair, str], ...]:
    return tuple(
        (pair, f"{prefix}-{index}")
        for index, block in enumerate(partition)
        for pair in block
        if pair in included
    )


def _validate_diagnosis(row: NormalizedGroupPolicyRow) -> None:
    diagnosis_pairs = set(row.diagnosis_pairs)
    expected_rows = _diagnosis_rows(
        row.historical_expected_partition, "expected", diagnosis_pairs
    )
    actual_rows = _diagnosis_rows(
        row.historical_observed_partition.partition, "actual", diagnosis_pairs
    )
    diagnosis = compare_common_pair_partition(
        expected_rows, actual_rows
    ).primary_diagnosis
    if diagnosis is None or diagnosis.value != row.input_diagnosis:
        raise ValueError("normalized group policy diagnosis differs from partitions")


def _validate_rule_kind(row: NormalizedGroupPolicyRow) -> None:
    expected_kind = (
        "source-evidence-grouping"
        if row.concept_code in DETERMINISTIC_SOURCE_CODES
        else "reviewed-regrouping"
    )
    if row.rule_kind != expected_kind:
        raise ValueError("policy rule kind differs from declared concept set")
    if row.rule_kind == "source-evidence-grouping" and (
        _computed_source_partition(row.source_pair_evidence) != row.output_partition
    ):
        raise ValueError("output differs from computed source partition")


def _validate_axis_blocks(row: NormalizedGroupPolicyRow) -> None:
    if row.rule_kind == "source-evidence-grouping" and any(
        len({pair[0] for pair in block.pairs}) != 1 for block in row.blocks
    ):
        raise ValueError("source-evidence normalized group spans final axes")


def _computed_source_partition(
    evidence: tuple[SourcePairEvidence, ...],
) -> Partition:
    grouped: dict[tuple[object, ...], list[Pair]] = {}
    for item in evidence:
        coordinates = tuple(
            sorted(
                {
                    (
                        coordinate.source_group_id,
                        coordinate.anchor_code,
                        coordinate.depth,
                    )
                    for coordinate in item.source_coordinates
                }
            )
        )
        key = (
            (item.pair[0], "source-coordinates", coordinates)
            if coordinates
            else (item.pair[0], "current-pair-preservation", item.pair)
        )
        grouped.setdefault(key, []).append(item.pair)
    return tuple(sorted(tuple(sorted(block)) for block in grouped.values()))


def normalized_group_identity(
    *,
    concept_code: str,
    canonical_block_members: tuple[Pair, ...],
    rule_kind: str,
    transformation_rules: tuple[TransformationName, ...],
    decision_regime: DecisionRegime,
    decision_identity: str,
    source_evidence_identity: str,
) -> str:
    return canonical_identity(
        {
            "concept_code": concept_code,
            "canonical_block_members": tuple(sorted(canonical_block_members)),
            "rule_kind": rule_kind,
            "transformation_rules": tuple(sorted(transformation_rules)),
            "decision_regime": decision_regime,
            "decision_identity": decision_identity,
            "source_evidence_identity": source_evidence_identity,
        }
    )


def normalized_group_label(concept_code: str, rule_kind: str, identity: str) -> str:
    return f"{rule_kind}:{concept_code}:{identity[:12]}"


class ActiveNormalizedGroupPolicy(StrictFrozenBoundaryModel):
    schema_version: Literal[7]
    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    ncit_version: str
    basis_run_id: str
    basis_artifact_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    basis_evidence_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    basis_comparison_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    basis_packet_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    unavailable_historical_artifact: UnavailableHistoricalArtifact
    historical_packet_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    historical_packet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rationale_markdown_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rationale_sidecar_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    rationale_sidecar_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rows: tuple[NormalizedGroupPolicyRow, ...] = Field(min_length=15, max_length=15)
    pair_only_codes: tuple[str, ...]
    policy_identity: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def by_code(self) -> dict[str, NormalizedGroupPolicyRow]:
        return {row.concept_code: row for row in self.rows}

    @model_validator(mode="after")
    def _validate_policy(self) -> Self:
        _validate_policy_concept_sets(self)
        _validate_policy_provenance(self)
        expected = canonical_identity(
            self.model_dump(mode="json", exclude={"policy_identity"})
        )
        if self.policy_identity != expected:
            raise ValueError("normalized group policy identity differs")
        return self


def _validate_policy_concept_sets(policy: ActiveNormalizedGroupPolicy) -> None:
    codes = {row.concept_code for row in policy.rows}
    if codes != ACTIVE_GROUP_CODES or len(codes) != len(policy.rows):
        raise ValueError("active normalized group policy concept set differs")
    if policy.pair_only_codes != tuple(sorted(PAIR_ONLY_CODES)):
        raise ValueError("pair-only exclusion set differs")


def _validate_policy_provenance(policy: ActiveNormalizedGroupPolicy) -> None:
    if any(
        row.historical_observed_partition.packet_identity
        != policy.historical_packet_identity
        for row in policy.rows
    ):
        raise ValueError("policy row provenance bindings differ")


def load_normalized_group_policy(path: Path) -> ActiveNormalizedGroupPolicy:
    return ActiveNormalizedGroupPolicy.model_validate_json(path.read_bytes())


def load_packaged_normalized_group_policy() -> ActiveNormalizedGroupPolicy:
    resource = files("ontolib.decomposition").joinpath(
        "data/normalized-group-policy.json"
    )
    return ActiveNormalizedGroupPolicy.model_validate_json(resource.read_bytes())


def _constituent_evidence_identity(constituents: tuple[Constituent, ...]) -> str:
    return canonical_identity(
        tuple(
            {
                "pair": (item.axis, item.filler_code),
                "source_definition_ids": item.source_definition_ids,
                "source_occurrence_ids": item.source_occurrence_ids,
            }
            for item in sorted(
                constituents, key=lambda row: (row.axis, row.filler_code)
            )
        )
    )


def apply_normalized_group_policy(
    decomposition: Decomposition, policy: ActiveNormalizedGroupPolicy
) -> Applied | NotApplicable:
    row = policy.by_code.get(decomposition.code)
    if row is None:
        return NotApplicable(decomposition=decomposition)
    _validate_decomposition_input(decomposition, row)
    _validate_genus_evidence(decomposition, row)
    grouped = []
    for item in decomposition.constituents:
        pair = (item.axis, item.filler_code)
        block = row.block_for(pair)
        grouped.append(
            replace(
                item,
                normalized_group_id=block.normalized_group_id,
                normalized_group_label=block.normalized_group_label,
            )
        )
    return Applied(decomposition=replace(decomposition, constituents=tuple(grouped)))


@dataclass(frozen=True, slots=True)
class Applied:
    decomposition: Decomposition


@dataclass(frozen=True, slots=True)
class NotApplicable:
    decomposition: Decomposition


def _validate_decomposition_input(
    decomposition: Decomposition, row: NormalizedGroupPolicyRow
) -> None:
    constituents = tuple(decomposition.constituents)
    pairs = {(item.axis, item.filler_code) for item in constituents}
    expected_pairs = {pair for block in row.output_partition for pair in block}
    if pairs != expected_pairs:
        raise ValueError(
            f"normalized group policy pair set differs for {decomposition.code}"
        )
    if row.rule_kind == "reviewed-regrouping":
        _validate_reviewed_decomposition_targets(decomposition.code, pairs, row)
    if _constituent_evidence_identity(constituents) != row.input_pair_evidence_identity:
        raise ValueError(
            f"normalized group policy source evidence differs for {decomposition.code}"
        )


def _validate_reviewed_decomposition_targets(
    code: str, pairs: set[Pair], row: NormalizedGroupPolicyRow
) -> None:
    current_targets = {
        pair
        for pair in pairs
        if pair[0] in {"op:StageSystem", "op:StageValue"} and pair[1].startswith("C")
    }
    declared_targets = set(row.decision_target_pair_set)
    if current_targets - declared_targets:
        raise ValueError(f"reviewed stage target added current pair for {code}")
    if declared_targets - current_targets:
        raise ValueError(f"reviewed stage target removed current pair for {code}")


def _validate_genus_evidence(
    decomposition: Decomposition, row: NormalizedGroupPolicyRow
) -> None:
    facts = {
        fact.fact_id: fact
        for fact in (
            decomposition.complete_definition.facts
            if decomposition.complete_definition
            else ()
        )
    }
    for block in row.blocks:
        if block.occurrence_availability == "not-applicable-genus-fact" and not all(
            isinstance(facts.get(value), GenusDefinitionFact)
            for value in block.source_fact_ids
        ):
            raise ValueError("normalized genus grouping lacks exact genus facts")
