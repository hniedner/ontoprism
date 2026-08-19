"""Purpose-built, source-bound human review workflow for the R101 ledger."""

# ruff: noqa: E501 - exact reviewer-facing prose remains readable as whole sentences.

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, Self, cast
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill, Protection
from openpyxl.worksheet.datavalidation import DataValidation
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import to_jsonable_python

from ontolib.decomposition.r101_conservation import (
    ContentAuthorization,
    LedgerOccurrence,
    R101ConservationReport,
    validate_r101_publication,
)
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.ncit.sibling_store import validate_ncit_sibling_manifest
from ontolib.terminologies.sparql_transport import safe_iri

if TYPE_CHECKING:
    from collections.abc import Collection

    from openpyxl.cell import Cell, MergedCell
    from openpyxl.workbook.workbook import Workbook as WorkbookType
    from openpyxl.worksheet.worksheet import Worksheet

_SHA256 = r"^[0-9a-f]{64}$"
_CODE = r"^C[0-9]+$"
_NCIT = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#"
_SCOPE = (
    "Decide coverage under OntoPrism project policy for the directed stated R82 "
    "path; this is not equivalence. Source occurrences remain preserved, and a "
    "decision applies only to this exact packet/report/source digest."
)
_PATTERN_HEADERS = (
    "Pattern ID",
    "Row Identity",
    "Old Broader Code",
    "Old Broader Label",
    "Retained Narrower Code",
    "Retained Narrower Label",
    "Axis",
    "Occurrence Count",
    "One-step Count",
    "Closure-only Count",
    "Min Path Length",
    "Max Path Length",
    "Affected Concept Count",
    "Affected Concept IDs",
    "Affected Occurrence Count",
    "Affected Occurrence IDs",
    "Directed R82 Paths",
    "Sentinel C6135",
    "Sentinel C101539",
    "Sentinel C4791",
    "Representative Examples",
    "Decision",
    "Rationale",
    "Reviewer Identity",
    "Review Date",
)
_EDITABLE_HEADERS = frozenset(
    {"Decision", "Rationale", "Reviewer Identity", "Review Date"}
)
_OCCURRENCE_HEADERS = (
    "Pattern ID",
    "Concept Code",
    "Occurrence ID",
    "Source Fact ID",
    "Source Group ID",
    "Anchor Code",
    "Depth",
    "Structural Path",
    "Member Position",
    "Evidence Kind",
    "Path Length",
    "Path Identity",
)
_DEFINITION_HEADERS = (
    "Sheet",
    "Header",
    "Plain-language definition",
    "Source / procedure",
    "Reviewer action",
    "Warning signs",
)
_PATTERN_DEFINITIONS = (
    (
        "Pattern ID",
        "Stable provenance ID for this endpoint pattern; it is an audit field, not a concept the reviewer must interpret.",
        "Derived from axis, endpoint codes, report identity, and source identity.",
        "Use only to cross-reference the occurrence appendix or investigate a problem.",
        "A missing, duplicated, or changed ID.",
    ),
    (
        "Row Identity",
        "Cryptographic audit identity of all generated evidence in this pattern row; it is a provenance field, not clinical evidence to interpret.",
        "SHA-256 of the packet's complete pattern payload except this identity itself.",
        "Ignore during ordinary review; use when investigating evidence integrity.",
        "Any unexplained identity mismatch reported by import.",
    ),
    (
        "Old Broader Code",
        "NCIt code for the broader PrimarySite assertion present in the old occurrence.",
        "Taken from the depth-matched v3 occurrence ledger.",
        "Compare it with the retained narrower site and every directed R82 path.",
        "The code or its label is surprising for the listed contexts.",
    ),
    (
        "Old Broader Label",
        "Release-bound NCIt 26.07d label for the old broader PrimarySite code.",
        "Read from the certified stated NCIt source before packet generation.",
        "Use with the code to understand the omitted broader site.",
        "The label appears inconsistent with the code or clinical context.",
    ),
    (
        "Retained Narrower Code",
        "NCIt code for the narrower PrimarySite assertion retained in the new occurrence.",
        "Taken from the depth-matched v4 occurrence ledger.",
        "Decide whether this retained site adequately covers the old broader site in every listed context.",
        "It is not genuinely part of the old broader site or belongs to another axis.",
    ),
    (
        "Retained Narrower Label",
        "Release-bound NCIt 26.07d label for the retained narrower PrimarySite code.",
        "Read from the certified stated NCIt source before packet generation.",
        "Use with the code and paths to understand the retained site.",
        "The label appears inconsistent with the code, path, or context.",
    ),
    (
        "Axis",
        "Relationship axis shared by the old and retained assertions; these rows are PrimarySite.",
        "Validated from both depth-matched ledger occurrences before grouping.",
        "Confirm the comparison is on the same clinical axis.",
        "Anything other than op:PrimarySite or an apparent cross-axis comparison.",
    ),
    (
        "Occurrence Count",
        "Number of covered ledger occurrences grouped into this endpoint pattern.",
        "Count of exact occurrences sharing axis, old broader code, and retained narrower code.",
        "Use it to understand the pattern's scale and compare with the appendix.",
        "It differs from Affected Occurrence Count or appendix rows.",
    ),
    (
        "One-step Count",
        "Occurrences supported by one directly stated R82 edge from retained narrower to old broader.",
        "Counted from one-step evidence kinds in the complete occurrence ledger.",
        "Inspect alongside Closure-only Count and the distinct paths.",
        "The counts do not sum to Occurrence Count.",
    ),
    (
        "Closure-only Count",
        "Occurrences supported only by a directed chain of two or more stated R82 edges.",
        "Counted from closure-only evidence kinds in the complete occurrence ledger.",
        "Scrutinize every multi-step chain for clinical appropriateness.",
        "A chain over-collapses a composite, system, or clinically distinct component.",
    ),
    (
        "Min Path Length",
        "Smallest number of directed stated R82 edges used by occurrences in this pattern.",
        "Minimum Path Length across grouped occurrence evidence.",
        "Compare with Max Path Length and inspect each distinct path.",
        "The displayed paths do not support this minimum.",
    ),
    (
        "Max Path Length",
        "Largest number of directed stated R82 edges used by occurrences in this pattern.",
        "Maximum Path Length across grouped occurrence evidence.",
        "Give longer chains extra scrutiny.",
        "The displayed paths do not support this maximum or seem clinically inappropriate.",
    ),
    (
        "Affected Concept Count",
        "Number of distinct NCIt concepts containing occurrences in this pattern.",
        "Count of unique Concept Code values in the grouped occurrences.",
        "Use it to judge breadth and compare with examples and appendix.",
        "It differs from the listed IDs or appendix.",
    ),
    (
        "Affected Concept IDs",
        "JSON list of every distinct NCIt concept code affected by this pattern.",
        "Canonical sorted unique Concept Code values from grouped occurrences.",
        "Consult when examples are insufficient or contexts may differ.",
        "Missing, duplicate, or unexpected concepts; contexts are not clinically consistent.",
    ),
    (
        "Affected Occurrence Count",
        "Number of exact source occurrences affected by this pattern.",
        "Count of unique occurrence identities in the group.",
        "Compare with Occurrence Count and appendix rows.",
        "It differs from Occurrence Count, IDs, or appendix.",
    ),
    (
        "Affected Occurrence IDs",
        "JSON list of every exact occurrence provenance identity in this pattern.",
        "Canonical sorted identities from the depth-matched ledger.",
        "Use only to trace or investigate an occurrence in the appendix.",
        "A missing, duplicate, or untraceable identity.",
    ),
    (
        "Directed R82 Paths",
        "JSON for every distinct directed stated R82 part-to-whole path, including codes, labels, source facts, and path identity.",
        "Deduplicated from certified NCIt 26.07d stated R82 evidence for grouped occurrences.",
        "Inspect every path, its direction, labels, and number of edges before deciding.",
        "Direction seems wrong, a link is clinically inappropriate, or codes and labels are surprising.",
    ),
    (
        "Sentinel C6135",
        "TRUE when affected concepts include this release-bound sentinel; FALSE means no covered occurrence for it is in this pattern.",
        "Exact membership test against Affected Concept IDs.",
        "Use as a quick context flag; FALSE does not mean unchecked.",
        "The appendix or affected IDs contradict the value.",
    ),
    (
        "Sentinel C101539",
        "TRUE when affected concepts include this release-bound sentinel; FALSE means no covered occurrence for it is in this pattern.",
        "Exact membership test against Affected Concept IDs.",
        "Use as a quick context flag; FALSE does not mean unchecked.",
        "The appendix or affected IDs contradict the value.",
    ),
    (
        "Sentinel C4791",
        "TRUE when affected concepts include this release-bound sentinel; FALSE means no covered occurrence for it is in this pattern.",
        "Exact membership test against Affected Concept IDs.",
        "Use as a quick context flag; FALSE does not mean unchecked.",
        "The appendix or affected IDs contradict the value.",
    ),
    (
        "Representative Examples",
        "JSON containing up to five deterministic concept and occurrence examples from this pattern.",
        "First five grouped occurrences in canonical structural order.",
        "Use for orientation, then inspect the full IDs and occurrence appendix.",
        "Examples appear inconsistent with the pattern or are treated as exhaustive.",
    ),
    (
        "Decision",
        "Required project-policy decision for this exact pattern: approve or reject.",
        "Entered by the human reviewer; no SME decision is generated.",
        "The reviewer chooses approve only when every criterion is met; otherwise reject.",
        "Blank, another value, or approval despite ambiguity.",
    ),
    (
        "Rationale",
        "Required explanation for the decision, including any ambiguity or concern.",
        "Entered by the human reviewer.",
        "State why coverage is acceptable or identify the reason for rejection/escalation.",
        "Blank, generic, or does not address paths and contexts.",
    ),
    (
        "Reviewer Identity",
        "Required identity of the person accountable for the decision.",
        "Entered by the human reviewer.",
        "Enter a traceable reviewer name or approved identifier.",
        "Blank, synthetic, or untraceable identity.",
    ),
    (
        "Review Date",
        "Required date the human decision was completed, in YYYY-MM-DD form.",
        "Entered by the human reviewer and validated on import.",
        "Enter the actual completion date for this row.",
        "Blank, future-placeholder, or wrong format.",
    ),
)
_OCCURRENCE_DEFINITIONS = (
    (
        "Pattern ID",
        "Provenance link from this occurrence to its Pattern Decisions row.",
        "Copied from the generated endpoint group.",
        "Use to filter all evidence for a pattern when investigating.",
        "No matching pattern or an unexpected group assignment.",
    ),
    (
        "Concept Code",
        "NCIt concept containing this exact PrimarySite occurrence.",
        "Taken from the mechanically complete occurrence ledger.",
        "Check full clinical context when the pattern examples are insufficient.",
        "A concept context conflicts with the proposed coverage.",
    ),
    (
        "Occurrence ID",
        "Cryptographic provenance identity for this exact structural occurrence.",
        "Derived and validated by the depth-matched occurrence ledger.",
        "Use only for audit tracing or investigating a discrepancy.",
        "Duplicate, missing, or absent from the pattern's occurrence IDs.",
    ),
    (
        "Source Fact ID",
        "Cryptographic provenance identity of the old source assertion fact represented by this occurrence.",
        "Taken from the exact v3 source occurrence ledger.",
        "Use only when investigating the source assertion.",
        "A source fact is missing, duplicated unexpectedly, or cannot be traced.",
    ),
    (
        "Source Group ID",
        "Provenance identity of the source relationship group containing the fact.",
        "Taken from the exact v3 source occurrence ledger.",
        "Use only when investigating grouped source context.",
        "Occurrences appear assigned to an inconsistent source group.",
    ),
    (
        "Anchor Code",
        "NCIt code of the structural anchor under which this occurrence was found.",
        "Recorded by the decomposition occurrence walker.",
        "Consult when investigating where the assertion occurs in the concept structure.",
        "Anchor context is surprising or inconsistent with the concept.",
    ),
    (
        "Depth",
        "Nesting depth of this occurrence below its structural anchor.",
        "Recorded by the depth-matched v3/v4 occurrence walk.",
        "Use with Structural Path to investigate placement, not clinical meaning by itself.",
        "Depth and structural path appear inconsistent.",
    ),
    (
        "Structural Path",
        "JSON list locating the occurrence within the decomposed structure.",
        "Recorded exactly by the occurrence walker and retained through reconciliation.",
        "Use only for audit tracing or investigating structural context.",
        "The path is missing, malformed, or conflicts with depth and member position.",
    ),
    (
        "Member Position",
        "Zero-based position of the occurrence within its structural member list.",
        "Recorded exactly by the occurrence walker.",
        "Use only to locate the source occurrence when investigating.",
        "Position conflicts with the structural path or source context.",
    ),
    (
        "Evidence Kind",
        "Whether coverage uses one stated R82 edge (one-step) or only a multi-edge closure path (closure-only).",
        "Classified from the exact directed path length in the ledger.",
        "Give closure-only occurrences and their full chains extra scrutiny.",
        "Value conflicts with Path Length or displayed path.",
    ),
    (
        "Path Length",
        "Number of directed stated R82 edges supporting this occurrence.",
        "Counted from the occurrence's certified path.",
        "Compare with the pattern range and inspect the referenced path.",
        "Length conflicts with evidence kind, pattern range, or path.",
    ),
    (
        "Path Identity",
        "Cryptographic provenance and audit identity of the exact directed R82 path used.",
        "Derived from path codes, labels, source fact identities, and exact source identity.",
        "Ignore ordinarily; use to join repeated paths when investigating evidence.",
        "No matching path in Directed R82 Paths or an unexplained mismatch.",
    ),
)
_SENTINEL_CODES = ("C6135", "C101539", "C4791")


def _pattern_definitions(
    source_release_id: str, sentinel_labels: SentinelLabels
) -> tuple[tuple[str, str, str, str, str], ...]:
    labels = sentinel_labels.by_code()
    result: list[tuple[str, str, str, str, str]] = []
    for row in _PATTERN_DEFINITIONS:
        header = row[0]
        values = tuple(value.replace("26.07d", source_release_id) for value in row[1:])
        if header.startswith("Sentinel "):
            code = header.removeprefix("Sentinel ")
            values = (
                f"TRUE when affected concepts include release-bound sentinel {labels[code]} ({code}); FALSE means no covered occurrence for it is in this pattern.",
                *values[1:],
            )
        result.append((header, values[0], values[1], values[2], values[3]))
    return tuple(result)


def _instruction_rows(
    source_release_id: str, sentinel_labels: SentinelLabels
) -> tuple[tuple[str, str], ...]:
    sentinel_text = "; ".join(
        f"{label} ({code})" for code, label in sentinel_labels.by_code().items()
    )
    return (
        ("R101 human SME pattern review", "Read this sheet before reviewing."),
        (
            "Purpose",
            "Review whether a retained narrower PrimarySite connected by a directed stated R82 path adequately covers the omitted broader PrimarySite for every occurrence grouped in that pattern.",
        ),
        (
            "What approval means",
            "Approval records accepted OntoPrism project-policy coverage for this exact packet, report, and certified source only.",
        ),
        (
            "What approval does NOT mean",
            "Approval asserts no equivalence, source deletion, NCIt acceptance, or general ontology rule. Source occurrences remain preserved.",
        ),
        (
            "How this workbook was generated",
            f"A depth-matched v3/v4 occurrence ledger was certified against the exact NCIt {source_release_id} source. A mechanically complete report grouped 3,291 covered occurrences into 162 endpoint patterns. Labels are release-bound; every full directed stated R82 path is included; no SME decisions generated by the software.",
        ),
        (
            "Reviewer procedure",
            "For each row: (1) inspect the old broader and retained narrower sites; (2) inspect every distinct path and path length; (3) inspect affected concept count, examples, IDs, and the full occurrence appendix; (4) check that clinical context is consistent; (5) choose approve or reject; (6) provide rationale, reviewer identity, and date; review all rows; do not edit evidence cells.",
        ),
        (
            "Approve only when",
            "The retained site is genuinely part of the old broader site along the stated path; both assertions use the same axis; omitting the broader assertion is acceptable across every listed context; and no clinically important meaning is lost.",
        ),
        (
            "Reject or flag these problems",
            "Reject when path direction seems wrong; a part-whole chain is clinically inappropriate; the broad site is needed explicitly; contexts differ; a composite or system is over-collapsed to a component; labels, codes, or a path are surprising; counts or examples are inconsistent; the occurrence appendix contradicts the pattern; or evidence is insufficient; reject rather than guess.",
        ),
        (
            "Escalation and incomplete reviews",
            "Use rationale/comments to identify ambiguity. Any rejection blocks authorization. There are no partial defaults: all rows and all four decision fields are required.",
        ),
        (
            "TRUE/FALSE sentinels and workbook safeguards",
            f"The sentinel names are release-bound values: {sentinel_text}. TRUE means that sentinel concept has a covered occurrence in the pattern. FALSE means no covered occurrence for that sentinel appears in the pattern; it does not mean the pattern was unchecked. Sheet protection is anti-accident only, not password security. Import validation revalidates every cell, including guidance, evidence, decisions, and bindings, and refuses tampering, formulas, missing fields, or stale bindings.",
        ),
    )


def _example_rows() -> tuple[tuple[str, str, str, str], ...]:
    return (
        (
            "ILLUSTRATIVE ONLY — not packet evidence",
            "Synthetic situation",
            "Illustrative response",
            "Reasoning to document",
        ),
        (
            "Synthetic approve example",
            "A retained component has an unsurprising direct stated part-to-whole path to the old broader site, and every affected context supports omission without meaning loss.",
            "approve (illustrative only; never a real decision default)",
            "Document the path, same-axis context, appendix review, and why the broader assertion adds no clinically important meaning in all listed occurrences.",
        ),
        (
            "Synthetic reject / ambiguous example",
            "A multi-step chain crosses a composite or system context, or listed concepts use the broader site differently, leaving coverage ambiguous.",
            "reject; describe ambiguity and escalate rather than guess",
            "Document the surprising link or differing context and why explicit broader meaning may be clinically important.",
        ),
    )


def _guidance_payload(
    source_release_id: str, sentinel_labels: SentinelLabels
) -> dict[str, object]:
    return {
        "instructions": _instruction_rows(source_release_id, sentinel_labels),
        "column_definitions": (
            _DEFINITION_HEADERS,
            *(
                ("Pattern Decisions", *row)
                for row in _pattern_definitions(source_release_id, sentinel_labels)
            ),
            *(("Occurrence Evidence", *row) for row in _OCCURRENCE_DEFINITIONS),
        ),
        "review_examples": _example_rows(),
    }


def _guidance_identity(source_release_id: str, sentinel_labels: SentinelLabels) -> str:
    return _identity(_guidance_payload(source_release_id, sentinel_labels))


class R101ReviewValidationError(ValueError):
    """The review artifact cannot support a packet-bound human decision."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


def _canonical(value: object) -> bytes:
    return json.dumps(
        to_jsonable_python(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def _identity(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


class ReviewBindings(_StrictModel):
    report_identity: str = Field(pattern=_SHA256)
    json_identity: str = Field(pattern=_SHA256)
    tsv_identity: str = Field(pattern=_SHA256)
    source_identity: str = Field(pattern=_SHA256)
    source_release_id: str = Field(min_length=1)
    old_run_id: str = Field(min_length=1)
    old_run_fingerprint_identity: str = Field(pattern=_SHA256)
    old_representation_identity: str = Field(pattern=_SHA256)
    old_baseline_identity: str = Field(pattern=_SHA256)
    new_run_id: str = Field(min_length=1)
    new_run_fingerprint_identity: str = Field(pattern=_SHA256)
    new_representation_identity: str = Field(pattern=_SHA256)
    detector_identity: str = Field(pattern=_SHA256)
    pre_resume_proof_identity: str = Field(pattern=_SHA256)
    resume_dry_run_identity: str = Field(pattern=_SHA256)
    mixed_cohort_identity: str = Field(pattern=_SHA256)
    proof_identity: str = Field(pattern=_SHA256)


class SentinelLabels(_StrictModel):
    c6135: str = Field(min_length=1)
    c101539: str = Field(min_length=1)
    c4791: str = Field(min_length=1)

    def by_code(self) -> dict[str, str]:
        return {
            "C6135": self.c6135,
            "C101539": self.c101539,
            "C4791": self.c4791,
        }


class EvidenceKindCounts(_StrictModel):
    one_step: int = Field(ge=0)
    closure_only: int = Field(ge=0)


class ReviewPath(_StrictModel):
    path_identity: str = Field(pattern=_SHA256)
    code_path: tuple[str, ...] = Field(min_length=2)
    labels: tuple[str, ...] = Field(min_length=2)
    fact_identities: tuple[str, ...] = Field(min_length=1)
    source_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _shape_and_identity(self) -> Self:
        if len(self.labels) != len(self.code_path):
            raise ValueError("path labels do not match code path")
        if len(self.fact_identities) + 1 != len(self.code_path):
            raise ValueError("path facts do not match code path")
        payload = self.model_dump(exclude={"path_identity"})
        if self.path_identity != _identity(payload):
            raise ValueError("path identity differs")
        return self


class RepresentativeExample(_StrictModel):
    concept_code: str = Field(pattern=_CODE)
    occurrence_id: str = Field(pattern=_SHA256)


class ReviewPattern(_StrictModel):
    pattern_id: str = Field(pattern=r"^r101-[0-9a-f]{16}$")
    row_identity: str = Field(pattern=_SHA256)
    old_broader_code: str = Field(pattern=_CODE)
    old_broader_label: str = Field(min_length=1)
    retained_narrower_code: str = Field(pattern=_CODE)
    retained_narrower_label: str = Field(min_length=1)
    axis: str = Field(min_length=1)
    occurrence_count: int = Field(gt=0)
    evidence_kind_counts: EvidenceKindCounts
    min_path_length: int = Field(ge=1, le=8)
    max_path_length: int = Field(ge=1, le=8)
    paths: tuple[ReviewPath, ...] = Field(min_length=1)
    affected_concept_count: int = Field(gt=0)
    affected_concept_ids: tuple[str, ...] = Field(min_length=1)
    affected_occurrence_count: int = Field(gt=0)
    affected_occurrence_ids: tuple[str, ...] = Field(min_length=1)
    sentinel_c6135: bool
    sentinel_c101539: bool
    sentinel_c4791: bool
    representative_examples: tuple[RepresentativeExample, ...] = Field(
        min_length=1, max_length=5
    )

    @model_validator(mode="after")
    def _counts_and_identity(self) -> Self:
        if self.affected_concept_count != len(self.affected_concept_ids):
            raise ValueError("affected concept count differs")
        if self.affected_occurrence_count != len(self.affected_occurrence_ids):
            raise ValueError("affected occurrence count differs")
        if self.occurrence_count != self.affected_occurrence_count:
            raise ValueError("pattern occurrence count differs")
        if self.occurrence_count != (
            self.evidence_kind_counts.one_step + self.evidence_kind_counts.closure_only
        ):
            raise ValueError("pattern evidence counts differ")
        payload = self.model_dump(exclude={"row_identity"})
        if self.row_identity != _identity(payload):
            raise ValueError("row identity differs")
        return self


class ReviewOccurrence(_StrictModel):
    pattern_id: str = Field(pattern=r"^r101-[0-9a-f]{16}$")
    concept_code: str = Field(pattern=_CODE)
    occurrence_id: str = Field(pattern=_SHA256)
    source_fact_id: str = Field(pattern=_SHA256)
    source_group_id: str = Field(pattern=_SHA256)
    anchor_code: str = Field(pattern=_CODE)
    depth: int = Field(ge=0)
    structural_path: tuple[int, ...] = Field(min_length=1)
    member_position: int = Field(ge=0)
    evidence_kind: Literal["one-step", "closure-only"]
    path_length: int = Field(ge=1, le=8)
    path_identity: str = Field(pattern=_SHA256)


class R101ReviewPacket(_StrictModel):
    schema_version: Literal[1]
    review_scope: Literal[
        "Decide coverage under OntoPrism project policy for the directed stated R82 "
        "path; this is not equivalence. Source occurrences remain preserved, and a "
        "decision applies only to this exact packet/report/source digest."
    ]
    bindings: ReviewBindings
    sentinel_labels: SentinelLabels
    guidance_identity: str = Field(pattern=_SHA256)
    patterns: tuple[ReviewPattern, ...]
    occurrences: tuple[ReviewOccurrence, ...]
    packet_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _total_and_identified(self) -> Self:
        if self.guidance_identity != _guidance_identity(
            self.bindings.source_release_id, self.sentinel_labels
        ):
            raise ValueError("guidance identity differs")
        _validate_packet_identity(self)
        _validate_packet_counts(self)
        _validate_packet_pattern_ids(self.patterns)
        _validate_packet_appendix(self.patterns, self.occurrences)
        return self


def _validate_packet_identity(packet: R101ReviewPacket) -> None:
    payload = packet.model_dump(exclude={"packet_identity"})
    if packet.packet_identity != _identity(payload):
        raise ValueError("packet identity differs")


def _validate_packet_counts(packet: R101ReviewPacket) -> None:
    if len(packet.occurrences) != sum(row.occurrence_count for row in packet.patterns):
        raise ValueError("packet occurrence total differs from its patterns")


def _validate_packet_pattern_ids(patterns: tuple[ReviewPattern, ...]) -> None:
    ids = tuple(row.pattern_id for row in patterns)
    if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
        raise ValueError("pattern IDs are not canonical and unique")


def _validate_packet_appendix(
    patterns: tuple[ReviewPattern, ...], occurrences: tuple[ReviewOccurrence, ...]
) -> None:
    expected = Counter(row.pattern_id for row in occurrences)
    actual = Counter({row.pattern_id: row.occurrence_count for row in patterns})
    if expected != actual:
        raise ValueError("occurrence appendix does not exhaust patterns")


class ReviewLabelSource(Protocol):
    async def labels_for_review(
        self, codes: tuple[str, ...]
    ) -> dict[str, tuple[str, ...]]: ...


class ReviewSelectClient(Protocol):
    async def select(
        self, query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str]]: ...


class QLeverReviewLabels:
    """One-query stated-graph NCIt preferred-label reader for a bound source."""

    def __init__(self, client: ReviewSelectClient) -> None:
        self._client = client
        self.query_count = 0
        self.requested_codes: tuple[str, ...] = ()

    async def labels_for_review(
        self, codes: tuple[str, ...]
    ) -> dict[str, tuple[str, ...]]:
        self.requested_codes = codes
        values = " ".join(f"<{safe_iri(code, _NCIT)}>" for code in codes)
        query = (
            "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> "
            "SELECT DISTINCT ?c ?label WHERE { VALUES ?c { "
            f"{values} }} GRAPH <{STATED_GRAPH_IRI}> {{ ?c rdfs:label ?raw_label . }} "
            "BIND(STR(?raw_label) AS ?label) } ORDER BY ?c ?label"
        )
        self.query_count += 1
        rows = await self._client.select(query, required_variables=("c", "label"))
        requested = set(codes)
        result: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            code, label = _validated_label_row(row)
            if code not in requested:
                raise R101ReviewValidationError("unexpected label code binding")
            result[code].add(label)
        return {code: tuple(sorted(values)) for code, values in result.items()}


def _validated_label_row(row: dict[str, str]) -> tuple[str, str]:
    iri, label = row.get("c"), row.get("label")
    if not isinstance(iri, str) or not iri.startswith(_NCIT):
        raise R101ReviewValidationError("malformed label code binding")
    if not isinstance(label, str):
        raise R101ReviewValidationError("malformed label")
    return iri.removeprefix(_NCIT), label


def _bindings(report: R101ConservationReport) -> ReviewBindings:
    fields = ReviewBindings.model_fields
    return ReviewBindings.model_validate(
        {name: getattr(report, name) for name in fields}, strict=True
    )


def _path_payload(
    occurrence: LedgerOccurrence, labels: dict[str, str], source_identity: str
) -> dict[str, object]:
    codes = (
        occurrence.r82_path[0].part_code,
        *(edge.whole_code for edge in occurrence.r82_path),
    )
    payload: dict[str, object] = {
        "code_path": codes,
        "labels": tuple(labels[code] for code in codes),
        "fact_identities": tuple(edge.fact_identity for edge in occurrence.r82_path),
        "source_identity": source_identity,
    }
    return {"path_identity": _identity(payload), **payload}


def _pattern_id(
    report: R101ConservationReport, axis: str, old: str, retained: str
) -> str:
    return (
        "r101-"
        + _identity(
            {
                "axis": axis,
                "old": old,
                "report_identity": report.report_identity,
                "retained": retained,
                "source_identity": report.source_identity,
            }
        )[:16]
    )


def _review_groups(
    report: R101ConservationReport,
) -> tuple[
    tuple[LedgerOccurrence, ...],
    dict[tuple[str, str, str], list[LedgerOccurrence]],
]:
    covered = tuple(
        row
        for row in report.occurrences
        if row.disposition == "covered-by-retained-r82"
    )
    groups: dict[tuple[str, str, str], list[LedgerOccurrence]] = defaultdict(list)
    for row in covered:
        if len(row.old_links) != 1 or row.retained_r82_target is None:
            raise R101ReviewValidationError(
                "covered occurrence is not review-groupable"
            )
        old = row.old_links[0]
        retained = row.retained_r82_target
        if old.axis != retained.axis:
            raise R101ReviewValidationError("cross-axis review pattern")
        groups[(old.axis, old.filler_code, retained.filler_code)].append(row)
    return covered, groups


def _validate_grouping(
    report: R101ConservationReport,
    covered: tuple[LedgerOccurrence, ...],
    groups: dict[tuple[str, str, str], list[LedgerOccurrence]],
) -> None:
    presentation = {
        (row.old_filler_code, row.retained_filler_code): row.occurrence_count
        for row in report.grouping_presentation
    }
    derived = {
        (old, retained): len(rows) for (_, old, retained), rows in groups.items()
    }
    if len(covered) != report.counts.covered_by_retained_r82:
        raise R101ReviewValidationError("grouping does not exhaust covered occurrences")
    if derived != presentation:
        raise R101ReviewValidationError("grouping does not exhaust covered occurrences")


def _occurrence_codes(row: LedgerOccurrence) -> tuple[str, ...]:
    target = row.retained_r82_target
    if target is None:
        raise R101ReviewValidationError("covered occurrence has no retained target")
    return (
        row.old_links[0].filler_code,
        target.filler_code,
        row.r82_path[0].part_code,
        *(edge.whole_code for edge in row.r82_path),
    )


def _validate_label(code: str, values: tuple[str, ...]) -> str:
    if not values:
        raise R101ReviewValidationError(f"missing label for {code}")
    if len(values) != 1:
        raise R101ReviewValidationError(f"multiple labels for {code}")
    label = values[0]
    if not label.strip() or any(char in label for char in "\r\n\t"):
        raise R101ReviewValidationError(f"malformed label for {code}")
    return label


async def _review_labels(
    covered: tuple[LedgerOccurrence, ...], label_source: ReviewLabelSource
) -> dict[str, str]:
    codes = tuple(
        sorted(
            {code for row in covered for code in _occurrence_codes(row)}
            | set(_SENTINEL_CODES)
        )
    )
    raw_labels = await label_source.labels_for_review(codes)
    return {code: _validate_label(code, raw_labels.get(code, ())) for code in codes}


def _review_paths(
    rows: list[LedgerOccurrence], labels: dict[str, str], source_identity: str
) -> tuple[ReviewPath, ...]:
    payloads = {
        _identity(_path_payload(row, labels, source_identity)): _path_payload(
            row, labels, source_identity
        )
        for row in rows
    }
    return tuple(
        ReviewPath.model_validate(payload) for _, payload in sorted(payloads.items())
    )


def _pattern_payload(
    report: R101ConservationReport,
    key: tuple[str, str, str],
    rows: list[LedgerOccurrence],
    labels: dict[str, str],
) -> tuple[dict[str, object], tuple[ReviewPath, ...]]:
    axis, old, retained = key
    paths = _review_paths(rows, labels, report.source_identity)
    concept_ids = tuple(sorted({row.concept_code for row in rows}))
    occurrence_ids = tuple(sorted(row.occurrence_id for row in rows))
    evidence = Counter(row.r82_evidence_kind for row in rows)
    payload: dict[str, object] = {
        "pattern_id": _pattern_id(report, axis, old, retained),
        "old_broader_code": old,
        "old_broader_label": labels[old],
        "retained_narrower_code": retained,
        "retained_narrower_label": labels[retained],
        "axis": axis,
        "occurrence_count": len(rows),
        "evidence_kind_counts": {
            "one_step": evidence["one-step"],
            "closure_only": evidence["closure-only"],
        },
        "min_path_length": min(row.path_length for row in rows),
        "max_path_length": max(row.path_length for row in rows),
        "paths": paths,
        "affected_concept_count": len(concept_ids),
        "affected_concept_ids": concept_ids,
        "affected_occurrence_count": len(occurrence_ids),
        "affected_occurrence_ids": occurrence_ids,
        "sentinel_c6135": "C6135" in concept_ids,
        "sentinel_c101539": "C101539" in concept_ids,
        "sentinel_c4791": "C4791" in concept_ids,
        "representative_examples": tuple(
            {
                "concept_code": row.concept_code,
                "occurrence_id": row.occurrence_id,
            }
            for row in sorted(rows, key=lambda item: item.structural_key)[:5]
        ),
    }
    return payload, paths


def _appendix_rows(
    pattern_id: str,
    rows: list[LedgerOccurrence],
    paths: tuple[ReviewPath, ...],
) -> list[ReviewOccurrence]:
    path_by_codes = {path.code_path: path.path_identity for path in paths}
    return [
        ReviewOccurrence(
            pattern_id=pattern_id,
            concept_code=row.concept_code,
            occurrence_id=row.occurrence_id,
            source_fact_id=row.source_fact_id,
            source_group_id=row.source_group_id,
            anchor_code=row.anchor_code,
            depth=row.depth,
            structural_path=row.structural_path,
            member_position=row.member_position,
            evidence_kind=row.r82_evidence_kind,  # type: ignore[arg-type]
            path_length=row.path_length,
            path_identity=path_by_codes[
                (
                    row.r82_path[0].part_code,
                    *(edge.whole_code for edge in row.r82_path),
                )
            ],
        )
        for row in sorted(rows, key=lambda item: item.structural_key)
    ]


def _build_pattern_rows(
    report: R101ConservationReport,
    groups: dict[tuple[str, str, str], list[LedgerOccurrence]],
    labels: dict[str, str],
) -> tuple[list[ReviewPattern], list[ReviewOccurrence]]:
    patterns: list[ReviewPattern] = []
    appendix: list[ReviewOccurrence] = []
    for key, rows in sorted(groups.items()):
        payload, paths = _pattern_payload(report, key, rows, labels)
        pattern = ReviewPattern.model_validate(
            {"row_identity": _identity(payload), **payload}
        )
        patterns.append(pattern)
        appendix.extend(_appendix_rows(pattern.pattern_id, rows, paths))
    return patterns, appendix


async def build_r101_review_packet(
    report: R101ConservationReport,
    source_manifest: Path,
    label_source: ReviewLabelSource,
) -> R101ReviewPacket:
    """Build the immutable review packet after exhaustive ledger reconciliation."""
    manifest = validate_ncit_sibling_manifest(source_manifest)
    if (
        manifest.source_identity != report.source_identity
        or manifest.ontology_version != report.source_release_id
    ):
        raise R101ReviewValidationError("source manifest does not bind report")
    if report.mechanical_status != "complete" or report.counts.unresolved:
        raise R101ReviewValidationError("report mechanics are incomplete")
    covered, groups = _review_groups(report)
    _validate_grouping(report, covered, groups)
    labels = await _review_labels(covered, label_source)
    sentinel_labels = SentinelLabels(
        c6135=labels["C6135"],
        c101539=labels["C101539"],
        c4791=labels["C4791"],
    )
    patterns, appendix = _build_pattern_rows(report, groups, labels)
    patterns.sort(key=lambda row: row.pattern_id)
    appendix.sort(key=lambda row: (row.pattern_id, row.concept_code, row.occurrence_id))
    payload: dict[str, object] = {
        "schema_version": 1,
        "review_scope": _SCOPE,
        "bindings": _bindings(report),
        "sentinel_labels": sentinel_labels,
        "guidance_identity": _guidance_identity(
            report.source_release_id, sentinel_labels
        ),
        "patterns": tuple(patterns),
        "occurrences": tuple(appendix),
    }
    return R101ReviewPacket.model_validate(
        {**payload, "packet_identity": _identity(payload)}
    )


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        json.dumps(
            to_jsonable_python(payload), sort_keys=True, indent=2, ensure_ascii=True
        ).encode("ascii")
        + b"\n"
    )
    fd, staging_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging_name, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(staging_name)
        raise


def write_r101_review_packet(path: Path, packet: R101ReviewPacket) -> None:
    _write_json(path, packet.model_dump(mode="json"))


def _load_json(path: Path) -> object:
    def reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise R101ReviewValidationError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(path.read_text(), object_pairs_hook=reject_duplicate)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise R101ReviewValidationError("invalid review JSON") from error


def load_r101_review_packet(path: Path) -> R101ReviewPacket:
    try:
        return R101ReviewPacket.model_validate_json(_canonical(_load_json(path)))
    except ValueError as error:
        raise R101ReviewValidationError(str(error)) from error


def _json_cell(value: object) -> str:
    return _canonical(value).decode("ascii")


def _pattern_values(row: ReviewPattern) -> tuple[str | int | bool, ...]:
    return (
        row.pattern_id,
        row.row_identity,
        row.old_broader_code,
        row.old_broader_label,
        row.retained_narrower_code,
        row.retained_narrower_label,
        row.axis,
        row.occurrence_count,
        row.evidence_kind_counts.one_step,
        row.evidence_kind_counts.closure_only,
        row.min_path_length,
        row.max_path_length,
        row.affected_concept_count,
        _json_cell(row.affected_concept_ids),
        row.affected_occurrence_count,
        _json_cell(row.affected_occurrence_ids),
        _json_cell(row.paths),
        row.sentinel_c6135,
        row.sentinel_c101539,
        row.sentinel_c4791,
        _json_cell(row.representative_examples),
    )


def _occurrence_values(row: ReviewOccurrence) -> tuple[str | int, ...]:
    return (
        row.pattern_id,
        row.concept_code,
        row.occurrence_id,
        row.source_fact_id,
        row.source_group_id,
        row.anchor_code,
        row.depth,
        _json_cell(row.structural_path),
        row.member_position,
        row.evidence_kind,
        row.path_length,
        row.path_identity,
    )


def _style_header(cell: Cell | MergedCell) -> None:
    cell.font = Font(bold=True, color="FFFFFF")
    cell.fill = PatternFill("solid", fgColor="1F4E78")
    cell.alignment = Alignment(wrap_text=True, vertical="top")


def _fill_instructions(instructions: Worksheet, packet: R101ReviewPacket) -> None:
    instructions.title = "Instructions"
    for row in _instruction_rows(
        packet.bindings.source_release_id, packet.sentinel_labels
    ):
        instructions.append(row)
    for cell in instructions[1]:
        _style_header(cell)
    for row in instructions.iter_rows(min_row=2):
        row[0].font = Font(bold=True, color="1F4E78")
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    instructions.column_dimensions["A"].width = 36
    instructions.column_dimensions["B"].width = 115
    instructions.freeze_panes = "A2"
    instructions.protection.sheet = True


def _add_bindings(book: WorkbookType, packet: R101ReviewPacket) -> None:
    bindings = book.create_sheet("Bindings")
    binding_rows = (
        ("packet_identity", packet.packet_identity),
        ("guidance_identity", packet.guidance_identity),
        *(packet.bindings.model_dump().items()),
        ("schema_version", packet.schema_version),
    )
    bindings.append(["Binding", "Value"])
    for name, value in binding_rows:
        bindings.append([name, value])
    bindings.sheet_state = "veryHidden"
    bindings.protection.sheet = True


def _add_column_definitions(book: WorkbookType, packet: R101ReviewPacket) -> None:
    definitions = book.create_sheet("Column Definitions")
    definitions.append(_DEFINITION_HEADERS)
    for cell in definitions[1]:
        _style_header(cell)
    for sheet, rows in (
        (
            "Pattern Decisions",
            _pattern_definitions(
                packet.bindings.source_release_id, packet.sentinel_labels
            ),
        ),
        ("Occurrence Evidence", _OCCURRENCE_DEFINITIONS),
    ):
        for row in rows:
            definitions.append((sheet, *row))
    for row in definitions.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    for column, width in zip("ABCDEF", (22, 28, 68, 62, 58, 58), strict=True):
        definitions.column_dimensions[column].width = width
    definitions.freeze_panes = "A2"
    definitions.auto_filter.ref = definitions.dimensions
    definitions.protection.sheet = True


def _add_review_examples(book: WorkbookType) -> None:
    examples = book.create_sheet("Review Examples")
    for row in _example_rows():
        examples.append(row)
    for cell in examples[1]:
        _style_header(cell)
    for row in examples.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    for column, width in zip("ABCD", (34, 72, 52, 72), strict=True):
        examples.column_dimensions[column].width = width
    examples.freeze_panes = "A2"
    examples.protection.sheet = True


def _decision_column_width(header: str) -> int:
    if header in _EDITABLE_HEADERS:
        return 55 if header == "Rationale" else 30
    if "IDs" in header or "Paths" in header or "Examples" in header:
        return 45
    if "Label" in header:
        return 30
    return 19


def _add_pattern_decision_rows(decisions: Worksheet, packet: R101ReviewPacket) -> None:
    editable_fill = PatternFill("solid", fgColor="FFF2CC")
    for pattern in packet.patterns:
        decisions.append((*_pattern_values(pattern), None, None, None, None))
    for row in decisions.iter_rows(min_row=2):
        for cell, header in zip(row, _PATTERN_HEADERS, strict=True):
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            cell.protection = Protection(locked=header not in _EDITABLE_HEADERS)
            if header in _EDITABLE_HEADERS:
                cell.fill = editable_fill


def _add_decisions(book: WorkbookType, packet: R101ReviewPacket) -> None:
    decisions = book.create_sheet("Pattern Decisions")
    decisions.append(_PATTERN_HEADERS)
    for cell in decisions[1]:
        _style_header(cell)
    definitions = {
        row[0]: row[1]
        for row in _pattern_definitions(
            packet.bindings.source_release_id, packet.sentinel_labels
        )
    }
    for header in (
        "Pattern ID",
        "Old Broader Code",
        "Retained Narrower Code",
        "Directed R82 Paths",
        "Decision",
        "Rationale",
        "Reviewer Identity",
        "Review Date",
    ):
        column = _PATTERN_HEADERS.index(header) + 1
        decisions.cell(1, column).comment = Comment(definitions[header], "OntoPrism")
    _add_pattern_decision_rows(decisions, packet)
    decision_column = _PATTERN_HEADERS.index("Decision") + 1
    validation = DataValidation(type="list", formula1='"approve,reject"')
    validation.error = "Choose approve or reject"
    validation.errorTitle = "Invalid decision"
    validation.showErrorMessage = True
    decisions.add_data_validation(validation)
    validation.add(
        f"{decisions.cell(2, decision_column).coordinate}:"
        f"{decisions.cell(decisions.max_row, decision_column).coordinate}"
    )
    decisions.freeze_panes = "A2"
    decisions.auto_filter.ref = decisions.dimensions
    for index, header in enumerate(_PATTERN_HEADERS, start=1):
        column = decisions.cell(1, index).column_letter
        decisions.column_dimensions[column].width = _decision_column_width(header)
    decisions.protection.sheet = True
    decisions.protection.selectLockedCells = False
    decisions.protection.selectUnlockedCells = True


def _add_occurrence_evidence(book: WorkbookType, packet: R101ReviewPacket) -> None:
    evidence = book.create_sheet("Occurrence Evidence")
    evidence.append(_OCCURRENCE_HEADERS)
    for cell in evidence[1]:
        _style_header(cell)
    for occurrence in packet.occurrences:
        evidence.append(_occurrence_values(occurrence))
    evidence.freeze_panes = "A2"
    evidence.auto_filter.ref = evidence.dimensions
    for index, header in enumerate(_OCCURRENCE_HEADERS, start=1):
        width = 45 if "ID" in header or "Path" in header else 19
        evidence.column_dimensions[evidence.cell(1, index).column_letter].width = width
    evidence.protection.sheet = True


def write_r101_review_workbook(path: Path, packet: R101ReviewPacket) -> None:
    """Write the macro-free packet workbook with only decision fields unlocked."""
    book = Workbook()
    instructions = book.active
    if instructions is None:
        raise R101ReviewValidationError("workbook has no active sheet")
    _fill_instructions(instructions, packet)
    _add_bindings(book, packet)
    _add_column_definitions(book, packet)
    _add_review_examples(book)
    _add_decisions(book, packet)
    _add_occurrence_evidence(book, packet)

    for sheet in book.worksheets:
        sheet.sheet_view.showGridLines = False
    book.calculation.calcMode = "auto"
    book.calculation.fullCalcOnLoad = False
    book.calculation.forceFullCalc = False
    fixed_time = datetime(2000, 1, 1)
    book.properties.created = fixed_time
    book.properties.modified = fixed_time
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, staging_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(fd)
    try:
        book.save(staging_name)
        _normalize_xlsx_archive(Path(staging_name), path)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(staging_name)


def _normalize_xlsx_archive(source: Path, destination: Path) -> None:
    """Rewrite an XLSX with canonical member order and metadata."""
    fd, normalized_name = tempfile.mkstemp(
        prefix=f".{destination.name}.normalized.", dir=destination.parent
    )
    os.close(fd)
    try:
        with (
            ZipFile(source) as original,
            ZipFile(
                normalized_name, "w", compression=ZIP_DEFLATED, compresslevel=9
            ) as normalized,
        ):
            for name in sorted(original.namelist()):
                info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o600 << 16
                content = original.read(name)
                if name == "docProps/core.xml":
                    content = re.sub(
                        rb"<dcterms:(created|modified)[^>]*>.*?</dcterms:\1>",
                        rb'<dcterms:\1 xsi:type="dcterms:W3CDTF">'
                        rb"2000-01-01T00:00:00Z</dcterms:\1>",
                        content,
                    )
                elif name == "xl/workbook.xml":
                    content = re.sub(rb' calcId="[0-9]+"', b"", content)
                normalized.writestr(info, content, compress_type=ZIP_DEFLATED)
        os.replace(normalized_name, destination)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(normalized_name)


class PatternDecision(_StrictModel):
    pattern_id: str = Field(pattern=r"^r101-[0-9a-f]{16}$")
    decision: Literal["approve", "reject"]
    rationale: str = Field(min_length=1)
    reviewer_identity: str = Field(min_length=1)
    review_date: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class R101DecisionRegistry(_StrictModel):
    schema_version: Literal[1]
    status: Literal["proposed"]
    provenance: Literal["sme", "test-only"]
    packet_identity: str = Field(pattern=_SHA256)
    report_identity: str = Field(pattern=_SHA256)
    source_identity: str = Field(pattern=_SHA256)
    decisions: tuple[PatternDecision, ...]
    registry_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _total_and_identified(self) -> Self:
        ids = tuple(row.pattern_id for row in self.decisions)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise ValueError("registry pattern decisions are not canonical and unique")
        if self.registry_identity != _identity(
            self.model_dump(exclude={"registry_identity"})
        ):
            raise ValueError("registry identity differs")
        return self


def _validate_archive(path: Path) -> None:
    try:
        with ZipFile(path) as archive:
            names = archive.namelist()
    except (OSError, BadZipFile) as error:
        raise R101ReviewValidationError("invalid XLSX archive") from error
    if any(name.casefold().endswith(("vbaproject.bin", ".vba")) for name in names):
        raise R101ReviewValidationError("macro content is forbidden")
    if any(name.startswith("xl/externalLinks/") for name in names):
        raise R101ReviewValidationError("external link content is forbidden")


def _validate_no_formulas(book: WorkbookType) -> None:
    for sheet in book.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if cell.data_type == "f" or (
                    isinstance(cell.value, str) and cell.value.startswith("=")
                ):
                    raise R101ReviewValidationError("formula cells are forbidden")


def _validate_workbook_bindings(book: WorkbookType, packet: R101ReviewPacket) -> None:
    expected_sheets = [
        "Instructions",
        "Bindings",
        "Column Definitions",
        "Review Examples",
        "Pattern Decisions",
        "Occurrence Evidence",
    ]
    if book.sheetnames != expected_sheets:
        raise R101ReviewValidationError("workbook sheet contract differs")
    bindings = book["Bindings"]
    if bindings.sheet_state != "veryHidden":
        raise R101ReviewValidationError("binding sheet visibility differs")
    expected = [
        ("Binding", "Value"),
        ("packet_identity", packet.packet_identity),
        ("guidance_identity", packet.guidance_identity),
        *list(packet.bindings.model_dump().items()),
        ("schema_version", packet.schema_version),
    ]
    actual = [tuple(cell.value for cell in row[:2]) for row in bindings.iter_rows()]
    if actual != expected:
        raise R101ReviewValidationError("workbook binding cells differ")


def _validate_guidance_sheets(book: WorkbookType, packet: R101ReviewPacket) -> None:
    payload = _guidance_payload(
        packet.bindings.source_release_id, packet.sentinel_labels
    )
    expected = {
        "Instructions": payload["instructions"],
        "Column Definitions": payload["column_definitions"],
        "Review Examples": payload["review_examples"],
    }
    for sheet_name, rows in expected.items():
        actual = tuple(
            tuple(cell.value for cell in row) for row in book[sheet_name].iter_rows()
        )
        if actual != rows:
            raise R101ReviewValidationError(
                f"workbook guidance differs in {sheet_name}"
            )


def _read_decisions(
    book: WorkbookType, packet: R101ReviewPacket
) -> tuple[PatternDecision, ...]:
    sheet = book["Pattern Decisions"]
    headers = tuple(cell.value for cell in sheet[1])
    if headers != _PATTERN_HEADERS:
        raise R101ReviewValidationError("pattern headers differ")
    if sheet.max_row != len(packet.patterns) + 1:
        raise R101ReviewValidationError("extra or missing pattern rows")
    evidence_count = len(_PATTERN_HEADERS) - len(_EDITABLE_HEADERS)
    result: list[PatternDecision] = []
    for index, (row, pattern) in enumerate(
        zip(sheet.iter_rows(min_row=2), packet.patterns, strict=True), start=2
    ):
        result.append(_read_decision_row(row, pattern, evidence_count, index))
    return tuple(sorted(result, key=lambda row: row.pattern_id))


def _read_decision_row(
    row: tuple[Cell | MergedCell, ...],
    pattern: ReviewPattern,
    evidence_count: int,
    index: int,
) -> PatternDecision:
    actual_evidence = tuple(cell.value for cell in row[:evidence_count])
    if actual_evidence != _pattern_values(pattern):
        _raise_evidence_difference(actual_evidence, pattern, index)
    editable = _validated_editable_values(row[evidence_count:])
    decision, rationale, reviewer_identity, review_date = editable
    try:
        return PatternDecision(
            pattern_id=pattern.pattern_id,
            decision=decision,  # type: ignore[arg-type]
            rationale=rationale,
            reviewer_identity=reviewer_identity,
            review_date=review_date,
        )
    except ValueError as error:
        raise R101ReviewValidationError(
            f"invalid decision row {index}: {error}"
        ) from error


def _validated_editable_values(
    cells: tuple[Cell | MergedCell, ...],
) -> tuple[str, str, str, str]:
    editable = tuple(cell.value for cell in cells)
    if any(not isinstance(value, str) or not value.strip() for value in editable):
        raise R101ReviewValidationError(
            "exactly one decision and all reviewer fields are required"
        )
    values = cast("tuple[str, str, str, str]", editable)
    return tuple(value.strip() for value in values)  # type: ignore[return-value]


def _raise_evidence_difference(
    actual: tuple[object, ...], pattern: ReviewPattern, index: int
) -> None:
    if actual[0] != pattern.pattern_id:
        raise R101ReviewValidationError(f"pattern order differs at row {index}")
    raise R101ReviewValidationError(f"pattern evidence differs at row {index}")


def _validate_occurrence_sheet(book: WorkbookType, packet: R101ReviewPacket) -> None:
    sheet = book["Occurrence Evidence"]
    if tuple(cell.value for cell in sheet[1]) != _OCCURRENCE_HEADERS:
        raise R101ReviewValidationError("occurrence evidence headers differ")
    actual = [tuple(cell.value for cell in row) for row in sheet.iter_rows(min_row=2)]
    expected = [_occurrence_values(row) for row in packet.occurrences]
    if actual != expected:
        raise R101ReviewValidationError("occurrence evidence differs")


def import_r101_review_decisions(
    packet: R101ReviewPacket,
    workbook_path: Path,
    output_path: Path,
    *,
    provenance: Literal["sme", "test-only"],
) -> R101DecisionRegistry:
    """Import a total proposed registry without changing report authorization."""
    _validate_archive(workbook_path)
    try:
        book = load_workbook(workbook_path, data_only=False, keep_links=False)
    except (OSError, ValueError, BadZipFile) as error:
        raise R101ReviewValidationError("invalid review workbook") from error
    _validate_no_formulas(book)
    _validate_workbook_bindings(book, packet)
    _validate_guidance_sheets(book, packet)
    _validate_occurrence_sheet(book, packet)
    decisions = _read_decisions(book, packet)
    payload: dict[str, object] = {
        "schema_version": 1,
        "status": "proposed",
        "provenance": provenance,
        "packet_identity": packet.packet_identity,
        "report_identity": packet.bindings.report_identity,
        "source_identity": packet.bindings.source_identity,
        "decisions": decisions,
    }
    registry = R101DecisionRegistry.model_validate(
        {**payload, "registry_identity": _identity(payload)}
    )
    _write_json(output_path, registry.model_dump(mode="json"))
    return registry


def load_r101_decision_registry(path: Path) -> R101DecisionRegistry:
    try:
        return R101DecisionRegistry.model_validate_json(_canonical(_load_json(path)))
    except ValueError as error:
        raise R101ReviewValidationError(str(error)) from error


class AuthorizationDryRunResult(_StrictModel):
    schema_version: Literal[1]
    verdict: Literal["logically-eligible", "blocked"]
    report_identity: str = Field(pattern=_SHA256)
    packet_identity: str = Field(pattern=_SHA256)
    registry_identity: str = Field(pattern=_SHA256)
    provenance: Literal["sme", "test-only"]
    approved_patterns: int = Field(ge=0)
    rejected_patterns: int = Field(ge=0)
    writes_performed: Literal[False]


def _validate_registry_bindings(
    report: R101ConservationReport,
    packet: R101ReviewPacket,
    registry: R101DecisionRegistry,
) -> None:
    checks = (
        (packet.bindings, _bindings(report), "packet bindings do not match report"),
        (
            registry.packet_identity,
            packet.packet_identity,
            "registry packet identity differs",
        ),
        (
            registry.report_identity,
            report.report_identity,
            "registry report identity differs",
        ),
        (
            registry.source_identity,
            report.source_identity,
            "registry source identity differs",
        ),
        (
            len(packet.patterns),
            len(report.grouping_presentation),
            "packet pattern inventory differs from report",
        ),
        (
            len(packet.occurrences),
            report.counts.covered_by_retained_r82,
            "packet occurrence inventory differs from report",
        ),
        (
            tuple(row.pattern_id for row in registry.decisions),
            tuple(row.pattern_id for row in packet.patterns),
            "registry pattern inventory differs",
        ),
    )
    for actual, expected, message in checks:
        if actual != expected:
            raise R101ReviewValidationError(message)


def _apply_r101_authorization(
    report: R101ConservationReport,
    packet: R101ReviewPacket,
    registry: R101DecisionRegistry,
    *,
    permit_test_only: bool,
) -> R101ConservationReport | None:
    """Apply and validate authorization semantics without persistence."""
    _validate_registry_bindings(report, packet, registry)
    if registry.provenance == "test-only" and not permit_test_only:
        raise R101ReviewValidationError(
            "test-only registry cannot authorize a real report"
        )
    counts = Counter(row.decision for row in registry.decisions)
    if counts["reject"] or counts["approve"] != len(packet.patterns):
        return None
    payload = report.model_dump(mode="python", exclude={"report_identity"})
    payload.update(
        content_authorization=ContentAuthorization(
            status="authorized", authorized_digest=report.json_identity
        ),
        publication_gate="eligible",
    )
    candidate = R101ConservationReport.model_validate(
        {**payload, "report_identity": _identity(payload)}
    )
    validate_r101_publication(candidate)
    return candidate


def apply_r101_authorization(
    report: R101ConservationReport,
    packet: R101ReviewPacket,
    registry: R101DecisionRegistry,
) -> R101ConservationReport:
    """Return a validated real authorization candidate; perform no persistence."""
    candidate = _apply_r101_authorization(
        report, packet, registry, permit_test_only=False
    )
    if candidate is None:
        raise R101ReviewValidationError("rejected patterns cannot authorize report")
    return candidate


def dry_run_r101_authorization(
    report: R101ConservationReport,
    packet: R101ReviewPacket,
    registry: R101DecisionRegistry,
) -> AuthorizationDryRunResult:
    """Exercise the exact application path in memory and perform no writes."""
    candidate = _apply_r101_authorization(
        report, packet, registry, permit_test_only=True
    )
    counts = Counter(row.decision for row in registry.decisions)
    verdict: Literal["logically-eligible", "blocked"] = (
        "logically-eligible" if candidate is not None else "blocked"
    )
    return AuthorizationDryRunResult(
        schema_version=1,
        verdict=verdict,
        report_identity=report.report_identity,
        packet_identity=packet.packet_identity,
        registry_identity=registry.registry_identity,
        provenance=registry.provenance,
        approved_patterns=counts["approve"],
        rejected_patterns=counts["reject"],
        writes_performed=False,
    )


def write_r101_authorization_dry_run(
    path: Path, result: AuthorizationDryRunResult
) -> None:
    _write_json(path, result.model_dump(mode="json"))
