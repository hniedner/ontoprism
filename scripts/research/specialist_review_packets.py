# ruff: noqa: E501
"""Generate typed, write-free specialist packets and validate returned Markdown."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Literal, Self, cast

from defusedxml.ElementTree import iterparse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ontolib.decomposition.axis_contracts import AXIS_CONTRACTS

try:
    from scripts.research.specialist_cadsr_usage import (
        CadsrUsageRow,
        SpecialistCadsrUsageReport,
    )
    from scripts.research.specialist_literature_context import (
        GeneratedLiteratureContext,
        LiteratureDossierSource,
        LiteraturePairKey,
        citation_supports_pair,
    )
except ModuleNotFoundError:  # direct ``python scripts/adjudication.py`` entry point
    from research.specialist_cadsr_usage import (  # type: ignore[import-not-found]
        CadsrUsageRow,
        SpecialistCadsrUsageReport,
    )
    from research.specialist_literature_context import (
        GeneratedLiteratureContext,
        LiteratureDossierSource,
        LiteraturePairKey,
        citation_supports_pair,
    )

CONCEPT_ORDER = ("C27262", "C102870", "C6135", "C4791", "C100054", "C198031", "C35756")
_SHA256 = r"^[0-9a-f]{64}$"
_MINT = re.compile(r"MINT-[0-9a-f]{12}")
_GROUP_PACKET_SCHEMA = 4
_DIAGNOSTIC_SCHEMA = 3
_CONTROLLING_AUTHORITY_MAX = 2
_MIN_SCOPE_TOKEN_LENGTH = 4
Relation = Literal[
    "expected-matched-scoreable",
    "expected-emitted-review-bearing",
    "expected-not-emitted",
    "current-only-scoreable",
    "current-only-review-bearing",
    "current-only-proposed",
]
ReviewScope = Literal[
    "stage-a-and-stage-b",
    "stage-a-clinical-only",
    "engineering-only",
    "context-not-under-review",
]
PairScopeStatus = Literal[
    "suppressed",
    "engineering-only",
    "clinical-only",
    "actionable",
    "context",
    "refused-invalid",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class PairKey(_StrictModel):
    axis: str = Field(min_length=1)
    filler: str = Field(min_length=1)


class ReturnChannel(_StrictModel):
    instruction: Literal[
        "Return the completed file, with the same filename, as a file attachment to the OntoPrism project coordinator through the same secure channel by which this packet was received."
    ] = "Return the completed file, with the same filename, as a file attachment to the OntoPrism project coordinator through the same secure channel by which this packet was received."
    deadline: Literal["No deadline assigned; coordinator will communicate changes."] = (
        "No deadline assigned; coordinator will communicate changes."
    )
    fallback: Literal[
        "If that channel is unavailable, contact the OntoPrism project coordinator before transmitting review material."
    ] = "If that channel is unavailable, contact the OntoPrism project coordinator before transmitting review material."


class PairScopeInput(_StrictModel):
    relation: Relation
    range_verdict: Literal["valid", "invalid", "unknown"]
    source_evidence_status: Literal[
        "available", "source-backed-coordinate-missing", "unavailable"
    ]
    diagnostic_classification: Literal[
        "added",
        "matched-scoreable",
        "emitted-review-bearing",
        "current-only-scoreable",
        "current-only-review-bearing",
        "current-only-proposed",
        "selection-miss",
        "extraction-miss",
        "not-diagnosed",
        "proposal-only",
        "unavailable-source-evidence",
    ]
    has_clinical_claim: bool
    claim_contests_projection: bool
    action_representable: bool
    governance_status: Literal["eligible", "suppressed"]


class PairScopeVerdict(_StrictModel):
    status: PairScopeStatus
    reason: str = Field(min_length=1)
    engineering_blocker: str | None = None


class DispatchDecision(_StrictModel):
    status: Literal["dispatchable", "withheld"]
    reasons: tuple[str, ...]

    @model_validator(mode="after")
    def _status_matches_reasons(self) -> Self:
        if (self.status == "withheld") != bool(self.reasons):
            raise ValueError("dispatch status and withholding reasons disagree")
        return self


def derive_dispatch_decision(
    *,
    engineering_blockers: dict[str, str],
    asked_pair_ids: tuple[str, ...],
    supported_pair_ids: tuple[str, ...],
) -> DispatchDecision:
    """Withhold exactly when prerequisites or pair-relevant evidence are incomplete."""
    supported = set(supported_pair_ids)
    reasons = tuple(
        f"{pair_id} engineering prerequisite unresolved: {reason}"
        for pair_id, reason in sorted(engineering_blockers.items())
    ) + tuple(
        f"{pair_id} lacks complete accessible pair-relevant evidence"
        for pair_id in asked_pair_ids
        if pair_id not in supported
    )
    return DispatchDecision(
        status="withheld" if reasons else "dispatchable", reasons=reasons
    )


_SOURCE_CLINICAL_CUE = re.compile(
    r"(?:\bnot through\b|\bbut not\b.{0,80}\buniversal\b|"
    r"\bwithout (?:proving|making)\b.{0,80}\buniversal\b|"
    r"\brather than (?:a )?universal\b|\bnot (?:a )?universal\b|"
    r"\bnot presumed universal\b|\bdoes not (?:prove|establish)\b.{0,80}\buniversal|"
    r"\b(?:UNIVERSAL-DEFINING|UNIVERSAL-NONDEFINING|CHARACTERISTIC-NONUNIVERSAL|"
    r"CLASSIFICATION-DEPENDENT|INAPPLICABLE|UNRESOLVED)\b|"
    r"\buniversal\b.{0,30}\bdefining\b)",
    re.IGNORECASE,
)
_ONTOLOGY_ACTION_CUE = re.compile(
    r"\b(?:should|must)(?:\s+\w+){0,6}\s+be (?:retained|removed|promoted)\b|"
    r"\b(?:retain|remove|promote)(?:d)?[- ]scoreable\b",
    re.IGNORECASE,
)


def semantic_answer_cue_findings(
    records: tuple[tuple[str, str, str], ...],
) -> tuple[str, ...]:
    """Detect semantic conclusions in evidence slots and action-leading questions."""
    findings: list[str] = []
    for code, field, text in records:
        if field != "question" and _SOURCE_CLINICAL_CUE.search(text):
            findings.append(
                f"{code} answer cue in {field}: pre-answered clinical status [{text}]"
            )
        if _ONTOLOGY_ACTION_CUE.search(text):
            suffix = (
                "requested ontology action instead of human applicability"
                if field == "question"
                else "pre-answered ontology action"
            )
            findings.append(f"{code} answer cue in {field}: {suffix} [{text}]")
    return tuple(findings)


def classify_pair_scope(scope_input: PairScopeInput) -> PairScopeVerdict:  # noqa: PLR0911
    """Classify every valid scope product with fail-closed precedence."""
    if scope_input.governance_status == "suppressed":
        return PairScopeVerdict(
            status="suppressed",
            reason="Governance suppresses this candidate before human pair numbering.",
        )
    if scope_input.range_verdict in {"invalid", "unknown"}:
        return PairScopeVerdict(
            status="engineering-only",
            reason=f"The stored range verdict is {scope_input.range_verdict}.",
            engineering_blocker="#271 range repair blocked; rerun deterministic range gate after repair",
        )
    if scope_input.source_evidence_status == "unavailable":
        return PairScopeVerdict(
            status="engineering-only",
            reason="No source-backed fact is available for a human ontology disposition.",
            engineering_blocker="#267 source-routing repair queued; regenerate packets and rerun provenance validation",
        )
    if (
        scope_input.diagnostic_classification in {"selection-miss", "extraction-miss"}
        or scope_input.relation == "expected-not-emitted"
    ):
        if scope_input.has_clinical_claim:
            return PairScopeVerdict(
                status="clinical-only",
                reason="The pair has material clinical evidence, but extraction or selection repair is engineering-owned.",
                engineering_blocker="#274 selector/extractor repair queued; regenerate packets and rerun the release gate",
            )
        return PairScopeVerdict(
            status="engineering-only",
            reason="Extraction or selection repair is engineering-owned and no clinical claim is indexed.",
            engineering_blocker="#274 selector/extractor repair queued; regenerate packets and rerun the release gate",
        )
    if (
        not scope_input.has_clinical_claim
        or scope_input.relation == "current-only-proposed"
    ):
        return PairScopeVerdict(
            status="context",
            reason="The pair is indexed as visible context and has no specialist disposition.",
        )
    if (
        scope_input.relation == "expected-matched-scoreable"
        and not scope_input.claim_contests_projection
    ):
        return PairScopeVerdict(
            status="clinical-only",
            reason="The source-backed, range-valid pair has a material clinical applicability question and its current scoreable disposition requires no engineering repair.",
        )
    if not scope_input.action_representable:
        return PairScopeVerdict(
            status="refused-invalid",
            reason="The required pair action is not representable by the current response contract; the row must be withheld.",
            engineering_blocker="#274 response-contract repair required before dispatch",
        )
    return PairScopeVerdict(
        status="actionable",
        reason="The source-backed, range-valid pair has a material clinical claim and a representable ontology disposition.",
    )


class SourceOccurrence(_StrictModel):
    occurrence_id: str
    anchor_code: str
    anchor_label: str
    depth: int = Field(ge=0)
    role_code: str
    structural_path: tuple[int, ...]
    member_position: int = Field(ge=0)


class SpecialistPair(_StrictModel):
    pair_id: str = Field(pattern=r"^P[1-9][0-9]*$")
    key: PairKey
    relation: Relation
    scope_verdict: PairScopeStatus
    review_scope: ReviewScope
    scope_reason: str = Field(min_length=1)
    contested: bool
    filler_label: str = Field(min_length=1)
    filler_definition: str = Field(min_length=1)
    source_role_code: str = Field(min_length=1)
    source_role_label: str = Field(min_length=1)
    source_role_definition: str = Field(min_length=1)
    source_occurrences: tuple[SourceOccurrence, ...]
    source_evidence_status: Literal[
        "available", "source-backed-coordinate-missing", "unavailable"
    ] = "unavailable"
    source_evidence_reason: str = "no-matching-stated-definition-fact"
    source_definition_ids: tuple[str, ...] = ()
    current_projection_status: str = Field(min_length=1)
    axis_range_verdict: Literal["valid", "invalid", "unknown"]
    modality: str = Field(min_length=1)
    derivation: str = "source-coordinate-unavailable"
    governance: str = Field(min_length=1)
    fallback: str = Field(min_length=1)


class SpecialistRowPacket(_StrictModel):
    code: str
    label: str
    definition: str
    pairs: tuple[SpecialistPair, ...]
    question_pair_keys: tuple[tuple[PairKey, ...], ...]
    engineering_blockers: dict[str, str]

    @model_validator(mode="after")
    def _contract_is_total(self) -> Self:
        expected_ids = tuple(f"P{number}" for number in range(1, len(self.pairs) + 1))
        if tuple(pair.pair_id for pair in self.pairs) != expected_ids:
            raise ValueError("pair IDs must be contiguous after governance filtering")
        keys = {pair.key: pair.pair_id for pair in self.pairs}
        for question in self.question_pair_keys:
            for key in question:
                if key not in keys:
                    raise ValueError(
                        f"unknown semantic pair in clinical question: {key.axis}/{key.filler}"
                    )
        questioned = [
            (key.axis, key.filler)
            for question in self.question_pair_keys
            for key in question
        ]
        asked = {
            (pair.key.axis, pair.key.filler)
            for pair in self.pairs
            if pair.review_scope in {"stage-a-and-stage-b", "stage-a-clinical-only"}
        }
        if set(questioned) != asked or len(questioned) != len(set(questioned)):
            missing = sorted(asked - set(questioned))
            extra = sorted(set(questioned) - asked)
            raise ValueError(
                "curated question set must cover every asked pair exactly once; "
                f"missing={missing}; extra={extra}"
            )
        engineering = set(self.engineering_pair_ids)
        if set(self.engineering_blockers) != engineering:
            raise ValueError(
                "every engineering pair requires exactly one owned blocker"
            )
        if any(
            not re.search(
                r"#(?:267|271|274).*(?:regenerate|rerun|queue|repair)",
                text,
                re.IGNORECASE,
            )
            for text in self.engineering_blockers.values()
        ):
            raise ValueError(
                "engineering blockers require owner, status, and next step"
            )
        return self

    @property
    def asked_pair_ids(self) -> tuple[str, ...]:
        return tuple(
            pair.pair_id
            for pair in self.pairs
            if pair.review_scope in {"stage-a-and-stage-b", "stage-a-clinical-only"}
        )

    @property
    def action_pair_ids(self) -> tuple[str, ...]:
        return tuple(
            pair.pair_id
            for pair in self.pairs
            if pair.review_scope == "stage-a-and-stage-b"
        )

    @property
    def clinical_only_pair_ids(self) -> tuple[str, ...]:
        return tuple(
            pair.pair_id
            for pair in self.pairs
            if pair.review_scope == "stage-a-clinical-only"
            and pair.pair_id not in self.engineering_blockers
        )

    @property
    def engineering_pair_ids(self) -> tuple[str, ...]:
        return tuple(
            pair.pair_id
            for pair in self.pairs
            if pair.review_scope == "engineering-only"
            or pair.scope_verdict == "refused-invalid"
            or pair.pair_id in self.engineering_blockers
        )

    @property
    def context_pair_ids(self) -> tuple[str, ...]:
        return tuple(
            pair.pair_id
            for pair in self.pairs
            if pair.review_scope == "context-not-under-review"
        )

    @property
    def resolved_question_pair_ids(self) -> tuple[tuple[str, ...], ...]:
        ids = {pair.key: pair.pair_id for pair in self.pairs}
        return tuple(
            tuple(ids[key] for key in question) for question in self.question_pair_keys
        )


class ClinicalPairAssessment(_StrictModel):
    pair_id: str
    status: Literal[
        "UNIVERSAL-DEFINING",
        "UNIVERSAL-NONDEFINING",
        "CHARACTERISTIC-NONUNIVERSAL",
        "CLASSIFICATION-DEPENDENT",
        "INAPPLICABLE",
        "UNRESOLVED",
    ]
    citations: tuple[str, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1)


class HumanAttestation(_StrictModel):
    role: Literal["clinical", "ontology"]
    attester_name: str = Field(min_length=1)
    attester_capacity: str = Field(min_length=1)
    attestation_date: str
    conflict_of_interest: str = Field(min_length=1)
    source_confirmation: str = Field(min_length=1)
    human_attestation: Literal[True]

    @model_validator(mode="after")
    def _is_human(self) -> Self:
        date.fromisoformat(self.attestation_date)
        if re.search(r"\b(?:ai|agent|bot|model|llm)\b", self.attester_name, re.I):
            raise ValueError("a human must personally attest each review role")
        return self


class ClinicalStageA(_StrictModel):
    attestation: HumanAttestation
    assessments: tuple[ClinicalPairAssessment, ...]
    clinical_stage: Literal[
        "SUFFICIENT-FOR-ONTOLOGY-REVIEW",
        "CLINICAL-COMPLETE-ENGINEERING-PENDING",
        "DEFERRED",
    ]
    blocker: str | None

    @model_validator(mode="after")
    def _outcome(self) -> Self:
        if self.attestation.role != "clinical":
            raise ValueError("Stage A requires the clinical role attestation")
        if self.clinical_stage == "DEFERRED" and self.assessments:
            raise ValueError("DEFERRED Stage A requires empty assessments")
        if self.clinical_stage != "DEFERRED" and any(
            item.status == "UNRESOLVED" for item in self.assessments
        ):
            raise ValueError("UNRESOLVED assessments cannot be terminal")
        if (self.clinical_stage == "DEFERRED") != (self.blocker is not None):
            raise ValueError("DEFERRED Stage A requires exactly one blocker")
        return self


class PairDisposition(_StrictModel):
    pair_id: str
    action: Literal[
        "RETAIN-SCOREABLE",
        "PROMOTE-SCOREABLE",
        "REMOVE-FROM-PROJECTION",
    ]
    rationale: str = Field(min_length=1)


class PartitionDisposition(_StrictModel):
    mode: Literal[
        "RETAIN-CURRENT",
        "GROUP-SPECIFIED-PAIRS-TOGETHER",
        "KEEP-SPECIFIED-PAIRS-SEPARATE",
        "CUSTOM-CURRENT-MODEL",
        "EMPTY",
    ]
    groups: tuple[tuple[str, ...], ...]
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.mode == "EMPTY" and self.groups:
            raise ValueError("EMPTY partition cannot contain groups")
        if self.mode != "EMPTY" and not self.groups:
            raise ValueError("non-EMPTY partition requires groups")
        if any(not group for group in self.groups):
            raise ValueError("partition groups cannot be empty")
        flattened = tuple(pair for group in self.groups for pair in group)
        if len(flattened) != len(set(flattened)):
            raise ValueError("partition groups cannot duplicate a pair")
        return self


class OntologyStageB(_StrictModel):
    attestation: HumanAttestation
    row_outcome: Literal["RESOLVED", "DEFERRED"]
    dispositions: tuple[PairDisposition, ...]
    partition: PartitionDisposition | None
    blocker: str | None
    blocker_source: str | None = None
    next_action: str | None = None
    ontology_writes: Literal[False] = False
    readiness: Literal[False] = False
    publication: Literal[False] = False

    @model_validator(mode="after")
    def _outcome(self) -> Self:
        if self.attestation.role != "ontology":
            raise ValueError("Stage B requires the ontology role attestation")
        deferred_fields = (self.blocker, self.blocker_source, self.next_action)
        if self.row_outcome == "DEFERRED" and not all(deferred_fields):
            raise ValueError(
                "DEFERRED Stage B requires blocker, source, and next action"
            )
        if self.row_outcome == "RESOLVED" and any(deferred_fields):
            raise ValueError("RESOLVED Stage B cannot carry deferred fields")
        if self.row_outcome == "DEFERRED" and (self.dispositions or self.partition):
            raise ValueError(
                "DEFERRED Stage B cannot contain pair or partition responses"
            )
        if self.row_outcome == "RESOLVED" and self.partition is None:
            raise ValueError("RESOLVED Stage B requires a partition disposition")
        return self


class ActionConsequence(_StrictModel):
    comparison_tp_delta: int
    comparison_fp_delta: int
    comparison_fn_delta: int
    scoreable_emitted_delta: int
    source_preserved: Literal[True]
    pair_after: str
    needs_review_after: bool
    group_effect: str
    row_readiness: Literal[False]
    publication: Literal[False]


class IndexedPairContract(_StrictModel):
    pair_id: str
    relation: Relation
    scope_verdict: PairScopeStatus
    review_scope: ReviewScope
    source_evidence_status: Literal[
        "available", "source-backed-coordinate-missing", "unavailable"
    ]
    axis_range_verdict: Literal["valid", "invalid", "unknown"]
    allowed_actions: tuple[str, ...]
    citation_ids: tuple[str, ...]
    consequence_by_action: dict[str, ActionConsequence]


class PacketWorkload(_StrictModel):
    asked: int = Field(ge=0)
    action: int = Field(ge=0)
    engineering: int = Field(ge=0)
    context: int = Field(ge=0)


class GroupingContract(_StrictModel):
    allowed_dispositions: tuple[
        Literal[
            "RETAIN-CURRENT",
            "GROUP-SPECIFIED-PAIRS-TOGETHER",
            "KEEP-SPECIFIED-PAIRS-SEPARATE",
            "CUSTOM-CURRENT-MODEL",
            "EMPTY",
        ],
        ...,
    ]
    baseline_scoreable_pair_ids: tuple[str, ...]
    baseline_partition: tuple[tuple[str, ...], ...]


class SuppressedCandidate(_StrictModel):
    axis: str
    generated_id: str = Field(pattern=r"^MINT-[0-9a-f]{12}$")
    reason: Literal["unregistered", "range-ineligible"]


class PacketIndexEntry(_StrictModel):
    code: str
    path: str
    row_sha256: str = Field(pattern=_SHA256)
    row_contract_identity: str = Field(pattern=_SHA256)
    asked_pair_ids: tuple[str, ...]
    action_pair_ids: tuple[str, ...]
    clinical_only_pair_ids: tuple[str, ...]
    engineering_pair_ids: tuple[str, ...]
    context_pair_ids: tuple[str, ...]
    workload: PacketWorkload
    return_channel: ReturnChannel
    grouping_contract: GroupingContract
    current_partition: tuple[tuple[PairKey, ...], ...]
    historical_partition: tuple[tuple[PairKey, ...], ...]
    suppressed_candidates: tuple[SuppressedCandidate, ...]
    pair_contracts: tuple[IndexedPairContract, ...]
    stage_a_mode: Literal["clinical-review"] = "clinical-review"
    stage_b_mode: Literal["ontology-review", "not-applicable-pending-engineering"] = (
        "ontology-review"
    )
    dispatch_status: Literal["dispatchable", "withheld"]
    withholding_reasons: tuple[str, ...]
    expected_return_validation_path: str
    generated: Literal[False] = False

    @model_validator(mode="after")
    def _partitions_and_dispatch_are_exact(self) -> Self:
        pair_ids = {item.pair_id for item in self.pair_contracts}
        partitions = (
            set(self.action_pair_ids),
            set(self.clinical_only_pair_ids),
            set(self.engineering_pair_ids),
            set(self.context_pair_ids),
        )
        if any(
            left & right
            for number, left in enumerate(partitions)
            for right in partitions[number + 1 :]
        ):
            raise ValueError("indexed row partitions must be disjoint")
        if set.union(*partitions) != pair_ids:
            raise ValueError("indexed row partitions must exactly cover pair contracts")
        asked = {
            item.pair_id
            for item in self.pair_contracts
            if item.review_scope in {"stage-a-and-stage-b", "stage-a-clinical-only"}
        }
        if asked != set(self.asked_pair_ids):
            raise ValueError("indexed asked set must derive from pair scopes")
        if self.workload != PacketWorkload(
            asked=len(self.asked_pair_ids),
            action=len(self.action_pair_ids),
            engineering=len(self.engineering_pair_ids),
            context=len(self.context_pair_ids),
        ):
            raise ValueError("indexed workload must be computed from row partitions")
        if (self.dispatch_status == "withheld") != bool(self.withholding_reasons):
            raise ValueError("dispatch status and withholding reasons disagree")
        if (
            Path(self.path).is_absolute()
            or Path(self.expected_return_validation_path).is_absolute()
        ):
            raise ValueError("packet index paths must be repository-relative")
        expected_stage_b = (
            "ontology-review"
            if self.action_pair_ids
            else "not-applicable-pending-engineering"
        )
        if self.stage_b_mode != expected_stage_b:
            raise ValueError("Stage B mode must derive from the indexed action set")
        if Path(self.path).name != f"{self.code}.md":
            raise ValueError("packet return filename must exactly match the row code")
        baseline = tuple(
            pair
            for group in self.grouping_contract.baseline_partition
            for pair in group
        )
        if set(baseline) != set(
            self.grouping_contract.baseline_scoreable_pair_ids
        ) or len(baseline) != len(set(baseline)):
            raise ValueError(
                "baseline partition must exactly cover baseline scoreable pairs"
            )
        return self


class PacketIndex(_StrictModel):
    schema_version: Literal[3] = 3
    ncit_version: Literal["26.07d"]
    input_identities: dict[str, str]
    literature_context_identity: str = Field(pattern=_SHA256)
    cadsr_usage_identity: str = Field(pattern=_SHA256)
    suppressed_candidates_by_row: dict[str, tuple[SuppressedCandidate, ...]]
    packets: tuple[PacketIndexEntry, ...]
    context_correction_note: str = Field(min_length=1)
    registered_mint_expected_set: tuple[str, ...]
    release_ready_codes: tuple[str, ...]
    withheld_codes: tuple[str, ...]
    release_ready: bool
    index_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _row_suppressions_are_bound(self) -> Self:
        if set(self.suppressed_candidates_by_row) != {
            entry.code for entry in self.packets
        }:
            raise ValueError("suppression registry must contain every indexed row")
        if any(
            self.suppressed_candidates_by_row[entry.code] != entry.suppressed_candidates
            for entry in self.packets
        ):
            raise ValueError("row suppressions must match the packet entry")
        context_count = sum(len(entry.context_pair_ids) for entry in self.packets)
        if self.context_correction_note != context_correction_note(context_count):
            raise ValueError(
                "context-correction note must match indexed bundle liveness"
            )
        dispatchable = tuple(
            entry.code
            for entry in self.packets
            if entry.dispatch_status == "dispatchable"
        )
        withheld = tuple(
            entry.code for entry in self.packets if entry.dispatch_status == "withheld"
        )
        if self.release_ready_codes != dispatchable or self.withheld_codes != withheld:
            raise ValueError("release subsets must derive from packet dispatch status")
        if self.release_ready != (not withheld):
            raise ValueError("release readiness must require every requested row")
        return self


class GenerationValidation(_StrictModel):
    schema_version: Literal[3]
    index_identity: str = Field(pattern=_SHA256)
    index_file_sha256: str = Field(pattern=_SHA256)
    status: Literal["passed", "failed"]
    findings: tuple[str, ...]
    producing_command: str
    produced_on: str
    artifact_files_written: tuple[str, ...]
    ontology_writes: Literal[False]
    runtime_mutated: Literal[False]
    readiness: Literal[False]
    readiness_meaning: Literal[
        "ontology/publication readiness; separate from dispatch readiness"
    ]
    release_ready_codes: tuple[str, ...]
    withheld_codes: tuple[str, ...]
    release_ready: bool
    publication: Literal[False]
    validation_identity: str = Field(pattern=_SHA256)


class DispatchManifestEntry(_StrictModel):
    code: str
    path: str
    sha256: str = Field(pattern=_SHA256)


class DispatchManifest(_StrictModel):
    schema_version: Literal[1] = 1
    recipient: Literal["OntoPrism project coordinator"] = (
        "OntoPrism project coordinator"
    )
    instruction: str
    dispatch_ready: Literal[True] = True
    release_ready_codes: tuple[str, ...]
    packets: tuple[DispatchManifestEntry, ...]
    index_identity: str = Field(pattern=_SHA256)
    manifest_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _listed_packets_are_exact(self) -> Self:
        if tuple(item.code for item in self.packets) != self.release_ready_codes:
            raise ValueError("dispatch packets must exactly match release-ready codes")
        if any(item.path != f"{item.code}.md" for item in self.packets):
            raise ValueError("dispatch packet path must match its code")
        return self


class CompletionValidation(_StrictModel):
    status: Literal["passed"]
    completed_codes: tuple[str, ...]
    ontology_writes: Literal[False]
    readiness: Literal[False]
    publication: Literal[False]


class RowCompletionValidation(_StrictModel):
    schema_version: Literal[1] = 1
    status: Literal["passed"]
    code: str
    index_identity: str = Field(pattern=_SHA256)
    canonical_sha256: str = Field(pattern=_SHA256)
    return_sha256: str = Field(pattern=_SHA256)
    deferred_valid: bool
    ontology_writes: Literal[False]
    readiness: Literal[False]
    publication: Literal[False]
    validation_identity: str = Field(pattern=_SHA256)


def _canonical(value: object) -> bytes:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return (
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            default=lambda item: (
                item.model_dump(mode="json")
                if isinstance(item, BaseModel)
                else TypeError(type(item).__name__)
            ),
        ).encode()
        + b"\n"
    )


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return f"external/{path.name}"


def _identity_without(payload: dict[str, object], field: str) -> str:
    return _sha(
        _canonical({key: value for key, value in payload.items() if key != field})
    )


def _write_dispatch_bundle(
    *,
    packet_directory: Path,
    index: PacketIndex,
    packet_payloads: dict[str, bytes],
) -> None:
    dispatch_directory = packet_directory.parent / "m1-6-specialist-dispatch"
    entries = tuple(
        DispatchManifestEntry(
            code=code,
            path=f"{code}.md",
            sha256=_sha(packet_payloads[f"{code}.md"]),
        )
        for code in index.release_ready_codes
    )
    values: dict[str, object] = {
        "schema_version": 1,
        "recipient": "OntoPrism project coordinator",
        "instruction": ReturnChannel().instruction,
        "dispatch_ready": True,
        "release_ready_codes": index.release_ready_codes,
        "packets": entries,
        "index_identity": index.index_identity,
        "manifest_identity": "0" * 64,
    }
    values["manifest_identity"] = _identity_without(values, "manifest_identity")
    payloads = {
        **{
            f"{code}.md": packet_payloads[f"{code}.md"]
            for code in index.release_ready_codes
        },
        "dispatch-manifest.json": _canonical(DispatchManifest.model_validate(values)),
    }
    dispatch_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{dispatch_directory.name}.", dir=dispatch_directory.parent
        )
    )
    backup = dispatch_directory.with_name(f".{dispatch_directory.name}.previous")
    try:
        for name, payload in payloads.items():
            (temporary / name).write_bytes(payload)
        if backup.exists():
            shutil.rmtree(backup)
        if dispatch_directory.exists():
            os.replace(dispatch_directory, backup)
        os.replace(temporary, dispatch_directory)
        if backup.exists():
            shutil.rmtree(backup)
    except BaseException:
        if not dispatch_directory.exists() and backup.exists():
            os.replace(backup, dispatch_directory)
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    validate_dispatch_bundle(
        dispatch_directory=dispatch_directory,
        packet_directory=packet_directory,
        index=index,
    )


def validate_dispatch_bundle(
    *, dispatch_directory: Path, packet_directory: Path, index: PacketIndex
) -> DispatchManifest:
    """Parse strictly and independently recompute the dispatch identity chain."""
    manifest = DispatchManifest.model_validate_json(
        (dispatch_directory / "dispatch-manifest.json").read_bytes()
    )
    values = manifest.model_dump(mode="json")
    if _identity_without(values, "manifest_identity") != manifest.manifest_identity:
        raise ValueError("dispatch manifest identity mismatch")
    if (
        manifest.index_identity != index.index_identity
        or manifest.release_ready_codes != index.release_ready_codes
    ):
        raise ValueError("dispatch manifest is not bound to the packet index")
    expected_files = {
        "dispatch-manifest.json",
        *(item.path for item in manifest.packets),
    }
    if {
        path.name for path in dispatch_directory.iterdir() if path.is_file()
    } != expected_files:
        raise ValueError("dispatch directory contains an unexpected file set")
    for item in manifest.packets:
        dispatched = (dispatch_directory / item.path).read_bytes()
        canonical = (packet_directory / item.path).read_bytes()
        if dispatched != canonical or _sha(dispatched) != item.sha256:
            raise ValueError(f"dispatch packet identity mismatch: {item.path}")
    return manifest


def _labels(raw_inputs: tuple[Any, ...]) -> tuple[dict[str, str], dict[str, str]]:
    labels: dict[str, str] = {}
    role_labels: dict[str, str] = {}
    draft = next(
        (
            item
            for item in raw_inputs
            if isinstance(item, dict)
            and isinstance(item.get("concepts"), dict)
            and "_meta" in item
        ),
        None,
    )
    if not isinstance(draft, dict):
        return labels, role_labels
    for concept in draft["concepts"].values():
        if not isinstance(concept, dict):
            continue
        if isinstance(concept.get("genus"), dict):
            labels[str(concept["genus"]["code"])] = str(concept["genus"]["label"])
        if isinstance(concept.get("morphology"), dict):
            labels[str(concept["morphology"]["code"])] = str(
                concept["morphology"]["label"]
            )
        for bucket in concept.get("review_buckets", {}).values():
            for item in bucket:
                if not {"axis", "filler", "filler_label"} <= set(item):
                    continue
                labels[str(item["filler"])] = str(item["filler_label"])
                axis = str(item["axis"])
                if axis.startswith("R"):
                    role_labels[axis] = str(item["axis_label"])
    return labels, role_labels


def _source_definition_ids(
    raw_inputs: tuple[Any, ...],
) -> dict[tuple[str, str, str], tuple[str, ...]]:
    """Index definition facts retained by the bound current-engine evidence."""
    evidence = next(
        (
            item
            for item in raw_inputs
            if isinstance(item, dict)
            and isinstance(item.get("artifact_identity"), str)
            and isinstance(item.get("concepts"), list)
            and any(
                isinstance(concept, dict) and "constituents" in concept
                for concept in item["concepts"]
            )
        ),
        None,
    )
    if not isinstance(evidence, dict):
        return {}
    return {
        (str(concept["code"]), str(item["axis"]), str(item["filler"])): tuple(
            str(identity) for identity in item.get("source_definition_ids", ())
        )
        for concept in evidence["concepts"]
        if isinstance(concept, dict)
        for item in concept.get("constituents", ())
        if isinstance(item, dict) and item.get("source_definition_ids")
    }


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _ncit_metadata(
    path: Path, wanted: set[str]
) -> tuple[dict[str, str], dict[str, str]]:
    labels: dict[str, str] = {}
    definitions: dict[str, str] = {}
    about = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about"
    for _event, element in iterparse(path, events=("end",)):
        iri = element.attrib.get(about)
        if iri:
            code = iri.rsplit("#", 1)[-1]
            if code in wanted:
                for child in element:
                    local = child.tag.rsplit("}", 1)[-1]
                    text = (child.text or "").strip()
                    if local == "label" and text:
                        labels[code] = text
                    elif local == "P97" and text and code not in definitions:
                        definitions[code] = text
            element.clear()
    missing = sorted(wanted - set(labels))
    if missing:
        raise ValueError(f"NCIt source lacks exact labels for packet codes: {missing}")
    return labels, definitions


def validate_source_preferred_labels(
    context: GeneratedLiteratureContext, source_labels: dict[str, str]
) -> None:
    """Reject literature display labels that differ from stated NCIt labels."""
    mismatches = tuple(
        dossier.code
        for dossier in context.dossiers
        if dossier.code in source_labels
        and dossier.exact_label != source_labels[dossier.code]
    )
    if mismatches:
        raise ValueError(
            "literature/context label does not match NCIt stated preferred label: "
            + ", ".join(mismatches)
        )


def filter_governed_pairs(
    *,
    relations: tuple[tuple[tuple[str, str], Relation], ...],
    registered_mints: set[str],
    range_status: dict[tuple[str, str], str],
) -> tuple[
    tuple[tuple[tuple[str, str], Relation], ...],
    tuple[SuppressedCandidate, ...],
    tuple[str, ...],
]:
    """Filter MINT lifecycle/range eligibility before assigning human pair IDs."""
    visible: list[tuple[tuple[str, str], Relation]] = []
    registered_visible: set[str] = set()
    suppressed: list[SuppressedCandidate] = []
    for key, relation in relations:
        filler = key[1]
        if filler.startswith("MINT-"):
            if filler not in registered_mints or range_status.get(key) != "valid":
                suppressed.append(
                    SuppressedCandidate(
                        axis=key[0],
                        generated_id=filler,
                        reason=(
                            "unregistered"
                            if filler not in registered_mints
                            else "range-ineligible"
                        ),
                    )
                )
                continue
            registered_visible.add(filler)
        visible.append((key, relation))
    return tuple(visible), tuple(suppressed), tuple(sorted(registered_visible))


def _axis_governance(axis: str) -> str:
    contract = AXIS_CONTRACTS[axis]
    governance = contract.governance
    if governance.status == "stable":
        return "stable"
    return (
        f"provisional since {governance.since.isoformat()}; review by "
        f"{governance.review_by.isoformat()}; trigger={governance.review_trigger}; "
        f"evidence_count={governance.evidence_count}"
    )


def _axis_fallback(axis: str) -> str:
    governance = AXIS_CONTRACTS[axis].governance
    if governance.status == "stable":
        return "none (stable contract)"
    return (
        f"{governance.fallback_axis}; needsReview="
        f"{str(governance.fallback_needs_review).lower()}"
    )


def _allowed_actions_for_relation(relation: Relation) -> tuple[str, ...]:
    if relation in {"expected-matched-scoreable", "current-only-scoreable"}:
        return ("RETAIN-SCOREABLE", "REMOVE-FROM-PROJECTION")
    if relation in {
        "expected-emitted-review-bearing",
        "current-only-review-bearing",
    }:
        return ("PROMOTE-SCOREABLE", "REMOVE-FROM-PROJECTION")
    return ()


def _claim_contests_projection(
    dossier: LiteratureDossierSource,
    *,
    filler: str,
    filler_label: str,
) -> bool:
    meaningful = {
        token
        for token in re.findall(r"[a-z0-9]+", filler_label.lower())
        if len(token) >= _MIN_SCOPE_TOKEN_LENGTH
        and token
        not in {
            "carcinoma",
            "clinical",
            "finding",
            "gland",
            "lesion",
            "neoplasm",
            "thyroid",
        }
    }
    for context in dossier.factual_context:
        lowered = context.lower()
        if filler in context and "requires explicit review" in lowered:
            return True
        if "not presumed universal" in lowered and any(
            token in lowered for token in meaningful
        ):
            return True
    return False


def _build_rows(  # noqa: PLR0915
    context: GeneratedLiteratureContext,
    raw_inputs: tuple[Any, ...],
    registered_mints: set[str],
    ncit_labels: dict[str, str] | None = None,
    ncit_definitions: dict[str, str] | None = None,
) -> tuple[
    tuple[SpecialistRowPacket, ...],
    dict[str, tuple[SuppressedCandidate, ...]],
    tuple[str, ...],
]:
    group = next(
        (
            item
            for item in raw_inputs
            if isinstance(item, dict)
            and item.get("schema_version") == _GROUP_PACKET_SCHEMA
            and "review_boundary" in item
        ),
        None,
    )
    diagnostic = next(
        (
            item
            for item in raw_inputs
            if isinstance(item, dict)
            and item.get("schema_version") == _DIAGNOSTIC_SCHEMA
            and "candidate_rows" in item
        ),
        None,
    )
    if not isinstance(group, dict) or not isinstance(diagnostic, dict):
        raise ValueError("group packet and axis diagnostics are required")
    labels, role_labels = _labels(raw_inputs)
    retained_definition_ids = _source_definition_ids(raw_inputs)
    labels.update(ncit_labels or {})
    role_labels.update(
        {
            code: label
            for code, label in (ncit_labels or {}).items()
            if code.startswith("R")
        }
    )
    definitions = ncit_definitions or {}
    ranges = {
        (item["code"], item["axis"], item["filler"]): item
        for item in diagnostic["range_diagnostics"]
    }
    candidates = {
        (item["code"], item["expected"]["axis"], item["expected"]["filler"]): item
        for item in diagnostic["candidate_rows"]
    }
    relation_fields: tuple[tuple[str, Relation], ...] = (
        ("expected_matched_scoreable", "expected-matched-scoreable"),
        ("expected_emitted_review_bearing", "expected-emitted-review-bearing"),
        ("expected_not_emitted", "expected-not-emitted"),
        ("current_only_scoreable", "current-only-scoreable"),
        ("current_only_review_bearing", "current-only-review-bearing"),
        ("current_only_proposed", "current-only-proposed"),
    )
    dossiers = {row.code: row for row in context.dossiers}
    rows: list[SpecialistRowPacket] = []
    suppression: dict[str, tuple[SuppressedCandidate, ...]] = {}
    visible_registered: set[str] = set()
    for code in CONCEPT_ORDER:
        dossier = dossiers[code]
        claimed_keys = {
            (claim.pair_key.axis, claim.pair_key.filler)
            for question in dossier.questions
            for claim in question.claims
        }
        claims_by_key = {
            key: tuple(
                (question.question_id, claim)
                for question in dossier.questions
                for claim in question.claims
                if (claim.pair_key.axis, claim.pair_key.filler) == key
            )
            for key in {
                (claim.pair_key.axis, claim.pair_key.filler)
                for question in dossier.questions
                for claim in question.claims
            }
        }
        primary_claim_by_key = {
            key: records[0][1] for key, records in claims_by_key.items()
        }
        concept = next(item for item in group["concepts"] if item["code"] == code)
        occurrences: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for actual_group in concept["actual_groups"]:
            for item in actual_group["pairs"]:
                occurrences.setdefault(tuple(item["pair"]), []).extend(
                    item.get("occurrences", ())
                )
        for item in concept["non_scoreable_emitted_pairs"]:
            occurrences.setdefault(tuple(item["pair"]), []).extend(
                item.get("source_occurrences", ())
            )
        expected_relations = [
            (tuple(pair), relation)
            for field, relation in relation_fields[:3]
            for pair in concept["pair_relations"][field]
        ]
        current_relations = [
            (tuple(pair), relation)
            for field, relation in relation_fields[3:]
            for pair in concept["pair_relations"][field]
        ]
        relations = [
            *sorted(expected_relations, key=lambda item: item[0]),
            *sorted(current_relations, key=lambda item: item[0]),
        ]
        visible, suppressed_count, row_registered_visible = filter_governed_pairs(
            relations=tuple(
                (raw_key, cast("Relation", relation)) for raw_key, relation in relations
            ),
            registered_mints=registered_mints,
            range_status={
                (axis, filler): ranges[(code, axis, filler)]["verdict"]["status"]
                for axis, filler in (key for key, _relation in relations)
                if (code, axis, filler) in ranges
            },
        )
        visible_registered.update(row_registered_visible)
        suppression[code] = suppressed_count
        pairs: list[SpecialistPair] = []
        blockers: dict[str, str] = {}
        for number, (raw_key, relation) in enumerate(visible, start=1):
            axis, filler = raw_key
            key = PairKey(axis=axis, filler=filler)
            diagnostic_row = ranges.get((code, axis, filler))
            range_status = (
                diagnostic_row["verdict"]["status"] if diagnostic_row else "unknown"
            )
            source_rows: list[dict[str, Any]] = occurrences.get(raw_key, [])
            candidate = candidates.get((code, axis, filler))
            source_evidence = candidate.get("source_evidence", {}) if candidate else {}
            definition_ids = tuple(
                str(value) for value in source_evidence.get("source_definition_ids", ())
            ) or retained_definition_ids.get((code, axis, filler), ())
            if not source_rows:
                source_rows = source_evidence.get("occurrences", [])
            source_status = (
                "available"
                if source_rows
                else (
                    "source-backed-coordinate-missing"
                    if definition_ids
                    else str(source_evidence.get("status", "unavailable"))
                )
            )
            source_reason = (
                "exact-occurrence-coordinates"
                if source_rows
                else str(
                    source_evidence.get(
                        "reason",
                        (
                            "definition-fact-retained-without-occurrence-coordinate"
                            if definition_ids
                            else "no-matching-stated-definition-fact"
                        ),
                    )
                )
            )
            verdict = classify_pair_scope(
                PairScopeInput(
                    relation=relation,
                    range_verdict=cast(
                        "Literal['valid', 'invalid', 'unknown']", range_status
                    ),
                    source_evidence_status=cast(
                        "Literal['available', 'source-backed-coordinate-missing', 'unavailable']",
                        source_status,
                    ),
                    diagnostic_classification=cast(
                        "Literal['added', 'matched-scoreable', 'emitted-review-bearing', 'current-only-scoreable', 'current-only-review-bearing', 'current-only-proposed', 'selection-miss', 'extraction-miss', 'not-diagnosed', 'proposal-only', 'unavailable-source-evidence']",
                        str(candidate.get("classification"))
                        if candidate
                        else "not-diagnosed",
                    ),
                    has_clinical_claim=(axis, filler) in claimed_keys,
                    claim_contests_projection=(
                        (axis, filler) in primary_claim_by_key
                        and _claim_contests_projection(
                            dossier,
                            filler=filler,
                            filler_label=labels.get(filler, ""),
                        )
                    ),
                    action_representable=bool(_allowed_actions_for_relation(relation)),
                    governance_status="eligible",
                )
            )
            scope = cast(
                "ReviewScope",
                {
                    "actionable": "stage-a-and-stage-b",
                    "clinical-only": "stage-a-clinical-only",
                    "engineering-only": "engineering-only",
                    "context": "context-not-under-review",
                    "refused-invalid": "engineering-only",
                    "suppressed": "engineering-only",
                }[verdict.status],
            )
            source_occurrences = tuple(
                SourceOccurrence(
                    occurrence_id=str(item["occurrence_id"]),
                    anchor_code=str(item["anchor_code"]),
                    anchor_label=labels.get(
                        str(item["anchor_code"]),
                        f"Label unavailable for {item['anchor_code']} in bound label source",
                    ),
                    depth=int(item["depth"]),
                    role_code=str(item["role_code"]),
                    structural_path=tuple(
                        int(value) for value in item["structural_path"]
                    ),
                    member_position=int(item["member_position"]),
                )
                for item in source_rows
            )
            pair_id = f"P{number}"
            if verdict.engineering_blocker:
                blockers[pair_id] = verdict.engineering_blocker
            projection = (
                diagnostic_row["current_projection_status"]
                if diagnostic_row
                else (
                    "scoreable-release-bound"
                    if relation
                    in {"expected-matched-scoreable", "current-only-scoreable"}
                    else "not-emitted"
                )
            )
            role_codes = sorted({item.role_code for item in source_occurrences})
            role_code = (
                ", ".join(role_codes)
                if role_codes
                else ("rdfs:subClassOf" if definition_ids else "unavailable")
            )
            pairs.append(
                SpecialistPair(
                    pair_id=pair_id,
                    key=key,
                    relation=relation,
                    scope_verdict=verdict.status,
                    review_scope=scope,
                    scope_reason=verdict.reason,
                    contested=(axis, filler) in claimed_keys,
                    filler_label=labels.get(
                        filler, f"Label unavailable for {filler} in bound label source"
                    ),
                    filler_definition=definitions.get(
                        filler,
                        "Explicitly unavailable: this NCIt concept has no P97 definition in the bound 26.07d source.",
                    ),
                    source_role_code=role_code,
                    source_role_label="; ".join(
                        role_labels.get(value, f"Role label unavailable for {value}")
                        for value in role_codes
                    )
                    if role_codes
                    else (
                        "Named parent definition operand"
                        if definition_ids
                        else "No source role occurrence available"
                    ),
                    source_role_definition="; ".join(
                        definitions.get(
                            value,
                            f"Explicitly unavailable: {value} has no P97 definition in the bound 26.07d source.",
                        )
                        for value in role_codes
                    )
                    if role_codes
                    else (
                        "The named parent is retained as a stated definition fact without a role occurrence coordinate."
                        if definition_ids
                        else "Explicitly unavailable: no source role occurrence exists for this pair."
                    ),
                    source_occurrences=source_occurrences,
                    source_evidence_status=cast(
                        "Literal['available', 'source-backed-coordinate-missing', 'unavailable']",
                        source_status,
                    ),
                    source_evidence_reason=source_reason,
                    source_definition_ids=definition_ids,
                    current_projection_status=str(projection),
                    axis_range_verdict=range_status,
                    modality=AXIS_CONTRACTS[axis].modality,
                    derivation="direct"
                    if any(item.depth == 0 for item in source_occurrences)
                    else (
                        "inherited"
                        if source_occurrences
                        else (
                            "named-parent"
                            if definition_ids
                            else "source-coordinate-unavailable"
                        )
                    ),
                    governance=_axis_governance(axis),
                    fallback=_axis_fallback(axis),
                )
            )
        asked_keys = {
            (pair.key.axis, pair.key.filler)
            for pair in pairs
            if pair.review_scope in {"stage-a-and-stage-b", "stage-a-clinical-only"}
        }
        question_keys = tuple(
            tuple(
                PairKey(axis=key.axis, filler=key.filler)
                for key in question.pair_keys
                if (key.axis, key.filler) in asked_keys
            )
            for question in dossier.questions
            if any((key.axis, key.filler) in asked_keys for key in question.pair_keys)
        )
        rows.append(
            SpecialistRowPacket(
                code=code,
                label=dossier.exact_label,
                definition=dossier.exact_definition,
                pairs=tuple(pairs),
                question_pair_keys=question_keys,
                engineering_blockers=blockers,
            )
        )
    return tuple(rows), suppression, tuple(sorted(visible_registered))


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def _allowed_actions(pair: SpecialistPair) -> tuple[str, ...]:
    return _allowed_actions_for_relation(pair.relation)


def pair_consequences(pair: SpecialistPair) -> dict[str, ActionConsequence]:
    """Return only actions valid for this relation and stored range evidence."""
    relation = pair.relation
    deltas: dict[str, tuple[int, int, int, int, str, bool, str]] = {
        "RETAIN-SCOREABLE": (0, 0, 0, 0, "scoreable-retained", False, "unchanged"),
        "REMOVE-FROM-PROJECTION": (
            -1 if relation == "expected-matched-scoreable" else 0,
            -1 if relation == "current-only-scoreable" else 0,
            1 if relation == "expected-matched-scoreable" else 0,
            -1
            if relation in {"expected-matched-scoreable", "current-only-scoreable"}
            else 0,
            "projection-removed-source-retained",
            True,
            "unchanged",
        ),
        "PROMOTE-SCOREABLE": (
            1 if relation == "expected-emitted-review-bearing" else 0,
            1 if relation == "current-only-review-bearing" else 0,
            -1 if relation == "expected-emitted-review-bearing" else 0,
            1,
            "scoreable-promoted",
            False,
            "assigned-by-reviewer",
        ),
    }
    return {
        action: ActionConsequence(
            comparison_tp_delta=values[0],
            comparison_fp_delta=values[1],
            comparison_fn_delta=values[2],
            scoreable_emitted_delta=values[3],
            source_preserved=True,
            pair_after=values[4],
            needs_review_after=values[5],
            group_effect=values[6],
            row_readiness=False,
            publication=False,
        )
        for action in _allowed_actions(pair)
        if (values := deltas[action])
    }


def allowed_partition_modes(
    action_pairs: tuple[SpecialistPair, ...],
) -> tuple[
    Literal[
        "RETAIN-CURRENT",
        "GROUP-SPECIFIED-PAIRS-TOGETHER",
        "KEEP-SPECIFIED-PAIRS-SEPARATE",
        "CUSTOM-CURRENT-MODEL",
        "EMPTY",
    ],
    ...,
]:
    """Derive partition modes from possible indexed pair actions."""
    modes: tuple[
        Literal[
            "RETAIN-CURRENT",
            "GROUP-SPECIFIED-PAIRS-TOGETHER",
            "KEEP-SPECIFIED-PAIRS-SEPARATE",
            "CUSTOM-CURRENT-MODEL",
            "EMPTY",
        ],
        ...,
    ] = (
        "RETAIN-CURRENT",
        "GROUP-SPECIFIED-PAIRS-TOGETHER",
        "KEEP-SPECIFIED-PAIRS-SEPARATE",
        "CUSTOM-CURRENT-MODEL",
        "EMPTY",
    )
    if any("PROMOTE-SCOREABLE" in _allowed_actions(pair) for pair in action_pairs):
        return tuple(mode for mode in modes if mode != "RETAIN-CURRENT")
    return modes


def context_correction_note(context_count: int) -> str:
    """Report observed context-pair liveness without overstating schema support."""
    if context_count < 0:
        raise ValueError("context count cannot be negative")
    if context_count == 0:
        return (
            "No context-only pairs occur in this seven-row generation; "
            "context-correction support is schema-tested but not exercised by this bundle."
        )
    if context_count == 1:
        return (
            "This packet contains one context-only pair. Its optional context-correction "
            "response region is schema-supported and does not request a clinical answer "
            "or ontology action."
        )
    return (
        f"This packet contains {context_count} context-only pairs. Their optional "
        "context-correction response regions are schema-supported and do not request "
        "clinical answers or ontology actions."
    )


def _render_partition(
    partition: object, pair_id_by_key: dict[tuple[str, str], str]
) -> str:
    groups = cast("list[list[list[str]]]", partition)
    rendered = []
    for number, group in enumerate(groups, start=1):
        members = "; ".join(
            f"{pair_id_by_key.get((axis, filler), 'UNINDEXED')} {axis} {filler}"
            for axis, filler in group
        )
        rendered.append(f"G{number}={{{members}}}")
    return "; ".join(rendered) or "none"


def _render_packet(  # noqa: C901, PLR0912
    row: SpecialistRowPacket,
    dossier: LiteratureDossierSource,
    cadsr: CadsrUsageRow,
    cadsr_report: SpecialistCadsrUsageReport,
    actual_partition: object,
    expected_partition: object,
    suppressed_candidates: tuple[SuppressedCandidate, ...],
    dispatch: DispatchDecision,
    row_contract_identity: str,
    partition_modes: tuple[str, ...],
) -> bytes:
    suppressed_axes: dict[str, int] = {}
    for candidate in suppressed_candidates:
        suppressed_axes[candidate.axis] = suppressed_axes.get(candidate.axis, 0) + 1
    axis_lines = []
    for axis in sorted({pair.key.axis for pair in row.pairs}):
        contract = AXIS_CONTRACTS[axis]
        axis_lines.append(
            f"- `{axis}` — {contract.label}: {contract.definition} Governance: {_axis_governance(axis)}. Fallback: {_axis_fallback(axis)}. Modality: {contract.modality}."
        )
    pair_id_by_key = {
        (pair.key.axis, pair.key.filler): pair.pair_id for pair in row.pairs
    }
    lines = [
        f"# {row.code} — {row.label}",
        "",
        (
            "**Blank specialist packet.** Current NCIt 26.07d baseline only. Historical proposals are warnings, not selected responses. This packet is write-free; ontology/publication readiness is false and it cannot authorize equivalence, adoption, or publication. Dispatch readiness is separately recorded in the dispatch manifest."
            if dispatch.status == "dispatchable"
            else "**NOT FOR DISPATCH.** Read-only engineering-coordination dossier. It contains no attestation, response, return, or ontology-action request and must be regenerated after every withholding reason is resolved."
        ),
        f"**Dispatch status:** {dispatch.status}",
        *(f"- **Withholding reason:** {reason}" for reason in dispatch.reasons),
        f"**Workload:** asked={len(row.asked_pair_ids)}; action={len(row.action_pair_ids)}; engineering={len(row.engineering_pair_ids)}; context={len(row.context_pair_ids)}.",
        context_correction_note(len(row.context_pair_ids)),
        *(
            [
                "**No answer changes the projection this cycle because this row has zero ontology-action pairs. Stage A still informs the owned engineering repair.**"
            ]
            if not row.action_pair_ids
            else []
        ),
        "",
        *(
            [
                "## Return workflow",
                "",
                f"1. {ReturnChannel().instruction}",
                f"2. {ReturnChannel().fallback} {ReturnChannel().deadline}",
                "3. Edit as UTF-8. Preserve the filename and every visible ONTOPRISM marker. Edit only the text inside response regions; paragraphs and the `|` character are allowed. Stage A is clinical classification; Stage B is ontology action; separately complete both role attestations if one person performs both.",
                "4. Leave everything outside response regions unchanged, request receipt confirmation, and retain that confirmation. The OntoPrism project coordinator performs independent row validation after return.",
                "",
                f"Packet reference: {row.code} / NCIt 26.07d / {row_contract_identity[:12]}. This short contract reference is non-authoritative; the coordinator validates the full packet SHA-256 and manifest identity.",
                f"Expected post-return output (not a current bundle file): {row.code}.validation.json.",
                "",
            ]
            if dispatch.status == "dispatchable"
            else []
        ),
        "## Source-bound concept",
        "",
        f"**Exact label:** {row.label}",
        f"**Exact definition:** {row.definition}",
        "",
        "## caDSR usage",
        "",
        f"**Status:** {cadsr.status}  ",
        f"**Linked CDE IDs (bounded):** {', '.join(cadsr.cde_ids) or 'none'}  ",
        *(
            f"- CDE {item.public_id} v{item.version}: {item.long_name} ({item.short_name}); context={item.context or 'unavailable'}; datatype={item.datatype or 'unavailable'}"
            for item in cadsr.cdes
        ),
        f"**Truncated:** {str(cadsr.truncated).lower()}  ",
        f"**Bound report source identity:** {cadsr_report.source_identity}  ",
        f"**Bound database provenance:** database={cadsr_report.database_path}; database SHA-256={cadsr_report.database_sha256}; source identity={cadsr_report.source_identity}; source={cadsr_report.source_provenance}; query identity={cadsr_report.query_identity}; report identity={cadsr_report.report_identity}; query limit={cadsr_report.query_limit}  ",
        "caDSR usage does not determine clinical or ontology correctness.",
        "",
        "## Audited factual context",
        "",
        *(f"- {item}" for item in dossier.factual_context),
        "",
        "## Axis contract legend",
        "",
        "D23 governs these univocal axes: R101 resolves to the named organ; extent/spread belongs to stage; metastasis belongs to a separate site axis; same anatomy may legitimately appear on multiple axes.",
        *axis_lines,
        "",
        "## Pair inventory",
        "",
        "| Pair | Semantic key and exact filler | Relation / scope / contested | Source role and coordinates | Projection / range | Modality / governance / fallback |",
        "|---|---|---|---|---|---|",
    ]
    for pair in row.pairs:
        coordinates = (
            "; ".join(
                f"{item.anchor_code} {item.anchor_label}, depth={item.depth}, path={list(item.structural_path)}, member={item.member_position}"
                for item in pair.source_occurrences
            )
            or "explicitly unavailable"
        )
        lines.append(
            f"| {pair.pair_id} | `{pair.key.axis} {pair.key.filler}` — {_escape(pair.filler_label)}; P97: {_escape(pair.filler_definition)} | {pair.relation}; **{pair.review_scope}**; reason={_escape(pair.scope_reason)}; contested={str(pair.contested).lower()} | {pair.source_role_code} {_escape(pair.source_role_label)}; definition={_escape(pair.source_role_definition)}; evidence={pair.source_evidence_status}; reason={_escape(pair.source_evidence_reason)}; source_definition_ids={','.join(pair.source_definition_ids) or 'none'}; {coordinates} | {pair.current_projection_status}; {pair.axis_range_verdict} | modality={pair.modality}; derivation={pair.derivation}; governance={pair.governance}; fallback={pair.fallback} |"
        )
    lines.extend(
        [
            "",
            f"Suppression disclosure: the complete machine inventory included {len(suppressed_candidates)} withheld unregistered or range-ineligible generated candidate(s) ({', '.join(f'{axis}={count}' for axis, count in sorted(suppressed_axes.items())) or 'no affected axis'}); the reviewer action inventory excludes them and identifiers are intentionally absent from this human packet.",
            "",
            "**Pairs absent from the current projection remain engineering-owned; this packet offers no human action that creates or silently ignores an absent pair.**",
            "",
            "**Current metric identity/context:** pair precision, recall, and F1 over the bound current comparison; row-local potential changes are bounded to one TP/FP/FN contribution per pair. No predicted post-review values are rendered.",
            "",
            "**Current scoreable baseline partition:** "
            + _render_partition(actual_partition, pair_id_by_key),
            "",
            "**Historical proposal warning and partition:** admitted historical witness only; not source-stated grouping or authority. "
            + _render_partition(expected_partition, pair_id_by_key),
            "",
            "## Literature dossier",
            "",
            "| ID / status / authority | Full bibliography / URL / DOI / PMID | Verified / exact locator / exact passage | Supports | Does not support | Limitations / conflicts or supersession |",
            "|---|---|---|---|---|---|",
        ]
    )
    for citation in dossier.citations:
        verification = citation.verified_on or "NOT VERIFIED"
        lines.append(
            f"| {citation.citation_id}; {citation.status}; {citation.authority_class} #{citation.authority_order} | {_escape(citation.bibliography)}; {citation.url}; DOI={citation.doi or 'none'}; PMID={citation.pmid or 'none'} | {verification}; {_escape(citation.exact_locator)}; {_escape(citation.exact_passage)} | {_escape(citation.supports)} | {_escape(citation.does_not_support)} | {_escape(citation.limitations)}; {_escape(citation.conflicts_or_supersession)} |"
        )
    lines.extend(
        [
            "",
            "### Limitations / does not support",
            "",
            *(
                f"- **{citation.citation_id}:** Does not support: {citation.does_not_support} Limitations: {citation.limitations}"
                for citation in dossier.citations
            ),
        ]
    )
    lines.extend(
        [
            "",
            "## Engineering blockers and consequences",
            "",
            *(
                f"- **{pair_id}:** {blocker}. Consequence: no clinical response creates an ontology action; current projection and metrics remain unchanged and readiness remains false."
                for pair_id, blocker in sorted(row.engineering_blockers.items())
            ),
            *(
                ["None for this packet generation."]
                if not row.engineering_blockers
                else []
            ),
            "",
        ]
    )
    if dispatch.status == "withheld":
        lines.extend(
            [
                "## NOT FOR DISPATCH",
                "",
                "Clinical questions remain documented in the bound literature dossier for engineering coordination only. No Stage A or Stage B response, attestation, action, or return instruction is requested in this withheld packet.",
                "",
            ]
        )
        return "\n".join(lines).encode()
    lines.extend(
        [
            "## Stage A — Clinical review",
            "",
            f"Required specialty: {dossier.specialty}",
            "Allowed statuses: UNIVERSAL-DEFINING (part of every definition); UNIVERSAL-NONDEFINING (present in every case but not defining); CHARACTERISTIC-NONUNIVERSAL (frequent, not universal); CLASSIFICATION-DEPENDENT (varies by named system/version/entity); INAPPLICABLE; UNRESOLVED.",
            "Stage A records clinical evidence only and performs no ontology action.",
            "If Clinical stage is DEFERRED, leave every pair assessment empty and provide the whole-row blocker. Otherwise complete every asked pair assessment.",
            "",
            "[[ONTOPRISM:STAGE-A:START]]",
            "Attester name:",
            "Attester capacity:",
            "Attestation date (YYYY-MM-DD):",
            "Conflict of interest:",
            "Source confirmation:",
            "Human attestation (TRUE):",
            "Clinical stage (SUFFICIENT-FOR-ONTOLOGY-REVIEW, CLINICAL-COMPLETE-ENGINEERING-PENDING, or DEFERRED):",
            "Whole-row blocker if DEFERRED:",
            "[[ONTOPRISM:STAGE-A:END]]",
        ]
    )
    asked = set(row.asked_pair_ids)
    question_by_pair = {
        pair_id_by_key[(key.axis, key.filler)]: question
        for question in dossier.questions
        for key in question.pair_keys
        if pair_id_by_key.get((key.axis, key.filler)) in asked
    }
    for pair in row.pairs:
        if pair.pair_id in asked:
            question = question_by_pair[pair.pair_id]
            lines.extend(
                [
                    "",
                    f"### Clinical question for {pair.pair_id}",
                    "",
                    f"Within **{row.label}**, how broadly does **{pair.filler_label}** apply in the clinical meaning represented by `{pair.key.axis} {pair.key.filler}`? Evaluate the cited pair-specific evidence for this exact concept scope.",
                    "",
                    "Supporting source facts:",
                    *(
                        f"- **{claim.citation_id}:** {claim.source_fact}"
                        for claim in question.claims
                        if (claim.pair_key.axis, claim.pair_key.filler)
                        == (pair.key.axis, pair.key.filler)
                    ),
                    "",
                    f"[[ONTOPRISM:STAGE-A-PAIR:{pair.pair_id}:START]]",
                    "Status:",
                    "Citations:",
                    "Rationale:",
                    f"[[ONTOPRISM:STAGE-A-PAIR:{pair.pair_id}:END]]",
                ]
            )
    if row.context_pair_ids:
        lines.extend(["", "## Context pairs not under review", ""])
        for pair_id in row.context_pair_ids:
            lines.extend(
                [
                    f"- **{pair_id}: context-not-under-review.** No clinical answer or ontology action is requested. A factual correction is optional.",
                    f"[[ONTOPRISM:CONTEXT-CORRECTION:{pair_id}:START]]",
                    "",
                    f"[[ONTOPRISM:CONTEXT-CORRECTION:{pair_id}:END]]",
                ]
            )
    lines.append("")
    action_ids = set(row.action_pair_ids)
    if action_ids:
        lines.extend(
            [
                "## Stage B — Ontology review",
                "",
                "Stage B mode: ontology-review.",
                "Requires sufficient Stage A. Exact pair actions below are the complete allowed set. Pair disposition and partition disposition are independent: neither can substitute for the other. No response asserts adoption, equivalence, or publication.",
                "Whole-row DEFER applies no changes to the current projection, final partition, or TP/FP/FN/emitted-scoreable metrics; readiness remains false.",
                "If ROW-OUTCOME is DEFERRED, leave every pair disposition and the partition disposition empty and complete blocker, blocker source, and next action.",
                "",
                "[[ONTOPRISM:STAGE-B:START]]",
                "Attester name:",
                "Attester capacity:",
                "Attestation date (YYYY-MM-DD):",
                "Conflict of interest:",
                "Source confirmation:",
                "Human attestation (TRUE):",
                "ROW-OUTCOME (RESOLVED or DEFERRED):",
                "Whole-row blocker if DEFERRED:",
                "Blocker source if DEFERRED:",
                "Next action if DEFERRED:",
                "[[ONTOPRISM:STAGE-B:END]]",
            ]
        )
        for pair in row.pairs:
            if pair.pair_id not in action_ids:
                continue
            lines.extend(["", f"### Ontology action for {pair.pair_id}"])
            lines.append(f"Allowed actions: {', '.join(_allowed_actions(pair))}")
            for action, value in pair_consequences(pair).items():
                lines.append(
                    f"- **{action}:** comparison-relative TP={value.comparison_tp_delta:+d}; FP={value.comparison_fp_delta:+d}; FN={value.comparison_fn_delta:+d}; scoreable-emitted={value.scoreable_emitted_delta:+d}; source-preserved=true; pair-after={value.pair_after}; needsReview-after={str(value.needs_review_after).lower()}; group-effect={value.group_effect}; row-readiness=false; publication=false; adoption/equivalence=not asserted."
                )
            lines.extend(
                [
                    f"[[ONTOPRISM:STAGE-B-PAIR:{pair.pair_id}:START]]",
                    "Action:",
                    "Rationale:",
                    f"[[ONTOPRISM:STAGE-B-PAIR:{pair.pair_id}:END]]",
                ]
            )
        lines.extend(
            [
                "## Independent partition disposition",
                "The final groups must cover every post-action scoreable pair exactly once, including unchanged baseline context. Removed, non-scoreable, engineering-only, and context-only pairs are forbidden. EMPTY is allowed only when no scoreable pair remains. Partition choices have zero pair-metric deltas.",
                f"Partition modes: {', '.join(partition_modes)}",
                "RETAIN-CURRENT appears only when every allowed pair action preserves the baseline scoreable set. If it is absent, choose one listed mode and provide an exact final total partition.",
                "CUSTOM-CURRENT-MODEL response syntax: after `Groups`, write one complete group per line as semicolon-separated ending-scoreable pair IDs. Every ending-scoreable pair ID must occur exactly once across all lines.",
                "Neutral syntax example using synthetic IDs only: `PX1; PX2` on one line and `PX3` on the next line.",
                "[[ONTOPRISM:PARTITION-DISPOSITION:START]]",
                "Mode:",
                "Groups (one group per line; semicolon-separated pair IDs):",
                "Rationale:",
                "[[ONTOPRISM:PARTITION-DISPOSITION:END]]",
                "",
                "The same individual may attest both only after separately completing both human role blocks.",
            ]
        )
    else:
        lines.extend(
            [
                "## Stage B — not applicable pending engineering",
                "",
                "Stage B mode: not-applicable-pending-engineering. This row has no indexed Stage B action. Stage A may return CLINICAL-COMPLETE-ENGINEERING-PENDING. No second attestation is requested.",
            ]
        )
    lines.append("")
    return "\n".join(lines).encode()


def generate_specialist_review_packets(  # noqa: C901, PLR0912, PLR0915
    *,
    literature_context_path: Path,
    proposal_registry_path: Path,
    cadsr_usage_path: Path,
    output_directory: Path,
    producing_command: str,
    additional_input_paths: tuple[Path, ...] = (),
    ncit_source_path: Path | None = None,
) -> PacketIndex:
    paths = (
        literature_context_path,
        proposal_registry_path,
        cadsr_usage_path,
        *additional_input_paths,
    )
    if len({path.resolve() for path in paths}) != len(paths):
        raise ValueError("specialist packet input paths must be unique")
    payloads = {path: path.read_bytes() for path in paths}
    context = GeneratedLiteratureContext.model_validate_json(
        payloads[literature_context_path]
    )
    cadsr = SpecialistCadsrUsageReport.model_validate_json(payloads[cadsr_usage_path])
    registry = json.loads(payloads[proposal_registry_path])
    registered = {item["id"] for item in registry.get("proposals", ()) if "id" in item}
    raw_inputs = tuple(
        json.loads(payload)
        for path, payload in payloads.items()
        if path
        not in {literature_context_path, proposal_registry_path, cadsr_usage_path}
    )
    group = next(
        item
        for item in raw_inputs
        if isinstance(item, dict)
        and item.get("schema_version") == _GROUP_PACKET_SCHEMA
        and "review_boundary" in item
    )
    identities = {_portable(path): _sha(payload) for path, payload in payloads.items()}
    ncit_labels: dict[str, str] = {}
    ncit_definitions: dict[str, str] = {}
    if ncit_source_path is not None:
        diagnostic = next(
            item
            for item in raw_inputs
            if isinstance(item, dict)
            and item.get("schema_version") == _DIAGNOSTIC_SCHEMA
            and "candidate_rows" in item
        )
        wanted = {
            str(value)
            for concept in group["concepts"]
            if concept["code"] in CONCEPT_ORDER
            for field in concept["pair_relations"].values()
            for pair in field
            for value in pair[1:]
        }
        wanted.update(
            str(value)
            for concept in group["concepts"]
            if concept["code"] in CONCEPT_ORDER
            for actual_group in concept["actual_groups"]
            for pair in actual_group["pairs"]
            for occurrence in pair.get("occurrences", ())
            for value in (occurrence["anchor_code"], occurrence["role_code"])
        )
        wanted.update(
            str(value)
            for candidate in diagnostic["candidate_rows"]
            if candidate["code"] in CONCEPT_ORDER
            for occurrence in candidate["source_evidence"].get("occurrences", ())
            for value in (occurrence["anchor_code"], occurrence["role_code"])
        )
        wanted.update(
            str(value)
            for concept in group["concepts"]
            if concept["code"] in CONCEPT_ORDER
            for pair in concept["non_scoreable_emitted_pairs"]
            for occurrence in pair.get("source_occurrences", ())
            for value in (occurrence["anchor_code"], occurrence["role_code"])
        )
        wanted = {value for value in wanted if value.startswith(("C", "R"))}
        ncit_labels, ncit_definitions = _ncit_metadata(ncit_source_path, wanted)
        validate_source_preferred_labels(context, ncit_labels)
        identities[_portable(ncit_source_path)] = _hash_file(ncit_source_path)
    rows, suppression, visible_registered = _build_rows(
        context, raw_inputs, registered, ncit_labels, ncit_definitions
    )
    dossier_by_code = {item.code: item for item in context.dossiers}
    cadsr_by_code = {item.code: item for item in cadsr.rows}
    packet_payloads: dict[str, bytes] = {}
    entries: list[PacketIndexEntry] = []
    for row in rows:
        concept = next(item for item in group["concepts"] if item["code"] == row.code)
        name = f"{row.code}.md"
        dossier = dossier_by_code[row.code]
        citations = {item.citation_id: item for item in dossier.citations}
        claims_by_key = {
            key: tuple(
                (question.question_id, claim)
                for question in dossier.questions
                for claim in question.claims
                if (claim.pair_key.axis, claim.pair_key.filler) == key
            )
            for key in {
                (claim.pair_key.axis, claim.pair_key.filler)
                for question in dossier.questions
                for claim in question.claims
            }
        }
        pair_id_by_key = {
            (pair.key.axis, pair.key.filler): pair.pair_id for pair in row.pairs
        }
        current_partition = tuple(
            tuple(PairKey(axis=str(key[0]), filler=str(key[1])) for key in group)
            for group in concept["actual_partition"]
        )
        historical_partition = tuple(
            tuple(PairKey(axis=str(key[0]), filler=str(key[1])) for key in group)
            for group in concept["expected_partition"]
        )
        baseline_partition = tuple(
            tuple(pair_id_by_key[(key.axis, key.filler)] for key in group)
            for group in current_partition
            if all((key.axis, key.filler) in pair_id_by_key for key in group)
        )
        baseline_scoreable = tuple(
            pair_id for group_ids in baseline_partition for pair_id in group_ids
        )
        supported_pair_ids = tuple(
            pair.pair_id
            for pair in row.pairs
            if pair.pair_id in row.asked_pair_ids
            and (claim_records := claims_by_key.get((pair.key.axis, pair.key.filler)))
            is not None
            and any(
                citation_supports_pair(
                    question_id=question_id,
                    pair_key=LiteraturePairKey(
                        axis=pair.key.axis, filler=pair.key.filler
                    ),
                    claim=claim,
                    citation=citations[claim.citation_id],
                )
                for question_id, claim in claim_records
            )
        )
        dispatch = derive_dispatch_decision(
            engineering_blockers=row.engineering_blockers,
            asked_pair_ids=row.asked_pair_ids,
            supported_pair_ids=supported_pair_ids,
        )
        action_pairs = tuple(
            pair for pair in row.pairs if pair.pair_id in row.action_pair_ids
        )
        partition_modes = allowed_partition_modes(action_pairs)
        row_contract_identity = _sha(_canonical(row))
        payload = _render_packet(
            row,
            dossier,
            cadsr_by_code[row.code],
            cadsr,
            concept["actual_partition"],
            concept["expected_partition"],
            suppression[row.code],
            dispatch,
            row_contract_identity,
            partition_modes,
        )
        packet_payloads[name] = payload
        entries.append(
            PacketIndexEntry(
                code=row.code,
                path=name,
                row_sha256=_sha(payload),
                row_contract_identity=row_contract_identity,
                asked_pair_ids=row.asked_pair_ids,
                action_pair_ids=row.action_pair_ids,
                clinical_only_pair_ids=row.clinical_only_pair_ids,
                engineering_pair_ids=row.engineering_pair_ids,
                context_pair_ids=row.context_pair_ids,
                workload=PacketWorkload(
                    asked=len(row.asked_pair_ids),
                    action=len(row.action_pair_ids),
                    engineering=len(row.engineering_pair_ids),
                    context=len(row.context_pair_ids),
                ),
                return_channel=ReturnChannel(),
                grouping_contract=GroupingContract(
                    allowed_dispositions=partition_modes,
                    baseline_scoreable_pair_ids=baseline_scoreable,
                    baseline_partition=baseline_partition,
                ),
                current_partition=current_partition,
                historical_partition=historical_partition,
                suppressed_candidates=suppression[row.code],
                pair_contracts=tuple(
                    IndexedPairContract(
                        pair_id=pair.pair_id,
                        relation=pair.relation,
                        scope_verdict=pair.scope_verdict,
                        review_scope=pair.review_scope,
                        source_evidence_status=pair.source_evidence_status,
                        axis_range_verdict=pair.axis_range_verdict,
                        allowed_actions=(
                            _allowed_actions(pair)
                            if pair.pair_id in row.action_pair_ids
                            else ()
                        ),
                        citation_ids=(
                            tuple(
                                claim.citation_id
                                for question_id, claim in claims_by_key[
                                    (pair.key.axis, pair.key.filler)
                                ]
                                if citation_supports_pair(
                                    question_id=question_id,
                                    pair_key=LiteraturePairKey(
                                        axis=pair.key.axis, filler=pair.key.filler
                                    ),
                                    claim=claim,
                                    citation=citations[claim.citation_id],
                                )
                            )
                            if (pair.key.axis, pair.key.filler) in claims_by_key
                            else ()
                        ),
                        consequence_by_action=(
                            pair_consequences(pair)
                            if pair.pair_id in row.action_pair_ids
                            else {}
                        ),
                    )
                    for pair in row.pairs
                ),
                stage_a_mode="clinical-review",
                stage_b_mode=(
                    "ontology-review"
                    if row.action_pair_ids
                    else "not-applicable-pending-engineering"
                ),
                dispatch_status=dispatch.status,
                withholding_reasons=dispatch.reasons,
                expected_return_validation_path=f"{row.code}.validation.json",
                generated=False,
            )
        )
    release_ready_codes = tuple(
        entry.code for entry in entries if entry.dispatch_status == "dispatchable"
    )
    withheld_codes = tuple(
        entry.code for entry in entries if entry.dispatch_status == "withheld"
    )
    index_values: dict[str, object] = {
        "schema_version": 3,
        "ncit_version": "26.07d",
        "input_identities": identities,
        "literature_context_identity": _sha(payloads[literature_context_path]),
        "cadsr_usage_identity": _sha(payloads[cadsr_usage_path]),
        "suppressed_candidates_by_row": suppression,
        "packets": tuple(entries),
        "context_correction_note": context_correction_note(
            sum(len(row.context_pair_ids) for row in rows)
        ),
        "registered_mint_expected_set": visible_registered,
        "release_ready_codes": release_ready_codes,
        "withheld_codes": withheld_codes,
        "release_ready": not withheld_codes,
        "index_identity": "0" * 64,
    }
    index_values["index_identity"] = _identity_without(index_values, "index_identity")
    index = PacketIndex.model_validate(index_values)
    index_payload = _canonical(index)
    packet_payloads["index.json"] = index_payload
    findings: list[str] = []
    rendered = b"\n".join(
        payload for name, payload in packet_payloads.items() if name.endswith(".md")
    )
    all_mints = set(
        _MINT.findall(b"\n".join(payloads.values()).decode(errors="ignore"))
    )
    suppressed_mints = (all_mints - registered) | {
        candidate.generated_id
        for row_candidates in suppression.values()
        for candidate in row_candidates
    }
    leaked = sorted(item for item in suppressed_mints if item.encode() in rendered)
    if leaked:
        findings.append("suppressed MINT identifiers leaked into rendered bytes")
    if any(row.status == "error" for row in cadsr.rows):
        findings.append("caDSR report contains an error row")
    cue_records = (
        tuple(
            (dossier.code, "factual_context", text)
            for dossier in context.dossiers
            for text in dossier.factual_context
        )
        + tuple(
            (dossier.code, "question", question.text)
            for dossier in context.dossiers
            for question in dossier.questions
        )
        + tuple(
            (dossier.code, "source_fact", claim.source_fact)
            for dossier in context.dossiers
            for question in dossier.questions
            for claim in question.claims
        )
    )
    findings.extend(semantic_answer_cue_findings(cue_records))
    evidence_text = "\n".join(text for _code, _field, text in cue_records)
    if any(item in evidence_text for item in suppressed_mints):
        findings.append("suppressed MINT identifiers leaked into rendered bytes")
    for row in rows:
        dossier = dossier_by_code[row.code]
        if not any(
            citation.status == "cited"
            and citation.authority_order <= _CONTROLLING_AUTHORITY_MAX
            and not citation.exact_passage.startswith("Unavailable:")
            for citation in dossier.citations
        ):
            findings.append(f"{row.code} lacks a passage-bearing controlling citation")
        if any(
            pair.source_evidence_status == "unavailable"
            for pair in row.pairs
            if pair.pair_id in row.action_pair_ids
        ):
            findings.append(
                f"{row.code} has an actionable pair without source provenance"
            )
    rendered_text = rendered.decode(errors="replace")
    audited_text = rendered_text + "\n" + evidence_text
    if "<!-- QUESTION" in audited_text or "<!-- Allowed actions" in audited_text:
        findings.append("critical review content is hidden in HTML comments")
    if any(
        token in audited_text
        for token in ("/Users/", "\\Users\\", "pdm run", "python ", "git ")
    ):
        findings.append("rendered packets contain a local path or repository command")
    for marker in (
        "Classify this exact semantic pair",
        "specialist must supply",
        "research gap",
        "not-found",
        "UNRESOLVED |",
    ):
        if marker.lower() in rendered_text.lower():
            findings.append(f"rendered packets contain prohibited marker: {marker}")
    validation_values: dict[str, object] = {
        "schema_version": 3,
        "index_identity": index.index_identity,
        "index_file_sha256": _sha(index_payload),
        "status": "failed" if findings else "passed",
        "findings": tuple(findings),
        "producing_command": producing_command,
        "produced_on": date.today().isoformat(),
        "artifact_files_written": (
            *packet_payloads.keys(),
            "generation-validation.json",
        ),
        "ontology_writes": False,
        "runtime_mutated": False,
        "readiness": False,
        "readiness_meaning": (
            "ontology/publication readiness; separate from dispatch readiness"
        ),
        "release_ready_codes": release_ready_codes,
        "withheld_codes": withheld_codes,
        "release_ready": not withheld_codes,
        "publication": False,
        "validation_identity": "0" * 64,
    }
    validation_values["validation_identity"] = _identity_without(
        validation_values, "validation_identity"
    )
    packet_payloads["generation-validation.json"] = _canonical(
        GenerationValidation.model_validate(validation_values)
    )
    expected = set(packet_payloads)
    if (
        output_directory.is_dir()
        and {item.name for item in output_directory.iterdir()} == expected
        and all(
            (output_directory / name).read_bytes() == payload
            for name, payload in packet_payloads.items()
        )
    ):
        if findings:
            raise ValueError(
                "specialist packet generation validation failed: " + "; ".join(findings)
            )
        _write_dispatch_bundle(
            packet_directory=output_directory,
            index=index,
            packet_payloads=packet_payloads,
        )
        return index
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{output_directory.name}.", dir=output_directory.parent
        )
    )
    backup = output_directory.with_name(f".{output_directory.name}.previous")
    try:
        for name, payload in packet_payloads.items():
            (temporary / name).write_bytes(payload)
        if backup.exists():
            shutil.rmtree(backup)
        if output_directory.exists():
            os.replace(output_directory, backup)
        os.replace(temporary, output_directory)
        if backup.exists():
            shutil.rmtree(backup)
    except BaseException:
        if not output_directory.exists() and backup.exists():
            os.replace(backup, output_directory)
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    if findings:
        raise ValueError(
            "specialist packet generation validation failed: " + "; ".join(findings)
        )
    _write_dispatch_bundle(
        packet_directory=output_directory,
        index=index,
        packet_payloads=packet_payloads,
    )
    return index


def validate_completion(  # noqa: C901, PLR0912
    stage_a: ClinicalStageA,
    stage_b: OntologyStageB,
    *,
    clinically_asked_pairs: tuple[str, ...],
    action_pairs: tuple[str, ...] | None = None,
    engineering_only_pairs: tuple[str, ...] = (),
    allowed_actions_by_pair: dict[str, tuple[str, ...]] | None = None,
    baseline_scoreable_pairs: tuple[str, ...] = (),
    allowed_partition_modes: tuple[str, ...] | None = None,
) -> bool:
    if action_pairs is None and stage_b.row_outcome == "DEFERRED":
        raise ValueError(
            "index action-pair set must be supplied for a DEFERRED Stage B"
        )
    asked, assessed = (
        set(clinically_asked_pairs),
        {item.pair_id for item in stage_a.assessments},
    )
    actions = set(action_pairs if action_pairs is not None else clinically_asked_pairs)
    decided = {item.pair_id for item in stage_b.dispositions}
    engineering = set(engineering_only_pairs)
    if stage_a.clinical_stage == "DEFERRED" and assessed:
        raise ValueError("DEFERRED Stage A cannot contain pair assessments")
    if stage_a.clinical_stage != "DEFERRED" and assessed != asked:
        raise ValueError("Stage A assessments must exactly equal index asked-pair set")
    if stage_b.row_outcome == "RESOLVED" and decided != actions:
        raise ValueError("Stage B decisions must exactly equal index action-pair set")
    if stage_b.row_outcome == "DEFERRED" and decided:
        raise ValueError("DEFERRED Stage B cannot contain terminal decisions")
    if decided & engineering:
        raise ValueError("engineering-only pair cannot have an ontology action")
    for decision in stage_b.dispositions:
        if (
            allowed_actions_by_pair is not None
            and decision.action not in allowed_actions_by_pair.get(decision.pair_id, ())
        ):
            raise ValueError(
                "ontology action is not allowed by the indexed pair contract"
            )
    if (
        stage_a.clinical_stage != "SUFFICIENT-FOR-ONTOLOGY-REVIEW"
        and stage_b.dispositions
    ):
        raise ValueError("Stage B decisions require sufficient Stage A")
    if (
        stage_b.row_outcome == "RESOLVED"
        and stage_a.clinical_stage != "SUFFICIENT-FOR-ONTOLOGY-REVIEW"
    ):
        raise ValueError("resolved Stage B requires sufficient Stage A")
    if stage_b.row_outcome == "RESOLVED":
        final_scoreable = set(baseline_scoreable_pairs)
        for disposition in stage_b.dispositions:
            if disposition.action == "PROMOTE-SCOREABLE":
                final_scoreable.add(disposition.pair_id)
            elif disposition.action == "REMOVE-FROM-PROJECTION":
                final_scoreable.discard(disposition.pair_id)
        partition = stage_b.partition
        if partition is None:
            raise ValueError("resolved Stage B requires a partition disposition")
        if (
            allowed_partition_modes is not None
            and partition.mode not in allowed_partition_modes
        ):
            raise ValueError(
                "partition mode is not allowed by the indexed row contract"
            )
        covered = tuple(pair for group in partition.groups for pair in group)
        if partition.mode == "EMPTY" and final_scoreable:
            raise ValueError("EMPTY partition is valid only with no scoreable pairs")
        if set(covered) != final_scoreable or len(covered) != len(final_scoreable):
            raise ValueError(
                "final partition must exactly cover all post-action scoreable pairs"
            )
    return True


_REGION_MARKER = re.compile(
    r"^\[\[ONTOPRISM:(?P<name>[A-Z0-9:-]+):(?P<edge>START|END)\]\]$"
)


def _normalized_utf8(payload: bytes) -> str:
    """Strictly decode a returned packet and normalize only BOM/newline form."""
    text = payload.decode("utf-8-sig")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _response_regions(text: str) -> dict[str, str]:
    regions: dict[str, str] = {}
    active: tuple[str, int] | None = None
    lines = text.splitlines(keepends=True)
    position = 0
    for line in lines:
        stripped = line.rstrip("\n")
        match = _REGION_MARKER.fullmatch(stripped)
        next_position = position + len(line)
        if match is None:
            position = next_position
            continue
        name, edge = match.group("name"), match.group("edge")
        if edge == "START":
            if active is not None:
                raise ValueError("response regions cannot be nested")
            if name in regions:
                raise ValueError(f"duplicate response region: {name}")
            active = (name, next_position)
        else:
            if active is None or active[0] != name:
                raise ValueError("malformed or mismatched response region marker")
            regions[name] = text[active[1] : position]
            active = None
        position = next_position
    if active is not None:
        raise ValueError("unterminated response region")
    return regions


def _immutable_normalized(text: str, canonical_regions: dict[str, str]) -> str:
    returned_regions = _response_regions(text)
    if tuple(returned_regions) != tuple(canonical_regions):
        raise ValueError(
            "returned packet response regions are missing, duplicated, or reordered"
        )
    result = text
    for name, returned_content in returned_regions.items():
        start = f"[[ONTOPRISM:{name}:START]]\n"
        end = f"[[ONTOPRISM:{name}:END]]"
        old = start + returned_content + end
        new = start + canonical_regions[name] + end
        if result.count(old) != 1:
            raise ValueError(f"malformed response region: {name}")
        result = result.replace(old, new, 1)
    return result


def _region_fields(block: str, field_names: tuple[str, ...]) -> dict[str, str]:
    fields: dict[str, str] = {}
    current: str | None = None
    for line in block.splitlines():
        matched = next(
            (name for name in field_names if line.startswith(f"{name}:")), None
        )
        if matched is not None:
            if matched in fields:
                raise ValueError(f"duplicate response field: {matched}")
            fields[matched] = line[len(matched) + 1 :].strip()
            current = matched
        elif current is not None:
            fields[current] = (fields[current] + "\n" + line).strip()
        elif line.strip():
            raise ValueError("response text precedes its field label")
    missing = set(field_names) - set(fields)
    if missing:
        raise ValueError(f"response region lacks fields: {sorted(missing)}")
    return fields


def _validate_return_text(
    *, text: str, canonical: bytes, entry: PacketIndexEntry
) -> tuple[ClinicalStageA, OntologyStageB | None]:
    canonical_text = _normalized_utf8(canonical)
    canonical_regions = _response_regions(canonical_text)
    if _immutable_normalized(text, canonical_regions) != canonical_text:
        raise ValueError(
            f"returned packet has edits outside response regions: {entry.path}"
        )
    blocks = _response_regions(text)
    a_values = _region_fields(
        blocks["STAGE-A"],
        (
            "Attester name",
            "Attester capacity",
            "Attestation date (YYYY-MM-DD)",
            "Conflict of interest",
            "Source confirmation",
            "Human attestation (TRUE)",
            "Clinical stage (SUFFICIENT-FOR-ONTOLOGY-REVIEW, CLINICAL-COMPLETE-ENGINEERING-PENDING, or DEFERRED)",
            "Whole-row blocker if DEFERRED",
        ),
    )
    assessment_values = {
        pair_id: _region_fields(
            blocks[f"STAGE-A-PAIR:{pair_id}"],
            ("Status", "Citations", "Rationale"),
        )
        for pair_id in entry.asked_pair_ids
    }
    clinical_stage = a_values[
        "Clinical stage (SUFFICIENT-FOR-ONTOLOGY-REVIEW, CLINICAL-COMPLETE-ENGINEERING-PENDING, or DEFERRED)"
    ]
    if clinical_stage == "DEFERRED" and any(
        value for fields in assessment_values.values() for value in fields.values()
    ):
        raise ValueError("DEFERRED Stage A requires empty pair assessment fields")
    contracts = {item.pair_id: item for item in entry.pair_contracts}
    if clinical_stage != "DEFERRED":
        for pair_id, values in assessment_values.items():
            entered = {
                item.strip() for item in values["Citations"].split(";") if item.strip()
            }
            if not entered or not entered <= set(contracts[pair_id].citation_ids):
                raise ValueError(
                    f"citation IDs must resolve to the same row and pair claim: {pair_id}"
                )
    stage_a = ClinicalStageA.model_validate(
        {
            "attestation": {
                "role": "clinical",
                "attester_name": a_values["Attester name"],
                "attester_capacity": a_values["Attester capacity"],
                "attestation_date": a_values["Attestation date (YYYY-MM-DD)"],
                "conflict_of_interest": a_values["Conflict of interest"],
                "source_confirmation": a_values["Source confirmation"],
                "human_attestation": a_values["Human attestation (TRUE)"].upper()
                == "TRUE",
            },
            "assessments": ()
            if clinical_stage == "DEFERRED"
            else tuple(
                {
                    "pair_id": pair_id,
                    "status": values["Status"],
                    "citations": tuple(
                        item.strip()
                        for item in values["Citations"].split(";")
                        if item.strip()
                    ),
                    "rationale": values["Rationale"],
                }
                for pair_id, values in assessment_values.items()
            ),
            "clinical_stage": clinical_stage,
            "blocker": a_values["Whole-row blocker if DEFERRED"] or None,
        }
    )
    if entry.stage_b_mode == "not-applicable-pending-engineering":
        if "STAGE-B" in blocks or any(
            name.startswith("STAGE-B-PAIR:") for name in blocks
        ):
            raise ValueError("not-applicable Stage B cannot expose response regions")
        if stage_a.clinical_stage == "SUFFICIENT-FOR-ONTOLOGY-REVIEW":
            raise ValueError(
                "row without Stage B actions must use CLINICAL-COMPLETE-ENGINEERING-PENDING"
            )
        return stage_a, None
    b_values = _region_fields(
        blocks["STAGE-B"],
        (
            "Attester name",
            "Attester capacity",
            "Attestation date (YYYY-MM-DD)",
            "Conflict of interest",
            "Source confirmation",
            "Human attestation (TRUE)",
            "ROW-OUTCOME (RESOLVED or DEFERRED)",
            "Whole-row blocker if DEFERRED",
            "Blocker source if DEFERRED",
            "Next action if DEFERRED",
        ),
    )
    row_outcome = b_values["ROW-OUTCOME (RESOLVED or DEFERRED)"]
    decision_values = {
        pair_id: _region_fields(
            blocks[f"STAGE-B-PAIR:{pair_id}"],
            ("Action", "Rationale"),
        )
        for pair_id in entry.action_pair_ids
    }
    partition_values = _region_fields(
        blocks["PARTITION-DISPOSITION"],
        (
            "Mode",
            "Groups (one group per line; semicolon-separated pair IDs)",
            "Rationale",
        ),
    )
    if row_outcome == "DEFERRED" and (
        any(value for fields in decision_values.values() for value in fields.values())
        or any(partition_values.values())
    ):
        raise ValueError(
            "DEFERRED Stage B cannot contain pair or partition response fields"
        )
    groups = tuple(
        tuple(item.strip() for item in line.split(";") if item.strip())
        for line in partition_values[
            "Groups (one group per line; semicolon-separated pair IDs)"
        ].splitlines()
        if line.strip()
    )
    stage_b = OntologyStageB.model_validate(
        {
            "attestation": {
                "role": "ontology",
                "attester_name": b_values["Attester name"],
                "attester_capacity": b_values["Attester capacity"],
                "attestation_date": b_values["Attestation date (YYYY-MM-DD)"],
                "conflict_of_interest": b_values["Conflict of interest"],
                "source_confirmation": b_values["Source confirmation"],
                "human_attestation": b_values["Human attestation (TRUE)"].upper()
                == "TRUE",
            },
            "row_outcome": row_outcome,
            "dispositions": tuple(
                {
                    "pair_id": pair_id,
                    "action": values["Action"],
                    "rationale": values["Rationale"],
                }
                for pair_id, values in decision_values.items()
                if row_outcome == "RESOLVED"
            ),
            "partition": (
                {
                    "mode": partition_values["Mode"],
                    "groups": groups,
                    "rationale": partition_values["Rationale"],
                }
                if row_outcome == "RESOLVED"
                else None
            ),
            "blocker": b_values["Whole-row blocker if DEFERRED"] or None,
            "blocker_source": b_values["Blocker source if DEFERRED"] or None,
            "next_action": b_values["Next action if DEFERRED"] or None,
        }
    )
    validate_completion(
        stage_a,
        stage_b,
        clinically_asked_pairs=entry.asked_pair_ids,
        action_pairs=entry.action_pair_ids,
        engineering_only_pairs=entry.engineering_pair_ids,
        allowed_actions_by_pair={
            key: value.allowed_actions for key, value in contracts.items()
        },
        baseline_scoreable_pairs=entry.grouping_contract.baseline_scoreable_pair_ids,
        allowed_partition_modes=entry.grouping_contract.allowed_dispositions,
    )
    return stage_a, stage_b


def validate_specialist_review_row(
    *,
    code: str,
    return_path: Path,
    index_path: Path,
    validation_output: Path,
) -> RowCompletionValidation:
    index_payload = index_path.read_bytes()
    index = PacketIndex.model_validate_json(index_payload)
    entry = next((item for item in index.packets if item.code == code), None)
    if entry is None:
        raise ValueError(f"code is not present in packet index: {code}")
    if return_path.name != f"{code}.md":
        raise ValueError("return file name must exactly match the indexed code")
    if entry.dispatch_status == "dispatchable":
        validate_dispatch_bundle(
            dispatch_directory=index_path.parent.parent / "m1-6-specialist-dispatch",
            packet_directory=index_path.parent,
            index=index,
        )
    canonical_path = index_path.parent / Path(entry.path).name
    canonical = canonical_path.read_bytes()
    returned = return_path.read_bytes()
    stage_a, stage_b = _validate_return_text(
        text=_normalized_utf8(returned), canonical=canonical, entry=entry
    )
    values: dict[str, object] = {
        "schema_version": 1,
        "status": "passed",
        "code": code,
        "index_identity": index.index_identity,
        "canonical_sha256": _sha(canonical),
        "return_sha256": _sha(returned),
        "deferred_valid": stage_a.clinical_stage
        in {"DEFERRED", "CLINICAL-COMPLETE-ENGINEERING-PENDING"}
        or (stage_b is not None and stage_b.row_outcome == "DEFERRED"),
        "ontology_writes": False,
        "readiness": False,
        "publication": False,
        "validation_identity": "0" * 64,
    }
    values["validation_identity"] = _identity_without(values, "validation_identity")
    validation = RowCompletionValidation.model_validate(values)
    validation_output.parent.mkdir(parents=True, exist_ok=True)
    validation_output.write_bytes(_canonical(validation))
    return validation


def validate_specialist_review_packet_directory(
    directory: Path,
    *,
    index_path: Path | None = None,
) -> CompletionValidation:
    index_path = index_path or Path("tmp/m1-6-specialist-packets/index.json")
    index = PacketIndex.model_validate_json(index_path.read_bytes())
    expected = {f"{entry.code}.md" for entry in index.packets} | {
        f"{entry.code}.validation.json" for entry in index.packets
    }
    actual = {path.name for path in directory.iterdir() if path.is_file()}
    if actual != expected:
        raise ValueError(
            "aggregate specialist return set must contain seven exact returns and row validations"
        )
    completed: list[str] = []
    for entry in index.packets:
        validation = RowCompletionValidation.model_validate_json(
            (directory / f"{entry.code}.validation.json").read_bytes()
        )
        returned = (directory / f"{entry.code}.md").read_bytes()
        canonical = (index_path.parent / Path(entry.path).name).read_bytes()
        if (
            validation.code != entry.code
            or validation.index_identity != index.index_identity
            or validation.return_sha256 != _sha(returned)
            or validation.canonical_sha256 != _sha(canonical)
            or _identity_without(
                validation.model_dump(mode="json"), "validation_identity"
            )
            != validation.validation_identity
        ):
            raise ValueError(f"row validation identity mismatch: {entry.code}")
        _validate_return_text(
            text=_normalized_utf8(returned), canonical=canonical, entry=entry
        )
        completed.append(entry.code)
    return CompletionValidation(
        status="passed",
        completed_codes=tuple(completed),
        ontology_writes=False,
        readiness=False,
        publication=False,
    )


def validate_specialist_review_generation(directory: Path) -> GenerationValidation:
    """Strict-load the blank generation and recompute every bound file identity."""
    index_payload = (directory / "index.json").read_bytes()
    index = PacketIndex.model_validate_json(index_payload)
    index_values = index.model_dump(mode="json")
    if _identity_without(index_values, "index_identity") != index.index_identity:
        raise ValueError("packet index identity mismatch")
    for entry in index.packets:
        if _sha((directory / entry.path).read_bytes()) != entry.row_sha256:
            raise ValueError(f"packet row digest mismatch: {entry.path}")
    validation = GenerationValidation.model_validate_json(
        (directory / "generation-validation.json").read_bytes()
    )
    if (
        validation.index_identity != index.index_identity
        or validation.index_file_sha256 != _sha(index_payload)
    ):
        raise ValueError("generation validation is not bound to the packet index")
    values = validation.model_dump(mode="json")
    if (
        _identity_without(values, "validation_identity")
        != validation.validation_identity
    ):
        raise ValueError("generation validation identity mismatch")
    if validation.status != "passed" or validation.findings:
        raise ValueError("generation validation has unresolved findings")
    return validation
