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


class PolicyBlock(StrictFrozenBoundaryModel):
    pairs: tuple[Pair, ...] = Field(min_length=1)
    normalized_group_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
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
    supersedes_review_row_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision_identity: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _identity(self) -> Self:
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
    input_partition: Partition
    output_partition: Partition
    input_pair_evidence_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
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
        _validate_partition_shape(self)
        _validate_diagnosis(self)
        _validate_rule_kind(self)
        expected = canonical_identity(
            self.model_dump(mode="json", exclude={"row_identity"})
        )
        if self.row_identity != expected:
            raise ValueError("normalized group policy row identity differs")
        return self


def _validate_partition_shape(row: NormalizedGroupPolicyRow) -> None:
    if tuple(block.pairs for block in row.blocks) != row.output_partition:
        raise ValueError("policy blocks differ from output partition")
    if _partition_pairs(row.input_partition) != _partition_pairs(row.output_partition):
        raise ValueError("normalized grouping policy changes the pair set")


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
    actual_rows = _diagnosis_rows(row.input_partition, "actual", diagnosis_pairs)
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
    if row.rule_kind == "source-evidence-grouping" and any(
        len({pair[0] for pair in block.pairs}) != 1 for block in row.blocks
    ):
        raise ValueError("source-evidence normalized group spans final axes")


class ActiveNormalizedGroupPolicy(StrictFrozenBoundaryModel):
    schema_version: Literal[4]
    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    ncit_version: str
    basis_run_id: str
    basis_artifact_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    basis_evidence_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    basis_comparison_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    basis_packet_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    prechange_artifact_identity: Literal[
        "4febb77cb0e0b91418a22a08c19d9fa05d65529f00af30e85afe53a8d716424d"
    ]
    prechange_evidence_identity: Literal[
        "4475f9ec231e5f5fc6714eb5e7c899ec894d65c6075ca402b851c1bed70c2a32"
    ]
    prechange_comparison_identity: Literal[
        "4be29eb27325d533415048773253392a97c30a119f20e6afb06ea14ae703c87e"
    ]
    prechange_packet_identity: Literal[
        "0f60c6f89c59624cf3e95685b3134fba8180f3bb312a1a03b99f8d949aea801f"
    ]
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
        codes = {row.concept_code for row in self.rows}
        if codes != ACTIVE_GROUP_CODES or len(codes) != len(self.rows):
            raise ValueError("active normalized group policy concept set differs")
        if set(self.pair_only_codes) != PAIR_ONLY_CODES:
            raise ValueError("pair-only exclusion set differs")
        expected = canonical_identity(
            self.model_dump(mode="json", exclude={"policy_identity"})
        )
        if self.policy_identity != expected:
            raise ValueError("normalized group policy identity differs")
        return self


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
    expected_pairs = {pair for block in row.input_partition for pair in block}
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
