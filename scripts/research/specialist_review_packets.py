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
_STAGE_B_DECISION_WIDTH = 7
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
    current_projection_status: str = Field(min_length=1)
    axis_range_verdict: Literal["valid", "invalid", "unknown"]
    modality: str = Field(min_length=1)
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
    clinical_stage: Literal["SUFFICIENT-FOR-ONTOLOGY-REVIEW", "DEFERRED"]
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
    target_range_verdict: Literal["valid", "invalid", "unknown"] | None
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
        if self.action == "RE-AXIS" and self.target_range_verdict != "valid":
            raise ValueError(
                "RE-AXIS requires a valid stored target-axis range verdict"
            )
        if self.action != "RE-AXIS" and (
            self.target_axis is not None or self.target_range_verdict is not None
        ):
            raise ValueError("only RE-AXIS may name a target axis and range verdict")
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

    @model_validator(mode="after")
    def _outcome(self) -> Self:
        date.fromisoformat(self.review_date)
        if (self.row_outcome == "DEFERRED") != (self.blocker is not None):
            raise ValueError("DEFERRED Stage B requires exactly one blocker")
        return self


class PacketIndexEntry(_StrictModel):
    code: str
    path: str
    row_sha256: str = Field(pattern=_SHA256)
    row_contract_identity: str = Field(pattern=_SHA256)
    asked_pair_ids: tuple[str, ...]
    action_pair_ids: tuple[str, ...]
    engineering_pair_ids: tuple[str, ...]
    context_pair_ids: tuple[str, ...]


class PacketIndex(_StrictModel):
    schema_version: Literal[2] = 2
    ncit_version: Literal["26.07d"]
    input_identities: dict[str, str]
    literature_context_identity: str = Field(pattern=_SHA256)
    cadsr_usage_identity: str = Field(pattern=_SHA256)
    suppressed_unregistered_mints_by_row: dict[str, int]
    packets: tuple[PacketIndexEntry, ...]
    index_identity: str = Field(pattern=_SHA256)


class GenerationValidation(_StrictModel):
    schema_version: Literal[2]
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


def _scope(
    code: str, key: PairKey, relation: Relation, range_status: str
) -> tuple[ReviewScope, str, str | None]:
    clinical_only = {
        ("C102870", "op:AssociatedSite", "C12321"),
        ("C198031", "op:NormalTissueOrigin", "C13049"),
        ("C198031", "op:PrimarySite", "C12431"),
    }
    if (code, key.axis, key.filler) in clinical_only:
        return (
            "stage-a-clinical-only",
            "Clinical applicability is reviewable, but selector output cannot receive Stage B action until #274 is repaired.",
            "#274 selector repair queued; regenerate packets after repair",
        )
    if code == "C35756" and key.filler == "C141685":
        return (
            "engineering-only",
            "Stored range verdict is invalid; deterministic omission is owned by engineering and is not a human action.",
            "#271 range repair blocked; rerun deterministic range gate after repair",
        )
    if range_status == "invalid":
        return (
            "engineering-only",
            "Stored range verdict is invalid; no human ontology action is offered.",
            "#271 range repair blocked; rerun deterministic range gate after repair",
        )
    if relation == "current-only-proposed":
        return (
            "context-not-under-review",
            "Current proposed content is shown as governance context and is not eligible for specialist action.",
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
) -> tuple[tuple[tuple[tuple[str, str], Relation], ...], int, tuple[str, ...]]:
    """Filter MINT lifecycle/range eligibility before assigning human pair IDs."""
    visible: list[tuple[tuple[str, str], Relation]] = []
    registered_visible: set[str] = set()
    suppressed = 0
    for key, relation in relations:
        filler = key[1]
        if filler.startswith("MINT-"):
            if filler not in registered_mints or range_status.get(key) != "valid":
                suppressed += 1
                continue
            registered_visible.add(filler)
        visible.append((key, relation))
    return tuple(visible), suppressed, tuple(sorted(registered_visible))


def _build_rows(
    context: GeneratedLiteratureContext,
    raw_inputs: tuple[Any, ...],
    registered_mints: set[str],
    ncit_labels: dict[str, str] | None = None,
    ncit_definitions: dict[str, str] | None = None,
) -> tuple[tuple[SpecialistRowPacket, ...], dict[str, int], tuple[str, ...]]:
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
    suppression: dict[str, int] = {}
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
            scope, reason, blocker = _scope(code, key, relation, range_status)
            source_rows: list[dict[str, Any]] = occurrences.get(raw_key, [])
            if not source_rows and (code, axis, filler) in candidates:
                source_rows = candidates[(code, axis, filler)]["source_evidence"].get(
                    "occurrences", []
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
            role_code = ", ".join(role_codes) if role_codes else "unavailable"
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
                    else "No source role occurrence available",
                    source_role_definition="; ".join(
                        definitions.get(
                            value,
                            f"Explicitly unavailable: {value} has no P97 definition in the bound 26.07d source.",
                        )
                        for value in role_codes
                    )
                    if role_codes
                    else "Explicitly unavailable: no source role occurrence exists for this pair.",
                    source_occurrences=source_occurrences,
                    current_projection_status=str(projection),
                    axis_range_verdict=range_status,
                    modality="direct"
                    if any(item.depth == 0 for item in source_occurrences)
                    else (
                        "inherited"
                        if source_occurrences
                        else "source-coordinate-unavailable"
                    ),
                    governance="active axis contract; op:NormalTissueOrigin is non-defining"
                    if axis != "op:NormalTissueOrigin"
                    else "provisional non-defining axis contract",
                    fallback="No fallback used."
                    if source_occurrences
                    else "No source coordinate; no fallback inference admitted.",
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
        question_keys = tuple(
            tuple(
                PairKey(axis=key.axis, filler=key.filler) for key in question.pair_keys
            )
            for question in dossier.questions
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


def _consequence(pair: SpecialistPair) -> str:
    return "Every action preserves source facts. RETAIN: scoreable unchanged (ΔTP=0, ΔFP=0, ΔFN=0). REMOVE: scoreability off and needsReview retained (potential ΔTP=-1 or ΔFP=-1; recall/precision recomputed exactly from TP, FP, FN). PROMOTE/ADD: scoreability on and needsReview cleared (potential ΔTP=+1 or ΔFP=+1 and ΔFN=-1 only when the pair is expected). RE-AXIS: same arithmetic only after a stored valid target; invalid/unknown offers no action. GROUP-TOGETHER changes normalized group partition only, not assessment association or equivalence. No predicted post-review metric is asserted."


def _render_packet(
    row: SpecialistRowPacket,
    dossier: LiteratureDossierSource,
    cadsr: CadsrUsageRow,
    actual_partition: object,
    expected_partition: object,
    suppression_count: int,
) -> bytes:
    lines = [
        f"# {row.code} — {row.label}",
        "",
        "**Blank specialist packet.** Current NCIt 26.07d baseline only. Historical proposals are warnings, not selected responses. This packet is write-free; readiness is false and it cannot authorize equivalence, adoption, or publication.",
        "",
        "## Return workflow",
        "",
        f"1. Save the returned file as `{row.code}-specialist-return.md`; do not edit its file name prefix.",
        "2. Edit only blank response cells in the Stage A and Stage B Markdown tables. Do not edit JSON (there is no human JSON response surface).",
        "3. Stage A is clinical classification; Stage B is ontology action. One person may perform both only by separately signing both roles.",
        "4. Return the one row file. The validator rejects edits outside response cells and rejects partial-ready claims.",
        "",
        "Neutral format example (synthetic, not a selected answer): `PX | UNRESOLVED | PMID:00000000 | More evidence needed`.",
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
        f"**Truncated:** {str(cadsr.truncated).lower()}  ",
        "caDSR usage does not determine clinical or ontology correctness.",
        "",
        "## Audited factual context",
        "",
        *(f"- {item}" for item in dossier.factual_context),
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
            f"| {pair.pair_id} | `{pair.key.axis} {pair.key.filler}` — {_escape(pair.filler_label)}; P97: {_escape(pair.filler_definition)} | {pair.relation}; **{pair.review_scope}**; reason={_escape(pair.scope_reason)}; contested={str(pair.contested).lower()} | {pair.source_role_code} {_escape(pair.source_role_label)}; definition={_escape(pair.source_role_definition)}; {coordinates} | {pair.current_projection_status}; {pair.axis_range_verdict}; RE-AXIS targets={', '.join(pair.allowed_reaxis_targets) or 'none'} | {pair.modality}; {pair.governance}; {pair.fallback} |"
        )
    lines.extend(
        [
            "",
            f"Suppression disclosure: {suppression_count} unregistered candidate(s) were removed before pair numbering; identifiers and actions are intentionally absent from this human packet.",
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
        lines.append(
            f"| {citation.citation_id}; {citation.status}; {citation.authority_class} #{citation.authority_order} | {_escape(citation.bibliography)}; {citation.url}; DOI={citation.doi or 'none'}; PMID={citation.pmid or 'none'} | {citation.verified_on}; {_escape(citation.exact_locator)}; {_escape(citation.exact_passage)} | {_escape(citation.supports)} | {_escape(citation.does_not_support)} | {_escape(citation.limitations)}; {_escape(citation.conflicts_or_supersession)} |"
        )
    lines.extend(
        [
            "",
            "## Stage A — Clinical review",
            "",
            f"Required specialty: {dossier.specialty}",
            "Allowed statuses: UNIVERSAL-DEFINING (part of every definition); UNIVERSAL-NONDEFINING (present in every case but not defining); CHARACTERISTIC-NONUNIVERSAL (frequent, not universal); CLASSIFICATION-DEPENDENT (varies by named system/version/entity); INAPPLICABLE; UNRESOLVED.",
            "Stage A records clinical evidence only and performs no ontology action.",
            "",
            "<!-- RESPONSE-CELLS-START A -->",
            "| Field | Response |",
            "|---|---|",
            "| Reviewer name |  |",
            "| Specialty |  |",
            "| Review date (YYYY-MM-DD) |  |",
            "| Conflict of interest |  |",
            "| Source confirmation |  |",
            "| Clinical stage (SUFFICIENT-FOR-ONTOLOGY-REVIEW or DEFERRED) |  |",
            "| Whole-row blocker if DEFERRED |  |",
            "",
            "| Pair | Status | Citations | Rationale |",
            "|---|---|---|---|",
        ]
    )
    asked = set(row.asked_pair_ids)
    question_text = {
        pair_id: question.text
        for question, ids in zip(
            dossier.questions, row.resolved_question_pair_ids, strict=True
        )
        for pair_id in ids
    }
    for pair in row.pairs:
        if pair.pair_id in asked:
            lines.append(f"| {pair.pair_id} |  |  |  |")
            lines.append(
                f"<!-- QUESTION {pair.pair_id}: {_escape(question_text.get(pair.pair_id, 'Classify this exact semantic pair under the cited row context.'))} -->"
            )
    lines.extend(
        [
            "<!-- RESPONSE-CELLS-END A -->",
            "",
            "## Stage B — Ontology review",
            "",
            "Requires sufficient Stage A. Actions: RETAIN-SCOREABLE, REMOVE-FROM-PROJECTION, RE-AXIS, PROMOTE-SCOREABLE, ADD-SCOREABLE, OMIT, GROUP-TOGETHER, KEEP-SEPARATE. Relation-specific allowed actions are validated. RE-AXIS is offered only for listed stored-valid targets. GROUP-TOGETHER changes normalized group only; it creates no assessment association or equivalence.",
            "Whole-row DEFER retains nonterminal pair evidence but emits no terminal delta or final partition. Readiness remains false; there is no partial-ready state.",
            "",
            "<!-- RESPONSE-CELLS-START B -->",
            "| Field | Response |",
            "|---|---|",
            "| Reviewer name |  |",
            "| Review date (YYYY-MM-DD) |  |",
            "| Conflict of interest |  |",
            "| Row outcome (RESOLVED or DEFERRED) |  |",
            "| Whole-row blocker if DEFERRED |  |",
            "",
            "| Pair | Relation | Action | Target axis | Target range | Group | Rationale |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for pair in row.pairs:
        if pair.pair_id in set(row.action_pair_ids):
            lines.append(f"| {pair.pair_id} | {pair.relation} |  |  |  |  |  |")
    lines.extend(
        [
            "<!-- RESPONSE-CELLS-END B -->",
            "",
            "## Per-action consequences",
            "",
            *[
                f"- **{pair.pair_id}:** {_consequence(pair)}"
                for pair in row.pairs
                if pair.pair_id in set(row.action_pair_ids)
            ],
            "",
            "Stage A signature: ______  Stage B signature: ______",
            "The same individual may sign both only after separately completing both role blocks.",
            "",
        ]
    )
    return "\n".join(lines).encode()


def generate_specialist_review_packets(  # noqa: C901, PLR0915
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
    rows, suppression, _visible_registered = _build_rows(
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
            concept["actual_partition"],
            concept["expected_partition"],
            suppression[row.code],
        )
        name = f"{row.code}.md"
        packet_payloads[name] = payload
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
            )
        )
    index_values: dict[str, object] = {
        "schema_version": 2,
        "ncit_version": "26.07d",
        "input_identities": identities,
        "literature_context_identity": _sha(payloads[literature_context_path]),
        "cadsr_usage_identity": _sha(payloads[cadsr_usage_path]),
        "suppressed_unregistered_mints_by_row": suppression,
        "packets": tuple(entries),
        "index_identity": "0" * 64,
    }
    index_values["index_identity"] = _identity_without(index_values, "index_identity")
    index = PacketIndex.model_validate(index_values)
    index_payload = _canonical(index)
    packet_payloads["index.json"] = index_payload
    findings: list[str] = []
    rendered = b"\n".join(packet_payloads.values())
    all_mints = set(
        _MINT.findall(b"\n".join(payloads.values()).decode(errors="ignore"))
    )
    suppressed_mints = all_mints - registered
    leaked = sorted(item for item in suppressed_mints if item.encode() in rendered)
    if leaked:
        findings.append("suppressed MINT identifiers leaked into rendered bytes")
    validation_values: dict[str, object] = {
        "schema_version": 2,
        "index_identity": index.index_identity,
        "index_file_sha256": _sha(index_payload),
        "status": "failed" if findings else "passed",
        "findings": tuple(findings),
        "producing_command": producing_command,
        "produced_on": "2026-08-31",
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
    if findings:
        raise ValueError(
            "specialist packet generation validation failed: " + "; ".join(findings)
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
    return index


def validate_completion(
    stage_a: ClinicalStageA,
    stage_b: OntologyStageB,
    *,
    clinically_asked_pairs: tuple[str, ...],
    action_pairs: tuple[str, ...] | None = None,
    engineering_only_pairs: tuple[str, ...] = (),
) -> bool:
    asked, assessed = (
        set(clinically_asked_pairs),
        {item.pair_id for item in stage_a.assessments},
    )
    actions = set(action_pairs if action_pairs is not None else clinically_asked_pairs)
    decided = {item.pair_id for item in stage_b.decisions}
    engineering = set(engineering_only_pairs)
    if assessed != asked:
        raise ValueError("Stage A assessments must exactly equal index asked-pair set")
    if decided != actions:
        raise ValueError("Stage B decisions must exactly equal index action-pair set")
    if decided & engineering:
        raise ValueError("engineering-only pair cannot have an ontology action")
    if stage_a.clinical_stage != "SUFFICIENT-FOR-ONTOLOGY-REVIEW" and stage_b.decisions:
        raise ValueError("Stage B decisions require sufficient Stage A")
    if (
        stage_b.row_outcome == "RESOLVED"
        and stage_a.clinical_stage != "SUFFICIENT-FOR-ONTOLOGY-REVIEW"
    ):
        raise ValueError("resolved Stage B requires sufficient Stage A")
    return True


def _response_normalized(text: str) -> str:
    def blank_block(match: re.Match[str]) -> str:
        block = match.group(0)
        lines = []
        for line in block.splitlines():
            if line.startswith("|") and "---" not in line:
                cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                if cells and cells[0] not in {"Field", "Pair"}:
                    keep = 2 if len(cells) == _STAGE_B_DECISION_WIDTH else 1
                    cells = cells[:keep] + [""] * (len(cells) - keep)
                    normalized_line = "| " + " | ".join(cells) + " |"
                else:
                    normalized_line = line
            else:
                normalized_line = line
            lines.append(normalized_line)
        return "\n".join(lines)

    return re.sub(
        r"<!-- RESPONSE-CELLS-START [AB] -->.*?<!-- RESPONSE-CELLS-END [AB] -->",
        blank_block,
        text,
        flags=re.DOTALL,
    )


def _table_rows(block: str, width: int) -> list[list[str]]:
    result = []
    for line in block.splitlines():
        if not line.startswith("|") or "---" in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == width and cells[0] not in {"Field", "Pair"}:
            result.append(cells)
    return result


def validate_specialist_review_packet_directory(
    directory: Path,
) -> CompletionValidation:
    index = PacketIndex.model_validate_json((directory / "index.json").read_bytes())
    completed: list[str] = []
    for entry in index.packets:
        path = directory / entry.path
        text = path.read_text(encoding="utf-8")
        if _sha(_response_normalized(text).encode()) != entry.row_sha256:
            raise ValueError(
                f"returned packet has edits outside response cells: {entry.path}"
            )
        blocks = re.findall(
            r"<!-- RESPONSE-CELLS-START ([AB]) -->(.*?)<!-- RESPONSE-CELLS-END \1 -->",
            text,
            re.DOTALL,
        )
        if tuple(kind for kind, _ in blocks) != ("A", "B"):
            raise ValueError(
                f"returned packet response tables are missing: {entry.path}"
            )
        a_rows = _table_rows(blocks[0][1], 2)
        a_values = dict(a_rows[:7])
        assessment_rows = _table_rows(blocks[0][1], 4)
        stage_a = ClinicalStageA.model_validate(
            {
                "reviewer_name": a_values["Reviewer name"],
                "specialty": a_values["Specialty"],
                "review_date": a_values["Review date (YYYY-MM-DD)"],
                "conflict_of_interest": a_values["Conflict of interest"],
                "source_confirmation": a_values["Source confirmation"],
                "assessments": tuple(
                    {
                        "pair_id": pair,
                        "status": status,
                        "citations": tuple(
                            item.strip()
                            for item in citations.split(";")
                            if item.strip()
                        ),
                        "rationale": rationale,
                    }
                    for pair, status, citations, rationale in assessment_rows
                ),
                "clinical_stage": a_values[
                    "Clinical stage (SUFFICIENT-FOR-ONTOLOGY-REVIEW or DEFERRED)"
                ],
                "blocker": a_values["Whole-row blocker if DEFERRED"] or None,
            }
        )
        b_rows = _table_rows(blocks[1][1], 2)
        b_values = dict(b_rows[:5])
        decision_rows = _table_rows(blocks[1][1], 7)
        stage_b = OntologyStageB.model_validate(
            {
                "reviewer_name": b_values["Reviewer name"],
                "review_date": b_values["Review date (YYYY-MM-DD)"],
                "conflict_of_interest": b_values["Conflict of interest"],
                "row_outcome": b_values["Row outcome (RESOLVED or DEFERRED)"],
                "decisions": tuple(
                    {
                        "pair_id": pair,
                        "relation": relation,
                        "action": action,
                        "target_axis": target or None,
                        "target_range_verdict": target_range or None,
                        "group_assignment": group or None,
                        "rationale": rationale,
                    }
                    for pair, relation, action, target, target_range, group, rationale in decision_rows
                ),
                "blocker": b_values["Whole-row blocker if DEFERRED"] or None,
            }
        )
        validate_completion(
            stage_a,
            stage_b,
            clinically_asked_pairs=entry.asked_pair_ids,
            action_pairs=entry.action_pair_ids,
            engineering_only_pairs=entry.engineering_pair_ids,
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
