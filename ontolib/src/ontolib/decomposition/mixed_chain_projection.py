"""Bounded corrected projection from persisted mixed-chain evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ontolib.decomposition.filler_selection import (
    CollapseDecision,
    RoutedOccurrence,
    RoutedSelection,
    _constituent_from_routed,
)
from ontolib.decomposition.mixed_chain_inventory import (
    MixedChainCandidate,
    MixedChainPathEdge,
    PersistedSelectorOccurrence,
)
from ontolib.decomposition.models import (
    Constituent,
    OccurrenceDisposition,
    RoleRestriction,
    SpecificityPathEdge,
)

_SHA256 = r"^[0-9a-f]{64}$"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class ConstituentSnapshot(_StrictModel):
    axis: str
    filler_code: str
    axis_source: Literal["role", "parent", "nlp"]
    source_roles: tuple[str, ...]
    most_specific: bool
    needs_review: bool
    group: str | None
    source_definition_ids: tuple[str, ...]
    source_occurrence_ids: tuple[str, ...]

    @classmethod
    def from_constituent(cls, row: Constituent) -> ConstituentSnapshot:
        return cls(
            axis=row.axis,
            filler_code=row.filler_code,
            axis_source=row.axis_source,
            source_roles=row.source_roles,
            most_specific=row.most_specific,
            needs_review=row.needs_review,
            group=row.group,
            source_definition_ids=row.source_definition_ids,
            source_occurrence_ids=row.source_occurrence_ids,
        )


class DispositionSnapshot(_StrictModel):
    kind: str
    source_occurrence_id: str = Field(pattern=_SHA256)
    source_fact_id: str = Field(pattern=_SHA256)
    normalized_axis: str
    source_filler: str
    retained_filler: str
    semantic_route: str
    semantic_type: str | None
    r82_part: str | None
    r82_whole: str | None
    specificity_path: tuple[MixedChainPathEdge, ...]
    policy_decision_identity: str | None

    @classmethod
    def from_disposition(cls, row: OccurrenceDisposition) -> DispositionSnapshot:
        return cls(
            kind=row.kind,
            source_occurrence_id=row.source_occurrence_id,
            source_fact_id=row.source_fact_id,
            normalized_axis=row.normalized_axis,
            source_filler=row.source_filler,
            retained_filler=row.retained_filler,
            semantic_route=row.semantic_route,
            semantic_type=row.semantic_type,
            r82_part=row.r82_part,
            r82_whole=row.r82_whole,
            specificity_path=tuple(
                MixedChainPathEdge(
                    kind=edge.kind,
                    broader_code=edge.broader_code,
                    narrower_code=edge.narrower_code,
                    source_identity=edge.source_identity,
                )
                for edge in row.specificity_path
            ),
            policy_decision_identity=row.policy_decision_identity,
        )


class ConstituentTransition(_StrictModel):
    kind: Literal["added", "removed", "metadata-changed"]
    axis: str
    filler_code: str
    changed_fields: tuple[str, ...]
    before: ConstituentSnapshot | None
    after: ConstituentSnapshot | None


class DispositionTransition(_StrictModel):
    source_occurrence_id: str = Field(pattern=_SHA256)
    changed_fields: tuple[str, ...]
    before: DispositionSnapshot
    after: DispositionSnapshot


class CandidateProjectionSnapshot(_StrictModel):
    concept_code: str
    before_constituents: tuple[ConstituentSnapshot, ...]
    after_constituents: tuple[ConstituentSnapshot, ...]
    before_dispositions: tuple[DispositionSnapshot, ...]
    after_dispositions: tuple[DispositionSnapshot, ...]
    constituent_transitions: tuple[ConstituentTransition, ...]
    disposition_transitions: tuple[DispositionTransition, ...]


class TransitionCounts(_StrictModel):
    added: int = Field(ge=0)
    removed: int = Field(ge=0)
    metadata_changed: int = Field(ge=0)


class MetadataTransitionCounts(_StrictModel):
    most_specific_false_to_true: int = Field(ge=0)
    most_specific_true_to_false: int = Field(ge=0)
    needs_review_false_to_true: int = Field(ge=0)
    needs_review_true_to_false: int = Field(ge=0)
    group_changed: int = Field(ge=0)


class MixedChainCorrectedProjection(_StrictModel):
    """Content-addressed evidence; deliberately not a decomposition run."""

    schema_version: int = 1
    evidence_kind: Literal["corrected-projection-not-a-run"] = (
        "corrected-projection-not-a-run"
    )
    source_run_id: str
    source_report_identity: str = Field(pattern=_SHA256)
    source_identity: str = Field(pattern=_SHA256)
    selector_identity: str = Field(pattern=_SHA256)
    inventory_identity: str = Field(pattern=_SHA256)
    candidate_codes: tuple[str, ...]
    candidate_count: int = Field(ge=1)
    projections: tuple[CandidateProjectionSnapshot, ...]
    constituent_transition_counts: TransitionCounts
    metadata_transition_counts: MetadataTransitionCounts
    disposition_transition_count: int = Field(ge=0)
    projection_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _is_complete_and_identified(self) -> Self:
        projection_codes = tuple(row.concept_code for row in self.projections)
        _require(
            self.candidate_codes == tuple(sorted(set(self.candidate_codes))),
            "projection candidate codes are not canonical",
        )
        _require(
            projection_codes == self.candidate_codes,
            "projection evidence does not cover every candidate",
        )
        _require(
            self.candidate_count == len(self.candidate_codes),
            "projection candidate count differs",
        )
        payload = self.model_dump(mode="json", exclude={"projection_identity"})
        expected = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        _require(
            self.projection_identity == expected,
            "corrected projection identity differs",
        )
        return self


@dataclass(frozen=True, slots=True)
class MixedChainCandidateProjection:
    """Complete before/after output and provenance for one affected concept."""

    concept_code: str
    before_constituents: tuple[Constituent, ...]
    after_constituents: tuple[Constituent, ...]
    before_dispositions: tuple[OccurrenceDisposition, ...]
    after_dispositions: tuple[OccurrenceDisposition, ...]

    @property
    def constituent_transition_counts(self) -> dict[str, int]:
        before = {(row.axis, row.filler_code): row for row in self.before_constituents}
        after = {(row.axis, row.filler_code): row for row in self.after_constituents}
        shared = before.keys() & after.keys()
        return {
            "added": len(after.keys() - before.keys()),
            "removed": len(before.keys() - after.keys()),
            "metadata-changed": sum(before[key] != after[key] for key in shared),
        }


@dataclass(frozen=True, slots=True)
class PersistedProjectionState:
    """Complete persisted output and disposition state for one bounded concept."""

    concept_code: str
    constituents: tuple[Constituent, ...]
    dispositions: tuple[OccurrenceDisposition, ...]


def _materialize_routed_axis_from_dispositions(
    axis_name: str,
    occurrences: tuple[RoutedOccurrence, ...],
    dispositions: tuple[OccurrenceDisposition, ...],
) -> RoutedSelection:
    occurrence_by_id, disposition_by_id = _projection_rows_by_id(
        occurrences, dispositions
    )
    collapsed = _projection_collapse_decisions(
        axis_name, occurrence_by_id, disposition_by_id
    )
    retained = {row.restriction.filler_code for row in occurrences} - collapsed.keys()
    _require_projection_retained_fillers(dispositions, retained)
    constituents = _materialized_constituents(
        axis_name, occurrences, dispositions, collapsed, retained
    )
    return RoutedSelection(
        constituents=constituents,
        dispositions=tuple(
            sorted(dispositions, key=lambda row: row.source_occurrence_id)
        ),
    )


def _projection_rows_by_id(
    occurrences: tuple[RoutedOccurrence, ...],
    dispositions: tuple[OccurrenceDisposition, ...],
) -> tuple[dict[str | None, RoutedOccurrence], dict[str, OccurrenceDisposition]]:
    occurrence_by_id = {
        occurrence.source_occurrence_id: occurrence for occurrence in occurrences
    }
    _require(
        None not in occurrence_by_id and len(occurrence_by_id) == len(occurrences),
        "projected axis occurrences require unique source identities",
    )
    disposition_by_id = {
        disposition.source_occurrence_id: disposition for disposition in dispositions
    }
    _require(
        set(disposition_by_id) == set(occurrence_by_id),
        "projected axis dispositions differ from occurrences",
    )
    return occurrence_by_id, disposition_by_id


def _materialized_constituents(
    axis_name: str,
    occurrences: tuple[RoutedOccurrence, ...],
    dispositions: tuple[OccurrenceDisposition, ...],
    collapsed: dict[str, CollapseDecision],
    retained: set[str],
) -> tuple[Constituent, ...]:
    protected = any(
        disposition.kind == "retained-policy-veto" for disposition in dispositions
    )
    return tuple(
        _constituent_from_routed(
            axis_name,
            filler,
            occurrences,
            len(retained),
            collapsed,
            protected,
        )
        for filler in sorted(retained)
    )


def _projection_collapse_decisions(
    axis_name: str,
    occurrence_by_id: dict[str | None, RoutedOccurrence],
    disposition_by_id: dict[str, OccurrenceDisposition],
) -> dict[str, CollapseDecision]:
    collapsed: dict[str, CollapseDecision] = {}
    for occurrence_id, occurrence in occurrence_by_id.items():
        _require(
            occurrence_id is not None,
            "projected axis occurrence lacks source identity",
        )
        occurrence_id = cast("str", occurrence_id)
        disposition = disposition_by_id[occurrence_id]
        expected = (
            occurrence.source_fact_id,
            occurrence.normalized_axis,
            occurrence.restriction.filler_code,
            occurrence.semantic_route,
            occurrence.semantic_type,
        )
        actual = (
            disposition.source_fact_id,
            disposition.normalized_axis,
            disposition.source_filler,
            disposition.semantic_route,
            disposition.semantic_type,
        )
        _require(
            actual == expected and disposition.normalized_axis == axis_name,
            "projected disposition differs from selector occurrence",
        )
        if disposition.kind.startswith("collapsed-"):
            decision = CollapseDecision(
                retained_filler=disposition.retained_filler,
                relation_kind=disposition.kind,
                specificity_path=disposition.specificity_path,
            )
            existing = collapsed.setdefault(disposition.source_filler, decision)
            _require(
                existing == decision,
                "projected filler has conflicting collapse decisions",
            )
    return collapsed


def _require_projection_retained_fillers(
    dispositions: tuple[OccurrenceDisposition, ...], retained: set[str]
) -> None:
    for disposition in dispositions:
        expected_retained = disposition.source_filler in retained
        _require(
            disposition.kind.startswith("retained-") == expected_retained,
            "projected retained fillers differ from dispositions",
        )
        _require(
            disposition.retained_filler in retained,
            "projected disposition target is not retained",
        )


def _routed_occurrence(row: PersistedSelectorOccurrence) -> RoutedOccurrence:
    return RoutedOccurrence(
        restriction=RoleRestriction(
            role_code=row.source_role,
            filler_code=row.source_filler,
            anchoring_genus=row.anchoring_genus,
            source_definition_ids=(row.source_fact_id,),
            source_occurrence_ids=(row.source_occurrence_id,),
            source_kind="stated",
        ),
        normalized_axis=row.normalized_axis,
        semantic_route=row.semantic_route,
        semantic_type=row.semantic_type,
        source_fact_id=row.source_fact_id,
        source_occurrence_id=row.source_occurrence_id,
    )


def _corrected_disposition(
    disposition: OccurrenceDisposition,
    candidate: MixedChainCandidate,
) -> OccurrenceDisposition:
    if disposition.source_occurrence_id not in candidate.source_occurrence_ids:
        return disposition
    return OccurrenceDisposition(
        kind="collapsed-mixed",
        source_occurrence_id=disposition.source_occurrence_id,
        source_fact_id=disposition.source_fact_id,
        normalized_axis=disposition.normalized_axis,
        source_filler=disposition.source_filler,
        retained_filler=candidate.terminal_filler,
        semantic_route=disposition.semantic_route,
        semantic_type=disposition.semantic_type,
        specificity_path=tuple(
            SpecificityPathEdge(
                kind=edge.kind,
                broader_code=edge.broader_code,
                narrower_code=edge.narrower_code,
                source_identity=edge.source_identity,
            )
            for edge in candidate.specificity_path
        ),
    )


def project_mixed_chain_candidate(
    *,
    candidate: MixedChainCandidate,
    occurrences: tuple[PersistedSelectorOccurrence, ...],
    before_constituents: tuple[Constituent, ...],
    before_dispositions: tuple[OccurrenceDisposition, ...],
    source_identity: str,
) -> MixedChainCandidateProjection:
    """Project one exact mixed-chain correction without hierarchy traversal."""
    _require(
        all(
            edge.source_identity == source_identity
            for edge in candidate.specificity_path
        ),
        "projection path source identity differs",
    )
    axis_occurrences = _candidate_axis_occurrences(candidate, occurrences)
    broad_occurrence_ids = {
        row.source_occurrence_id
        for row in axis_occurrences
        if row.restriction.filler_code == candidate.broad_filler
        and row.restriction.role_code == candidate.source_role
    }
    _require(
        broad_occurrence_ids == set(candidate.source_occurrence_ids),
        "projection candidate occurrences differ",
    )
    axis_dispositions = _candidate_axis_dispositions(candidate, before_dispositions)
    projected_axis = _materialize_routed_axis_from_dispositions(
        candidate.axis, axis_occurrences, axis_dispositions
    )
    after_constituents = _replace_constituent_axis(
        candidate.axis, before_constituents, projected_axis.constituents
    )
    after_dispositions = _replace_disposition_axis(
        candidate.axis, before_dispositions, projected_axis.dispositions
    )
    return MixedChainCandidateProjection(
        concept_code=candidate.concept_code,
        before_constituents=tuple(
            sorted(before_constituents, key=lambda row: (row.axis, row.filler_code))
        ),
        after_constituents=after_constituents,
        before_dispositions=tuple(
            sorted(before_dispositions, key=lambda row: row.source_occurrence_id)
        ),
        after_dispositions=after_dispositions,
    )


def _candidate_axis_occurrences(
    candidate: MixedChainCandidate,
    occurrences: tuple[PersistedSelectorOccurrence, ...],
) -> tuple[RoutedOccurrence, ...]:
    return tuple(
        _routed_occurrence(row)
        for row in occurrences
        if (row.concept_code, row.normalized_axis)
        == (candidate.concept_code, candidate.axis)
    )


def _candidate_axis_dispositions(
    candidate: MixedChainCandidate,
    dispositions: tuple[OccurrenceDisposition, ...],
) -> tuple[OccurrenceDisposition, ...]:
    return tuple(
        _corrected_disposition(row, candidate)
        for row in dispositions
        if row.normalized_axis == candidate.axis
    )


def _replace_constituent_axis(
    axis: str,
    before: tuple[Constituent, ...],
    replacement: tuple[Constituent, ...],
) -> tuple[Constituent, ...]:
    unaffected = tuple(row for row in before if row.axis != axis)
    return tuple(
        sorted(
            (*unaffected, *replacement),
            key=lambda row: (row.axis, row.filler_code),
        )
    )


def _replace_disposition_axis(
    axis: str,
    before: tuple[OccurrenceDisposition, ...],
    replacement: tuple[OccurrenceDisposition, ...],
) -> tuple[OccurrenceDisposition, ...]:
    unaffected = tuple(row for row in before if row.normalized_axis != axis)
    return tuple(
        sorted(
            (*unaffected, *replacement),
            key=lambda row: row.source_occurrence_id,
        )
    )


def _changed_fields(before: BaseModel, after: BaseModel) -> tuple[str, ...]:
    before_payload = before.model_dump(mode="json")
    after_payload = after.model_dump(mode="json")
    return tuple(
        sorted(
            key
            for key in before_payload.keys() | after_payload.keys()
            if before_payload.get(key) != after_payload.get(key)
        )
    )


def _constituent_transitions(
    before: tuple[ConstituentSnapshot, ...],
    after: tuple[ConstituentSnapshot, ...],
) -> tuple[ConstituentTransition, ...]:
    before_by_key = {(row.axis, row.filler_code): row for row in before}
    after_by_key = {(row.axis, row.filler_code): row for row in after}
    transitions: list[ConstituentTransition] = []
    for key in sorted(before_by_key.keys() | after_by_key.keys()):
        old = before_by_key.get(key)
        new = after_by_key.get(key)
        if old == new:
            continue
        kind: Literal["added", "removed", "metadata-changed"]
        if old is None:
            kind = "added"
            changed = tuple(ConstituentSnapshot.model_fields)
        elif new is None:
            kind = "removed"
            changed = tuple(ConstituentSnapshot.model_fields)
        else:
            kind = "metadata-changed"
            changed = _changed_fields(old, new)
        transitions.append(
            ConstituentTransition(
                kind=kind,
                axis=key[0],
                filler_code=key[1],
                changed_fields=changed,
                before=old,
                after=new,
            )
        )
    return tuple(transitions)


def _projection_snapshot(
    projection: MixedChainCandidateProjection,
) -> CandidateProjectionSnapshot:
    before_constituents = tuple(
        ConstituentSnapshot.from_constituent(row)
        for row in projection.before_constituents
    )
    after_constituents = tuple(
        ConstituentSnapshot.from_constituent(row)
        for row in projection.after_constituents
    )
    before_dispositions = tuple(
        DispositionSnapshot.from_disposition(row)
        for row in projection.before_dispositions
    )
    after_dispositions = tuple(
        DispositionSnapshot.from_disposition(row)
        for row in projection.after_dispositions
    )
    disposition_transitions = _disposition_transitions(
        before_dispositions, after_dispositions
    )
    return CandidateProjectionSnapshot(
        concept_code=projection.concept_code,
        before_constituents=before_constituents,
        after_constituents=after_constituents,
        before_dispositions=before_dispositions,
        after_dispositions=after_dispositions,
        constituent_transitions=_constituent_transitions(
            before_constituents, after_constituents
        ),
        disposition_transitions=disposition_transitions,
    )


def _disposition_transitions(
    before: tuple[DispositionSnapshot, ...],
    after: tuple[DispositionSnapshot, ...],
) -> tuple[DispositionTransition, ...]:
    before_by_id = {row.source_occurrence_id: row for row in before}
    after_by_id = {row.source_occurrence_id: row for row in after}
    _require(
        before_by_id.keys() == after_by_id.keys(),
        "projection disposition occurrence inventory differs",
    )
    return tuple(
        DispositionTransition(
            source_occurrence_id=occurrence_id,
            changed_fields=_changed_fields(
                before_by_id[occurrence_id], after_by_id[occurrence_id]
            ),
            before=before_by_id[occurrence_id],
            after=after_by_id[occurrence_id],
        )
        for occurrence_id in sorted(before_by_id)
        if before_by_id[occurrence_id] != after_by_id[occurrence_id]
    )


def create_corrected_projection(
    *,
    source_run_id: str,
    source_report_identity: str,
    source_identity: str,
    selector_identity: str,
    inventory_identity: str,
    expected_candidate_codes: tuple[str, ...],
    projections: tuple[MixedChainCandidateProjection, ...],
) -> MixedChainCorrectedProjection:
    """Create strict, complete, content-addressed corrected-projection evidence."""
    snapshots = tuple(
        sorted(
            (_projection_snapshot(row) for row in projections),
            key=lambda row: row.concept_code,
        )
    )
    candidate_codes = tuple(row.concept_code for row in snapshots)
    _require(
        candidate_codes == expected_candidate_codes,
        "projection does not cover exact expected candidate codes",
    )
    transitions = _all_constituent_transitions(snapshots)
    counts = _transition_counts(transitions)
    metadata_counts = _metadata_transition_counts(transitions)
    payload = _corrected_projection_payload(
        source_run_id=source_run_id,
        source_report_identity=source_report_identity,
        source_identity=source_identity,
        selector_identity=selector_identity,
        inventory_identity=inventory_identity,
        candidate_codes=candidate_codes,
        snapshots=snapshots,
        counts=counts,
        metadata_counts=metadata_counts,
    )
    canonical = json.loads(
        json.dumps(
            payload,
            default=lambda value: value.model_dump(mode="json"),
            sort_keys=True,
        )
    )
    identity = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    canonical["projection_identity"] = identity
    return MixedChainCorrectedProjection.model_validate_json(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    )


def _all_constituent_transitions(
    snapshots: tuple[CandidateProjectionSnapshot, ...],
) -> tuple[ConstituentTransition, ...]:
    return tuple(
        transition
        for projection in snapshots
        for transition in projection.constituent_transitions
    )


def _transition_counts(
    transitions: tuple[ConstituentTransition, ...],
) -> TransitionCounts:
    return TransitionCounts(
        added=sum(row.kind == "added" for row in transitions),
        removed=sum(row.kind == "removed" for row in transitions),
        metadata_changed=sum(row.kind == "metadata-changed" for row in transitions),
    )


def _metadata_transition_counts(
    transitions: tuple[ConstituentTransition, ...],
) -> MetadataTransitionCounts:
    metadata = tuple(
        row
        for row in transitions
        if row.kind == "metadata-changed"
        and row.before is not None
        and row.after is not None
    )
    pairs = tuple(
        (
            cast("ConstituentSnapshot", row.before),
            cast("ConstituentSnapshot", row.after),
        )
        for row in metadata
    )
    return MetadataTransitionCounts(
        most_specific_false_to_true=_boolean_transition_count(
            pairs, "most_specific", False, True
        ),
        most_specific_true_to_false=_boolean_transition_count(
            pairs, "most_specific", True, False
        ),
        needs_review_false_to_true=_boolean_transition_count(
            pairs, "needs_review", False, True
        ),
        needs_review_true_to_false=_boolean_transition_count(
            pairs, "needs_review", True, False
        ),
        group_changed=sum(before.group != after.group for before, after in pairs),
    )


def _boolean_transition_count(
    pairs: tuple[tuple[ConstituentSnapshot, ConstituentSnapshot], ...],
    field: Literal["most_specific", "needs_review"],
    before_value: bool,
    after_value: bool,
) -> int:
    return sum(
        getattr(before, field) is before_value and getattr(after, field) is after_value
        for before, after in pairs
    )


def _corrected_projection_payload(
    *,
    source_run_id: str,
    source_report_identity: str,
    source_identity: str,
    selector_identity: str,
    inventory_identity: str,
    candidate_codes: tuple[str, ...],
    snapshots: tuple[CandidateProjectionSnapshot, ...],
    counts: TransitionCounts,
    metadata_counts: MetadataTransitionCounts,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "evidence_kind": "corrected-projection-not-a-run",
        "source_run_id": source_run_id,
        "source_report_identity": source_report_identity,
        "source_identity": source_identity,
        "selector_identity": selector_identity,
        "inventory_identity": inventory_identity,
        "candidate_codes": candidate_codes,
        "candidate_count": len(candidate_codes),
        "projections": snapshots,
        "constituent_transition_counts": counts,
        "metadata_transition_counts": metadata_counts,
        "disposition_transition_count": sum(
            len(row.disposition_transitions) for row in snapshots
        ),
    }


def write_corrected_projection(
    path: Path, projection: MixedChainCorrectedProjection
) -> None:
    """Write canonical corrected-projection evidence after strict validation."""
    validated = MixedChainCorrectedProjection.model_validate(projection.model_dump())
    path.write_text(
        json.dumps(validated.model_dump(mode="json"), sort_keys=True, indent=2) + "\n"
    )


def load_corrected_projection(path: Path) -> MixedChainCorrectedProjection:
    """Load strict content-addressed corrected-projection evidence."""
    return MixedChainCorrectedProjection.model_validate_json(path.read_bytes())
