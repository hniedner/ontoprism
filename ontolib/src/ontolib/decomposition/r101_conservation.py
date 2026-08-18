"""Source-bound full-corpus conservation report for the R101 v3 to v4 change."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import tempfile
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ontolib.decomposition.branches import DecompositionBranch, branch_spec
from ontolib.decomposition.corpus_baseline import CorpusBaseline, load_corpus_baseline

if TYPE_CHECKING:
    from pathlib import Path

    from ontolib.decomposition.provenance_models import CompletedRunForEvidence

_SHA256 = r"^[0-9a-f]{64}$"
R101_CONSERVATION_SCHEMA_VERSION = 2
R101_SOURCE_OCCURRENCE_SCHEMA_VERSION = 1
_OLD_ALGORITHM_VERSION = "decomposition-v3"
_NEW_ALGORITHM_VERSION = "decomposition-v4"
_LOCATION_AXES = frozenset(("op:PrimarySite", "op:AssociatedRegion"))
_PROGRESS_HEARTBEAT_SECONDS = 15.0
_R82_BATCH_SIZE = 256


class R101ConservationValidationError(ValueError):
    """The baseline or a changed pair cannot support a conservation claim."""


@dataclass(frozen=True, slots=True)
class R101ConservationProgress:
    phase: Literal["started", "heartbeat", "completed"]
    concept_code: str
    completed: int
    total: int
    elapsed_seconds: float


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class Pair(_StrictModel):
    axis: str = Field(min_length=1)
    filler_code: str = Field(pattern=r"^(?:C[0-9]+|MINT-[0-9a-f]{12})$")

    def __hash__(self) -> int:
        return hash((self.axis, self.filler_code))


class SourceOccurrence(_StrictModel):
    occurrence_id: str = Field(pattern=_SHA256)
    role_code: Literal["R101"]
    filler_code: str = Field(pattern=r"^C[0-9]+$")


class OccurrenceLinks(_StrictModel):
    occurrence_id: str = Field(pattern=_SHA256)
    old_pairs: tuple[Pair, ...]
    new_pairs: tuple[Pair, ...]

    @model_validator(mode="after")
    def _pairs_are_unique(self) -> Self:
        if len(self.old_pairs) != len(set(self.old_pairs)) or len(
            self.new_pairs
        ) != len(set(self.new_pairs)):
            raise ValueError("occurrence links must contain unique pairs")
        return self


class CandidateBaseline(_StrictModel):
    concept_code: str = Field(pattern=r"^C[0-9]+$")
    old_pairs: tuple[Pair, ...]
    new_pairs: tuple[Pair, ...]
    r101_old_pairs: tuple[Pair, ...]
    r101_new_pairs: tuple[Pair, ...] = ()
    source_occurrences: tuple[SourceOccurrence, ...]
    occurrence_links: tuple[OccurrenceLinks, ...] = ()

    @model_validator(mode="after")
    def _is_valid_baseline(self) -> Self:
        if len(self.old_pairs) != len(set(self.old_pairs)):
            raise ValueError("old normalized pairs must be unique")
        if len(self.r101_old_pairs) != len(set(self.r101_old_pairs)):
            raise ValueError("old R101-derived pairs must be unique")
        if len(self.new_pairs) != len(set(self.new_pairs)):
            raise ValueError("new normalized pairs must be unique")
        if not set(self.r101_old_pairs).issubset(self.old_pairs):
            raise ValueError("old R101-derived pairs must be normalized old pairs")
        if not set(self.r101_new_pairs).issubset(self.new_pairs):
            raise ValueError("new R101-derived pairs must be normalized new pairs")
        return self

    def classify_occurrences(
        self,
        *,
        semantic_types: dict[str, str | None],
        live_r82_pairs: tuple[tuple[str, str], ...],
    ) -> tuple[OccurrenceDisposition, ...]:
        return _classify_occurrences(self, semantic_types, live_r82_pairs)

    def conservation_analysis(
        self,
        *,
        semantic_types: dict[str, str | None],
        live_r82_pairs: tuple[tuple[str, str], ...],
    ) -> ConceptConservation:
        return _conservation_analysis(self, semantic_types, live_r82_pairs)


class CandidateAnalysis(_StrictModel):
    concept_code: str = Field(pattern=r"^C[0-9]+$")
    old_pairs: tuple[Pair, ...]
    new_pairs: tuple[Pair, ...]
    source_occurrences: tuple[SourceOccurrence, ...]
    r101_old_pairs: tuple[Pair, ...]
    r101_new_pairs: tuple[Pair, ...]
    occurrence_links: tuple[OccurrenceLinks, ...]
    semantic_types: dict[str, str | None]
    live_r82_pairs: tuple[tuple[str, str], ...]


EvidenceId = Annotated[str, Field(min_length=1)]


class ProjectedDisposition(_StrictModel):
    kind: Literal["projected"] = "projected"
    occurrence_id: str = Field(pattern=_SHA256)
    target_pair: Pair
    evidence_ids: tuple[EvidenceId, ...] = Field(min_length=1)
    review_required: bool = False


class CollapsedR82Disposition(_StrictModel):
    kind: Literal["collapsed-r82"] = "collapsed-r82"
    occurrence_id: str = Field(pattern=_SHA256)
    broader_pair: Pair
    retained_pairs: tuple[Pair, ...] = Field(min_length=1)
    evidence_ids: tuple[EvidenceId, ...] = Field(min_length=1)
    review_required: Literal[False] = False


class UnresolvedLossDisposition(_StrictModel):
    kind: Literal["unresolved-loss"] = "unresolved-loss"
    occurrence_id: str = Field(pattern=_SHA256)
    reason: str = Field(min_length=1)
    evidence_ids: tuple[EvidenceId, ...] = Field(min_length=1)
    review_required: Literal[True] = True


OccurrenceDisposition = Annotated[
    ProjectedDisposition | CollapsedR82Disposition | UnresolvedLossDisposition,
    Field(discriminator="kind"),
]


class PairDelta(_StrictModel):
    change: Literal["added", "removed"]
    pair: Pair


class ConceptConservation(_StrictModel):
    concept_code: str = Field(pattern=r"^C[0-9]+$")
    occurrence_dispositions: tuple[OccurrenceDisposition, ...]
    r101_pair_delta: tuple[PairDelta, ...]
    non_r101_pair_delta: tuple[PairDelta, ...]
    authorizable: bool

    @model_validator(mode="after")
    def _authorization_matches_content(self) -> Self:
        blocked = bool(self.non_r101_pair_delta) or any(
            item.kind == "unresolved-loss" for item in self.occurrence_dispositions
        )
        if self.authorizable == blocked:
            raise ValueError("authorizable does not match conservation content")
        return self


def _pair_delta(old: set[Pair], new: set[Pair]) -> tuple[PairDelta, ...]:
    groups: tuple[tuple[Literal["added", "removed"], set[Pair]], ...] = (
        ("removed", old - new),
        ("added", new - old),
    )
    return tuple(
        PairDelta(change=change, pair=pair)
        for change, pairs in groups
        for pair in sorted(pairs, key=lambda item: (item.axis, item.filler_code))
    )


def _classify_occurrences(
    candidate: CandidateBaseline,
    semantic_types: dict[str, str | None],
    live_r82_pairs: tuple[tuple[str, str], ...],
) -> tuple[OccurrenceDisposition, ...]:
    occurrences, links = _validated_occurrence_inputs(candidate, semantic_types)
    r82 = set(live_r82_pairs)
    return tuple(
        _classify_occurrence(
            occurrence=occurrences[occurrence_id],
            link=links[occurrence_id],
            candidate=candidate,
            semantic_types=semantic_types,
            r82=r82,
        )
        for occurrence_id in sorted(occurrences)
    )


def _validated_occurrence_inputs(
    candidate: CandidateBaseline, semantic_types: dict[str, str | None]
) -> tuple[dict[str, SourceOccurrence], dict[str, OccurrenceLinks]]:
    occurrences = {item.occurrence_id: item for item in candidate.source_occurrences}
    links = {item.occurrence_id: item for item in candidate.occurrence_links}
    _require_unique_occurrence_inputs(candidate, occurrences, links)
    missing_semantics = sorted(
        {item.filler_code for item in candidate.source_occurrences}
        - semantic_types.keys()
    )
    if missing_semantics:
        raise R101ConservationValidationError(
            f"missing semantic lookup keys: {', '.join(missing_semantics)}"
        )
    return occurrences, links


def _require_unique_occurrence_inputs(
    candidate: CandidateBaseline,
    occurrences: dict[str, SourceOccurrence],
    links: dict[str, OccurrenceLinks],
) -> None:
    if len(occurrences) != len(candidate.source_occurrences):
        raise R101ConservationValidationError("source occurrence IDs must be unique")
    links_are_unique = len(links) == len(candidate.occurrence_links)
    if not links_are_unique or set(links) != set(occurrences):
        raise R101ConservationValidationError(
            "every R101 occurrence must have exactly one occurrence link record"
        )


def _is_live_collapse(
    old_pair: Pair,
    new_pair: Pair,
    r82: set[tuple[str, str]],
) -> bool:
    return (
        old_pair.axis == new_pair.axis
        and (
            new_pair.filler_code,
            old_pair.filler_code,
        )
        in r82
    )


def _collapsed_disposition(
    *,
    occurrence_id: str,
    link: OccurrenceLinks,
    r101_new_pairs: tuple[Pair, ...],
    r82: set[tuple[str, str]],
) -> CollapsedR82Disposition | None:
    retained = tuple(
        sorted(
            {
                new_pair
                for old_pair in link.old_pairs
                for new_pair in r101_new_pairs
                if _is_live_collapse(old_pair, new_pair, r82)
            },
            key=lambda item: (item.axis, item.filler_code),
        )
    )
    broader = tuple(
        sorted(link.old_pairs, key=lambda item: (item.axis, item.filler_code))
    )
    if not retained or len(broader) != 1:
        return None
    return CollapsedR82Disposition(
        occurrence_id=occurrence_id,
        broader_pair=broader[0],
        retained_pairs=retained,
        evidence_ids=tuple(
            f"occurrence:{occurrence_id}:live-r82:"
            f"{item.filler_code}:{broader[0].filler_code}"
            for item in retained
        ),
    )


def _classify_occurrence(
    *,
    occurrence: SourceOccurrence,
    link: OccurrenceLinks,
    candidate: CandidateBaseline,
    semantic_types: dict[str, str | None],
    r82: set[tuple[str, str]],
) -> OccurrenceDisposition:
    occurrence_id = occurrence.occurrence_id
    new_pairs = tuple(
        sorted(link.new_pairs, key=lambda item: (item.axis, item.filler_code))
    )
    if len(new_pairs) == 1:
        pair = new_pairs[0]
        return ProjectedDisposition(
            occurrence_id=occurrence_id,
            target_pair=pair,
            evidence_ids=(
                f"persisted:new:{occurrence_id}:{pair.axis}:{pair.filler_code}",
            ),
            review_required=semantic_types[occurrence.filler_code] is None,
        )
    collapsed = _collapsed_disposition(
        occurrence_id=occurrence_id,
        link=link,
        r101_new_pairs=candidate.r101_new_pairs,
        r82=r82,
    )
    if not new_pairs and collapsed is not None:
        return collapsed
    reason = (
        "occurrence projects to multiple normalized pairs"
        if new_pairs
        else "no persisted projection, versioned suppression, or live R82 collapse"
    )
    return UnresolvedLossDisposition(
        occurrence_id=occurrence_id,
        reason=reason,
        evidence_ids=(f"source-occurrence:{occurrence_id}",),
    )


def _conservation_analysis(
    candidate: CandidateBaseline,
    semantic_types: dict[str, str | None],
    live_r82_pairs: tuple[tuple[str, str], ...],
) -> ConceptConservation:
    dispositions = _classify_occurrences(candidate, semantic_types, live_r82_pairs)
    r101_old = set(candidate.r101_old_pairs)
    r101_new = set(candidate.r101_new_pairs)
    non_r101_old = set(candidate.old_pairs) - r101_old
    non_r101_new = set(candidate.new_pairs) - r101_new
    non_r101_delta = _pair_delta(non_r101_old, non_r101_new)
    blocked = bool(non_r101_delta) or any(
        item.kind == "unresolved-loss" for item in dispositions
    )
    return ConceptConservation(
        concept_code=candidate.concept_code,
        occurrence_dispositions=dispositions,
        r101_pair_delta=_pair_delta(r101_old, r101_new),
        non_r101_pair_delta=non_r101_delta,
        authorizable=not blocked,
    )


def validate_r101_conservation_report(
    analyses: tuple[ConceptConservation, ...],
) -> None:
    unresolved = sum(
        item.kind == "unresolved-loss"
        for analysis in analyses
        for item in analysis.occurrence_dispositions
    )
    if unresolved:
        raise R101ConservationValidationError(
            f"unresolved loss blocks authorization ({unresolved} occurrences)"
        )
    non_r101 = sum(len(item.non_r101_pair_delta) for item in analyses)
    if non_r101:
        raise R101ConservationValidationError(
            f"non-R101 pair delta blocks authorization ({non_r101} pairs)"
        )


def require_authorizable_r101_report(
    report: R101ConservationReport | tuple[ConceptConservation, ...],
) -> None:
    concepts = report.concepts if isinstance(report, R101ConservationReport) else report
    validate_r101_conservation_report(concepts)


class R101ConservationReport(_StrictModel):
    schema_version: int
    source_occurrence_schema_version: int
    source_identity: str = Field(pattern=_SHA256)
    ontology_release: str = Field(min_length=1)
    old_run_id: str = Field(min_length=1)
    old_run_fingerprint_identity: str = Field(pattern=_SHA256)
    old_representation_identity: str = Field(pattern=_SHA256)
    old_baseline_identity: str = Field(pattern=_SHA256)
    old_algorithm_version: Literal["decomposition-v3"]
    new_run_id: str = Field(min_length=1)
    new_run_fingerprint_identity: str = Field(pattern=_SHA256)
    new_representation_identity: str = Field(pattern=_SHA256)
    new_algorithm_version: Literal["decomposition-v4"]
    new_detector_identity: str = Field(pattern=_SHA256)
    pre_resume_proof_identity: str = Field(pattern=_SHA256)
    resume_dry_run_identity: str = Field(pattern=_SHA256)
    mixed_cohort_identity: str = Field(pattern=_SHA256)
    candidate_count: int = Field(ge=0)
    occurrence_count: int = Field(ge=0)
    r101_changed_pair_count: int = Field(ge=0)
    non_r101_changed_pair_count: int = Field(ge=0)
    unresolved_loss_count: int = Field(ge=0)
    authorizable: bool
    concepts: tuple[ConceptConservation, ...]
    report_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _payload_is_complete(self) -> Self:
        _validate_report_versions(self)
        _validate_report_counts(self)
        _validate_report_identity(self)
        return self


def _conservation_counts(
    concepts: tuple[ConceptConservation, ...],
) -> tuple[int, int, int, int]:
    return (
        sum(len(item.occurrence_dispositions) for item in concepts),
        sum(len(item.r101_pair_delta) for item in concepts),
        sum(len(item.non_r101_pair_delta) for item in concepts),
        sum(
            disposition.kind == "unresolved-loss"
            for item in concepts
            for disposition in item.occurrence_dispositions
        ),
    )


def _validate_report_versions(report: R101ConservationReport) -> None:
    if report.schema_version != R101_CONSERVATION_SCHEMA_VERSION:
        raise ValueError("unsupported R101 conservation schema version")
    if report.source_occurrence_schema_version != R101_SOURCE_OCCURRENCE_SCHEMA_VERSION:
        raise ValueError("unsupported source occurrence schema version")


def _validate_report_counts(report: R101ConservationReport) -> None:
    if report.candidate_count != len(report.concepts):
        raise ValueError("candidate count does not match concepts")
    expected = _conservation_counts(report.concepts)
    actual = (
        report.occurrence_count,
        report.r101_changed_pair_count,
        report.non_r101_changed_pair_count,
        report.unresolved_loss_count,
    )
    if actual != expected:
        raise ValueError("conservation report counts do not match concepts")
    if report.authorizable != (expected[2] == 0 and expected[3] == 0):
        raise ValueError("report authorization does not match blocking findings")


def _validate_report_identity(report: R101ConservationReport) -> None:
    if report.report_identity != r101_conservation_report_identity(report):
        raise ValueError("R101 conservation report identity does not match payload")


class R101ConservationStore(Protocol):
    async def completed_run_for_evidence(
        self, run_id: str
    ) -> CompletedRunForEvidence: ...

    async def r101_conservation_candidates(
        self, old_run_id: str, new_run_id: str
    ) -> tuple[CandidateBaseline, ...]: ...


ResolveLiveR82 = Callable[[tuple[str, ...]], Awaitable[tuple[tuple[str, str], ...]]]
ResolveSemanticTypes = Callable[[tuple[str, ...]], Awaitable[dict[str, str | None]]]
ProgressCallback = Callable[[R101ConservationProgress], None]


def _identity(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def r101_conservation_detector_identity(
    classifier: Callable[..., object] = _classify_occurrences,
    *,
    report_model: type[object] = R101ConservationReport,
) -> str:
    return _identity(
        {
            "algorithm_version": _NEW_ALGORITHM_VERSION,
            "candidate_rule": "every-stated-r101-source-occurrence-v3",
            "classifier_source": inspect.getsource(classifier),
            "analysis_source": inspect.getsource(_conservation_analysis),
            "report_schema_sources": tuple(
                inspect.getsource(model)
                for model in (
                    report_model,
                    ProjectedDisposition,
                    CollapsedR82Disposition,
                    UnresolvedLossDisposition,
                    ConceptConservation,
                )
            ),
            "source_occurrence_schema_version": R101_SOURCE_OCCURRENCE_SCHEMA_VERSION,
        }
    )


def r101_conservation_report_identity(
    value: R101ConservationReport | dict[str, object],
) -> str:
    payload = (
        value.model_dump(mode="json")
        if isinstance(value, R101ConservationReport)
        else dict(value)
    )
    payload.pop("report_identity", None)

    def serializable(item: object) -> object:
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        if isinstance(item, tuple):
            return [serializable(child) for child in item]
        if isinstance(item, dict):
            return {key: serializable(child) for key, child in item.items()}
        return item

    return _identity(serializable(payload))


def _validate_baseline_run(
    baseline: CorpusBaseline,
    run: CompletedRunForEvidence,
    *,
    run_id: str,
    expected_source_identity: str,
    expected_release: str,
) -> None:
    fingerprint = run.fingerprint
    checks = (
        (run.run_id == run_id == baseline.run_id, "baseline run ID does not match"),
        (
            fingerprint.source_identity
            == expected_source_identity
            == baseline.source_identity,
            "baseline source identity does not match",
        ),
        (
            run.ncit_version == expected_release == baseline.ontology_release,
            "baseline release does not match",
        ),
        (
            run.representation_identity == baseline.representation_identity,
            "baseline representation does not match",
        ),
        (
            fingerprint.algorithm_version == _OLD_ALGORITHM_VERSION,
            "baseline run must use decomposition v3",
        ),
        (
            fingerprint.identity == baseline.run_fingerprint_identity,
            "baseline run fingerprint does not match",
        ),
        (
            fingerprint.branch == DecompositionBranch.NEOPLASM,
            "baseline run must use the neoplasm branch",
        ),
        (fingerprint.total_limit is None, "baseline run cannot have a total limit"),
        (
            fingerprint.sample_manifest_identity is None,
            "baseline run cannot use a sample manifest",
        ),
    )
    for valid, message in checks:
        if not valid:
            raise R101ConservationValidationError(message)


def _validate_replay_fingerprint(
    old: CompletedRunForEvidence,
    new: CompletedRunForEvidence,
    *,
    expected_source_identity: str,
    expected_release: str,
) -> None:
    if new.fingerprint.algorithm_version != _NEW_ALGORITHM_VERSION:
        raise R101ConservationValidationError("new run must use decomposition v4")
    old_dimensions = old.fingerprint.model_dump(
        exclude={"algorithm_version", "emitted_at"}
    )
    new_dimensions = new.fingerprint.model_dump(
        exclude={"algorithm_version", "emitted_at"}
    )
    drift = sorted(
        key for key in old_dimensions if old_dimensions[key] != new_dimensions[key]
    )
    if drift:
        raise R101ConservationValidationError(
            f"new run fingerprint dimension drift: {', '.join(drift)}"
        )
    if (
        new.fingerprint.source_identity != expected_source_identity
        or new.ncit_version != expected_release
    ):
        raise R101ConservationValidationError(
            "new run source or release does not match baseline"
        )


async def analyze_r101_candidates(
    baselines: tuple[CandidateBaseline, ...],
    *,
    semantic_types: dict[str, str | None],
    resolve_live_r82: ResolveLiveR82,
    progress: ProgressCallback | None = None,
    max_concurrency: int = 8,
) -> tuple[CandidateAnalysis, ...]:
    del max_concurrency
    candidates = tuple(sorted(baselines, key=lambda item: item.concept_code))
    started_at = time.monotonic()
    batches = _r82_batches(candidates)
    live_r82 = await _resolve_r82_batches(
        batches,
        resolve_live_r82=resolve_live_r82,
        progress=progress,
        candidates=candidates,
        started_at=started_at,
    )

    def analyze(index: int, candidate: CandidateBaseline) -> CandidateAnalysis:
        _report_progress(
            progress,
            "started",
            candidate.concept_code,
            index,
            len(candidates),
            started_at,
        )
        codes = tuple(
            sorted({item.filler_code for item in candidate.source_occurrences})
        )
        result = CandidateAnalysis(
            concept_code=candidate.concept_code,
            old_pairs=candidate.old_pairs,
            new_pairs=candidate.new_pairs,
            source_occurrences=candidate.source_occurrences,
            r101_old_pairs=candidate.r101_old_pairs,
            r101_new_pairs=candidate.r101_new_pairs,
            occurrence_links=candidate.occurrence_links,
            semantic_types={code: semantic_types[code] for code in codes},
            live_r82_pairs=live_r82,
        )
        _report_progress(
            progress,
            "completed",
            candidate.concept_code,
            index + 1,
            len(candidates),
            started_at,
        )
        return result

    return tuple(analyze(index, item) for index, item in enumerate(candidates))


def _r82_batches(
    candidates: tuple[CandidateBaseline, ...],
) -> tuple[tuple[str, ...], ...]:
    return _pack_r82_groups(_r82_code_groups(candidates))


def _r82_code_groups(
    candidates: tuple[CandidateBaseline, ...],
) -> tuple[tuple[str, ...], ...]:
    groups: list[tuple[str, ...]] = []
    for candidate in candidates:
        if any(not link.new_pairs for link in candidate.occurrence_links):
            groups.append(
                tuple(
                    sorted({item.filler_code for item in candidate.source_occurrences})
                )
            )
    return tuple(groups)


def _pack_r82_groups(
    groups: tuple[tuple[str, ...], ...],
) -> tuple[tuple[str, ...], ...]:
    batches: list[set[str]] = []
    current: set[str] = set()
    for group in groups:
        if len(group) > _R82_BATCH_SIZE:
            raise R101ConservationValidationError(
                f"R101 concept exceeds R82 batch bound of {_R82_BATCH_SIZE} fillers"
            )
        if current and len(current | set(group)) > _R82_BATCH_SIZE:
            batches.append(current)
            current = set()
        current.update(group)
    if current:
        batches.append(current)
    return tuple(tuple(sorted(batch)) for batch in batches)


def _candidate_filler_codes(
    candidates: tuple[CandidateBaseline, ...],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                occurrence.filler_code
                for candidate in candidates
                for occurrence in candidate.source_occurrences
            }
        )
    )


def _concepts_from_analyses(
    candidates: tuple[CandidateBaseline, ...],
    analyses: tuple[CandidateAnalysis, ...],
) -> tuple[ConceptConservation, ...]:
    baselines_by_code = {item.concept_code: item for item in candidates}
    return tuple(
        baselines_by_code[analysis.concept_code].conservation_analysis(
            semantic_types=analysis.semantic_types,
            live_r82_pairs=analysis.live_r82_pairs,
        )
        for analysis in analyses
    )


async def _resolve_r82_batches(
    batches: tuple[tuple[str, ...], ...],
    *,
    resolve_live_r82: ResolveLiveR82,
    progress: ProgressCallback | None,
    candidates: tuple[CandidateBaseline, ...],
    started_at: float,
) -> tuple[tuple[str, str], ...]:
    if not batches:
        return ()
    _report_progress(
        progress,
        "started",
        candidates[0].concept_code,
        0,
        len(candidates),
        started_at,
    )
    resolved: set[tuple[str, str]] = set()
    for batch in batches:
        task = asyncio.ensure_future(resolve_live_r82(batch))
        while not task.done():
            done, _pending = await asyncio.wait(
                {task}, timeout=_PROGRESS_HEARTBEAT_SECONDS
            )
            if not done:
                _report_progress(
                    progress,
                    "heartbeat",
                    candidates[0].concept_code,
                    0,
                    len(candidates),
                    started_at,
                )
        resolved.update(await task)
    return tuple(sorted(resolved))


async def generate_r101_conservation_report(
    *,
    baseline_path: Path,
    run_id: str,
    new_run_id: str,
    expected_source_identity: str,
    expected_release: str,
    pre_resume_proof_identity: str,
    resume_dry_run_identity: str,
    mixed_cohort_identity: str,
    store: R101ConservationStore,
    resolve_semantic_types: ResolveSemanticTypes,
    resolve_live_r82: ResolveLiveR82,
    progress: ProgressCallback | None = None,
) -> R101ConservationReport:
    baseline = load_corpus_baseline(baseline_path)
    run = await store.completed_run_for_evidence(run_id)
    new_run = await store.completed_run_for_evidence(new_run_id)
    _validate_baseline_run(
        baseline,
        run,
        run_id=run_id,
        expected_source_identity=expected_source_identity,
        expected_release=expected_release,
    )
    if (
        branch_spec(DecompositionBranch.NEOPLASM).algorithm_version
        != _NEW_ALGORITHM_VERSION
    ):
        raise R101ConservationValidationError(
            "production algorithm is not decomposition v4"
        )
    _validate_replay_fingerprint(
        run,
        new_run,
        expected_source_identity=expected_source_identity,
        expected_release=expected_release,
    )
    candidates = await store.r101_conservation_candidates(run_id, new_run_id)
    filler_codes = _candidate_filler_codes(candidates)
    semantic_types = await resolve_semantic_types(filler_codes)
    if set(semantic_types) != set(filler_codes):
        raise R101ConservationValidationError(
            "semantic lookup must return every distinct R101 filler"
        )
    analyses = await analyze_r101_candidates(
        candidates,
        semantic_types=semantic_types,
        resolve_live_r82=resolve_live_r82,
        progress=progress,
    )
    concepts = _concepts_from_analyses(candidates, analyses)
    occurrence_count, r101_pair_count, non_r101_pair_count, unresolved_count = (
        _conservation_counts(concepts)
    )
    payload: dict[str, object] = {
        "schema_version": R101_CONSERVATION_SCHEMA_VERSION,
        "source_occurrence_schema_version": R101_SOURCE_OCCURRENCE_SCHEMA_VERSION,
        "source_identity": expected_source_identity,
        "ontology_release": expected_release,
        "old_run_id": run_id,
        "old_run_fingerprint_identity": run.fingerprint.identity,
        "old_representation_identity": run.representation_identity,
        "old_baseline_identity": baseline.baseline_identity,
        "old_algorithm_version": _OLD_ALGORITHM_VERSION,
        "new_run_id": new_run_id,
        "new_run_fingerprint_identity": new_run.fingerprint.identity,
        "new_representation_identity": new_run.representation_identity,
        "new_algorithm_version": _NEW_ALGORITHM_VERSION,
        "new_detector_identity": r101_conservation_detector_identity(),
        "pre_resume_proof_identity": pre_resume_proof_identity,
        "resume_dry_run_identity": resume_dry_run_identity,
        "mixed_cohort_identity": mixed_cohort_identity,
        "candidate_count": len(concepts),
        "occurrence_count": occurrence_count,
        "r101_changed_pair_count": r101_pair_count,
        "non_r101_changed_pair_count": non_r101_pair_count,
        "unresolved_loss_count": unresolved_count,
        "authorizable": non_r101_pair_count == 0 and unresolved_count == 0,
        "concepts": concepts,
    }
    return R101ConservationReport.model_validate(
        {**payload, "report_identity": r101_conservation_report_identity(payload)}
    )


def _report_progress(
    callback: ProgressCallback | None,
    phase: Literal["started", "heartbeat", "completed"],
    concept_code: str,
    completed: int,
    total: int,
    started_at: float,
) -> None:
    if callback is not None:
        callback(
            R101ConservationProgress(
                phase=phase,
                concept_code=concept_code,
                completed=completed,
                total=total,
                elapsed_seconds=time.monotonic() - started_at,
            )
        )


def write_r101_conservation_report(path: Path, report: R101ConservationReport) -> None:
    content = (
        json.dumps(
            report.model_dump(mode="json"),
            sort_keys=True,
            indent=2,
            ensure_ascii=True,
        )
        + "\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}."
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temporary_name)
        raise
