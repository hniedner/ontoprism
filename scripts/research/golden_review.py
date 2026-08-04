"""Fail-closed SME adjudication import and evaluation contracts."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, Self, cast

from openpyxl import load_workbook
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ontolib.decomposition.score import ExtractionScore, score

if TYPE_CHECKING:
    from openpyxl.workbook.workbook import Workbook
    from openpyxl.worksheet.worksheet import Worksheet

DecisionStatus = Literal["accepted", "rejected", "revision-needed"]
ExpectedOutcome = Literal[
    "decomposed",
    "residual",
    "semantic-excluded",
    "atomic-no-op",
]
ConstituentPair = tuple[str, str]
PairProvenance = Literal[
    "ncit-26.07d",
    "locally-approved",
    "proposed",
    "submitted",
    "accepted-in-ncit",
]

_ADJUDICATED_STATUS = "SME-ADJUDICATED"
_SCHEMA_VERSION = 2
_M1_REQUIRED_SEEDS = frozenset({"C4791", "C35756", "C89995"})
_M1_MIN_CONCEPTS = 20
_M1_MAX_CONCEPTS = 50
_EXPECTED_SHEETS = {
    "START HERE",
    "Reviewer & Attestation",
    "Concept Decisions",
    "Constituent Decisions",
    "Validation Summary",
    "Worked Examples",
    "Prior SME Evidence",
    "Source & Run Evidence",
}


class GoldenSetValidationError(ValueError):
    """The adjudication input cannot be trusted as an SME oracle."""


def _canonical_text(value: str, field: str) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{field} must be non-empty without outer whitespace")
    return value


def _payload_identity(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class Reviewer(_StrictModel):
    name: str
    qualification_or_role: str
    reviewed_at: str

    @model_validator(mode="after")
    def _validate_reviewer(self) -> Self:
        _canonical_text(self.name, "reviewer name")
        _canonical_text(self.qualification_or_role, "reviewer qualification")
        try:
            date.fromisoformat(_canonical_text(self.reviewed_at, "reviewed_at"))
        except ValueError as error:
            raise ValueError("reviewed_at must be an ISO date") from error
        return self


class AdjudicationMetadata(_StrictModel):
    schema_version: Literal[2]
    status: Literal["SME-ADJUDICATED"]
    ncit_version: str
    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    sample_manifest_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str
    run_fingerprint_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    engine_artifact_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    engine_evidence_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    corpus_evidence_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    detector_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    workbook_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: Reviewer

    @model_validator(mode="after")
    def _validate_text(self) -> Self:
        _canonical_text(self.ncit_version, "ncit_version")
        _canonical_text(self.run_id, "run_id")
        return self


class Constituent(_StrictModel):
    axis: str
    filler: str
    relationship_group: str | None
    needs_review: bool

    @model_validator(mode="after")
    def _validate_text(self) -> Self:
        _canonical_text(self.axis, "constituent axis")
        _canonical_text(self.filler, "constituent filler")
        if self.relationship_group is not None:
            _canonical_text(self.relationship_group, "relationship_group")
        return self

    @property
    def pair(self) -> ConstituentPair:
        return (self.axis, self.filler)


class GoldenConstituent(Constituent):
    provenance_status: PairProvenance


class GoldenExpectation(_StrictModel):
    outcome: ExpectedOutcome
    semantic_types: tuple[str, ...]
    constituents: tuple[GoldenConstituent, ...]

    @model_validator(mode="after")
    def _validate_shape(self) -> Self:
        if not self.semantic_types:
            raise ValueError("semantic_types must not be empty")
        for semantic_type in self.semantic_types:
            _canonical_text(semantic_type, "semantic type")
        if len(self.semantic_types) != len(set(self.semantic_types)):
            raise ValueError("semantic_types must be unique")
        pairs = [item.pair for item in self.constituents]
        if len(pairs) != len(set(pairs)):
            raise ValueError("constituent axis/filler pairs must be unique")
        if self.outcome == "decomposed" and not self.constituents:
            raise ValueError(
                "decomposed expectation must contain at least one constituent"
            )
        if self.outcome != "decomposed" and self.constituents:
            raise ValueError("non-decomposed expectation must not contain constituents")
        return self


class AdjudicationDecision(_StrictModel):
    status: DecisionStatus
    rationale: str

    @model_validator(mode="after")
    def _validate_rationale(self) -> Self:
        _canonical_text(self.rationale, "adjudication rationale")
        return self


class AdjudicatedConcept(_StrictModel):
    code: str = Field(pattern=r"^C[0-9]+$")
    label: str
    adjudication: AdjudicationDecision
    expected: GoldenExpectation | None

    @model_validator(mode="after")
    def _validate_decision_shape(self) -> Self:
        _canonical_text(self.label, f"{self.code} label")
        if self.adjudication.status == "accepted" and self.expected is None:
            raise ValueError("accepted decision requires expected")
        if self.adjudication.status != "accepted" and self.expected is not None:
            raise ValueError("nonaccepted decision must not define expected")
        return self


class AdjudicationArtifact(_StrictModel):
    meta: Annotated[AdjudicationMetadata, Field(alias="_meta")]
    concepts: tuple[AdjudicatedConcept, ...]
    artifact_identity: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _validate_m1_cohort(self) -> Self:
        if not _M1_MIN_CONCEPTS <= len(self.concepts) <= _M1_MAX_CONCEPTS:
            raise ValueError("M1 golden set must contain 20 to 50 adjudicated concepts")
        codes = [concept.code for concept in self.concepts]
        if len(codes) != len(set(codes)):
            raise ValueError("concept codes must be unique")
        missing = sorted(_M1_REQUIRED_SEEDS - set(codes))
        if missing:
            raise ValueError(
                "M1 golden set is missing named seeds: " + ", ".join(missing)
            )
        if not any(
            concept.adjudication.status == "accepted" for concept in self.concepts
        ):
            raise ValueError("M1 golden set must contain at least one accepted concept")
        if self.artifact_identity != _payload_identity(
            self.model_dump(
                mode="json",
                by_alias=True,
                exclude={"artifact_identity"},
            )
        ):
            raise ValueError(
                "adjudication artifact identity does not match its payload"
            )
        return self

    @property
    def identity(self) -> str:
        return self.artifact_identity


class EngineConcept(_StrictModel):
    code: str = Field(pattern=r"^C[0-9]+$")
    outcome: ExpectedOutcome
    semantic_types: tuple[str, ...]
    constituents: tuple[Constituent, ...]

    @model_validator(mode="after")
    def _validate_shape(self) -> Self:
        if len(self.semantic_types) != len(set(self.semantic_types)):
            raise ValueError("engine semantic_types must be unique")
        pairs = [item.pair for item in self.constituents]
        if len(pairs) != len(set(pairs)):
            raise ValueError("engine constituent axis/filler pairs must be unique")
        if self.outcome == "decomposed" and not self.constituents:
            raise ValueError("engine decomposed outcome requires constituents")
        if self.outcome != "decomposed" and self.constituents:
            raise ValueError("engine non-decomposed outcome cannot carry constituents")
        return self


class EngineEvidence(_StrictModel):
    schema_version: Literal[1]
    ncit_version: str
    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    sample_manifest_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str
    run_fingerprint_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    engine_artifact_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    detector_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    concepts: tuple[EngineConcept, ...]
    residual_precoordinated_codes: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_codes(self) -> Self:
        codes = [item.code for item in self.concepts]
        if len(codes) != len(set(codes)):
            raise ValueError("engine concept codes must be unique")
        if len(self.residual_precoordinated_codes) != len(
            set(self.residual_precoordinated_codes)
        ):
            raise ValueError("residual_precoordinated_codes must be unique")
        if not set(self.residual_precoordinated_codes) <= set(codes):
            raise ValueError("engine residual codes must be a subset of concept codes")
        outcomes = {item.code: item.outcome for item in self.concepts}
        if any(
            outcomes[code] != "decomposed"
            for code in self.residual_precoordinated_codes
        ):
            raise ValueError("engine residual codes require decomposed outcomes")
        if self.evidence_identity != _payload_identity(
            self.model_dump(mode="json", exclude={"evidence_identity"})
        ):
            raise ValueError("engine evidence identity does not match its payload")
        return self


class ResidualComparisonInput(_StrictModel):
    name: str
    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    sample_manifest_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str
    run_fingerprint_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    engine_artifact_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    detector_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    denominator_codes: tuple[str, ...]
    residual_codes: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_codes(self) -> Self:
        if len(self.denominator_codes) != len(set(self.denominator_codes)):
            raise ValueError("denominator codes must be unique")
        if len(self.residual_codes) != len(set(self.residual_codes)):
            raise ValueError("residual codes must be unique")
        if not set(self.residual_codes) <= set(self.denominator_codes):
            raise ValueError("residual codes must be a subset of denominator codes")
        if self.evidence_identity != _payload_identity(
            self.model_dump(mode="json", exclude={"evidence_identity"})
        ):
            raise ValueError("residual evidence identity does not match its payload")
        return self


class ScorableGoldenSet:
    """Validated accepted expectations plus a fail-closed pair view."""

    def __init__(self, artifact: AdjudicationArtifact) -> None:
        self.ncit_version = artifact.meta.ncit_version
        self.source_identity = artifact.meta.source_identity
        self.reviewer_qualification = artifact.meta.reviewer.qualification_or_role
        self.labels = {concept.code: concept.label for concept in artifact.concepts}
        self.decisions = {
            concept.code: concept.adjudication.status for concept in artifact.concepts
        }
        self.expectations = {
            concept.code: cast("GoldenExpectation", concept.expected)
            for concept in artifact.concepts
            if concept.adjudication.status == "accepted"
        }
        self.expected = {
            code: frozenset(
                item.pair for item in expectation.constituents if not item.needs_review
            )
            for code, expectation in self.expectations.items()
        }
        self.expected_ncit_bound = {
            code: frozenset(
                item.pair
                for item in expectation.constituents
                if not item.needs_review and item.provenance_status == "ncit-26.07d"
            )
            for code, expectation in self.expectations.items()
        }
        self.expected_augmented = {
            code: frozenset(
                item.pair
                for item in expectation.constituents
                if not item.needs_review and item.provenance_status != "proposed"
            )
            for code, expectation in self.expectations.items()
        }
        self.review_exclusions = {
            code: exclusions
            for code, expectation in self.expectations.items()
            if (
                exclusions := frozenset(
                    item.pair for item in expectation.constituents if item.needs_review
                )
            )
        }
        counts = Counter(self.decisions.values())
        self.decision_counts = {
            status: counts[status]
            for status in ("accepted", "rejected", "revision-needed")
        }


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise GoldenSetValidationError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _model_error(error: ValidationError | ValueError) -> GoldenSetValidationError:
    return GoldenSetValidationError(str(error))


def _read_adjudication_json(path: str | Path) -> dict[str, object]:
    try:
        raw = json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except GoldenSetValidationError:
        raise
    except (OSError, json.JSONDecodeError) as error:
        raise GoldenSetValidationError(f"cannot read golden set: {error}") from error
    if not isinstance(raw, dict):
        raise GoldenSetValidationError("golden set must be a JSON object")
    return raw


def read_json_without_duplicates(path: str | Path) -> object:
    """Read JSON while rejecting duplicate keys at every object level."""
    try:
        return json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except GoldenSetValidationError:
        raise
    except (OSError, json.JSONDecodeError) as error:
        raise GoldenSetValidationError(f"cannot read JSON evidence: {error}") from error


def _normalize_adjudication_lists(raw: dict[str, object]) -> None:
    concepts = raw.get("concepts")
    if not isinstance(concepts, list):
        return
    for concept in concepts:
        if not isinstance(concept, dict):
            continue
        expected = concept.get("expected")
        if not isinstance(expected, dict):
            continue
        if isinstance(expected.get("semantic_types"), list):
            expected["semantic_types"] = tuple(expected["semantic_types"])
        if isinstance(expected.get("constituents"), list):
            expected["constituents"] = tuple(expected["constituents"])
    raw["concepts"] = tuple(concepts)


def load_adjudication(path: str | Path) -> AdjudicationArtifact:
    """Load one strict, complete, provenance-bearing M1 adjudication artifact."""
    raw = _read_adjudication_json(path)
    meta = raw.get("_meta")
    if not isinstance(meta, dict) or meta.get("status") != _ADJUDICATED_STATUS:
        raise GoldenSetValidationError(
            "golden set is not SME-adjudicated; automated drafts cannot be scored"
        )
    _normalize_adjudication_lists(raw)
    try:
        return AdjudicationArtifact.model_validate(raw)
    except (ValidationError, ValueError) as error:
        raise _model_error(error) from error


def load_scorable_golden(path: str | Path) -> ScorableGoldenSet:
    """Load accepted expectations only after complete M1 validation."""
    return ScorableGoldenSet(load_adjudication(path))


def _headers(ws: Worksheet, row: int) -> dict[str, int]:
    values: dict[str, int] = {}
    for column in range(1, ws.max_column + 1):
        value = ws.cell(row, column).value
        if isinstance(value, str):
            if value in values:
                raise GoldenSetValidationError(f"duplicate workbook header: {value}")
            values[value] = column
    return values


def _cell_text(ws: Worksheet, row: int, column: int, field: str) -> str:
    value = ws.cell(row, column).value
    if not isinstance(value, str):
        raise GoldenSetValidationError(f"{field} must be text")
    try:
        return _canonical_text(value, field)
    except ValueError as error:
        raise GoldenSetValidationError(str(error)) from error


def _optional_text(ws: Worksheet, row: int, column: int, field: str) -> str | None:
    value = ws.cell(row, column).value
    if value is None:
        return None
    if not isinstance(value, str):
        raise GoldenSetValidationError(f"{field} must be text or blank")
    try:
        return _canonical_text(value, field)
    except ValueError as error:
        raise GoldenSetValidationError(str(error)) from error


def _reject_reviewer_formulas(workbook: Workbook) -> None:
    for sheet_name in (
        "Reviewer & Attestation",
        "Concept Decisions",
        "Constituent Decisions",
        "Source & Run Evidence",
    ):
        for row in workbook[sheet_name].iter_rows():
            for cell in row:
                if cell.data_type == "f":
                    raise GoldenSetValidationError(
                        f"formula cells are not permitted in reviewer input: "
                        f"{sheet_name}!{cell.coordinate}"
                    )


def _evidence_values(workbook: Workbook) -> dict[str, str]:
    ws = workbook["Source & Run Evidence"]
    result: dict[str, str] = {}
    for row in range(5, ws.max_row + 1):
        if ws.row_dimensions[row].hidden and _row_has_data(ws, row):
            raise GoldenSetValidationError(
                f"hidden source evidence rows are not permitted: {row}"
            )
        key = ws.cell(row, 1).value
        value = ws.cell(row, 2).value
        if isinstance(key, str) and isinstance(value, str):
            if key in result:
                raise GoldenSetValidationError(
                    f"duplicate Source & Run Evidence key: {key}"
                )
            result[key] = value
    return result


def _row_has_data(ws: Worksheet, row: int) -> bool:
    return any(
        ws.cell(row, column).value is not None for column in range(1, ws.max_column + 1)
    )


def _parse_semantic_types(value: str, field: str) -> tuple[str, ...]:
    items = tuple(value.split("; "))
    for item in items:
        try:
            _canonical_text(item, field)
        except ValueError as error:
            raise GoldenSetValidationError(str(error)) from error
    if len(items) != len(set(items)):
        raise GoldenSetValidationError(f"{field} must be unique")
    return items


def _parse_review_bool(value: object, field: str) -> bool:
    if value == "TRUE":
        return True
    if value == "FALSE":
        return False
    raise GoldenSetValidationError(f"{field} must be TRUE or FALSE")


def _constituent_row_identity(
    ws: Worksheet, row: int, headers: dict[str, int]
) -> tuple[str, str, str]:
    code = ws.cell(row, headers["Concept Code"]).value
    if not isinstance(code, str):
        raise GoldenSetValidationError("constituent concept code must be text")
    if ws.row_dimensions[row].hidden:
        raise GoldenSetValidationError(
            f"hidden constituent rows are not permitted: {row}"
        )
    row_type = _cell_text(ws, row, headers["Row Type"], f"{code} row type")
    if row_type not in {"ENGINE SUGGESTION", "ADD IF MISSING"}:
        raise GoldenSetValidationError(f"{code} has invalid row type: {row_type}")
    action = _cell_text(ws, row, headers["SME Action"], f"{code} SME action")
    return code, row_type, action


def _workbook_constituents(
    workbook: Workbook,
) -> tuple[dict[str, list[GoldenConstituent]], set[str]]:
    ws = workbook["Constituent Decisions"]
    headers = _headers(ws, 4)
    required = {
        "Concept Code",
        "Row Type",
        "SME Action",
        "Expected Axis",
        "Expected Filler",
        "Expected Group",
        "Expected needs_review",
        "Expected Provenance Status",
        "Row Complete?",
    }
    if missing := required - headers.keys():
        raise GoldenSetValidationError(
            "Constituent Decisions is missing headers: " + ", ".join(sorted(missing))
        )
    result: dict[str, list[GoldenConstituent]] = {}
    row_codes: set[str] = set()
    for row in range(5, ws.max_row + 1):
        code = ws.cell(row, headers["Concept Code"]).value
        if code is None:
            if _row_has_data(ws, row):
                raise GoldenSetValidationError(
                    f"populated constituent row has blank concept code: {row}"
                )
            continue
        code, row_type, action = _constituent_row_identity(ws, row, headers)
        row_codes.add(code)
        complete = _cell_text(
            ws, row, headers["Row Complete?"], f"{code} constituent completeness"
        )
        if row_type == "ENGINE SUGGESTION" and action == "PENDING":
            raise GoldenSetValidationError(f"{code} has pending constituent action")
        if action != "not-needed" and complete != "YES":
            raise GoldenSetValidationError(f"{code} has incomplete constituent row")
        if action in {"exclude", "not-needed"}:
            continue
        if action not in {"include", "revise"}:
            raise GoldenSetValidationError(f"{code} has invalid SME action: {action}")
        expected = GoldenConstituent(
            axis=_cell_text(ws, row, headers["Expected Axis"], f"{code} expected axis"),
            filler=_cell_text(
                ws, row, headers["Expected Filler"], f"{code} expected filler"
            ),
            relationship_group=_optional_text(
                ws, row, headers["Expected Group"], f"{code} expected group"
            ),
            needs_review=_parse_review_bool(
                ws.cell(row, headers["Expected needs_review"]).value,
                f"{code} expected needs_review",
            ),
            provenance_status=cast(
                "PairProvenance",
                _cell_text(
                    ws,
                    row,
                    headers["Expected Provenance Status"],
                    f"{code} expected provenance status",
                ),
            ),
        )
        result.setdefault(code, []).append(expected)
    return result, row_codes


def _load_review_workbook(path: Path) -> Workbook:
    try:
        workbook = load_workbook(path, data_only=False, read_only=False)
    except (OSError, ValueError) as error:
        raise GoldenSetValidationError(
            f"cannot read adjudication workbook: {error}"
        ) from error
    if set(workbook.sheetnames) != _EXPECTED_SHEETS:
        raise GoldenSetValidationError(
            "workbook sheets do not match the review contract"
        )
    for sheet_name in _EXPECTED_SHEETS:
        if workbook[sheet_name].sheet_state != "visible":
            raise GoldenSetValidationError(
                f"reviewer input sheet must be visible: {sheet_name}"
            )
    _reject_reviewer_formulas(workbook)
    return workbook


def _reviewer_from_workbook(workbook: Workbook) -> Reviewer:
    reviewer_ws = workbook["Reviewer & Attestation"]
    hidden = [
        row
        for row in range(1, reviewer_ws.max_row + 1)
        if reviewer_ws.row_dimensions[row].hidden and _row_has_data(reviewer_ws, row)
    ]
    if hidden:
        raise GoldenSetValidationError(
            "hidden reviewer rows are not permitted: "
            + ", ".join(str(row) for row in hidden)
        )
    reviewed_at_value = reviewer_ws["B7"].value
    if isinstance(reviewed_at_value, datetime):
        reviewed_at = reviewed_at_value.date().isoformat()
    elif isinstance(reviewed_at_value, date):
        reviewed_at = reviewed_at_value.isoformat()
    else:
        reviewed_at = reviewed_at_value
    if reviewer_ws["B9"].value != "ATTESTED":
        raise GoldenSetValidationError("reviewer attestation is pending")
    return Reviewer(
        name=_cell_text(reviewer_ws, 5, 2, "reviewer name"),
        qualification_or_role=_cell_text(reviewer_ws, 6, 2, "reviewer qualification"),
        reviewed_at=cast("str", reviewed_at),
    )


def _required_evidence(workbook: Workbook) -> dict[str, str]:
    evidence = _evidence_values(workbook)
    required_evidence = {
        "NCIt release",
        "Source identity",
        "Sample identity",
        "Engine run",
        "Run fingerprint identity",
        "Artifact SHA-256",
        "Engine evidence identity",
        "Corpus evidence identity",
        "Detector identity",
    }
    if missing := required_evidence - evidence.keys():
        raise GoldenSetValidationError(
            "Source & Run Evidence is missing: " + ", ".join(sorted(missing))
        )
    return evidence


def _concept_headers(ws: Worksheet) -> dict[str, int]:
    headers = _headers(ws, 4)
    required_headers = {
        "Concept Code",
        "Source Label",
        "Source Semantic Types",
        "Expected Semantic Types",
        "SME Decision Status",
        "Expected Outcome",
        "Rationale / Required Follow-up",
        "Source Reviewed?",
        "Concept Complete?",
    }
    if missing := required_headers - headers.keys():
        raise GoldenSetValidationError(
            "Concept Decisions is missing headers: " + ", ".join(sorted(missing))
        )
    return headers


def _decision_status(
    ws: Worksheet, row: int, headers: dict[str, int], code: str
) -> DecisionStatus:
    status = _cell_text(
        ws, row, headers["SME Decision Status"], f"{code} adjudication status"
    )
    if status == "PENDING":
        raise GoldenSetValidationError(f"{code} has pending adjudication")
    if status not in {"accepted", "rejected", "revision-needed"}:
        raise GoldenSetValidationError(f"{code} has invalid adjudication status")
    if ws.cell(row, headers["Source Reviewed?"]).value != "YES":
        raise GoldenSetValidationError(f"{code} source review is incomplete")
    if ws.cell(row, headers["Concept Complete?"]).value != "YES":
        raise GoldenSetValidationError(f"{code} adjudication is incomplete")
    return cast("DecisionStatus", status)


def _expectation_from_row(
    ws: Worksheet,
    row: int,
    headers: dict[str, int],
    code: str,
    status: DecisionStatus,
    expected_constituents: dict[str, list[GoldenConstituent]],
) -> GoldenExpectation | None:
    if status != "accepted":
        if expected_constituents.get(code):
            raise GoldenSetValidationError(
                f"{code} nonaccepted decision must not define expected constituents"
            )
        return None
    outcome = _cell_text(
        ws, row, headers["Expected Outcome"], f"{code} expected outcome"
    )
    semantic_types = _parse_semantic_types(
        _cell_text(
            ws,
            row,
            headers["Expected Semantic Types"],
            f"{code} semantic types",
        ),
        f"{code} semantic types",
    )
    return GoldenExpectation(
        outcome=cast("ExpectedOutcome", outcome),
        semantic_types=semantic_types,
        constituents=tuple(expected_constituents.get(code, [])),
    )


def _concept_from_row(
    ws: Worksheet,
    row: int,
    headers: dict[str, int],
    expected_constituents: dict[str, list[GoldenConstituent]],
) -> AdjudicatedConcept | None:
    code_value = ws.cell(row, headers["Concept Code"]).value
    if code_value is None:
        if _row_has_data(ws, row):
            raise GoldenSetValidationError(
                f"populated concept row has blank concept code: {row}"
            )
        return None
    if not isinstance(code_value, str):
        raise GoldenSetValidationError("concept code must be text")
    status = _decision_status(ws, row, headers, code_value)
    return AdjudicatedConcept(
        code=code_value,
        label=_cell_text(ws, row, headers["Source Label"], f"{code_value} label"),
        adjudication=AdjudicationDecision(
            status=status,
            rationale=_cell_text(
                ws,
                row,
                headers["Rationale / Required Follow-up"],
                f"{code_value} rationale",
            ),
        ),
        expected=_expectation_from_row(
            ws,
            row,
            headers,
            code_value,
            status,
            expected_constituents,
        ),
    )


def _workbook_concepts(workbook: Workbook) -> tuple[AdjudicatedConcept, ...]:
    ws = workbook["Concept Decisions"]
    headers = _concept_headers(ws)
    hidden = [
        row
        for row in range(5, ws.max_row + 1)
        if ws.row_dimensions[row].hidden
        and ws.cell(row, headers["Concept Code"]).value is not None
    ]
    if hidden:
        raise GoldenSetValidationError(
            "hidden concept rows are not permitted: "
            + ", ".join(str(row) for row in hidden)
        )
    blank = [
        row
        for row in range(5, ws.max_row + 1)
        if ws.cell(row, headers["Concept Code"]).value is None
        and _row_has_data(ws, row)
    ]
    if blank:
        raise GoldenSetValidationError(
            "populated concept row has blank concept code: "
            + ", ".join(str(row) for row in blank)
        )
    expected_constituents, constituent_codes = _workbook_constituents(workbook)
    declared_codes = {
        code
        for row in range(5, ws.max_row + 1)
        if isinstance(
            (code := ws.cell(row, headers["Concept Code"]).value),
            str,
        )
    }
    orphaned = sorted(constituent_codes - declared_codes)
    if orphaned:
        raise GoldenSetValidationError(
            "constituent rows reference unknown concepts: " + ", ".join(orphaned)
        )
    concepts = tuple(
        concept
        for row in range(5, ws.max_row + 1)
        if (concept := _concept_from_row(ws, row, headers, expected_constituents))
    )
    return concepts


def import_adjudication_workbook(path: str | Path) -> AdjudicationArtifact:
    """Import the issue #57 workbook without inferring any reviewer decision."""
    workbook_path = Path(path)
    workbook = _load_review_workbook(workbook_path)
    reviewer = _reviewer_from_workbook(workbook)
    evidence = _required_evidence(workbook)
    try:
        payload = {
            "_meta": AdjudicationMetadata(
                schema_version=_SCHEMA_VERSION,
                status=_ADJUDICATED_STATUS,
                ncit_version=evidence["NCIt release"],
                source_identity=evidence["Source identity"],
                sample_manifest_identity=evidence["Sample identity"],
                run_id=evidence["Engine run"],
                run_fingerprint_identity=evidence["Run fingerprint identity"],
                engine_artifact_identity=evidence["Artifact SHA-256"],
                engine_evidence_identity=evidence["Engine evidence identity"],
                corpus_evidence_identity=evidence["Corpus evidence identity"],
                detector_identity=evidence["Detector identity"],
                workbook_identity=hashlib.sha256(
                    workbook_path.read_bytes()
                ).hexdigest(),
                reviewer=reviewer,
            ),
            "concepts": _workbook_concepts(workbook),
        }
        return AdjudicationArtifact.model_validate(
            {
                **payload,
                "artifact_identity": _payload_identity(
                    {
                        "_meta": payload["_meta"].model_dump(mode="json"),
                        "concepts": [
                            concept.model_dump(mode="json")
                            for concept in payload["concepts"]
                        ],
                    }
                ),
            }
        )
    except (ValidationError, ValueError) as error:
        raise _model_error(error) from error


def _normalize_engine(value: dict[str, object]) -> dict[str, object]:
    normalized = dict(value)
    concepts = normalized.get("concepts")
    if isinstance(concepts, list):
        normalized["concepts"] = tuple(
            {
                **concept,
                "semantic_types": tuple(concept.get("semantic_types", ())),
                "constituents": tuple(concept.get("constituents", ())),
            }
            if isinstance(concept, dict)
            else concept
            for concept in concepts
        )
    residual = normalized.get("residual_precoordinated_codes")
    if isinstance(residual, list):
        normalized["residual_precoordinated_codes"] = tuple(residual)
    return normalized


def _normalize_residual(value: dict[str, object]) -> dict[str, object]:
    normalized = dict(value)
    for field in ("denominator_codes", "residual_codes"):
        field_value = normalized.get(field)
        if isinstance(field_value, list):
            normalized[field] = tuple(field_value)
    return normalized


def _model_validate(model: type[_StrictModel], value: object) -> object:
    if model is EngineEvidence and isinstance(value, dict):
        value = _normalize_engine(value)
    elif model is ResidualComparisonInput and isinstance(value, dict):
        value = _normalize_residual(value)
    try:
        return model.model_validate(value)
    except (ValidationError, ValueError) as error:
        raise _model_error(error) from error


def _require_engine_identity(
    artifact: AdjudicationArtifact, engine: EngineEvidence
) -> None:
    checks = (
        ("NCIt version", artifact.meta.ncit_version, engine.ncit_version),
        ("source identity", artifact.meta.source_identity, engine.source_identity),
        (
            "sample manifest identity",
            artifact.meta.sample_manifest_identity,
            engine.sample_manifest_identity,
        ),
        ("run id", artifact.meta.run_id, engine.run_id),
        (
            "run fingerprint identity",
            artifact.meta.run_fingerprint_identity,
            engine.run_fingerprint_identity,
        ),
        (
            "engine artifact identity",
            artifact.meta.engine_artifact_identity,
            engine.engine_artifact_identity,
        ),
        (
            "engine evidence identity",
            artifact.meta.engine_evidence_identity,
            engine.evidence_identity,
        ),
        (
            "detector identity",
            artifact.meta.detector_identity,
            engine.detector_identity,
        ),
    )
    for name, expected, actual in checks:
        if expected != actual:
            raise GoldenSetValidationError(f"{name} does not match adjudication")
    artifact_codes = [concept.code for concept in artifact.concepts]
    engine_codes = [concept.code for concept in engine.concepts]
    if artifact_codes != engine_codes:
        raise GoldenSetValidationError(
            "engine worklist does not match adjudication order"
        )


def _group_partition(
    constituents: tuple[Constituent, ...], excluded: set[ConstituentPair]
) -> dict[str, object]:
    grouped: dict[str, set[ConstituentPair]] = {}
    ungrouped: set[ConstituentPair] = set()
    for item in constituents:
        if item.pair in excluded:
            continue
        if item.relationship_group is None:
            ungrouped.add(item.pair)
        else:
            grouped.setdefault(item.relationship_group, set()).add(item.pair)
    groups = sorted(
        (sorted([list(pair) for pair in members]) for members in grouped.values()),
        key=lambda value: json.dumps(value, separators=(",", ":")),
    )
    return {
        "groups": groups,
        "ungrouped": sorted([list(pair) for pair in ungrouped]),
    }


def _score_dict(result: ExtractionScore) -> dict[str, object]:
    precision = result.precision if result.actual else None
    recall = result.recall if result.expected else None
    f1 = result.f1 if precision is not None and recall is not None else None
    return {
        "expected": result.expected,
        "actual": result.actual,
        "true_positive": result.true_positive,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "missing": sorted([list(pair) for pair in result.missing]),
        "extra": sorted([list(pair) for pair in result.extra]),
        "deferred": sorted([list(pair) for pair in result.deferred]),
    }


def _micro_score(expected: int, actual: int, true_positive: int) -> dict[str, object]:
    return {
        "expected": expected,
        "actual": actual,
        "true_positive": true_positive,
        "precision": true_positive / actual if actual else None,
        "recall": true_positive / expected if expected else None,
    }


def _axis_scores(
    counts: dict[str, Counter[str]],
) -> dict[str, dict[str, object]]:
    return {
        axis: _micro_score(
            values["expected"], values["actual"], values["true_positive"]
        )
        for axis, values in sorted(counts.items())
    }


def _proposal_counts(
    expected: GoldenExpectation, actual: EngineConcept
) -> tuple[Counter[str], Counter[str], int, tuple[str, ...]]:
    provenance = Counter(item.provenance_status for item in expected.constituents)
    proposal_statuses = Counter(
        item.provenance_status
        for item in expected.constituents
        if item.provenance_status != "ncit-26.07d"
    )
    augmented_expected = sum(
        not item.needs_review
        and item.provenance_status
        in {"locally-approved", "submitted", "accepted-in-ncit"}
        for item in expected.constituents
    )
    engine_ids = tuple(
        item.filler for item in actual.constituents if item.filler.startswith("MINT-")
    )
    return provenance, proposal_statuses, augmented_expected, engine_ids


def _update_axis_counts(
    counts: dict[str, Counter[str]],
    expected: set[ConstituentPair],
    actual: set[ConstituentPair],
) -> None:
    for pair in expected | actual:
        values = counts.setdefault(pair[0], Counter())
        values["expected"] += pair in expected
        values["actual"] += pair in actual
        values["true_positive"] += pair in expected & actual


def _score_concept_views(
    expected: GoldenExpectation,
    actual: EngineConcept,
    aggregates: dict[str, dict[str, int]],
    axis_aggregates: dict[str, dict[str, Counter[str]]],
    deferrals: dict[str, dict[str, int]],
) -> tuple[
    dict[str, tuple[GoldenConstituent, ...]],
    dict[str, ExtractionScore],
    set[ConstituentPair],
    set[ConstituentPair],
]:
    actual_pairs = {item.pair for item in actual.constituents}
    actual_exclusions = {item.pair for item in actual.constituents if item.needs_review}
    view_items = {
        "ncit_bound": tuple(
            item
            for item in expected.constituents
            if item.provenance_status == "ncit-26.07d"
        ),
        "augmented": tuple(
            item
            for item in expected.constituents
            if item.provenance_status != "proposed"
        ),
    }
    results: dict[str, ExtractionScore] = {}
    augmented_exclusions: set[ConstituentPair] = set()
    for view, items in view_items.items():
        expected_pairs = {item.pair for item in items}
        expected_exclusions = {item.pair for item in items if item.needs_review}
        result = score(
            expected_pairs,
            actual_pairs,
            expected_needs_review=expected_exclusions,
        )
        results[view] = result
        for field in ("expected", "actual", "true_positive"):
            aggregates[view][field] += getattr(result, field)
        deferrals[view]["expected"] += len(items)
        deferrals[view]["deferred"] += len(expected_exclusions)
        deferrals[view]["engine_matches"] += len(expected_exclusions & actual_pairs)
        exclusions = expected_exclusions
        _update_axis_counts(
            axis_aggregates[view],
            expected_pairs - exclusions,
            actual_pairs - exclusions,
        )
        if view == "augmented":
            augmented_exclusions = expected_exclusions
    return view_items, results, actual_exclusions, augmented_exclusions


def _concept_report(
    concept: AdjudicatedConcept,
    expected: GoldenExpectation,
    actual: EngineConcept,
    view_items: dict[str, tuple[GoldenConstituent, ...]],
    results: dict[str, ExtractionScore],
    actual_exclusions: set[ConstituentPair],
    expected_exclusions: set[ConstituentPair],
) -> dict[str, object]:
    exclusions = expected_exclusions
    expected_groups = _group_partition(view_items["augmented"], exclusions)
    actual_groups = _group_partition(actual.constituents, exclusions)
    return {
        "code": concept.code,
        "expected_outcome": expected.outcome,
        "actual_outcome": actual.outcome,
        "outcome_match": expected.outcome == actual.outcome,
        "expected_semantic_types": sorted(expected.semantic_types),
        "actual_semantic_types": sorted(actual.semantic_types),
        "semantic_types_match": set(expected.semantic_types)
        == set(actual.semantic_types),
        "expected_review_exclusions": sorted(
            [list(pair) for pair in expected_exclusions]
        ),
        "actual_review_exclusions": sorted([list(pair) for pair in actual_exclusions]),
        "pair_score": {view: _score_dict(result) for view, result in results.items()},
        "expected_group_partition": expected_groups,
        "actual_group_partition": actual_groups,
        "group_match": expected_groups == actual_groups,
    }


def _residual_dict(value: ResidualComparisonInput) -> dict[str, object]:
    denominator = len(value.denominator_codes)
    count = len(value.residual_codes)
    return {
        "name": value.name,
        "source_identity": value.source_identity,
        "sample_manifest_identity": value.sample_manifest_identity,
        "run_id": value.run_id,
        "run_fingerprint_identity": value.run_fingerprint_identity,
        "engine_artifact_identity": value.engine_artifact_identity,
        "detector_identity": value.detector_identity,
        "evidence_identity": value.evidence_identity,
        "denominator_codes": list(value.denominator_codes),
        "residual_codes": list(value.residual_codes),
        "count": count,
        "denominator": denominator,
        "rate": count / denominator if denominator else None,
    }


def evaluate_adjudication(
    artifact: AdjudicationArtifact,
    raw_engine: object,
    raw_corpus_comparison: object,
) -> dict[str, object]:
    """Evaluate accepted expectations without laundering identity or review failures."""
    engine = cast("EngineEvidence", _model_validate(EngineEvidence, raw_engine))
    _require_engine_identity(artifact, engine)
    corpus = cast(
        "ResidualComparisonInput",
        _model_validate(ResidualComparisonInput, raw_corpus_comparison),
    )
    if corpus.source_identity != artifact.meta.source_identity:
        raise GoldenSetValidationError(
            "corpus source identity does not match adjudication"
        )
    if corpus.detector_identity != artifact.meta.detector_identity:
        raise GoldenSetValidationError(
            "corpus detector identity does not match adjudication"
        )
    if corpus.evidence_identity != artifact.meta.corpus_evidence_identity:
        raise GoldenSetValidationError(
            "corpus evidence identity does not match adjudication"
        )
    engine_by_code = {concept.code: concept for concept in engine.concepts}
    concept_reports: list[dict[str, object]] = []
    aggregates = {
        "ncit_bound": {"expected": 0, "actual": 0, "true_positive": 0},
        "augmented": {"expected": 0, "actual": 0, "true_positive": 0},
    }
    axis_aggregates: dict[str, dict[str, Counter[str]]] = {
        "ncit_bound": {},
        "augmented": {},
    }
    deferrals = {
        "ncit_bound": {"expected": 0, "deferred": 0, "engine_matches": 0},
        "augmented": {"expected": 0, "deferred": 0, "engine_matches": 0},
    }
    provenance_counts: Counter[str] = Counter()
    proposal_status_counts: Counter[str] = Counter()
    augmented_proposal_expected = 0
    engine_proposal_emissions = 0
    engine_proposal_ids: set[str] = set()
    actual_decomposed: list[str] = []
    group_matches: list[bool] = []
    for concept in artifact.concepts:
        if concept.adjudication.status != "accepted":
            continue
        expected = cast("GoldenExpectation", concept.expected)
        actual = engine_by_code[concept.code]
        provenance, statuses, accepted, engine_ids = _proposal_counts(expected, actual)
        provenance_counts.update(provenance)
        proposal_status_counts.update(statuses)
        augmented_proposal_expected += accepted
        engine_proposal_emissions += len(engine_ids)
        engine_proposal_ids.update(engine_ids)
        view_items, results, actual_exclusions, expected_exclusions = (
            _score_concept_views(
                expected, actual, aggregates, axis_aggregates, deferrals
            )
        )
        if actual.outcome == "decomposed":
            actual_decomposed.append(concept.code)
        concept_report = _concept_report(
            concept,
            expected,
            actual,
            view_items,
            results,
            actual_exclusions,
            expected_exclusions,
        )
        group_matches.append(cast("bool", concept_report["group_match"]))
        concept_reports.append(concept_report)
    residual_payload = {
        "name": "accepted-adjudication",
        "source_identity": artifact.meta.source_identity,
        "sample_manifest_identity": artifact.meta.sample_manifest_identity,
        "run_id": artifact.meta.run_id,
        "run_fingerprint_identity": artifact.meta.run_fingerprint_identity,
        "engine_artifact_identity": artifact.meta.engine_artifact_identity,
        "detector_identity": artifact.meta.detector_identity,
        "denominator_codes": tuple(actual_decomposed),
        "residual_codes": tuple(
            code
            for code in actual_decomposed
            if code in set(engine.residual_precoordinated_codes)
        ),
    }
    adjudication_residual = ResidualComparisonInput(
        **residual_payload,
        evidence_identity=_payload_identity(residual_payload),
    )
    adjudication_residual_dict = _residual_dict(adjudication_residual)
    corpus_dict = _residual_dict(corpus)
    return {
        "schema_version": 2,
        "adjudication_identity": artifact.identity,
        "accepted_concepts": len(concept_reports),
        "decision_counts": dict(
            Counter(c.adjudication.status for c in artifact.concepts)
        ),
        "pair_micro": {
            view: _micro_score(
                counts["expected"], counts["actual"], counts["true_positive"]
            )
            for view, counts in aggregates.items()
        },
        "pair_by_axis": {
            view: _axis_scores(counts) for view, counts in axis_aggregates.items()
        },
        "expected_pair_provenance": dict(sorted(provenance_counts.items())),
        "expected_pair_deferrals": {
            view: dict(values) for view, values in sorted(deferrals.items())
        },
        "proposal_governance": {
            "engine_emissions": engine_proposal_emissions,
            "distinct_engine_proposals": len(engine_proposal_ids),
            "augmented_expected": augmented_proposal_expected,
            "expected_by_status": dict(sorted(proposal_status_counts.items())),
        },
        "group_partition_agreement": {
            "concepts_agree": sum(group_matches),
            "concepts_disagree": len(group_matches) - sum(group_matches),
        },
        "concepts": concept_reports,
        "residual_comparison": {
            "metric": "D37 detector-relative residual_precoordination",
            "adjudication": adjudication_residual_dict,
            "corpus_sample": corpus_dict,
            "absolute_rate_delta": (
                abs(
                    cast("float", adjudication_residual_dict["rate"])
                    - cast("float", corpus_dict["rate"])
                )
                if adjudication_residual_dict["rate"] is not None
                and corpus_dict["rate"] is not None
                else None
            ),
            "rates_averaged": False,
        },
    }


def write_evaluation_report(report: dict[str, object], path: str | Path) -> None:
    """Write canonical JSON so identical evidence produces byte-identical reports."""
    rendered = json.dumps(
        report,
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    )
    Path(path).write_text(rendered + "\n", encoding="utf-8")
