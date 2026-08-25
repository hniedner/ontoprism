"""Strict current-run decomposition evidence and oracle comparison generation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ontolib.decomposition.axis_contracts import normalized_axis_for_role
from ontolib.decomposition.evaluation import (
    PairPartition,
    PartitionComparison,
    PartitionDiagnosis,
    compare_common_pair_partition,
    compare_full_partition,
    grouping_difference_pairs,
)
from ontolib.decomposition.models import ConceptOutcome  # noqa: TC001
from ontolib.decomposition.proposal_registry import (
    ProposalRegistry,
    load_proposal_registry,
)
from ontolib.decomposition.publication import validate_artifact
from ontolib.decomposition.sampling import (
    DecompositionSampleManifest,
    load_sample_manifest,
)

try:
    from scripts.research.golden_review import (
        AdjudicatedConcept,
        AdjudicationArtifact,
        ConstituentRowDecision,
        EngineSuggestion,
        ExpectedPair,
        GoldenSetValidationError,
        KeptRow,
        RowDecisionExport,
        load_adjudication,
        load_row_decisions,
    )
except ModuleNotFoundError:  # direct `python scripts/adjudication.py` entry point
    from research.golden_review import (  # type: ignore[no-redef]
        AdjudicatedConcept,
        AdjudicationArtifact,
        ConstituentRowDecision,
        EngineSuggestion,
        ExpectedPair,
        GoldenSetValidationError,
        KeptRow,
        RowDecisionExport,
        load_adjudication,
        load_row_decisions,
    )

if TYPE_CHECKING:
    from scripts.research.golden_review import GoldenConstituent

    from ontolib.decomposition.models import Decomposition, SourceDefinitionOccurrence
    from ontolib.decomposition.provenance_models import (
        CompletedRunForEvidence,
        WorkItemOutcome,
    )

_SHA256 = r"^[0-9a-f]{64}$"


class CurrentEvidenceValidationError(ValueError):
    """Current inputs cannot be bound to one trusted persisted run."""


class CurrentEvidenceStore(Protocol):
    async def completed_run_for_evidence(
        self, run_id: str
    ) -> CompletedRunForEvidence: ...

    async def work_item_outcomes(self, run_id: str) -> list[WorkItemOutcome]: ...

    async def decompositions_for_run(self, run_id: str) -> list[Decomposition]: ...


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


class CurrentSourceOccurrence(_StrictModel):
    occurrence_id: str = Field(pattern=_SHA256)
    root_code: str
    source_fact_id: str = Field(pattern=_SHA256)
    source_group_id: str = Field(pattern=_SHA256)
    anchor_code: str
    depth: int = Field(ge=0)
    role_code: str
    filler_code: str
    structural_path: tuple[int, ...]
    member_position: int = Field(ge=0)


class CurrentConstituent(_StrictModel):
    axis: str = Field(pattern=r"^op:[A-Za-z][A-Za-z0-9]*$")
    filler: str = Field(pattern=r"^(?:C[0-9]+|MINT-[0-9a-f]+)$")
    relationship_group: str | None
    needs_review: bool
    source_definition_ids: tuple[str, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    source_occurrence_ids: tuple[str, ...]
    source_occurrences: tuple[CurrentSourceOccurrence, ...]

    @property
    def provenance_status(self) -> Literal["ncit-26.07d", "proposed"]:
        """Derive the typed scoreability status from the validated filler identity."""
        return (
            "ncit-26.07d"
            if re.fullmatch(r"C[0-9]+", self.filler) is not None
            else "proposed"
        )

    @model_validator(mode="after")
    def _citations_match_selected_ids(self) -> Self:
        if len(set(self.source_definition_ids)) != len(self.source_definition_ids):
            raise ValueError("duplicate source definition citations")
        if tuple(sorted(self.source_definition_ids)) != self.source_definition_ids:
            raise ValueError("source definition citations are not canonical")
        if any(
            re.fullmatch(_SHA256, item) is None for item in self.source_definition_ids
        ):
            raise ValueError("source definition citation is not a SHA-256 identity")
        cited = tuple(item.occurrence_id for item in self.source_occurrences)
        if cited != self.source_occurrence_ids:
            raise ValueError("source occurrence citations do not match selected IDs")
        if self.source_occurrences and not self.source_definition_ids:
            raise ValueError("source occurrences require source definition citations")
        occurrence_fact_ids = {item.source_fact_id for item in self.source_occurrences}
        if self.source_definition_ids and not occurrence_fact_ids <= set(
            self.source_definition_ids
        ):
            raise ValueError("source occurrences cite an unselected definition fact")
        return self


class CurrentConceptEvidence(_StrictModel):
    code: str = Field(pattern=r"^C[0-9]+$")
    outcome: ConceptOutcome
    semantic_types: tuple[str, ...]
    all_source_occurrences: tuple[CurrentSourceOccurrence, ...]
    constituents: tuple[CurrentConstituent, ...]

    @model_validator(mode="after")
    def _selected_occurrences_are_a_subset(self) -> Self:
        available = {item.occurrence_id for item in self.all_source_occurrences}
        selected = {
            occurrence_id
            for constituent in self.constituents
            for occurrence_id in constituent.source_occurrence_ids
        }
        if not selected <= available:
            raise ValueError(
                "selected source occurrences must be a subset of all source occurrences"
            )
        return self


class CurrentEngineEvidence(_StrictModel):
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
    concepts: tuple[CurrentConceptEvidence, ...]
    evidence_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_identity(self) -> Self:
        expected = _identity(
            self.model_dump(mode="json", exclude={"evidence_identity"})
        )
        if self.evidence_identity != expected:
            raise ValueError("current evidence identity does not match its payload")
        return self


class CurrentRateMetric(_StrictModel):
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    rate: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _rate_matches_counts(self) -> Self:
        expected = self.numerator / self.denominator if self.denominator else 0.0
        if self.rate != expected:
            raise ValueError("metric rate does not match numerator and denominator")
        return self


class CurrentCommonPartitionMetric(CurrentRateMetric):
    ineligible: int = Field(ge=0)


class CurrentMetrics(_StrictModel):
    exact_pair_precision: CurrentRateMetric
    exact_pair_recall: CurrentRateMetric
    full_partition_agreement: CurrentRateMetric
    common_pair_partition_agreement: CurrentCommonPartitionMetric


class AvailableActualPairCitation(_StrictModel):
    pair: tuple[str, str]
    availability: Literal["available"]
    occurrence_ids: tuple[str, ...] = Field(min_length=1)


class UnavailableActualPairCitation(_StrictModel):
    pair: tuple[str, str]
    availability: Literal["unavailable-no-occurrence-evidence"]


ActualPairCitation = Annotated[
    AvailableActualPairCitation | UnavailableActualPairCitation,
    Field(discriminator="availability"),
]


class HistoricalOraclePairCitation(_StrictModel):
    pair: tuple[str, str]
    availability: Literal["unavailable-historical-oracle"]


class PartitionDiagnosisEvidence(_StrictModel):
    diagnosis: PartitionDiagnosis
    normalization_rule: Literal["group-label-independent-co-membership"]
    affected_pairs: tuple[tuple[str, str], ...] = Field(min_length=2)
    actual_pair_citations: tuple[ActualPairCitation, ...]
    expected_pair_citations: tuple[HistoricalOraclePairCitation, ...] = Field(
        min_length=1
    )

    @model_validator(mode="after")
    def _citations_cover_affected_pairs_in_order(self) -> Self:
        if (
            tuple(item.pair for item in self.actual_pair_citations)
            != self.affected_pairs
        ):
            raise ValueError("actual pair citations must cover affected pairs in order")
        if (
            tuple(item.pair for item in self.expected_pair_citations)
            != self.affected_pairs
        ):
            raise ValueError(
                "expected pair citations must cover affected pairs in order"
            )
        return self


class CurrentPartitionComparison(_StrictModel):
    eligible: bool
    agrees: bool | None
    expected_partition: PairPartition
    actual_partition: PairPartition
    missing_pairs: tuple[tuple[str, str], ...]
    extra_pairs: tuple[tuple[str, str], ...]
    shared_pair_count: int = Field(ge=0)
    ineligibility_reason: Literal["zero-shared-pairs", "one-shared-pair"] | None
    primary_diagnosis: PartitionDiagnosisEvidence | None

    @model_validator(mode="after")
    def _eligibility_shape_is_closed(self) -> Self:
        if self.eligible:
            if self.agrees is None or self.ineligibility_reason is not None:
                raise ValueError("eligible partition comparison has invalid fields")
        elif self.agrees is not None or self.ineligibility_reason is None:
            raise ValueError("ineligible partition comparison has invalid fields")
        if self.agrees is not False and self.primary_diagnosis is not None:
            raise ValueError("partition diagnosis requires a disagreement")
        return self


class CurrentConceptComparison(_StrictModel):
    code: str
    full_partition: CurrentPartitionComparison
    common_pair_partition: CurrentPartitionComparison
    missing_pairs: tuple[tuple[str, str], ...]
    extra_pairs: tuple[tuple[str, str], ...]


class RowReplayStatus(StrEnum):
    RETAINED_EXACT = "retained-exact"
    RETAINED_REVISED = "retained-revised"
    EXCLUDED_STILL_EMITTED = "excluded-still-emitted"
    EXCLUDED_NOT_EMITTED = "excluded-not-emitted"
    MISSING_KEPT = "missing-kept"
    ADDED = "added"
    SELECTION_MISS = "selection-miss"
    PROPOSAL_ONLY = "proposal-only"
    UNAVAILABLE_SOURCE_EVIDENCE = "unavailable-source-evidence"
    EXPLICITLY_OUT_OF_SCOPE = "explicitly-out-of-scope"


EngineReplayStatus = Literal[
    RowReplayStatus.RETAINED_EXACT,
    RowReplayStatus.RETAINED_REVISED,
    RowReplayStatus.EXCLUDED_STILL_EMITTED,
    RowReplayStatus.EXCLUDED_NOT_EMITTED,
    RowReplayStatus.MISSING_KEPT,
]
CandidateReplayStatus = Literal[
    RowReplayStatus.ADDED,
    RowReplayStatus.SELECTION_MISS,
    RowReplayStatus.PROPOSAL_ONLY,
    RowReplayStatus.UNAVAILABLE_SOURCE_EVIDENCE,
    RowReplayStatus.EXPLICITLY_OUT_OF_SCOPE,
]


class _RowReplayResult(_StrictModel):
    ordinal: int = Field(ge=0)
    code: str = Field(pattern=r"^C[0-9]+$")
    expected: ExpectedPair | None


class EngineSuggestionReplayResult(_RowReplayResult):
    row_type: Literal["ENGINE SUGGESTION"]
    sme_action: Literal["include", "revise", "exclude"]
    engine: EngineSuggestion
    status: EngineReplayStatus


class AddIfMissingReplayResult(_RowReplayResult):
    row_type: Literal["ADD IF MISSING"]
    sme_action: Literal["include", "revise", "exclude", "not-needed"]
    engine: None
    status: CandidateReplayStatus


RowReplayResult = Annotated[
    EngineSuggestionReplayResult | AddIfMissingReplayResult,
    Field(discriminator="row_type"),
]


class RowReplayAggregates(_StrictModel):
    retained_exact: int = Field(ge=0)
    retained_revised: int = Field(ge=0)
    excluded_still_emitted: int = Field(ge=0)
    excluded_not_emitted: int = Field(ge=0)
    missing_kept: int = Field(ge=0)
    added: int = Field(ge=0)
    selection_miss: int = Field(ge=0)
    proposal_only: int = Field(ge=0)
    unavailable_source_evidence: int = Field(ge=0)
    explicitly_out_of_scope: int = Field(ge=0)


class CurrentRowReplay(_StrictModel):
    results: tuple[RowReplayResult, ...] = Field(min_length=189, max_length=189)
    aggregates: RowReplayAggregates

    @model_validator(mode="after")
    def _covers_each_row_once(self) -> Self:
        if tuple(result.ordinal for result in self.results) != tuple(range(189)):
            raise ValueError("row replay must contain every source row exactly once")
        counts = Counter(result.status.value for result in self.results)
        expected = {
            field: counts[field.replace("_", "-")]
            for field in RowReplayAggregates.model_fields
        }
        if self.aggregates.model_dump() != expected:
            raise ValueError("row replay aggregates do not match replay results")
        return self


class CurrentComparison(_StrictModel):
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
    metrics: CurrentMetrics
    concepts: tuple[CurrentConceptComparison, ...]
    row_replay: CurrentRowReplay
    comparison_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_identity(self) -> Self:
        expected = _identity(
            self.model_dump(mode="json", exclude={"comparison_identity"})
        )
        if self.comparison_identity != expected:
            raise ValueError("current comparison identity does not match its payload")
        return self


def _detector_identity(run: CompletedRunForEvidence) -> str:
    fingerprint = run.fingerprint
    return _identity(
        {
            "algorithm_version": fingerprint.algorithm_version,
            "config_version": fingerprint.config_version,
            "walker_max_depth": fingerprint.walker_max_depth,
            "semantic_types": fingerprint.semantic_types,
        }
    )


def _require_paths(paths: tuple[Path, ...], outputs: tuple[Path, Path]) -> None:
    for path in paths:
        if not path.exists():
            raise CurrentEvidenceValidationError(f"input does not exist: {path}")
    resolved_inputs = {path.resolve() for path in paths}
    resolved_outputs = tuple(path.resolve() for path in outputs)
    if len(set(resolved_outputs)) != len(resolved_outputs):
        raise CurrentEvidenceValidationError(
            "engine and comparison outputs must differ"
        )
    if resolved_inputs & set(resolved_outputs):
        raise CurrentEvidenceValidationError("outputs must differ from every input")
    for output in outputs:
        if not output.parent.exists():
            raise CurrentEvidenceValidationError(
                f"output parent does not exist: {output.parent}"
            )


def _require_run_matches_manifest(
    run: CompletedRunForEvidence, manifest: DecompositionSampleManifest
) -> None:
    fingerprint = run.fingerprint
    checks = (
        ("source identity", fingerprint.source_identity, manifest.source_identity),
        ("release", run.ncit_version, manifest.ontology_version),
        ("branch", fingerprint.branch, manifest.branch),
        ("scope root", fingerprint.scope_root, manifest.scope_root),
        ("scope version", fingerprint.scope_version, manifest.scope_version),
        ("worklist", fingerprint.worklist, manifest.codes),
        ("manifest identity", fingerprint.sample_manifest_identity, manifest.identity),
    )
    for name, actual, expected in checks:
        if actual != expected:
            raise CurrentEvidenceValidationError(
                f"run {name} does not match current sample manifest"
            )


def _occurrence(value: SourceDefinitionOccurrence) -> CurrentSourceOccurrence:
    return CurrentSourceOccurrence(
        occurrence_id=value.occurrence_id,
        root_code=value.root_code,
        source_fact_id=value.source_fact_id,
        source_group_id=value.source_group_id,
        anchor_code=value.anchor_code,
        depth=value.depth,
        role_code=value.role_code,
        filler_code=value.filler_code,
        structural_path=value.structural_path,
        member_position=value.member_position,
    )


def _concepts(
    outcomes: list[WorkItemOutcome], decompositions: list[Decomposition]
) -> tuple[CurrentConceptEvidence, ...]:
    decompositions_by_code = {item.code: item for item in decompositions}
    if len(decompositions_by_code) != len(decompositions):
        raise CurrentEvidenceValidationError(
            "persisted decompositions contain duplicate codes"
        )
    concepts: list[CurrentConceptEvidence] = []
    for outcome in outcomes:
        if outcome.state != "complete" or outcome.outcome is None:
            raise CurrentEvidenceValidationError("run contains an incomplete work item")
        decomposition = decompositions_by_code.pop(outcome.concept_code, None)
        if outcome.outcome == "decomposed" and decomposition is None:
            raise CurrentEvidenceValidationError(
                "decomposed outcome lacks persisted decomposition: "
                f"{outcome.concept_code}"
            )
        all_occurrences = tuple(
            _occurrence(item)
            for item in (
                decomposition.complete_definition.occurrences
                if decomposition is not None
                and decomposition.complete_definition is not None
                else ()
            )
        )
        occurrences = {item.occurrence_id: item for item in all_occurrences}
        constituents = tuple(
            CurrentConstituent(
                axis=item.axis,
                filler=item.filler_code,
                relationship_group=item.group,
                needs_review=item.needs_review,
                source_definition_ids=item.source_definition_ids,
                source_occurrence_ids=item.source_occurrence_ids,
                source_occurrences=tuple(
                    occurrences[occurrence_id]
                    for occurrence_id in item.source_occurrence_ids
                ),
            )
            for item in (
                decomposition.constituents if decomposition is not None else ()
            )
        )
        concepts.append(
            CurrentConceptEvidence(
                code=outcome.concept_code,
                outcome=outcome.outcome,
                semantic_types=outcome.semantic_types or (),
                all_source_occurrences=all_occurrences,
                constituents=constituents,
            )
        )
    if decompositions_by_code:
        raise CurrentEvidenceValidationError(
            "persisted decomposition is outside worklist"
        )
    return tuple(concepts)


def _scoreable_partition_rows(
    constituents: tuple[GoldenConstituent, ...] | tuple[CurrentConstituent, ...],
) -> tuple[tuple[tuple[str, str], str | None], ...]:
    return tuple(
        ((item.axis, item.filler), item.relationship_group)
        for item in constituents
        if not item.needs_review and item.provenance_status == "ncit-26.07d"
    )


def _comparison_payload(
    oracle_concepts: tuple[AdjudicatedConcept, ...],
    evidence: CurrentEngineEvidence,
) -> tuple[CurrentMetrics, tuple[CurrentConceptComparison, ...]]:
    actual_by_code = {item.code: item for item in evidence.concepts}
    reports: list[CurrentConceptComparison] = []
    full_agreements = 0
    common_agreements = 0
    common_eligible = 0
    expected_pairs = 0
    actual_pairs = 0
    true_positive_pairs = 0
    for concept in oracle_concepts:
        if concept.adjudication.status != "accepted" or concept.expected is None:
            continue
        actual = actual_by_code[concept.code]
        expected_rows = _scoreable_partition_rows(concept.expected.constituents)
        actual_rows = _scoreable_partition_rows(actual.constituents)
        full = compare_full_partition(expected_rows, actual_rows)
        common = compare_common_pair_partition(expected_rows, actual_rows)
        full_agreements += full.agrees is True
        common_eligible += common.eligible
        common_agreements += common.agrees is True
        expected_count = sum(len(block) for block in full.expected_partition)
        actual_count = sum(len(block) for block in full.actual_partition)
        expected_pairs += expected_count
        actual_pairs += actual_count
        true_positive_pairs += expected_count - len(full.missing_pairs)
        reports.append(
            CurrentConceptComparison(
                code=concept.code,
                full_partition=_typed_partition(full, actual),
                common_pair_partition=_typed_partition(common, actual),
                missing_pairs=full.missing_pairs,
                extra_pairs=full.extra_pairs,
            )
        )
    metrics = CurrentMetrics(
        exact_pair_precision=_rate_metric(true_positive_pairs, actual_pairs),
        exact_pair_recall=_rate_metric(true_positive_pairs, expected_pairs),
        full_partition_agreement=_rate_metric(full_agreements, len(reports)),
        common_pair_partition_agreement=CurrentCommonPartitionMetric(
            numerator=common_agreements,
            denominator=common_eligible,
            rate=_rate(common_agreements, common_eligible),
            ineligible=len(reports) - common_eligible,
        ),
    )
    return metrics, tuple(reports)


def _emitted_pairs(concept: CurrentConceptEvidence) -> set[tuple[str, str]]:
    return {(item.axis, item.filler) for item in concept.constituents}


def _oracle_proposals(
    oracle_concepts: tuple[AdjudicatedConcept, ...], registry: ProposalRegistry
) -> set[tuple[str, str, str]]:
    proposal_ids = {proposal.id for proposal in registry.proposals}
    proposals: set[tuple[str, str, str]] = set()
    for concept in oracle_concepts:
        if concept.expected is None:
            continue
        for constituent in concept.expected.constituents:
            if constituent.proposal_id is None:
                continue
            if constituent.proposal_id not in proposal_ids:
                raise CurrentEvidenceValidationError(
                    "oracle constituent proposal is absent from proposal registry"
                )
            proposals.add((concept.code, constituent.axis, constituent.filler))
    return proposals


def _source_supports_expected(
    expected: ExpectedPair, concept: CurrentConceptEvidence
) -> bool:
    return any(
        occurrence.filler_code == expected.filler
        and normalized_axis_for_role(occurrence.role_code) == expected.axis
        for occurrence in concept.all_source_occurrences
    )


def _engine_replay_status(
    row: ConstituentRowDecision, emitted: set[tuple[str, str]]
) -> EngineReplayStatus:
    if row.engine is None:
        raise CurrentEvidenceValidationError(
            "engine suggestion replay row lacks an engine pair"
        )
    engine_pair = (row.engine.axis, row.engine.filler)
    if row.sme_action == "exclude":
        if engine_pair in emitted:
            return RowReplayStatus.EXCLUDED_STILL_EMITTED
        return RowReplayStatus.EXCLUDED_NOT_EMITTED
    if not isinstance(row, KeptRow):
        raise CurrentEvidenceValidationError(
            "kept engine suggestion replay row lacks an expected pair"
        )
    expected_pair = (row.expected.axis, row.expected.filler)
    if expected_pair == engine_pair and engine_pair in emitted:
        return RowReplayStatus.RETAINED_EXACT
    if expected_pair != engine_pair and expected_pair in emitted:
        return RowReplayStatus.RETAINED_REVISED
    return RowReplayStatus.MISSING_KEPT


def _candidate_replay_status(
    row: ConstituentRowDecision,
    concept: CurrentConceptEvidence,
    proposals: set[tuple[str, str, str]],
) -> CandidateReplayStatus:
    if row.sme_action in {"exclude", "not-needed"}:
        return RowReplayStatus.EXPLICITLY_OUT_OF_SCOPE
    if not isinstance(row, KeptRow):
        raise CurrentEvidenceValidationError(
            "kept add-if-missing replay row lacks an expected pair"
        )
    expected_pair = (row.expected.axis, row.expected.filler)
    if expected_pair in _emitted_pairs(concept):
        return RowReplayStatus.ADDED
    if (row.code, *expected_pair) in proposals:
        return RowReplayStatus.PROPOSAL_ONLY
    if _source_supports_expected(row.expected, concept):
        return RowReplayStatus.SELECTION_MISS
    return RowReplayStatus.UNAVAILABLE_SOURCE_EVIDENCE


def _row_replay(
    rows: RowDecisionExport,
    oracle_concepts: tuple[AdjudicatedConcept, ...],
    registry: ProposalRegistry,
    current_concepts: tuple[CurrentConceptEvidence, ...],
) -> CurrentRowReplay:
    current_by_code = {concept.code: concept for concept in current_concepts}
    proposals = _oracle_proposals(oracle_concepts, registry)
    results: list[RowReplayResult] = []
    for ordinal, row in enumerate(rows.rows):
        concept = current_by_code.get(row.code)
        if concept is None:
            raise CurrentEvidenceValidationError(
                f"row replay concept is absent from current evidence: {row.code}"
            )
        common = {
            "ordinal": ordinal,
            "code": row.code,
            "sme_action": row.sme_action,
            "engine": row.engine,
            "expected": getattr(row, "expected", None),
        }
        if row.row_type == "ENGINE SUGGESTION":
            results.append(
                EngineSuggestionReplayResult(
                    **common,
                    row_type=row.row_type,
                    status=_engine_replay_status(row, _emitted_pairs(concept)),
                )
            )
        else:
            results.append(
                AddIfMissingReplayResult(
                    **common,
                    row_type=row.row_type,
                    status=_candidate_replay_status(row, concept, proposals),
                )
            )
    counts = Counter(result.status.value for result in results)
    aggregates = RowReplayAggregates.model_validate(
        {
            field: counts[field.replace("_", "-")]
            for field in RowReplayAggregates.model_fields
        }
    )
    return CurrentRowReplay(results=tuple(results), aggregates=aggregates)


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _rate_metric(numerator: int, denominator: int) -> CurrentRateMetric:
    return CurrentRateMetric(
        numerator=numerator,
        denominator=denominator,
        rate=_rate(numerator, denominator),
    )


def _pair_occurrence_ids(
    pair: tuple[str, str], concept: CurrentConceptEvidence
) -> tuple[str, ...]:
    return tuple(
        occurrence_id
        for constituent in concept.constituents
        if (constituent.axis, constituent.filler) == pair
        for occurrence_id in constituent.source_occurrence_ids
    )


def _typed_diagnosis(
    diagnosis: PartitionDiagnosis,
    comparison: PartitionComparison,
    concept: CurrentConceptEvidence,
) -> PartitionDiagnosisEvidence:
    affected_pairs = grouping_difference_pairs(
        comparison.expected_partition,
        comparison.actual_partition,
    )
    actual_citations: tuple[ActualPairCitation, ...] = tuple(
        AvailableActualPairCitation(
            pair=pair,
            availability="available",
            occurrence_ids=occurrence_ids,
        )
        if occurrence_ids
        else UnavailableActualPairCitation(
            pair=pair,
            availability="unavailable-no-occurrence-evidence",
        )
        for pair in affected_pairs
        for occurrence_ids in (_pair_occurrence_ids(pair, concept),)
    )
    expected_citations = tuple(
        HistoricalOraclePairCitation(
            pair=pair,
            availability="unavailable-historical-oracle",
        )
        for pair in affected_pairs
    )
    return PartitionDiagnosisEvidence(
        diagnosis=diagnosis,
        normalization_rule="group-label-independent-co-membership",
        affected_pairs=affected_pairs,
        actual_pair_citations=actual_citations,
        expected_pair_citations=expected_citations,
    )


def _typed_partition(
    comparison: PartitionComparison,
    concept: CurrentConceptEvidence,
) -> CurrentPartitionComparison:
    diagnosis = (
        _typed_diagnosis(comparison.primary_diagnosis, comparison, concept)
        if comparison.primary_diagnosis is not None
        else None
    )
    return CurrentPartitionComparison(
        eligible=comparison.eligible,
        agrees=comparison.agrees,
        expected_partition=comparison.expected_partition,
        actual_partition=comparison.actual_partition,
        missing_pairs=comparison.missing_pairs,
        extra_pairs=comparison.extra_pairs,
        shared_pair_count=comparison.shared_pair_count,
        ineligibility_reason=comparison.ineligibility_reason,
        primary_diagnosis=diagnosis,
    )


def validate_current_comparison(
    evidence: CurrentEngineEvidence, comparison: CurrentComparison
) -> None:
    """Reject a comparison rebound to any different current evidence dimension."""
    checks = (
        ("release", evidence.ncit_version, comparison.ncit_version),
        ("source", evidence.source_identity, comparison.source_identity),
        (
            "manifest",
            evidence.sample_manifest_identity,
            comparison.sample_manifest_identity,
        ),
        ("run", evidence.run_id, comparison.run_id),
        (
            "fingerprint",
            evidence.run_fingerprint_identity,
            comparison.run_fingerprint_identity,
        ),
        ("artifact", evidence.artifact_identity, comparison.artifact_identity),
        (
            "representation",
            evidence.representation_identity,
            comparison.representation_identity,
        ),
        ("detector", evidence.detector_identity, comparison.detector_identity),
        ("oracle", evidence.oracle_identity, comparison.oracle_identity),
        (
            "row decision",
            evidence.row_decision_identity,
            comparison.row_decision_identity,
        ),
        (
            "proposal registry",
            evidence.proposal_registry_identity,
            comparison.proposal_registry_identity,
        ),
        (
            "evidence",
            evidence.evidence_identity,
            comparison.current_evidence_identity,
        ),
    )
    for name, expected, actual in checks:
        if expected != actual:
            raise CurrentEvidenceValidationError(
                f"current comparison {name} identity does not match evidence"
            )


def _canonical_bytes(model: BaseModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            sort_keys=True,
            indent=2,
            ensure_ascii=True,
        )
        + "\n"
    ).encode()


def _write_single_output(output: Path, payload: bytes) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, output)
    finally:
        Path(name).unlink(missing_ok=True)


def _build_current_comparison(
    evidence: CurrentEngineEvidence,
    adjudication: AdjudicationArtifact,
    rows: RowDecisionExport,
    registry: ProposalRegistry,
) -> CurrentComparison:
    adjudicated = adjudication.concepts
    common = {
        field: getattr(evidence, field)
        for field in (
            "schema_version",
            "ncit_version",
            "source_identity",
            "sample_manifest_identity",
            "run_id",
            "run_fingerprint_identity",
            "artifact_identity",
            "representation_identity",
            "detector_identity",
            "oracle_identity",
            "row_decision_identity",
            "proposal_registry_identity",
        )
    }
    metrics, reports = _comparison_payload(adjudicated, evidence)
    row_replay = _row_replay(rows, adjudicated, registry, evidence.concepts)
    comparison_payload = {
        **common,
        "current_evidence_identity": evidence.evidence_identity,
        "metrics": metrics,
        "concepts": reports,
        "row_replay": row_replay,
    }
    comparison = CurrentComparison.model_validate(
        {
            **comparison_payload,
            "comparison_identity": _identity(comparison_payload),
        }
    )
    validate_current_comparison(evidence, comparison)
    return comparison


def regenerate_current_comparison(
    *,
    evidence_path: Path,
    oracle_path: Path,
    row_decisions_path: Path,
    proposal_registry_path: Path,
    output: Path,
) -> CurrentComparison:
    """Regenerate the derived comparison from source-bound tracked evidence."""
    inputs = (
        evidence_path,
        oracle_path,
        row_decisions_path,
        proposal_registry_path,
    )
    for path in inputs:
        if not path.exists():
            raise CurrentEvidenceValidationError(f"input does not exist: {path}")
    if not output.parent.exists():
        raise CurrentEvidenceValidationError(
            f"output parent does not exist: {output.parent}"
        )
    if output.resolve() in {path.resolve() for path in inputs}:
        raise CurrentEvidenceValidationError(
            "comparison output must differ from every input"
        )
    try:
        evidence = CurrentEngineEvidence.model_validate_json(evidence_path.read_bytes())
        registry = load_proposal_registry(proposal_registry_path)
        adjudication = load_adjudication(oracle_path, registry)
        rows = load_row_decisions(row_decisions_path)
    except (ValueError, GoldenSetValidationError) as error:
        raise CurrentEvidenceValidationError(str(error)) from error
    checks = (
        ("oracle", evidence.oracle_identity, adjudication.identity),
        ("row decision", evidence.row_decision_identity, rows.payload_identity),
        (
            "proposal registry",
            evidence.proposal_registry_identity,
            registry.registry_identity,
        ),
    )
    for name, actual, expected in checks:
        if actual != expected:
            raise CurrentEvidenceValidationError(
                f"current evidence {name} does not match comparison input"
            )
    if tuple(item.code for item in evidence.concepts) != tuple(
        item.code for item in adjudication.concepts
    ):
        raise CurrentEvidenceValidationError(
            "current evidence cohort does not match immutable oracle"
        )
    comparison = _build_current_comparison(evidence, adjudication, rows, registry)
    comparison_bytes = _canonical_bytes(comparison)
    CurrentComparison.model_validate_json(comparison_bytes)
    _write_single_output(output, comparison_bytes)
    return comparison


def _write_outputs(
    engine_output: Path,
    engine_bytes: bytes,
    comparison_output: Path,
    comparison_bytes: bytes,
) -> None:
    staged: list[tuple[str, Path]] = []
    originals: list[tuple[Path, bytes | None]] = []
    try:
        for output, payload in (
            (engine_output, engine_bytes),
            (comparison_output, comparison_bytes),
        ):
            descriptor, name = tempfile.mkstemp(
                prefix=f".{output.name}.", dir=output.parent
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            staged.append((name, output))
            originals.append((output, output.read_bytes() if output.exists() else None))
        replaced: list[Path] = []
        try:
            for name, output in staged:
                os.replace(name, output)
                replaced.append(output)
        except BaseException as original:
            original_by_path = dict(originals)
            for output in reversed(replaced):
                previous = original_by_path[output]
                try:
                    if previous is None:
                        output.unlink(missing_ok=True)
                    else:
                        descriptor, rollback_name = tempfile.mkstemp(
                            prefix=f".{output.name}.rollback.", dir=output.parent
                        )
                        with os.fdopen(descriptor, "wb") as stream:
                            stream.write(previous)
                            stream.flush()
                            os.fsync(stream.fileno())
                        try:
                            os.replace(rollback_name, output)
                        finally:
                            Path(rollback_name).unlink(missing_ok=True)
                except BaseException as rollback_error:
                    original.add_note(
                        f"Rolling back {output} also failed: "
                        f"{type(rollback_error).__name__}: {rollback_error}"
                    )
            raise
    finally:
        for name, _output in staged:
            Path(name).unlink(missing_ok=True)


async def generate_current_evidence(
    *,
    sample_manifest: Path,
    oracle: Path,
    row_decisions: Path,
    proposal_registry: Path,
    run_id: str,
    artifact: Path,
    engine_output: Path,
    comparison_output: Path,
    store: CurrentEvidenceStore,
) -> tuple[CurrentEngineEvidence, CurrentComparison]:
    """Validate inputs, derive identities, then publish a pair with error rollback.

    A reported replacement failure triggers an attempt to restore prior outputs; any
    rollback failure is attached explicitly to the raised error. The sequential
    filesystem replacements promise neither crash atomicity nor guaranteed rollback.
    """
    _require_paths(
        (sample_manifest, oracle, row_decisions, proposal_registry, artifact),
        (engine_output, comparison_output),
    )
    try:
        manifest = load_sample_manifest(sample_manifest)
        registry = load_proposal_registry(proposal_registry)
        adjudication = load_adjudication(oracle, registry)
        rows = load_row_decisions(row_decisions)
    except (ValueError, GoldenSetValidationError) as error:
        raise CurrentEvidenceValidationError(str(error)) from error
    oracle_codes = tuple(concept.code for concept in adjudication.concepts)
    if manifest.codes != oracle_codes:
        raise CurrentEvidenceValidationError(
            "current manifest ordered codes do not match immutable oracle cohort"
        )
    row_checks = (
        (
            "source identity",
            rows.meta.source_identity,
            adjudication.meta.source_identity,
        ),
        ("release", rows.meta.ncit_version, adjudication.meta.ncit_version),
        ("run", rows.meta.run_id, adjudication.meta.run_id),
        (
            "engine evidence identity",
            rows.meta.engine_evidence_identity,
            adjudication.meta.engine_evidence_identity,
        ),
        (
            "workbook identity",
            rows.meta.workbook_identity,
            adjudication.meta.workbook_identity,
        ),
    )
    for name, actual, expected in row_checks:
        if actual != expected:
            raise CurrentEvidenceValidationError(
                f"row decision {name} does not match immutable oracle"
            )
    run = await store.completed_run_for_evidence(run_id)
    if run.run_id != run_id:
        raise CurrentEvidenceValidationError(
            "persisted run id does not match requested run id"
        )
    _require_run_matches_manifest(run, manifest)
    if artifact.resolve() != Path(run.publication_artifact_path).resolve():
        raise CurrentEvidenceValidationError(
            "supplied artifact path does not match persisted publication artifact path"
        )
    outcomes = await store.work_item_outcomes(run_id)
    if tuple(item.concept_code for item in outcomes) != manifest.codes:
        raise CurrentEvidenceValidationError("work item outcomes do not match worklist")
    if any(item.state != "complete" or item.outcome is None for item in outcomes):
        raise CurrentEvidenceValidationError("run contains an incomplete work item")
    decomposed_codes = tuple(
        item.concept_code for item in outcomes if item.outcome == "decomposed"
    )
    representation_identity = validate_artifact(
        artifact, expected_codes=decomposed_codes, run_id=run_id
    )
    if representation_identity != run.representation_identity:
        raise CurrentEvidenceValidationError(
            "validated artifact representation identity does not match persisted run"
        )
    concepts = _concepts(outcomes, await store.decompositions_for_run(run_id))
    common = {
        "schema_version": 2,
        "ncit_version": run.ncit_version,
        "source_identity": manifest.source_identity,
        "sample_manifest_identity": manifest.identity,
        "run_id": run_id,
        "run_fingerprint_identity": run.fingerprint.identity,
        "artifact_identity": representation_identity,
        "representation_identity": representation_identity,
        "detector_identity": _detector_identity(run),
        "oracle_identity": adjudication.identity,
        "row_decision_identity": rows.payload_identity,
        "proposal_registry_identity": registry.registry_identity,
    }
    evidence_payload = {**common, "concepts": concepts}
    evidence = CurrentEngineEvidence.model_validate(
        {**evidence_payload, "evidence_identity": _identity(evidence_payload)}
    )
    comparison = _build_current_comparison(evidence, adjudication, rows, registry)
    engine_bytes = _canonical_bytes(evidence)
    comparison_bytes = _canonical_bytes(comparison)
    CurrentEngineEvidence.model_validate_json(engine_bytes)
    CurrentComparison.model_validate_json(comparison_bytes)
    _write_outputs(engine_output, engine_bytes, comparison_output, comparison_bytes)
    return evidence, comparison
