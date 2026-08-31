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
    )
except ModuleNotFoundError:  # direct ``python scripts/adjudication.py`` entry point
    from research.specialist_cadsr_usage import (  # type: ignore[import-not-found]
        CadsrUsageRow,
        SpecialistCadsrUsageReport,
    )
    from research.specialist_literature_context import (
        GeneratedLiteratureContext,
        LiteratureDossierSource,
    )

CONCEPT_ORDER = ("C27262", "C102870", "C6135", "C4791", "C100054", "C198031", "C35756")
_SHA256 = r"^[0-9a-f]{64}$"
_MINT = re.compile(r"MINT-[0-9a-f]{12}")
_GROUP_PACKET_SCHEMA = 4
_DIAGNOSTIC_SCHEMA = 3
_STAGE_B_DECISION_WIDTH = 6
_CONTROLLING_AUTHORITY_MAX = 2
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


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class PairKey(_StrictModel):
    axis: str = Field(min_length=1)
    filler: str = Field(min_length=1)


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
    allowed_reaxis_targets: tuple[str, ...]


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
    def engineering_pair_ids(self) -> tuple[str, ...]:
        return tuple(
            pair.pair_id
            for pair in self.pairs
            if pair.review_scope in {"stage-a-clinical-only", "engineering-only"}
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


class ClinicalStageA(_StrictModel):
    reviewer_name: str = Field(min_length=1)
    specialty: str = Field(min_length=1)
    review_date: str
    conflict_of_interest: str = Field(min_length=1)
    source_confirmation: str = Field(min_length=1)
    assessments: tuple[ClinicalPairAssessment, ...]
    clinical_stage: Literal[
        "SUFFICIENT-FOR-ONTOLOGY-REVIEW",
        "CLINICAL-COMPLETE-ENGINEERING-PENDING",
        "DEFERRED",
    ]
    blocker: str | None

    @model_validator(mode="after")
    def _outcome(self) -> Self:
        date.fromisoformat(self.review_date)
        unresolved = any(item.status == "UNRESOLVED" for item in self.assessments)
        if unresolved and self.clinical_stage != "DEFERRED":
            raise ValueError("an unresolved assessment requires DEFERRED Stage A")
        if (self.clinical_stage == "DEFERRED") != (self.blocker is not None):
            raise ValueError("DEFERRED Stage A requires exactly one blocker")
        return self


class OntologyPairDecision(_StrictModel):
    pair_id: str
    relation: Relation
    action: Literal[
        "RETAIN-SCOREABLE",
        "REMOVE-FROM-PROJECTION",
        "RE-AXIS",
        "PROMOTE-SCOREABLE",
        "ADD-SCOREABLE",
        "OMIT",
        "GROUP-TOGETHER",
        "KEEP-SEPARATE",
    ]
    target_axis: str | None
    group_assignment: str | None
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def _action(self) -> Self:
        allowed = {
            "expected-matched-scoreable": {
                "RETAIN-SCOREABLE",
                "REMOVE-FROM-PROJECTION",
                "RE-AXIS",
                "GROUP-TOGETHER",
                "KEEP-SEPARATE",
            },
            "expected-emitted-review-bearing": {
                "PROMOTE-SCOREABLE",
                "REMOVE-FROM-PROJECTION",
                "RE-AXIS",
                "GROUP-TOGETHER",
                "KEEP-SEPARATE",
            },
            "expected-not-emitted": {
                "ADD-SCOREABLE",
                "OMIT",
                "RE-AXIS",
                "GROUP-TOGETHER",
                "KEEP-SEPARATE",
            },
            "current-only-scoreable": {
                "RETAIN-SCOREABLE",
                "REMOVE-FROM-PROJECTION",
                "RE-AXIS",
                "GROUP-TOGETHER",
                "KEEP-SEPARATE",
            },
            "current-only-review-bearing": {
                "PROMOTE-SCOREABLE",
                "REMOVE-FROM-PROJECTION",
                "RE-AXIS",
                "GROUP-TOGETHER",
                "KEEP-SEPARATE",
            },
            "current-only-proposed": {
                "PROMOTE-SCOREABLE",
                "REMOVE-FROM-PROJECTION",
                "RE-AXIS",
                "GROUP-TOGETHER",
                "KEEP-SEPARATE",
            },
        }
        if self.action not in allowed[self.relation]:
            raise ValueError("ontology action is not allowed for this pair relation")
        if self.action != "RE-AXIS" and self.target_axis is not None:
            raise ValueError("only RE-AXIS may name a target axis")
        if self.action == "RE-AXIS" and self.target_axis is None:
            raise ValueError("RE-AXIS requires an indexed target axis")
        if (
            self.action
            in {"ADD-SCOREABLE", "PROMOTE-SCOREABLE", "RE-AXIS", "GROUP-TOGETHER"}
            and not self.group_assignment
        ):
            raise ValueError(
                "added, promoted, re-axis, or grouped pairs require group assignment"
            )
        return self


class OntologyStageB(_StrictModel):
    reviewer_name: str = Field(min_length=1)
    review_date: str
    conflict_of_interest: str = Field(min_length=1)
    row_outcome: Literal["RESOLVED", "DEFERRED"]
    decisions: tuple[OntologyPairDecision, ...]
    blocker: str | None
    blocker_source: str | None = None
    next_action: str | None = None

    @model_validator(mode="after")
    def _outcome(self) -> Self:
        date.fromisoformat(self.review_date)
        deferred_fields = (self.blocker, self.blocker_source, self.next_action)
        if self.row_outcome == "DEFERRED" and not all(deferred_fields):
            raise ValueError(
                "DEFERRED Stage B requires blocker, source, and next action"
            )
        if self.row_outcome == "RESOLVED" and any(deferred_fields):
            raise ValueError("RESOLVED Stage B cannot carry deferred fields")
        return self


class ActionConsequence(_StrictModel):
    tp_delta: int
    fp_delta: int
    fn_delta: int
    emitted_scoreable_delta: int
    source_preserved: Literal[True]
    pair_after: str
    needs_review_after: bool
    group_effect: str


class IndexedPairContract(_StrictModel):
    pair_id: str
    relation: Relation
    review_scope: ReviewScope
    source_evidence_status: Literal[
        "available", "source-backed-coordinate-missing", "unavailable"
    ]
    axis_range_verdict: Literal["valid", "invalid", "unknown"]
    allowed_actions: tuple[str, ...]
    allowed_reaxis_targets: tuple[str, ...]
    consequence_by_action: dict[str, ActionConsequence]


class PacketIndexEntry(_StrictModel):
    code: str
    path: str
    row_sha256: str = Field(pattern=_SHA256)
    row_contract_identity: str = Field(pattern=_SHA256)
    asked_pair_ids: tuple[str, ...]
    action_pair_ids: tuple[str, ...]
    engineering_pair_ids: tuple[str, ...]
    context_pair_ids: tuple[str, ...]
    pair_contracts: tuple[IndexedPairContract, ...]
    stage_a_mode: Literal["clinical-review"] = "clinical-review"
    stage_b_mode: Literal["ontology-review", "not-applicable-pending-engineering"] = (
        "ontology-review"
    )
    dispatch_status: Literal["dispatchable", "withheld"]
    withholding_reasons: tuple[str, ...]
    row_validation_path: str

    @model_validator(mode="after")
    def _partitions_and_dispatch_are_exact(self) -> Self:
        pair_ids = {item.pair_id for item in self.pair_contracts}
        partitions = (
            set(self.action_pair_ids),
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
        if (self.dispatch_status == "withheld") != bool(self.withholding_reasons):
            raise ValueError("dispatch status and withholding reasons disagree")
        if (
            Path(self.path).is_absolute()
            or Path(self.row_validation_path).is_absolute()
        ):
            raise ValueError("packet index paths must be repository-relative")
        expected_stage_b = (
            "ontology-review"
            if self.action_pair_ids
            else "not-applicable-pending-engineering"
        )
        if self.stage_b_mode != expected_stage_b:
            raise ValueError("Stage B mode must derive from the indexed action set")
        return self


class SuppressedCandidate(_StrictModel):
    axis: str
    generated_id: str = Field(pattern=r"^MINT-[0-9a-f]{12}$")
    reason: Literal["unregistered", "range-ineligible"]


class PacketIndex(_StrictModel):
    schema_version: Literal[3] = 3
    ncit_version: Literal["26.07d"]
    input_identities: dict[str, str]
    literature_context_identity: str = Field(pattern=_SHA256)
    cadsr_usage_identity: str = Field(pattern=_SHA256)
    suppressed_candidates_by_row: dict[str, tuple[SuppressedCandidate, ...]]
    packets: tuple[PacketIndexEntry, ...]
    unavailable_action_classes: dict[str, str]
    registered_mint_expected_set: tuple[str, ...]
    index_identity: str = Field(pattern=_SHA256)


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
    validation_identity: str = Field(pattern=_SHA256)


class CompletionValidation(_StrictModel):
    status: Literal["passed"]
    completed_codes: tuple[str, ...]
    ontology_writes: Literal[False]
    readiness: Literal[False]


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


def _scope(  # noqa: PLR0911
    code: str,
    key: PairKey,
    relation: Relation,
    range_status: str,
    diagnostic_classification: str | None,
    source_status: str,
) -> tuple[ReviewScope, str, str | None]:
    if code == "C102870" and key.filler in {
        "C12321",
        "C36903",
        "C156460",
        "C121619",
        "C39986",
    }:
        return (
            "stage-a-clinical-only",
            "The exact clinical scope is reviewable, but this row has no indexed Stage B action; ontology disposition remains pending engineering repair.",
            "#274 row action inventory blocked; regenerate packets and rerun the release gate after repair",
        )
    if code == "C6135" and key.filler in {"C129702", "C33782"}:
        return (
            "stage-a-and-stage-b",
            "The pair is source-backed and range-valid, but its clinical universality is disputed and requires separate clinical and ontology review.",
            None,
        )
    if diagnostic_classification == "selection-miss":
        return (
            "stage-a-clinical-only",
            "Diagnostic classification is selection-miss: clinical applicability is reviewable, but the current projection gate cannot represent a terminal Stage B action.",
            "#274 selector repair queued; regenerate packets and rerun the release gate after repair",
        )
    if range_status in {"invalid", "unknown"}:
        return (
            "engineering-only",
            f"Stored range verdict is {range_status}; range repair is engineering-owned and no human action is offered.",
            "#271 range repair blocked; rerun deterministic range gate after repair",
        )
    if relation == "expected-not-emitted":
        owner = (
            "#267"
            if key.axis in {"op:AssociatedRegion", "op:AssociatedSite"}
            else "#274"
        )
        return (
            "engineering-only",
            "The expected pair was not emitted; extraction or routing regeneration is engineering-owned and ADD-SCOREABLE/OMIT is unavailable in this packet.",
            f"{owner} extraction/routing repair queued; regenerate packets and rerun the release gate",
        )
    if source_status == "unavailable":
        owner = (
            "#267"
            if key.axis
            in {
                "op:AssociatedRegion",
                "op:AssociatedSite",
                "op:AssociatedLineageClassification",
            }
            else "#274"
        )
        return (
            "engineering-only",
            "No exact occurrence or typed source-backed coordinate-missing fact exists; human action is blocked rather than inferred from a label or historical proposal.",
            f"{owner} source-routing repair queued; regenerate packets and rerun provenance validation after repair",
        )
    if relation == "current-only-proposed":
        return (
            "context-not-under-review",
            "Current proposed content is shown as governance context and is not eligible for specialist action.",
            None,
        )
    contested = relation != "expected-matched-scoreable"
    if not contested:
        return (
            "context-not-under-review",
            "The source-backed, range-valid pair is uncontested in the current comparison and is shown as explicit context only.",
            None,
        )
    return (
        "stage-a-and-stage-b",
        "Clinical status and its ontology consequence are both unresolved and require independent Stage A and Stage B responses.",
        None,
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
            scope, reason, blocker = _scope(
                code,
                key,
                relation,
                range_status,
                str(candidate.get("classification")) if candidate else None,
                source_status,
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
            if blocker:
                blockers[pair_id] = blocker
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
                    review_scope=scope,
                    scope_reason=reason,
                    contested=relation not in {"expected-matched-scoreable"},
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
                    allowed_reaxis_targets=tuple(
                        sorted(
                            {
                                row["axis"]
                                for row in diagnostic["range_diagnostics"]
                                if row["code"] == code
                                and row["filler"] == filler
                                and row["verdict"]["status"] == "valid"
                                and row["axis"] != axis
                            }
                        )
                    ),
                )
            )
        dossier = dossiers[code]
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
    actions = {
        "expected-matched-scoreable": (
            "RETAIN-SCOREABLE",
            "REMOVE-FROM-PROJECTION",
            "RE-AXIS",
            "GROUP-TOGETHER",
            "KEEP-SEPARATE",
        ),
        "expected-emitted-review-bearing": (
            "PROMOTE-SCOREABLE",
            "REMOVE-FROM-PROJECTION",
            "RE-AXIS",
            "GROUP-TOGETHER",
            "KEEP-SEPARATE",
        ),
        "expected-not-emitted": (
            "ADD-SCOREABLE",
            "OMIT",
            "RE-AXIS",
            "GROUP-TOGETHER",
            "KEEP-SEPARATE",
        ),
        "current-only-scoreable": (
            "RETAIN-SCOREABLE",
            "REMOVE-FROM-PROJECTION",
            "RE-AXIS",
            "GROUP-TOGETHER",
            "KEEP-SEPARATE",
        ),
        "current-only-review-bearing": (
            "PROMOTE-SCOREABLE",
            "REMOVE-FROM-PROJECTION",
            "RE-AXIS",
            "GROUP-TOGETHER",
            "KEEP-SEPARATE",
        ),
        "current-only-proposed": (
            "PROMOTE-SCOREABLE",
            "REMOVE-FROM-PROJECTION",
            "RE-AXIS",
            "GROUP-TOGETHER",
            "KEEP-SEPARATE",
        ),
    }[pair.relation]
    # A range-valid alternative alone does not prove that it is the expected target.
    # Until diagnostics carry that identity, RE-AXIS is deliberately unavailable.
    actions = tuple(action for action in actions if action != "RE-AXIS")
    return actions


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
            1
            if relation in {"current-only-review-bearing", "current-only-proposed"}
            else 0,
            -1 if relation == "expected-emitted-review-bearing" else 0,
            1,
            "scoreable-promoted",
            False,
            "assigned-by-reviewer",
        ),
        "ADD-SCOREABLE": (
            1,
            0,
            -1,
            1,
            "scoreable-added",
            False,
            "assigned-by-reviewer",
        ),
        "OMIT": (0, 0, 0, 0, "absent", False, "unchanged"),
        "GROUP-TOGETHER": (
            0,
            0,
            0,
            0,
            "pair-unchanged",
            pair.current_projection_status != "scoreable-release-bound",
            "grouped",
        ),
        "KEEP-SEPARATE": (
            0,
            0,
            0,
            0,
            "pair-unchanged",
            pair.current_projection_status != "scoreable-release-bound",
            "kept-separate",
        ),
    }
    return {
        action: ActionConsequence(
            tp_delta=values[0],
            fp_delta=values[1],
            fn_delta=values[2],
            emitted_scoreable_delta=values[3],
            source_preserved=True,
            pair_after=values[4],
            needs_review_after=values[5],
            group_effect=values[6],
        )
        for action in _allowed_actions(pair)
        if (values := deltas[action])
    }


def _render_packet(  # noqa: C901, PLR0912
    row: SpecialistRowPacket,
    dossier: LiteratureDossierSource,
    cadsr: CadsrUsageRow,
    cadsr_report: SpecialistCadsrUsageReport,
    actual_partition: object,
    expected_partition: object,
    suppressed_candidates: tuple[SuppressedCandidate, ...],
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
    lines = [
        f"# {row.code} — {row.label}",
        "",
        "**Blank specialist packet.** Current NCIt 26.07d baseline only. Historical proposals are warnings, not selected responses. This packet is write-free; readiness is false and it cannot authorize equivalence, adoption, or publication.",
        "",
        "## Return workflow",
        "",
        f"1. Return the completed file with this same filename, `{row.code}.md`, to the review orchestrator. No deadline has been supplied.",
        "2. Edit as UTF-8. Preserve the filename and every visible ONTOPRISM marker. Edit only the text inside response regions; paragraphs and the `|` character are allowed.",
        "3. Stage A is clinical classification; Stage B is ontology action. One person may perform both only by separately signing both roles.",
        "4. Leave everything outside response regions unchanged and return the completed file to the review orchestrator, who performs independent row validation.",
        "",
        "Neutral syntactic format example (angle-bracket tokens are not valid responses): `<PAIR-ID> | <ALLOWED-STATUS> | <CITATION-ID> | <RATIONALE>`.",
        "",
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
            "**No ADD-SCOREABLE or OMIT action is currently eligible in this seven-row release because every absent expected pair is blocked for engineering repair. Decorative action rows are intentionally omitted.**",
            "",
            "**Current metric identity/context:** pair precision, recall, and F1 over the bound current comparison; row-local potential changes are bounded to one TP/FP/FN contribution per pair. No predicted post-review values are rendered.",
            "",
            "**Current scoreable baseline partition:** "
            + json.dumps(actual_partition, sort_keys=True),
            "",
            "**Historical proposal warning and partition:** admitted historical witness only; not source-stated grouping or authority. "
            + json.dumps(expected_partition, sort_keys=True),
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
            "## Engineering blockers and consequences",
            "",
            *(
                f"- **{pair_id}:** {blocker}. Consequence: no clinical response creates an ontology action; current projection and metrics remain unchanged and readiness remains false."
                for pair_id, blocker in sorted(row.engineering_blockers.items())
            ),
            "",
            "## Stage A — Clinical review",
            "",
            f"Required specialty: {dossier.specialty}",
            "Allowed statuses: UNIVERSAL-DEFINING (part of every definition); UNIVERSAL-NONDEFINING (present in every case but not defining); CHARACTERISTIC-NONUNIVERSAL (frequent, not universal); CLASSIFICATION-DEPENDENT (varies by named system/version/entity); INAPPLICABLE; UNRESOLVED.",
            "Stage A records clinical evidence only and performs no ontology action.",
            "",
            "[[ONTOPRISM:STAGE-A:START]]",
            "Reviewer name:",
            "Specialty:",
            "Review date (YYYY-MM-DD):",
            "Conflict of interest:",
            "Source confirmation:",
            "Clinical stage (SUFFICIENT-FOR-ONTOLOGY-REVIEW, CLINICAL-COMPLETE-ENGINEERING-PENDING, or DEFERRED):",
            "Whole-row blocker if DEFERRED:",
            "[[ONTOPRISM:STAGE-A:END]]",
        ]
    )
    asked = set(row.asked_pair_ids)
    pair_id_by_key = {
        (pair.key.axis, pair.key.filler): pair.pair_id for pair in row.pairs
    }
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
                    f"For **{pair.filler_label}** (`{pair.key.axis} {pair.key.filler}`): {question.text}",
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
    lines.extend(["", "Stage A signature: ______", ""])
    action_ids = set(row.action_pair_ids)
    if action_ids:
        lines.extend(
            [
                "## Stage B — Ontology review",
                "",
                "Stage B mode: ontology-review.",
                "Requires sufficient Stage A. Exact actions below are the complete allowed set for each pair. REMOVE preserves the source fact; RE-AXIS changes only normalization; grouping changes only partition. No action asserts adoption or equivalence.",
                "Whole-row DEFER applies no changes to the current projection, final partition, or TP/FP/FN/emitted-scoreable metrics; readiness remains false.",
                "",
                "[[ONTOPRISM:STAGE-B:START]]",
                "Reviewer name:",
                "Review date (YYYY-MM-DD):",
                "Conflict of interest:",
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
                    f"- **{action}:** TP={value.tp_delta:+d}; FP={value.fp_delta:+d}; FN={value.fn_delta:+d}; emitted-scoreable={value.emitted_scoreable_delta:+d}; source-preserved=true; pair-after={value.pair_after}; needsReview={str(value.needs_review_after).lower()}; group-effect={value.group_effect}; readiness=false; adoption/equivalence=not asserted."
                )
            lines.extend(
                [
                    f"[[ONTOPRISM:STAGE-B-PAIR:{pair.pair_id}:START]]",
                    "Action:",
                    "Target axis:",
                    "Group:",
                    "Rationale:",
                    f"[[ONTOPRISM:STAGE-B-PAIR:{pair.pair_id}:END]]",
                ]
            )
        lines.extend(
            [
                "[[ONTOPRISM:FINAL-PARTITION:START]]",
                "",
                "[[ONTOPRISM:FINAL-PARTITION:END]]",
                "",
                "Stage B signature: ______",
                "The same individual may sign both only after separately completing both role blocks.",
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
        payload = _render_packet(
            row,
            dossier_by_code[row.code],
            cadsr_by_code[row.code],
            cadsr,
            concept["actual_partition"],
            concept["expected_partition"],
            suppression[row.code],
        )
        name = f"{row.code}.md"
        packet_payloads[name] = payload
        dossier = dossier_by_code[row.code]
        citations = {item.citation_id: item for item in dossier.citations}
        withholding = tuple(
            f"{pair.pair_id} lacks an accessible high-value source-specific passage"
            for pair in row.pairs
            if pair.pair_id in row.asked_pair_ids
            and not any(
                (pair.key.axis, pair.key.filler)
                in {(key.axis, key.filler) for key in question.pair_keys}
                and any(
                    citations[citation_id].status == "cited"
                    and citations[citation_id].exact_passage != "NOT VERIFIED"
                    for citation_id in {claim.citation_id for claim in question.claims}
                )
                for question in dossier.questions
            )
        )
        entries.append(
            PacketIndexEntry(
                code=row.code,
                path=name,
                row_sha256=_sha(payload),
                row_contract_identity=_sha(_canonical(row)),
                asked_pair_ids=row.asked_pair_ids,
                action_pair_ids=row.action_pair_ids,
                engineering_pair_ids=row.engineering_pair_ids,
                context_pair_ids=row.context_pair_ids,
                pair_contracts=tuple(
                    IndexedPairContract(
                        pair_id=pair.pair_id,
                        relation=pair.relation,
                        review_scope=pair.review_scope,
                        source_evidence_status=pair.source_evidence_status,
                        axis_range_verdict=pair.axis_range_verdict,
                        allowed_actions=(
                            _allowed_actions(pair)
                            if pair.pair_id in row.action_pair_ids
                            else ()
                        ),
                        allowed_reaxis_targets=(
                            pair.allowed_reaxis_targets
                            if "RE-AXIS" in _allowed_actions(pair)
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
                dispatch_status="withheld" if withholding else "dispatchable",
                withholding_reasons=withholding,
                row_validation_path=f"{row.code}.validation.json",
            )
        )
    index_values: dict[str, object] = {
        "schema_version": 3,
        "ncit_version": "26.07d",
        "input_identities": identities,
        "literature_context_identity": _sha(payloads[literature_context_path]),
        "cadsr_usage_identity": _sha(payloads[cadsr_usage_path]),
        "suppressed_candidates_by_row": suppression,
        "packets": tuple(entries),
        "unavailable_action_classes": {
            "ADD-SCOREABLE": "zero expected-not-emitted actionable pairs in the actual seven-row packet; engineering regeneration #274/#267 is required",
            "OMIT": "zero expected-not-emitted actionable pairs in the actual seven-row packet; engineering regeneration #274/#267 is required",
        },
        "registered_mint_expected_set": visible_registered,
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
    suppressed_mints = all_mints - registered
    leaked = sorted(item for item in suppressed_mints if item.encode() in rendered)
    if leaked:
        findings.append("suppressed MINT identifiers leaked into rendered bytes")
    if any(row.status == "error" for row in cadsr.rows):
        findings.append("caDSR report contains an error row")
    withheld_codes = [
        entry.code for entry in entries if entry.dispatch_status == "withheld"
    ]
    if withheld_codes:
        findings.append(
            "release bundle contains withheld rows: " + ", ".join(withheld_codes)
        )
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
    if "<!-- QUESTION" in rendered_text or "<!-- Allowed actions" in rendered_text:
        findings.append("critical review content is hidden in HTML comments")
    if any(
        token in rendered_text
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
    return index


def validate_completion(  # noqa: C901
    stage_a: ClinicalStageA,
    stage_b: OntologyStageB,
    *,
    clinically_asked_pairs: tuple[str, ...],
    action_pairs: tuple[str, ...] | None = None,
    engineering_only_pairs: tuple[str, ...] = (),
    allowed_actions_by_pair: dict[str, tuple[str, ...]] | None = None,
    allowed_reaxis_targets_by_pair: dict[str, tuple[str, ...]] | None = None,
    relations_by_pair: dict[str, Relation] | None = None,
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
    decided = {item.pair_id for item in stage_b.decisions}
    engineering = set(engineering_only_pairs)
    if assessed != asked:
        raise ValueError("Stage A assessments must exactly equal index asked-pair set")
    if stage_b.row_outcome == "RESOLVED" and decided != actions:
        raise ValueError("Stage B decisions must exactly equal index action-pair set")
    if stage_b.row_outcome == "DEFERRED" and decided:
        raise ValueError("DEFERRED Stage B cannot contain terminal decisions")
    if decided & engineering:
        raise ValueError("engineering-only pair cannot have an ontology action")
    for decision in stage_b.decisions:
        if (
            relations_by_pair is not None
            and relations_by_pair.get(decision.pair_id) != decision.relation
        ):
            raise ValueError("Stage B relation must equal the indexed pair relation")
        if (
            allowed_actions_by_pair is not None
            and decision.action not in allowed_actions_by_pair.get(decision.pair_id, ())
        ):
            raise ValueError(
                "ontology action is not allowed by the indexed pair contract"
            )
        if decision.action == "RE-AXIS" and (
            allowed_reaxis_targets_by_pair is None
            or decision.target_axis
            not in allowed_reaxis_targets_by_pair.get(decision.pair_id, ())
        ):
            raise ValueError("target axis is not an indexed allowed re-axis target")
    if stage_a.clinical_stage != "SUFFICIENT-FOR-ONTOLOGY-REVIEW" and stage_b.decisions:
        raise ValueError("Stage B decisions require sufficient Stage A")
    if (
        stage_b.row_outcome == "RESOLVED"
        and stage_a.clinical_stage != "SUFFICIENT-FOR-ONTOLOGY-REVIEW"
    ):
        raise ValueError("resolved Stage B requires sufficient Stage A")
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
            "Reviewer name",
            "Specialty",
            "Review date (YYYY-MM-DD)",
            "Conflict of interest",
            "Source confirmation",
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
    stage_a = ClinicalStageA.model_validate(
        {
            "reviewer_name": a_values["Reviewer name"],
            "specialty": a_values["Specialty"],
            "review_date": a_values["Review date (YYYY-MM-DD)"],
            "conflict_of_interest": a_values["Conflict of interest"],
            "source_confirmation": a_values["Source confirmation"],
            "assessments": tuple(
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
            "clinical_stage": a_values[
                "Clinical stage (SUFFICIENT-FOR-ONTOLOGY-REVIEW, CLINICAL-COMPLETE-ENGINEERING-PENDING, or DEFERRED)"
            ],
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
            "Reviewer name",
            "Review date (YYYY-MM-DD)",
            "Conflict of interest",
            "ROW-OUTCOME (RESOLVED or DEFERRED)",
            "Whole-row blocker if DEFERRED",
            "Blocker source if DEFERRED",
            "Next action if DEFERRED",
        ),
    )
    row_outcome = b_values["ROW-OUTCOME (RESOLVED or DEFERRED)"]
    contracts = {item.pair_id: item for item in entry.pair_contracts}
    decision_values = {
        pair_id: _region_fields(
            blocks[f"STAGE-B-PAIR:{pair_id}"],
            ("Action", "Target axis", "Group", "Rationale"),
        )
        for pair_id in entry.action_pair_ids
    }
    stage_b = OntologyStageB.model_validate(
        {
            "reviewer_name": b_values["Reviewer name"],
            "review_date": b_values["Review date (YYYY-MM-DD)"],
            "conflict_of_interest": b_values["Conflict of interest"],
            "row_outcome": row_outcome,
            "decisions": tuple(
                {
                    "pair_id": pair_id,
                    "relation": contracts[pair_id].relation,
                    "action": values["Action"],
                    "target_axis": values["Target axis"] or None,
                    "group_assignment": values["Group"] or None,
                    "rationale": values["Rationale"],
                }
                for pair_id, values in decision_values.items()
                if row_outcome == "RESOLVED"
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
        allowed_reaxis_targets_by_pair={
            key: value.allowed_reaxis_targets for key, value in contracts.items()
        },
        relations_by_pair={key: value.relation for key, value in contracts.items()},
    )
    if row_outcome == "RESOLVED" and not blocks["FINAL-PARTITION"].strip():
        raise ValueError("resolved Stage B requires a final partition response")
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
