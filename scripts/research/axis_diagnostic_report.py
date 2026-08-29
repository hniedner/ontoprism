"""Identity-bound pre-SME axis and atomicity diagnostic report."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

try:
    from scripts.research.current_evidence import (
        CurrentCommonPartitionMetric,
        CurrentComparison,
        CurrentConstituent,
        CurrentEngineEvidence,
        CurrentMetrics,
        CurrentRateMetric,
        RowReplayStatus,
    )
    from scripts.research.golden_review import (
        AdjudicationArtifact,
        ExpectedPair,
        GoldenConstituent,
        KeptRow,
        RowDecisionExport,
        load_adjudication,
        load_row_decisions,
    )
except ModuleNotFoundError:  # direct `python scripts/adjudication.py` entry point
    from research.current_evidence import (
        CurrentCommonPartitionMetric,
        CurrentComparison,
        CurrentConstituent,
        CurrentEngineEvidence,
        CurrentMetrics,
        CurrentRateMetric,
        RowReplayStatus,
    )
    from research.golden_review import (
        AdjudicationArtifact,
        ExpectedPair,
        GoldenConstituent,
        KeptRow,
        RowDecisionExport,
        load_adjudication,
        load_row_decisions,
    )

from ontolib.decomposition.axis_contracts import (
    AXIS_CONTRACTS,
    normalized_axis_for_role,
)
from ontolib.decomposition.axis_diagnostics import (
    AxisRangeEvidence,
    InvalidAxisEvidence,
    ValidAxisEvidence,
    read_axis_diagnostic_source,
)
from ontolib.decomposition.complete_definition import (
    UnsupportedDefinitionConstructorError,
    read_complete_definition,
)
from ontolib.decomposition.models import (
    GenusDefinitionFact,
    RestrictionDefinitionFact,
)
from ontolib.decomposition.proposal_registry import load_proposal_registry
from ontolib.decomposition.run import _detect_concept
from ontolib.terminologies.ncit.client import ncit_sparql_client
from ontolib.terminologies.ncit.sibling_store import validate_ncit_sibling_manifest

if TYPE_CHECKING:
    from ontolib.decomposition.proposal_registry import ProposalRegistry

_SHA256 = r"^[0-9a-f]{64}$"
_REVISE_COUNT = 42
_CANDIDATE_COUNT = 64
_SME_INCLUDED = 48
_SME_SUGGESTIONS = 106
_MAX_RESIDUAL_DIAGNOSTICS = 8
_DIGEST = re.compile(r"[0-9a-f]{64}")


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


def _identity(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=lambda item: item.model_dump(mode="json"),
        ).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class SourcePairEvidence:
    stage: Literal["source-only", "extracted"]
    source_definition_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.source_definition_ids:
            raise ValueError("source definition IDs must not be empty")
        if any(
            _DIGEST.fullmatch(value) is None for value in self.source_definition_ids
        ):
            raise ValueError("source definition ID is invalid")


@dataclass(frozen=True, slots=True, kw_only=True)
class ResidualPrecoordinationVerdict:
    status: Literal["detected", "not-detected", "unknown"]
    reason: Literal[
        "production-detector",
        "unsupported-definition-constructor",
        "proposed-filler-not-in-source",
    ]
    detector_identity: str

    def __post_init__(self) -> None:
        if _DIGEST.fullmatch(self.detector_identity) is None:
            raise ValueError("detector identity is invalid")


async def collect_residual_verdicts(
    client: object,
    filler_codes: tuple[str, ...],
    *,
    detector_identity: str,
    walker_max_depth: int,
) -> dict[str, ResidualPrecoordinationVerdict]:
    """Classify one bounded, unique decision set with the production detector."""
    if len(filler_codes) != len(set(filler_codes)):
        raise ValueError("residual diagnostic filler codes must be unique")
    if len(filler_codes) > _MAX_RESIDUAL_DIAGNOSTICS:
        raise ValueError("residual diagnostics accept at most 8 fillers")
    if _DIGEST.fullmatch(detector_identity) is None:
        raise ValueError("detector identity is invalid")
    if walker_max_depth < 1:
        raise ValueError("walker max depth must be positive")
    result: dict[str, ResidualPrecoordinationVerdict] = {}
    for filler in filler_codes:
        if filler.startswith("MINT-"):
            result[filler] = ResidualPrecoordinationVerdict(
                status="unknown",
                reason="proposed-filler-not-in-source",
                detector_identity=detector_identity,
            )
            continue
        try:
            (
                detection,
                _roles,
                _morphology,
                _definition,
                _semantic_types,
            ) = await _detect_concept(
                filler,
                client,  # type: ignore[arg-type]
                label=None,
                walker_max_depth=walker_max_depth,
            )
        except UnsupportedDefinitionConstructorError:
            result[filler] = ResidualPrecoordinationVerdict(
                status="unknown",
                reason="unsupported-definition-constructor",
                detector_identity=detector_identity,
            )
            continue
        result[filler] = ResidualPrecoordinationVerdict(
            status=("detected" if detection.is_precoordinated else "not-detected"),
            reason="production-detector",
            detector_identity=detector_identity,
        )
    return result


def _expected_candidate_keys(
    rows: RowDecisionExport,
    concept_codes: frozenset[str],
) -> set[tuple[str, str, str]]:
    return {
        (row.code, row.expected.axis, row.expected.filler)
        for row in rows.rows
        if row.code in concept_codes
        and row.row_type == "ADD IF MISSING"
        and isinstance(row, KeptRow)
    }


async def collect_source_pair_evidence(
    client: object,
    rows: RowDecisionExport,
    *,
    concept_codes: tuple[str, ...],
) -> dict[tuple[str, str, str], SourcePairEvidence]:
    """Collect exact direct source facts for kept candidate rows."""
    requested = frozenset(concept_codes)
    if len(requested) != len(concept_codes):
        raise ValueError("source-evidence concept codes must be unique")
    expected = _expected_candidate_keys(rows, requested)
    result: dict[tuple[str, str, str], SourcePairEvidence] = {}
    for code in concept_codes:
        complete = await read_complete_definition(client.select_once, code)  # type: ignore[attr-defined]
        by_pair: dict[tuple[str, str], set[str]] = {}
        for fact in complete.facts:
            if isinstance(fact, GenusDefinitionFact):
                pair = ("op:Morphology", fact.genus_code)
            elif (
                isinstance(fact, RestrictionDefinitionFact)
                and (axis := normalized_axis_for_role(fact.role_code)) is not None
            ):
                pair = (axis, fact.filler_code)
            else:
                continue
            by_pair.setdefault(pair, set()).add(fact.fact_id)
        for expected_code, axis, filler in expected:
            if expected_code != code or (axis, filler) not in by_pair:
                continue
            result[(code, axis, filler)] = SourcePairEvidence(
                stage="source-only",
                source_definition_ids=tuple(sorted(by_pair[(axis, filler)])),
            )
    return result


class ReviseRowDiagnostic(_StrictModel):
    ordinal: int = Field(ge=0)
    code: str = Field(pattern=r"^C[0-9]+$")
    engine: ExpectedPair
    expected: ExpectedPair
    historical_pair_delta: Literal["unchanged", "changed"]
    current_presence: Literal["present", "missing"]
    group_delta: Literal["unchanged", "changed", "missing-current"]
    review_delta: Literal["unchanged", "changed", "missing-current"]
    provenance_delta: Literal["unchanged", "changed", "missing-current"]


class CandidateRowDiagnostic(_StrictModel):
    ordinal: int = Field(ge=0)
    code: str = Field(pattern=r"^C[0-9]+$")
    expected: ExpectedPair
    classification: Literal[
        "added",
        "extraction-miss",
        "selection-miss",
        "proposal-only",
        "unavailable-source-evidence",
    ]
    source_definition_ids: tuple[str, ...]


class DisjointPairDocument(_StrictModel):
    left: str = Field(pattern=r"^C[0-9]+$")
    right: str = Field(pattern=r"^C[0-9]+$")


class ValidAxisEvidenceDocument(_StrictModel):
    status: Literal["valid"]
    axis: str
    filler_code: str
    range_code: str
    source_identity: str = Field(pattern=_SHA256)
    reason: Literal["filler-is-range-or-descendant"]
    structural_path: tuple[str, ...]


class InvalidAxisEvidenceDocument(_StrictModel):
    status: Literal["invalid"]
    axis: str
    filler_code: str
    range_code: str
    source_identity: str = Field(pattern=_SHA256)
    reason: Literal["disjoint-ancestor-pair"]
    disjoint_pair: DisjointPairDocument
    filler_ancestor_path: tuple[str, ...]
    range_ancestor_path: tuple[str, ...]


class UnknownAxisEvidenceDocument(_StrictModel):
    status: Literal["unknown"]
    axis: str
    filler_code: str
    range_code: str
    source_identity: str = Field(pattern=_SHA256)
    reason: Literal[
        "no-positive-or-negative-proof",
        "contradictory-valid-and-invalid-evidence",
        "unknown-axis",
        "range-does-not-match-axis-contract",
    ]


type AxisRangeEvidenceDocument = Annotated[
    ValidAxisEvidenceDocument
    | InvalidAxisEvidenceDocument
    | UnknownAxisEvidenceDocument,
    Field(discriminator="status"),
]


class ResidualPrecoordinationDocument(_StrictModel):
    status: Literal["detected", "not-detected", "unknown"]
    reason: Literal[
        "production-detector",
        "unsupported-definition-constructor",
        "proposed-filler-not-in-source",
    ]
    detector_identity: str = Field(pattern=_SHA256)


def axis_evidence_to_document(
    evidence: AxisRangeEvidence,
) -> AxisRangeEvidenceDocument:
    if isinstance(evidence, ValidAxisEvidence):
        return ValidAxisEvidenceDocument(
            status="valid",
            axis=evidence.axis,
            filler_code=evidence.filler_code,
            range_code=evidence.range_code,
            source_identity=evidence.source_identity,
            reason=evidence.reason,
            structural_path=evidence.structural_path,
        )
    if isinstance(evidence, InvalidAxisEvidence):
        return InvalidAxisEvidenceDocument(
            status="invalid",
            axis=evidence.axis,
            filler_code=evidence.filler_code,
            range_code=evidence.range_code,
            source_identity=evidence.source_identity,
            reason=evidence.reason,
            disjoint_pair=DisjointPairDocument(
                left=evidence.disjoint_pair.left,
                right=evidence.disjoint_pair.right,
            ),
            filler_ancestor_path=evidence.filler_ancestor_path,
            range_ancestor_path=evidence.range_ancestor_path,
        )
    return UnknownAxisEvidenceDocument(
        status="unknown",
        axis=evidence.axis,
        filler_code=evidence.filler_code,
        range_code=evidence.range_code,
        source_identity=evidence.source_identity,
        reason=evidence.reason,
    )


def residual_evidence_to_document(
    evidence: ResidualPrecoordinationVerdict,
) -> ResidualPrecoordinationDocument:
    return ResidualPrecoordinationDocument(
        status=evidence.status,
        reason=evidence.reason,
        detector_identity=evidence.detector_identity,
    )


class PairRangeDiagnostic(_StrictModel):
    code: str = Field(pattern=r"^C[0-9]+$")
    axis: str
    filler: str
    current_projection_status: Literal[
        "scoreable-release-bound",
        "review-bearing-release-bound",
        "provisional-proposed",
        "not-emitted",
    ]
    in_expected_oracle: bool
    verdict: AxisRangeEvidenceDocument


class DiagnosticMetrics(_StrictModel):
    sme_include_rate: CurrentRateMetric
    exact_pair_precision: CurrentRateMetric
    exact_pair_recall: CurrentRateMetric
    full_partition_agreement: CurrentRateMetric
    common_pair_partition_agreement: CurrentCommonPartitionMetric


class AxisDiagnosticReport(_StrictModel):
    schema_version: Literal[2]
    ncit_version: str
    source_identity: str = Field(pattern=_SHA256)
    sample_manifest_identity: str = Field(pattern=_SHA256)
    run_id: str
    run_fingerprint_identity: str = Field(pattern=_SHA256)
    artifact_identity: str = Field(pattern=_SHA256)
    representation_identity: str = Field(pattern=_SHA256)
    detector_identity: str = Field(pattern=_SHA256)
    oracle_identity: str = Field(pattern=_SHA256)
    row_decision_identity: str = Field(pattern=_SHA256)
    proposal_registry_identity: str = Field(pattern=_SHA256)
    current_evidence_identity: str = Field(pattern=_SHA256)
    current_comparison_identity: str = Field(pattern=_SHA256)
    metrics: DiagnosticMetrics
    revise_rows: tuple[ReviseRowDiagnostic, ...] = Field(
        min_length=_REVISE_COUNT, max_length=_REVISE_COUNT
    )
    candidate_rows: tuple[CandidateRowDiagnostic, ...] = Field(
        min_length=_CANDIDATE_COUNT, max_length=_CANDIDATE_COUNT
    )
    range_diagnostics: tuple[PairRangeDiagnostic, ...]
    residual_diagnostics: dict[str, ResidualPrecoordinationDocument]
    report_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_identity(self) -> Self:
        range_keys = [
            (item.code, item.axis, item.filler) for item in self.range_diagnostics
        ]
        if range_keys != sorted(set(range_keys)):
            raise ValueError("range diagnostic keys must be sorted and unique")
        if any(
            item.verdict.source_identity != self.source_identity
            for item in self.range_diagnostics
        ):
            raise ValueError("range diagnostic source identity does not match report")
        if list(self.residual_diagnostics) != sorted(self.residual_diagnostics):
            raise ValueError("residual diagnostic keys must be sorted")
        if any(
            item.detector_identity != self.detector_identity
            for item in self.residual_diagnostics.values()
        ):
            raise ValueError("residual detector identity does not match report")
        expected = _identity(self.model_dump(mode="json", exclude={"report_identity"}))
        if self.report_identity != expected:
            raise ValueError(
                "axis diagnostic report identity does not match its payload"
            )
        return self


def _oracle_constituents(
    oracle: AdjudicationArtifact,
) -> dict[str, dict[tuple[str, str], GoldenConstituent]]:
    return {
        concept.code: {
            (item.axis, item.filler): item
            for item in (
                concept.expected.constituents if concept.expected is not None else ()
            )
        }
        for concept in oracle.concepts
    }


def _current_constituents(
    evidence: CurrentEngineEvidence,
) -> dict[str, dict[tuple[str, str], CurrentConstituent]]:
    return {
        concept.code: {(item.axis, item.filler): item for item in concept.constituents}
        for concept in evidence.concepts
    }


def _delta(expected: object, actual: object) -> Literal["unchanged", "changed"]:
    return "unchanged" if expected == actual else "changed"


def _revise_rows(
    rows: RowDecisionExport,
    oracle: AdjudicationArtifact,
    evidence: CurrentEngineEvidence,
) -> tuple[ReviseRowDiagnostic, ...]:
    expected_by_code = _oracle_constituents(oracle)
    current_by_code = _current_constituents(evidence)
    result: list[ReviseRowDiagnostic] = []
    for ordinal, row in enumerate(rows.rows):
        if row.row_type != "ENGINE SUGGESTION" or row.sme_action != "revise":
            continue
        if not isinstance(row, KeptRow) or row.engine is None:
            raise ValueError("revise row lacks its engine and expected pairs")
        pair = (row.expected.axis, row.expected.filler)
        expected = expected_by_code[row.code][pair]
        current = current_by_code[row.code].get(pair)
        missing = current is None
        result.append(
            ReviseRowDiagnostic(
                ordinal=ordinal,
                code=row.code,
                engine=ExpectedPair(axis=row.engine.axis, filler=row.engine.filler),
                expected=row.expected,
                historical_pair_delta=_delta(
                    (row.engine.axis, row.engine.filler),
                    (row.expected.axis, row.expected.filler),
                ),
                current_presence="missing" if missing else "present",
                group_delta=(
                    "missing-current"
                    if missing
                    else _delta(expected.relationship_group, current.relationship_group)
                ),
                review_delta=(
                    "missing-current"
                    if missing
                    else _delta(expected.needs_review, current.needs_review)
                ),
                provenance_delta=(
                    "missing-current"
                    if missing
                    else _delta(expected.provenance_status, current.provenance_status)
                ),
            )
        )
    if len(result) != _REVISE_COUNT:
        raise ValueError("diagnostic report must contain exactly 42 revise rows")
    return tuple(result)


def _candidate_classification(
    *,
    old_status: RowReplayStatus,
    source: SourcePairEvidence | None,
) -> Literal[
    "added",
    "extraction-miss",
    "selection-miss",
    "proposal-only",
    "unavailable-source-evidence",
]:
    if old_status == RowReplayStatus.ADDED:
        return "added"
    if old_status == RowReplayStatus.PROPOSAL_ONLY:
        return "proposal-only"
    if source is not None:
        return "extraction-miss" if source.stage == "source-only" else "selection-miss"
    return "unavailable-source-evidence"


def _candidate_rows(
    rows: RowDecisionExport,
    comparison: CurrentComparison,
    source_evidence: dict[tuple[str, str, str], SourcePairEvidence],
) -> tuple[CandidateRowDiagnostic, ...]:
    replay = {result.ordinal: result for result in comparison.row_replay.results}
    result: list[CandidateRowDiagnostic] = []
    for ordinal, row in enumerate(rows.rows):
        if row.row_type != "ADD IF MISSING" or row.sme_action not in {
            "include",
            "revise",
        }:
            continue
        if not isinstance(row, KeptRow):
            raise ValueError("kept candidate row lacks expected pair")
        key = (row.code, row.expected.axis, row.expected.filler)
        source = source_evidence.get(key)
        old_status = replay[ordinal].status
        result.append(
            CandidateRowDiagnostic(
                ordinal=ordinal,
                code=row.code,
                expected=row.expected,
                classification=_candidate_classification(
                    old_status=old_status,
                    source=source,
                ),
                source_definition_ids=(
                    source.source_definition_ids if source is not None else ()
                ),
            )
        )
    if len(result) != _CANDIDATE_COUNT:
        raise ValueError("diagnostic report must contain exactly 64 candidate rows")
    return tuple(result)


def build_axis_diagnostic_report(
    *,
    oracle: AdjudicationArtifact,
    rows: RowDecisionExport,
    registry: ProposalRegistry,
    evidence: CurrentEngineEvidence,
    comparison: CurrentComparison,
    source_evidence: dict[tuple[str, str, str], SourcePairEvidence],
    range_verdicts: dict[tuple[str, str, str], AxisRangeEvidence],
    residual_verdicts: dict[str, ResidualPrecoordinationVerdict],
) -> AxisDiagnosticReport:
    """Build the pre-SME report without changing any accepted engine artifact."""
    metrics = CurrentMetrics.model_validate(comparison.metrics.model_dump())
    current_pairs = {
        (concept.code, item.axis, item.filler): item
        for concept in evidence.concepts
        for item in concept.constituents
    }
    expected_pairs = {
        (concept.code, item.axis, item.filler)
        for concept in oracle.concepts
        if concept.expected is not None
        for item in concept.expected.constituents
    }
    payload = {
        "schema_version": 2,
        "ncit_version": comparison.ncit_version,
        "source_identity": comparison.source_identity,
        "sample_manifest_identity": comparison.sample_manifest_identity,
        "run_id": comparison.run_id,
        "run_fingerprint_identity": comparison.run_fingerprint_identity,
        "artifact_identity": comparison.artifact_identity,
        "representation_identity": comparison.representation_identity,
        "detector_identity": comparison.detector_identity,
        "oracle_identity": oracle.identity,
        "row_decision_identity": rows.payload_identity,
        "proposal_registry_identity": registry.registry_identity,
        "current_evidence_identity": evidence.evidence_identity,
        "current_comparison_identity": comparison.comparison_identity,
        "metrics": DiagnosticMetrics(
            sme_include_rate=CurrentRateMetric(
                numerator=_SME_INCLUDED,
                denominator=_SME_SUGGESTIONS,
                rate=_SME_INCLUDED / _SME_SUGGESTIONS,
            ),
            exact_pair_precision=metrics.exact_pair_precision,
            exact_pair_recall=metrics.exact_pair_recall,
            full_partition_agreement=metrics.full_partition_agreement,
            common_pair_partition_agreement=(metrics.common_pair_partition_agreement),
        ),
        "revise_rows": _revise_rows(rows, oracle, evidence),
        "candidate_rows": _candidate_rows(rows, comparison, source_evidence),
        "range_diagnostics": tuple(
            PairRangeDiagnostic(
                code=code,
                axis=axis,
                filler=filler,
                current_projection_status=_current_projection_status(
                    current_pairs.get((code, axis, filler))
                ),
                in_expected_oracle=(code, axis, filler) in expected_pairs,
                verdict=axis_evidence_to_document(verdict),
            )
            for (code, axis, filler), verdict in sorted(range_verdicts.items())
        ),
        "residual_diagnostics": {
            code: residual_evidence_to_document(verdict)
            for code, verdict in sorted(residual_verdicts.items())
        },
    }
    return AxisDiagnosticReport(
        **payload,
        report_identity=_identity(payload),
    )


def _current_projection_status(
    item: CurrentConstituent | None,
) -> Literal[
    "scoreable-release-bound",
    "review-bearing-release-bound",
    "provisional-proposed",
    "not-emitted",
]:
    if item is None:
        return "not-emitted"
    if item.provenance_status == "proposed":
        return "provisional-proposed"
    if item.needs_review:
        return "review-bearing-release-bound"
    return "scoreable-release-bound"


def _required_inputs(paths: tuple[Path, ...], output: Path) -> None:
    for path in paths:
        if not path.is_file():
            raise ValueError(f"input does not exist: {path}")
    if not output.parent.is_dir():
        raise ValueError(f"output parent does not exist: {output.parent}")
    if output.resolve() in {path.resolve() for path in paths}:
        raise ValueError("output must differ from every input")


def _diagnostic_pairs(
    oracle: AdjudicationArtifact,
    evidence: CurrentEngineEvidence,
) -> tuple[tuple[str, str, str], ...]:
    pairs = {
        (concept.code, item.axis, item.filler)
        for concept in evidence.concepts
        for item in concept.constituents
        if item.axis in AXIS_CONTRACTS and item.filler.startswith("C")
    }
    pairs.update(
        (concept.code, item.axis, item.filler)
        for concept in oracle.concepts
        if concept.expected is not None
        for item in concept.expected.constituents
        if item.axis in AXIS_CONTRACTS and item.filler.startswith("C")
    )
    return tuple(sorted(pairs))


def _mark_extracted_source_evidence(
    source_evidence: dict[tuple[str, str, str], SourcePairEvidence],
    comparison: CurrentComparison,
) -> dict[tuple[str, str, str], SourcePairEvidence]:
    result = dict(source_evidence)
    for row in comparison.row_replay.results:
        if (
            row.row_type != "ADD IF MISSING"
            or row.expected is None
            or row.status != RowReplayStatus.SELECTION_MISS
        ):
            continue
        key = (row.code, row.expected.axis, row.expected.filler)
        source = result.get(key)
        if source is not None:
            result[key] = SourcePairEvidence(
                stage="extracted",
                source_definition_ids=source.source_definition_ids,
            )
    return result


def _write_report(path: Path, report: AxisDiagnosticReport) -> None:
    payload = (
        json.dumps(
            report.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
        ).encode()
        + b"\n"
    )
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


async def generate_axis_diagnostic_report(
    *,
    source_manifest: Path,
    endpoint: str,
    oracle_path: Path,
    row_decisions_path: Path,
    proposal_registry_path: Path,
    current_evidence_path: Path,
    current_comparison_path: Path,
    residual_fillers: tuple[str, ...],
    output: Path,
) -> AxisDiagnosticReport:
    """Generate one current-source pre-SME diagnostic packet atomically."""
    inputs = (
        source_manifest,
        oracle_path,
        row_decisions_path,
        proposal_registry_path,
        current_evidence_path,
        current_comparison_path,
    )
    _required_inputs(inputs, output)
    manifest = validate_ncit_sibling_manifest(source_manifest)
    registry = load_proposal_registry(proposal_registry_path)
    oracle = load_adjudication(oracle_path, registry)
    rows = load_row_decisions(row_decisions_path)
    evidence = CurrentEngineEvidence.model_validate_json(
        current_evidence_path.read_bytes()
    )
    comparison = CurrentComparison.model_validate_json(
        current_comparison_path.read_bytes()
    )
    if (manifest.source_identity, manifest.ontology_version) != (
        evidence.source_identity,
        evidence.ncit_version,
    ):
        raise ValueError("source manifest does not match current evidence")
    if comparison.current_evidence_identity != evidence.evidence_identity:
        raise ValueError("current comparison does not match current evidence")

    async with ncit_sparql_client(endpoint, query_timeout=180.0) as client:
        source = await read_axis_diagnostic_source(client, manifest.source_identity)
        source_evidence = await collect_source_pair_evidence(
            client,
            rows,
            concept_codes=tuple(concept.code for concept in evidence.concepts),
        )
        residual = await collect_residual_verdicts(
            client,
            residual_fillers,
            detector_identity=evidence.detector_identity,
            walker_max_depth=5,
        )
    source_evidence = _mark_extracted_source_evidence(
        source_evidence,
        comparison,
    )
    range_verdicts = {
        key: source.classify(axis=key[1], filler_code=key[2])
        for key in _diagnostic_pairs(oracle, evidence)
    }
    report = build_axis_diagnostic_report(
        oracle=oracle,
        rows=rows,
        registry=registry,
        evidence=evidence,
        comparison=comparison,
        source_evidence=source_evidence,
        range_verdicts=range_verdicts,
        residual_verdicts=residual,
    )
    _write_report(output, report)
    return report
