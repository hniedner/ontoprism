"""Human-review packet for normalized relationship-group disagreements."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Annotated, Literal, Self, cast

from pydantic import Field, model_validator

from ontolib.common.boundary_models import StrictFrozenBoundaryModel
from ontolib.decomposition.evaluation import (
    PartitionComparison,
    compare_common_pair_partition,
    compare_full_partition,
    grouping_difference_pairs,
)

try:
    from scripts.research.current_evidence import (
        CurrentComparison,
        CurrentConceptComparison,
        CurrentConceptEvidence,
        CurrentEngineEvidence,
        CurrentMetrics,
        CurrentPartitionComparison,
        validate_current_comparison,
    )
except ModuleNotFoundError:  # direct `python scripts/adjudication.py` entry point
    from research.current_evidence import (  # type: ignore[no-redef]
        CurrentComparison,
        CurrentConceptComparison,
        CurrentConceptEvidence,
        CurrentEngineEvidence,
        CurrentMetrics,
        CurrentPartitionComparison,
        validate_current_comparison,
    )

_SHA256 = r"^[0-9a-f]{64}$"
_HISTORICAL_AGREEMENTS = 2
_HISTORICAL_COHORT = 20
_MIN_GROUPING_PAIRS = 2

Pair = tuple[str, str]
Partition = tuple[tuple[Pair, ...], ...]
RuleKind = Literal[
    "co-assertion-preservation",
    "routing",
    "specificity-collapse",
    "repeated-pairs",
    "reviewed-regrouping",
]


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


class HistoricalAgreement(StrictFrozenBoundaryModel):
    numerator: int = Field(ge=0)
    denominator: int = Field(gt=0)
    provenance: Literal["historical-57"]


class SourceOccurrenceDocument(StrictFrozenBoundaryModel):
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


class ControlConcept(StrictFrozenBoundaryModel):
    code: str = Field(pattern=r"^C[0-9]+$")
    outcome: Literal["semantic-excluded", "atomic-no-op"]
    actual_pair_count: Literal[0]
    interpretation: Literal["empty-partition-control-not-grouping-success"]


class ReviewCohort(StrictFrozenBoundaryModel):
    accepted_concept_count: int = Field(gt=0)
    outcome_counts: dict[str, int]
    decomposed_codes: tuple[str, ...]
    controls: tuple[ControlConcept, ...]
    full_disagreement_codes: tuple[str, ...]
    common_pair_eligible_codes: tuple[str, ...]
    common_pair_ineligible_codes: tuple[str, ...]
    highest_fanout_code: str = Field(pattern=r"^C[0-9]+$")
    highest_fanout_occurrences: int = Field(ge=0)


class PairDeltaDiagnosis(StrictFrozenBoundaryModel):
    status: Literal[
        "no-pair-delta", "missing-pair", "extra-pair", "missing-and-extra-pairs"
    ]
    missing_pairs: tuple[Pair, ...]
    extra_pairs: tuple[Pair, ...]

    @model_validator(mode="after")
    def _status_matches_pairs(self) -> Self:
        expected = (
            "missing-and-extra-pairs"
            if self.missing_pairs and self.extra_pairs
            else "missing-pair"
            if self.missing_pairs
            else "extra-pair"
            if self.extra_pairs
            else "no-pair-delta"
        )
        if self.status != expected:
            raise ValueError("pair delta status does not match pair sets")
        return self


class GroupingDiagnosis(StrictFrozenBoundaryModel):
    kind: Literal[
        "agrees-on-common-pairs",
        "over-merge",
        "over-split",
        "misassignment",
        "ineligible-zero-shared-pairs",
        "ineligible-one-shared-pair",
    ]
    affected_pairs: tuple[Pair, ...]

    @model_validator(mode="after")
    def _affected_pair_shape(self) -> Self:
        defect = self.kind in {"over-merge", "over-split", "misassignment"}
        if defect != (len(self.affected_pairs) >= _MIN_GROUPING_PAIRS):
            raise ValueError("grouping diagnosis affected pairs are inconsistent")
        return self


class CorrectionBlockedDisposition(StrictFrozenBoundaryModel):
    status: Literal["correction-blocked"]
    reason: Literal["missing-transformation-rule-evidence"]
    acceptance: Literal["not-recorded"]


class HumanReviewPendingDisposition(StrictFrozenBoundaryModel):
    status: Literal["human-review-pending"]
    proposed_diagnosis: Literal["intentionally-normalized"]
    acceptance: Literal["not-recorded"]


ReviewDisposition = Annotated[
    CorrectionBlockedDisposition | HumanReviewPendingDisposition,
    Field(discriminator="status"),
]


class HistoricalSourceEvidence(StrictFrozenBoundaryModel):
    availability: Literal["unavailable-historical-oracle"]


class TransformationEvidenceUnavailable(StrictFrozenBoundaryModel):
    status: Literal["unavailable-upstream"]
    required_rules: tuple[RuleKind, ...] = Field(min_length=1)


class ActualPairEvidence(StrictFrozenBoundaryModel):
    availability: Literal["available"]
    pair: Pair
    occurrence_ids: tuple[str, ...] = Field(min_length=1)
    occurrences: tuple[SourceOccurrenceDocument, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _citations_match(self) -> Self:
        if self.occurrence_ids != tuple(
            item.occurrence_id for item in self.occurrences
        ):
            raise ValueError("pair occurrence IDs do not match cited occurrences")
        if any(item.filler_code != self.pair[1] for item in self.occurrences):
            raise ValueError("pair filler does not match cited source occurrence")
        return self


class ActualPairEvidenceUnavailable(StrictFrozenBoundaryModel):
    availability: Literal["unavailable-upstream"]
    pair: Pair
    reason: Literal["source-occurrence-unavailable-upstream"]


ActualPairEvidenceDocument = Annotated[
    ActualPairEvidence | ActualPairEvidenceUnavailable,
    Field(discriminator="availability"),
]


class ExpectedNormalizedGroup(StrictFrozenBoundaryModel):
    normalized_group_id: str = Field(pattern=_SHA256)
    pairs: tuple[Pair, ...] = Field(min_length=1)
    source_evidence: HistoricalSourceEvidence


class ActualNormalizedGroup(StrictFrozenBoundaryModel):
    normalized_group_id: str = Field(pattern=_SHA256)
    source_group_ids: tuple[str, ...]
    pairs: tuple[ActualPairEvidenceDocument, ...] = Field(min_length=1)
    transformation_evidence: TransformationEvidenceUnavailable

    @model_validator(mode="after")
    def _group_identities_are_distinct(self) -> Self:
        if self.normalized_group_id in self.source_group_ids:
            raise ValueError("normalized group identity aliases source group identity")
        cited_groups = tuple(
            sorted(
                {
                    item.source_group_id
                    for pair in self.pairs
                    if isinstance(pair, ActualPairEvidence)
                    for item in pair.occurrences
                }
            )
        )
        if self.source_group_ids != cited_groups:
            raise ValueError("source group identities do not match cited occurrences")
        return self


class TransformationRuleCatalogEntry(StrictFrozenBoundaryModel):
    kind: RuleKind
    evidence_status: Literal["required-but-unavailable-upstream"]


class ReviewBoundary(StrictFrozenBoundaryModel):
    status: Literal["blocked"]
    reason: Literal["missing-transformation-rule-evidence"]
    sme_adjudication: Literal["not-recorded"]
    dependency: Literal["#267"]


class GroupReviewConcept(StrictFrozenBoundaryModel):
    code: str = Field(pattern=r"^C[0-9]+$")
    expected_partition: Partition
    actual_partition: Partition
    pair_delta: PairDeltaDiagnosis
    common_pair_eligible: bool
    common_pair_agrees: bool | None
    grouping_diagnosis: GroupingDiagnosis
    expected_groups: tuple[ExpectedNormalizedGroup, ...]
    actual_groups: tuple[ActualNormalizedGroup, ...]
    disposition: ReviewDisposition

    @model_validator(mode="after")
    def _partitions_and_diagnoses_are_consistent(self) -> Self:
        expected_rows = _partition_rows(self.expected_partition)
        actual_rows = _partition_rows(self.actual_partition)
        full = compare_full_partition(expected_rows, actual_rows)
        common = compare_common_pair_partition(expected_rows, actual_rows)
        if (
            full.missing_pairs != self.pair_delta.missing_pairs
            or full.extra_pairs != self.pair_delta.extra_pairs
        ):
            raise ValueError("pair delta does not match normalized partitions")
        expected_kind, affected = _grouping_diagnosis(common)
        if (self.grouping_diagnosis.kind, self.grouping_diagnosis.affected_pairs) != (
            expected_kind,
            affected,
        ):
            raise ValueError("grouping diagnosis does not match normalized partitions")
        expected_pairs = tuple(
            sorted(pair for block in self.expected_partition for pair in block)
        )
        documented_expected = tuple(
            sorted(pair for group in self.expected_groups for pair in group.pairs)
        )
        actual_pairs = tuple(
            sorted(pair for block in self.actual_partition for pair in block)
        )
        documented_actual = tuple(
            sorted(pair.pair for group in self.actual_groups for pair in group.pairs)
        )
        if expected_pairs != documented_expected or actual_pairs != documented_actual:
            raise ValueError("normalized group documents do not match partitions")
        return self


class GroupReviewPacket(StrictFrozenBoundaryModel):
    schema_version: Literal[1]
    source_identity: str = Field(pattern=_SHA256)
    ncit_version: str
    current_evidence_identity: str = Field(pattern=_SHA256)
    current_comparison_identity: str = Field(pattern=_SHA256)
    historical_full_partition_agreement: HistoricalAgreement
    current_metrics: CurrentMetrics
    cohort: ReviewCohort
    transformation_rule_catalog: tuple[TransformationRuleCatalogEntry, ...] = Field(
        min_length=5, max_length=5
    )
    review_boundary: ReviewBoundary
    concepts: tuple[GroupReviewConcept, ...] = Field(min_length=1)
    packet_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_packet(self) -> Self:
        concept_codes = tuple(item.code for item in self.concepts)
        if concept_codes != self.cohort.full_disagreement_codes:
            raise ValueError("packet concepts do not match full disagreement cohort")
        expected = _identity(self.model_dump(mode="json", exclude={"packet_identity"}))
        if self.packet_identity != expected:
            raise ValueError("group review packet identity does not match payload")
        return self


def _partition_rows(partition: Partition) -> tuple[tuple[Pair, str], ...]:
    return tuple(
        (pair, f"group-{index}")
        for index, block in enumerate(partition)
        for pair in block
    )


def _grouping_diagnosis(
    comparison: PartitionComparison | CurrentPartitionComparison,
) -> tuple[
    Literal[
        "agrees-on-common-pairs",
        "over-merge",
        "over-split",
        "misassignment",
        "ineligible-zero-shared-pairs",
        "ineligible-one-shared-pair",
    ],
    tuple[Pair, ...],
]:
    eligible = comparison.eligible
    if not eligible:
        if comparison.ineligibility_reason == "zero-shared-pairs":
            return "ineligible-zero-shared-pairs", ()
        return "ineligible-one-shared-pair", ()
    if comparison.agrees:
        return "agrees-on-common-pairs", ()
    affected = grouping_difference_pairs(
        comparison.expected_partition, comparison.actual_partition
    )
    # The comparison has already induced the partitions onto shared pairs.
    diagnosis = comparison.primary_diagnosis
    if diagnosis is None:
        raise ValueError("disagreeing eligible partition lacks a diagnosis")
    if isinstance(comparison, CurrentPartitionComparison):
        typed = comparison.primary_diagnosis
        if typed is None:
            raise ValueError("disagreeing eligible partition lacks a diagnosis")
        return typed.diagnosis.value, affected
    return comparison.primary_diagnosis.value, affected  # type: ignore[union-attr]


def diagnose_grouping(expected: Partition, actual: Partition) -> GroupingDiagnosis:
    """Return the total common-pair diagnosis for two normalized partitions."""
    comparison = compare_common_pair_partition(
        _partition_rows(expected), _partition_rows(actual)
    )
    kind, affected = _grouping_diagnosis(comparison)
    return GroupingDiagnosis(kind=kind, affected_pairs=affected)


def _pair_delta(
    missing: tuple[Pair, ...], extra: tuple[Pair, ...]
) -> PairDeltaDiagnosis:
    status = (
        "missing-and-extra-pairs"
        if missing and extra
        else "missing-pair"
        if missing
        else "extra-pair"
        if extra
        else "no-pair-delta"
    )
    return PairDeltaDiagnosis(status=status, missing_pairs=missing, extra_pairs=extra)


def _occurrence_document(value: object) -> SourceOccurrenceDocument:
    model_dump = getattr(value, "model_dump", None)
    if model_dump is None:
        raise TypeError("source occurrence must be a boundary model")
    return SourceOccurrenceDocument.model_validate(model_dump())


def _expected_groups(
    code: str, partition: Partition
) -> tuple[ExpectedNormalizedGroup, ...]:
    return tuple(
        ExpectedNormalizedGroup(
            normalized_group_id=_identity(
                {"stage": "historical-expected", "code": code, "pairs": block}
            ),
            pairs=block,
            source_evidence=HistoricalSourceEvidence(
                availability="unavailable-historical-oracle"
            ),
        )
        for block in partition
    )


def _actual_groups(
    concept: CurrentConceptEvidence, partition: Partition
) -> tuple[ActualNormalizedGroup, ...]:
    by_pair = {(item.axis, item.filler): item for item in concept.constituents}
    result: list[ActualNormalizedGroup] = []
    for block in partition:
        pairs: list[ActualPairEvidenceDocument] = []
        for pair in block:
            constituent = by_pair.get(pair)
            if constituent is None:
                raise ValueError(
                    "actual normalized partition pair is absent from evidence"
                )
            if constituent.source_occurrences:
                pairs.append(
                    ActualPairEvidence(
                        availability="available",
                        pair=pair,
                        occurrence_ids=constituent.source_occurrence_ids,
                        occurrences=tuple(
                            _occurrence_document(item)
                            for item in constituent.source_occurrences
                        ),
                    )
                )
            else:
                pairs.append(
                    ActualPairEvidenceUnavailable(
                        availability="unavailable-upstream",
                        pair=pair,
                        reason="source-occurrence-unavailable-upstream",
                    )
                )
        source_groups = tuple(
            sorted(
                {
                    item.source_group_id
                    for pair in pairs
                    if isinstance(pair, ActualPairEvidence)
                    for item in pair.occurrences
                }
            )
        )
        result.append(
            ActualNormalizedGroup(
                normalized_group_id=_identity(
                    {
                        "stage": "current-normalized",
                        "code": concept.code,
                        "pairs": block,
                    }
                ),
                source_group_ids=source_groups,
                pairs=tuple(pairs),
                transformation_evidence=TransformationEvidenceUnavailable(
                    status="unavailable-upstream",
                    required_rules=(
                        "co-assertion-preservation",
                        "routing",
                        "specificity-collapse",
                        "repeated-pairs",
                        "reviewed-regrouping",
                    ),
                ),
            )
        )
    return tuple(result)


def _validate_actual_partition(
    comparison: CurrentConceptComparison, evidence: CurrentConceptEvidence
) -> None:
    rows = tuple(
        ((item.axis, item.filler), item.relationship_group)
        for item in evidence.constituents
        if not item.needs_review and item.provenance_status == "ncit-26.07d"
    )
    actual = compare_full_partition(rows, rows).actual_partition
    if comparison.full_partition.actual_partition != actual:
        raise ValueError("actual normalized partition does not match current evidence")


def _concept_packet(
    comparison: CurrentConceptComparison,
    evidence: CurrentConceptEvidence,
) -> GroupReviewConcept:
    _validate_actual_partition(comparison, evidence)
    full = comparison.full_partition
    common = comparison.common_pair_partition
    return GroupReviewConcept(
        code=comparison.code,
        expected_partition=full.expected_partition,
        actual_partition=full.actual_partition,
        pair_delta=_pair_delta(full.missing_pairs, full.extra_pairs),
        common_pair_eligible=common.eligible,
        common_pair_agrees=common.agrees,
        grouping_diagnosis=diagnose_grouping(
            common.expected_partition, common.actual_partition
        ),
        expected_groups=_expected_groups(comparison.code, full.expected_partition),
        actual_groups=_actual_groups(evidence, full.actual_partition),
        disposition=CorrectionBlockedDisposition(
            status="correction-blocked",
            reason="missing-transformation-rule-evidence",
            acceptance="not-recorded",
        ),
    )


def _validate_inputs(
    evidence: CurrentEngineEvidence, comparison: CurrentComparison
) -> None:
    validate_current_comparison(evidence, comparison)
    evidence_codes = tuple(item.code for item in evidence.concepts)
    comparison_codes = tuple(item.code for item in comparison.concepts)
    if len(evidence_codes) != len(set(evidence_codes)):
        raise ValueError("current evidence contains duplicate concepts")
    if len(comparison_codes) != len(set(comparison_codes)):
        raise ValueError("current comparison contains duplicate concepts")
    if set(comparison_codes) != set(evidence_codes):
        raise ValueError("current comparison concepts do not match current evidence")


def build_group_review_packet(
    *, evidence: CurrentEngineEvidence, comparison: CurrentComparison
) -> GroupReviewPacket:
    """Derive the current disagreement packet without making an SME decision."""
    _validate_inputs(evidence, comparison)
    by_code = {item.code: item for item in evidence.concepts}
    disagreements = tuple(
        item for item in comparison.concepts if item.full_partition.agrees is False
    )
    controls = tuple(
        ControlConcept(
            code=item.code,
            outcome=cast("Literal['semantic-excluded', 'atomic-no-op']", item.outcome),
            actual_pair_count=cast("Literal[0]", len(item.constituents)),
            interpretation="empty-partition-control-not-grouping-success",
        )
        for item in evidence.concepts
        if item.outcome in {"semantic-excluded", "atomic-no-op"}
    )
    highest = max(
        evidence.concepts,
        key=lambda item: (len(item.all_source_occurrences), item.code),
    )
    payload = {
        "schema_version": 1,
        "source_identity": comparison.source_identity,
        "ncit_version": comparison.ncit_version,
        "current_evidence_identity": evidence.evidence_identity,
        "current_comparison_identity": comparison.comparison_identity,
        "historical_full_partition_agreement": HistoricalAgreement(
            numerator=_HISTORICAL_AGREEMENTS,
            denominator=_HISTORICAL_COHORT,
            provenance="historical-57",
        ),
        "current_metrics": comparison.metrics,
        "cohort": ReviewCohort(
            accepted_concept_count=len(comparison.concepts),
            outcome_counts=dict(
                sorted(Counter(item.outcome for item in evidence.concepts).items())
            ),
            decomposed_codes=tuple(
                item.code for item in evidence.concepts if item.outcome == "decomposed"
            ),
            controls=controls,
            full_disagreement_codes=tuple(item.code for item in disagreements),
            common_pair_eligible_codes=tuple(
                item.code
                for item in comparison.concepts
                if item.common_pair_partition.eligible
            ),
            common_pair_ineligible_codes=tuple(
                item.code
                for item in comparison.concepts
                if not item.common_pair_partition.eligible
            ),
            highest_fanout_code=highest.code,
            highest_fanout_occurrences=len(highest.all_source_occurrences),
        ),
        "transformation_rule_catalog": tuple(
            TransformationRuleCatalogEntry(
                kind=kind, evidence_status="required-but-unavailable-upstream"
            )
            for kind in (
                "co-assertion-preservation",
                "routing",
                "specificity-collapse",
                "repeated-pairs",
                "reviewed-regrouping",
            )
        ),
        "review_boundary": ReviewBoundary(
            status="blocked",
            reason="missing-transformation-rule-evidence",
            sme_adjudication="not-recorded",
            dependency="#267",
        ),
        "concepts": tuple(
            _concept_packet(item, by_code[item.code]) for item in disagreements
        ),
    }
    return GroupReviewPacket(**payload, packet_identity=_identity(payload))


def load_group_review_packet(path: Path) -> GroupReviewPacket:
    """Load and identity-check a persisted review packet."""
    return GroupReviewPacket.model_validate_json(path.read_bytes())


def _write_packet(path: Path, packet: GroupReviewPacket) -> None:
    payload = (
        json.dumps(
            packet.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
        ).encode()
        + b"\n"
    )
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def generate_group_review_packet(
    *, evidence_path: Path, comparison_path: Path, output: Path
) -> GroupReviewPacket:
    """Generate canonical JSON from the validated current evidence pair."""
    for path in (evidence_path, comparison_path):
        if not path.is_file():
            raise ValueError(f"input does not exist: {path}")
    if not output.parent.is_dir():
        raise ValueError(f"output parent does not exist: {output.parent}")
    if output.resolve() in {evidence_path.resolve(), comparison_path.resolve()}:
        raise ValueError("output must differ from inputs")
    packet = build_group_review_packet(
        evidence=CurrentEngineEvidence.model_validate_json(evidence_path.read_bytes()),
        comparison=CurrentComparison.model_validate_json(comparison_path.read_bytes()),
    )
    _write_packet(output, packet)
    if load_group_review_packet(output) != packet:
        raise ValueError("persisted group review packet did not round trip")
    return packet
