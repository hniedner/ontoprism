"""Per-run accounting for every stated R101 occurrence (D74/D77)."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from itertools import pairwise
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ontolib.decomposition.models import (
    CompleteDefinition,
    Constituent,
    OccurrenceDisposition,
    ResolvedR82PathEdge,
    SourceDefinitionOccurrence,
)

R101ConservationCategory = Literal[
    "projected",
    "unchanged-unprojected",
    "one-step-r82",
    "closure-only-r82",
    "unresolved",
]
R101ConservationReason = Literal[
    "concept-not-decomposed",
    "missing-disposition",
    "retained-routed",
    "retained-unknown",
    "retained-policy-veto",
    "retained-without-projection",
    "collapsed-is-a",
    "collapsed-mixed",
    "collapsed-target-not-projected",
    "retained-r82-path",
    "r82-target-not-projected",
    "missing-r82-path",
    "invalid-r82-path",
]
R101UnchangedReason = Literal["concept-not-decomposed"]
_CLOSURE_MIN_EDGES = 2


class R101PathEdge(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    part_code: str = Field(pattern=r"^C[0-9]+$")
    asserted_part_code: str = Field(pattern=r"^C[0-9]+$")
    whole_code: str = Field(pattern=r"^C[0-9]+$")
    restriction_node_id: str = Field(min_length=1)
    fact_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")


class R101ConservationOccurrence(BaseModel):
    """One source occurrence's per-run conservation category and evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    concept_code: str = Field(pattern=r"^C[0-9]+$")
    occurrence_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_fact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_filler: str = Field(pattern=r"^C[0-9]+$")
    category: R101ConservationCategory
    reason: R101ConservationReason
    r82_path: tuple[R101PathEdge, ...] = ()

    @model_validator(mode="after")
    def _path_matches_category(self) -> Self:
        path_length = len(self.r82_path)
        valid = {
            "one-step-r82": path_length == 1,
            "closure-only-r82": path_length >= _CLOSURE_MIN_EDGES,
        }.get(
            self.category,
            path_length == 0 if self.category != "unresolved" else True,
        )
        if not valid:
            raise ValueError("R101 conservation category has an invalid R82 path")
        return self


class R101ConservationCounts(BaseModel):
    """Run-level D77 category counts derived from occurrence rows."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    total: int = Field(ge=0)
    projected: int = Field(ge=0)
    unchanged_unprojected: int = Field(ge=0)
    one_step_r82: int = Field(ge=0)
    closure_only_r82: int = Field(ge=0)
    unresolved: int = Field(ge=0)

    @model_validator(mode="after")
    def _counts_partition_total(self) -> Self:
        categories = (
            self.projected,
            self.unchanged_unprojected,
            self.one_step_r82,
            self.closure_only_r82,
            self.unresolved,
        )
        if sum(categories) != self.total:
            raise ValueError("R101 conservation categories do not sum to total")
        return self


class R101RunConservation(BaseModel):
    """Complete per-run R101 conservation state."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    counts: R101ConservationCounts
    occurrences: tuple[R101ConservationOccurrence, ...]

    @model_validator(mode="after")
    def _rows_match_counts(self) -> Self:
        if conservation_counts(self.occurrences) != self.counts:
            raise ValueError("R101 conservation rows differ from counts")
        return self

    @property
    def unresolved_occurrences(self) -> tuple[R101ConservationOccurrence, ...]:
        return tuple(item for item in self.occurrences if item.category == "unresolved")


class R101ConservationRecord(Protocol):
    """Persistence-facing shape shared without embedding a Pydantic model in DTOs."""

    @property
    def occurrences(self) -> tuple[R101ConservationOccurrence, ...]: ...


def _retained_projection_exists(
    disposition: OccurrenceDisposition,
    pairs: set[tuple[str, str]],
) -> bool:
    return (disposition.normalized_axis, disposition.retained_filler) in pairs


def _path_is_valid(
    disposition: OccurrenceDisposition, path: tuple[ResolvedR82PathEdge, ...]
) -> bool:
    return (
        bool(path)
        and (
            path[0].part_code,
            path[-1].whole_code,
        )
        == (disposition.retained_filler, disposition.source_filler)
        and all(left.whole_code == right.part_code for left, right in pairwise(path))
    )


@dataclass(frozen=True, slots=True)
class R101ConservationInput:
    occurrence: SourceDefinitionOccurrence
    linked_pairs: frozenset[tuple[str, str]]
    projected_pairs: frozenset[tuple[str, str]]
    disposition: OccurrenceDisposition | None
    unchanged_reason: R101UnchangedReason | None


def _classify_occurrence(
    value: R101ConservationInput,
) -> R101ConservationOccurrence:
    occurrence = value.occurrence

    def row(
        category: R101ConservationCategory,
        reason: R101ConservationReason,
        *,
        r82_path: tuple[R101PathEdge, ...] = (),
    ) -> R101ConservationOccurrence:
        return R101ConservationOccurrence(
            concept_code=occurrence.root_code,
            occurrence_id=occurrence.occurrence_id,
            source_fact_id=occurrence.source_fact_id,
            source_filler=occurrence.filler_code,
            category=category,
            reason=reason,
            r82_path=r82_path,
        )

    if value.unchanged_reason is not None:
        return row("unchanged-unprojected", value.unchanged_reason)
    disposition = value.disposition
    if disposition is None:
        return row("unresolved", "missing-disposition")
    if disposition.kind in {
        "retained-routed",
        "retained-unknown",
        "retained-policy-veto",
    }:
        category, reason = _retained_category(disposition, value.linked_pairs)
        return row(category, reason)
    if disposition.kind == "collapsed-r82":
        category, reason, path = _r82_category(disposition, value.projected_pairs)
        return row(category, reason, r82_path=path)
    if disposition.kind in {"collapsed-is-a", "collapsed-mixed"}:
        if _retained_projection_exists(disposition, set(value.projected_pairs)):
            reason: R101ConservationReason = disposition.kind
            return row("projected", reason)
        return row("unresolved", "collapsed-target-not-projected")
    raise AssertionError(f"unhandled R101 disposition kind: {disposition.kind}")


def _retained_category(
    disposition: OccurrenceDisposition,
    linked_pairs: frozenset[tuple[str, str]],
) -> tuple[R101ConservationCategory, R101ConservationReason]:
    if _retained_projection_exists(disposition, set(linked_pairs)):
        if disposition.kind == "retained-routed":
            return "projected", "retained-routed"
        if disposition.kind == "retained-unknown":
            return "projected", "retained-unknown"
        if disposition.kind == "retained-policy-veto":
            return "projected", "retained-policy-veto"
        raise AssertionError(f"unhandled retained disposition: {disposition.kind}")
    return "unresolved", "retained-without-projection"


def _r82_category(
    disposition: OccurrenceDisposition,
    projected_pairs: frozenset[tuple[str, str]],
) -> tuple[
    R101ConservationCategory,
    R101ConservationReason,
    tuple[R101PathEdge, ...],
]:
    path = tuple(
        R101PathEdge.model_validate(asdict(edge)) for edge in disposition.r82_path
    )
    if not _retained_projection_exists(disposition, set(projected_pairs)):
        return "unresolved", "r82-target-not-projected", path
    if not path:
        return "unresolved", "missing-r82-path", ()
    if not _path_is_valid(disposition, disposition.r82_path):
        return "unresolved", "invalid-r82-path", path
    category: R101ConservationCategory = (
        "one-step-r82" if len(path) == 1 else "closure-only-r82"
    )
    return category, "retained-r82-path", path


def conservation_counts(
    rows: tuple[R101ConservationOccurrence, ...],
) -> R101ConservationCounts:
    categories = Counter(item.category for item in rows)
    return R101ConservationCounts(
        total=len(rows),
        projected=categories["projected"],
        unchanged_unprojected=categories["unchanged-unprojected"],
        one_step_r82=categories["one-step-r82"],
        closure_only_r82=categories["closure-only-r82"],
        unresolved=categories["unresolved"],
    )


def classify_r101_conservation(
    *,
    definition: CompleteDefinition,
    constituents: tuple[Constituent, ...],
    dispositions: tuple[OccurrenceDisposition, ...],
    unchanged_reason: R101UnchangedReason | None = None,
) -> R101RunConservation:
    """Partition every stated R101 occurrence into the D77 categories."""
    _require_unchanged_shape(unchanged_reason, constituents, dispositions)
    rows = tuple(
        _classify_occurrence(value)
        for value in _conservation_inputs(
            definition, constituents, dispositions, unchanged_reason
        )
    )
    return R101RunConservation(
        counts=conservation_counts(rows),
        occurrences=rows,
    )


def _require_unchanged_shape(
    unchanged_reason: R101UnchangedReason | None,
    constituents: tuple[Constituent, ...],
    dispositions: tuple[OccurrenceDisposition, ...],
) -> None:
    if unchanged_reason is not None and (constituents or dispositions):
        raise ValueError(
            "unchanged reason requires empty constituents and dispositions"
        )


def _conservation_inputs(
    definition: CompleteDefinition,
    constituents: tuple[Constituent, ...],
    dispositions: tuple[OccurrenceDisposition, ...],
    unchanged_reason: R101UnchangedReason | None,
) -> tuple[R101ConservationInput, ...]:
    linked_pairs_by_occurrence: dict[str, set[tuple[str, str]]] = {}
    for constituent in constituents:
        for occurrence_id in constituent.source_occurrence_ids:
            linked_pairs_by_occurrence.setdefault(occurrence_id, set()).add(
                (constituent.axis, constituent.filler_code)
            )
    dispositions_by_occurrence = {
        item.source_occurrence_id: item for item in dispositions
    }
    projected_pairs = {
        (constituent.axis, constituent.filler_code) for constituent in constituents
    }
    return tuple(
        R101ConservationInput(
            occurrence=occurrence,
            linked_pairs=frozenset(
                linked_pairs_by_occurrence.get(occurrence.occurrence_id, set())
            ),
            projected_pairs=frozenset(projected_pairs),
            disposition=dispositions_by_occurrence.get(occurrence.occurrence_id),
            unchanged_reason=unchanged_reason,
        )
        for occurrence in definition.occurrences
        if occurrence.role_code == "R101"
    )
