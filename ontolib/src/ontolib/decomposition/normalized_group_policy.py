"""Strict active policy for normalized decomposition relationship groups."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from ontolib.common.boundary_models import StrictFrozenBoundaryModel
from ontolib.decomposition.evaluation import compare_common_pair_partition
from ontolib.decomposition.models import Constituent, Decomposition, GenusDefinitionFact

Pair = tuple[str, str]
Partition = tuple[tuple[Pair, ...], ...]

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
SUPERSEDED_ABSTENTION_CODES = frozenset({"C27262", "C102870"})
HISTORICAL_APPROVAL_CODES = frozenset({"C181564", "C186620", "C162226"})
ACTIVATED_REVIEW_CODES = REVIEWED_STAGE_CODES - HISTORICAL_APPROVAL_CODES


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


class UnavailableParentBinding(StrictFrozenBoundaryModel):
    binding_kind: Literal["unavailable_parent_binding"]
    durable_record_path: Literal[
        "tmp/artifacts/v1/unavailable/"
        "neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997.json"
    ]
    record_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    family: Literal["m1-6-current-replay"]
    run_id: Literal["neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997"]
    expected_artifact_sha256: Literal[
        "4febb77cb0e0b91418a22a08c19d9fa05d65529f00af30e85afe53a8d716424d"
    ]
    last_known_path: Literal["tmp/m1-6-current-replay.ttl"]
    reason: Literal["overwritten-before-immutable-retention"]


class PolicyBlock(StrictFrozenBoundaryModel):
    pairs: tuple[Pair, ...] = Field(min_length=1)
    normalized_group_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    normalized_group_label: str | None
    source_fact_ids: tuple[str, ...]
    source_occurrence_ids: tuple[str, ...]
    source_group_ids: tuple[str, ...]
    source_coordinates: tuple[SourceCoordinate, ...]
    occurrence_availability: Literal["available", "not-applicable-genus-fact", "mixed"]

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if (len(self.pairs) > 1) != (self.normalized_group_id is not None):
            raise ValueError(
                "normalized group identity presence differs from block size"
            )
        if (self.normalized_group_id is None) != (self.normalized_group_label is None):
            raise ValueError("normalized group label presence differs from identity")
        if self.occurrence_availability == "not-applicable-genus-fact":
            if self.source_occurrence_ids:
                raise ValueError("genus fact grouping cannot carry occurrence IDs")
            if not self.source_fact_ids:
                raise ValueError("genus fact grouping requires fact evidence")
        return self


class HistoricalDecision(StrictFrozenBoundaryModel):
    review_row_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: str
    pair_decision: str | None
    reviewer: str
    review_date: str
    rationale: str
    rationale_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CurrentDecision(StrictFrozenBoundaryModel):
    authority_identifier: Literal["project-owner-current-conversation"]
    decision_date: Literal["2026-09-14"]
    decision: Literal[
        "supersede-abstention-with-source-evidence-grouping",
        "activate-reviewed-normalized-group-policy",
    ]
    basis_packet_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    basis_source_evidence_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    supersedes_review_row_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    grouping_decision_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision_identity: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _identity(self) -> Self:
        grouping_payload = self.model_dump(
            mode="json",
            include={
                "authority_identifier",
                "decision_date",
                "decision",
                "supersedes_review_row_identity",
            },
        )
        if self.grouping_decision_identity != canonical_identity(grouping_payload):
            raise ValueError("grouping decision identity differs")
        expected = canonical_identity(
            self.model_dump(mode="json", exclude={"decision_identity"})
        )
        if self.decision_identity != expected:
            raise ValueError("current decision identity differs")
        return self


class NormalizedGroupPolicyRow(StrictFrozenBoundaryModel):
    concept_code: str = Field(pattern=r"^C[0-9]+$")
    rule_kind: Literal["source-evidence-grouping", "reviewed-regrouping"]
    input_diagnosis: Literal["over-merge", "over-split", "misassignment"]
    diagnosis_pairs: tuple[Pair, ...] = Field(min_length=2)
    historical_observed_partition: HistoricalObservedPartition
    output_partition: Partition
    input_pair_evidence_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_evidence_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_pair_evidence: tuple[SourcePairEvidence, ...] = Field(min_length=1)
    blocks: tuple[PolicyBlock, ...] = Field(min_length=1)
    historical_decision: HistoricalDecision
    current_decision: CurrentDecision | None
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
        _validate_partition_shape(self)
        _validate_rule_kind(self)
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


def _validate_partition_evidence(row: NormalizedGroupPolicyRow) -> None:
    if not _partition_pairs(row.historical_observed_partition.partition) <= (
        _partition_pairs(row.output_partition)
    ):
        raise ValueError("historical observed partition contains unknown policy pairs")
    evidence_pairs = tuple(item.pair for item in row.source_pair_evidence)
    if len(evidence_pairs) != len(set(evidence_pairs)) or set(evidence_pairs) != (
        _partition_pairs(row.output_partition)
    ):
        raise ValueError("source pair evidence differs from policy pair inventory")
    expected_source_identity = canonical_identity(row.source_pair_evidence)
    if row.source_evidence_identity != expected_source_identity:
        raise ValueError("source evidence identity differs")


def _validate_normalized_group_identifiers(row: NormalizedGroupPolicyRow) -> None:
    decision_identity = (
        row.current_decision.grouping_decision_identity
        if row.current_decision is not None
        else row.historical_decision.review_row_identity
    )
    declared_group_ids = [
        block.normalized_group_id
        for block in row.blocks
        if block.normalized_group_id is not None
    ]
    if len(declared_group_ids) != len(set(declared_group_ids)):
        raise ValueError("normalized group identities are not unique within policy row")
    for block in row.blocks:
        _validate_normalized_group_block(row, block, decision_identity)


def _validate_normalized_group_block(
    row: NormalizedGroupPolicyRow,
    block: PolicyBlock,
    decision_identity: str,
) -> None:
    if len(block.pairs) == 1:
        return
    expected_id = normalized_group_identity(
        concept_code=row.concept_code,
        canonical_block_members=block.pairs,
        rule_kind=row.rule_kind,
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
    if item.occurrence_availability == "not-applicable-genus-fact":
        if item.source_occurrence_ids:
            raise ValueError("genus fact grouping cannot carry occurrence IDs")
    elif not item.source_occurrence_ids:
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
    expected_rows = _diagnosis_rows(row.output_partition, "expected", diagnosis_pairs)
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
        raise ValueError("output partition differs from computed source partition")


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
            else (item.pair[0], "unavailable-singleton", item.pair)
        )
        grouped.setdefault(key, []).append(item.pair)
    return tuple(sorted(tuple(sorted(block)) for block in grouped.values()))


def normalized_group_identity(
    *,
    concept_code: str,
    canonical_block_members: tuple[Pair, ...],
    rule_kind: str,
    decision_identity: str,
    source_evidence_identity: str,
) -> str:
    return canonical_identity(
        {
            "concept_code": concept_code,
            "canonical_block_members": tuple(sorted(canonical_block_members)),
            "rule_kind": rule_kind,
            "decision_identity": decision_identity,
            "source_evidence_identity": source_evidence_identity,
        }
    )


def normalized_group_label(concept_code: str, rule_kind: str, identity: str) -> str:
    return f"{rule_kind}:{concept_code}:{identity[:12]}"


class ActiveNormalizedGroupPolicy(StrictFrozenBoundaryModel):
    schema_version: Literal[5]
    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    ncit_version: str
    basis_run_id: str
    basis_artifact_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    basis_evidence_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    basis_comparison_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    basis_packet_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    unavailable_prechange_parent: UnavailableParentBinding
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
    superseded = _current_decision_codes(
        policy, "supersede-abstention-with-source-evidence-grouping"
    )
    activated = _current_decision_codes(
        policy, "activate-reviewed-normalized-group-policy"
    )
    if superseded != SUPERSEDED_ABSTENTION_CODES or activated != ACTIVATED_REVIEW_CODES:
        raise ValueError("current decision concept sets differ")


def _current_decision_codes(
    policy: ActiveNormalizedGroupPolicy,
    decision: Literal[
        "supersede-abstention-with-source-evidence-grouping",
        "activate-reviewed-normalized-group-policy",
    ],
) -> set[str]:
    return {
        row.concept_code
        for row in policy.rows
        if row.current_decision is not None
        and row.current_decision.decision == decision
    }


def _validate_policy_provenance(policy: ActiveNormalizedGroupPolicy) -> None:
    if any(
        row.historical_observed_partition.packet_identity
        != policy.historical_packet_identity
        or (
            row.current_decision is not None
            and (
                row.current_decision.basis_packet_identity
                != policy.basis_packet_identity
                or row.current_decision.basis_source_evidence_identity
                != row.source_evidence_identity
            )
        )
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
) -> Decomposition:
    row = policy.by_code.get(decomposition.code)
    if row is None:
        return decomposition
    _validate_decomposition_input(decomposition, row)
    _validate_genus_evidence(decomposition, row)
    grouped = tuple(
        replace(
            item, group=row.block_for((item.axis, item.filler_code)).normalized_group_id
        )
        for item in decomposition.constituents
    )
    return replace(decomposition, constituents=grouped)


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
    if _constituent_evidence_identity(constituents) != row.input_pair_evidence_identity:
        raise ValueError(
            f"normalized group policy source evidence differs for {decomposition.code}"
        )


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
