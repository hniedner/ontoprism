"""Filler selection — choose the intended constituent(s) per axis (design §6).

Working from the *stated* graph already eliminates most ancestor bleed; most-specific
selection is defense-in-depth for hierarchy-comparable axes that still return multiple
fillers. The selection is a pure function of the fillers and an injected
``is_ancestor`` predicate, so it is fully unit-testable without a store.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

from ontolib.decomposition import axes
from ontolib.decomposition.axis_contracts import normalized_axis_for_role
from ontolib.decomposition.models import (
    Constituent,
    OccurrenceDisposition,
    R101DispositionKind,
    RoleRestriction,
    SemanticRoute,
    SpecificityPathEdge,
    SpecificityRelationKind,
)
from ontolib.decomposition.projection_validity import (
    ProjectionAssessment,
    ProjectionDecision,
    decide_projection,
)
from ontolib.decomposition.site_resolution import (
    organ_for_morphology,
    primary_subsites_for_morphology,
)

if TYPE_CHECKING:
    from ontolib.decomposition.collapse_policy import CollapseVetoPolicy

# ``is_ancestor(a, b)`` means *a* is a proper superclass of *b*.
# R82 containment is supplied independently through ``IsPartOf``.
IsAncestor = Callable[[str, str], bool]
IsPartOf = Callable[[str, str], bool]

LocationAxis = str
_MIN_COMPARISON_FILLERS = 2
_LOCATION_AXES: frozenset[LocationAxis] = frozenset(
    {
        axes.PRIMARY_SITE_AXIS,
        axes.PRIMARY_SUBSITE_AXIS,
        axes.ASSOCIATED_REGION_AXIS,
        "op:AssociatedSite",
        "op:MetastaticSite",
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class RoutedOccurrence:
    """One surviving source restriction after its final semantic route is known."""

    restriction: RoleRestriction
    normalized_axis: str
    semantic_route: SemanticRoute
    semantic_type: str | None
    source_fact_id: str | None
    source_occurrence_id: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class RoutedPlan:
    """The single route-before-reduction plan shared by planning and selection."""

    occurrences: tuple[RoutedOccurrence, ...]
    parent_morphologies: tuple[str, ...]
    specificity_groups: tuple[tuple[str, tuple[str, ...]], ...]
    comparison_groups: tuple[tuple[str, tuple[str, ...]], ...]
    protected_pairs: frozenset[tuple[str, str]]
    policy_decisions: tuple[tuple[str, str], ...]
    source_identity: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class RoutedSelection:
    constituents: tuple[Constituent, ...]
    dispositions: tuple[OccurrenceDisposition, ...]
    synthetic_occurrence_count: int = 0
    projection_decisions: tuple[ProjectionDecisionRecord, ...] = ()


class DiagnosticReductionPurpose(Enum):
    """Closed authorization for a non-emitting unassessed diagnostic reduction."""

    HISTORICAL_MIXED_CHAIN_RECONSTRUCTION = "historical-mixed-chain-reconstruction"


@dataclass(frozen=True, slots=True, kw_only=True)
class HistoricalCollapseDiagnostic:
    """Historical collapse dispositions without projectable constituents."""

    dispositions: tuple[OccurrenceDisposition, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectionDecisionRecord:
    """Transient decision ledger; source definitions remain in the complete record."""

    axis: str
    filler_code: str
    outcome: str
    review_bearing: bool
    axis_range_status: str
    atomicity_status: str
    reasons: tuple[str, ...]
    source_definition_ids: tuple[str, ...]
    source_occurrence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class CollapseDecision:
    retained_filler: str
    relation_kind: R101DispositionKind
    specificity_path: tuple[SpecificityPathEdge, ...] = ()


def _is_strictly_broader(broader: str, narrower: str, is_ancestor: IsAncestor) -> bool:
    return (
        broader != narrower
        and is_ancestor(broader, narrower)
        and not is_ancestor(narrower, broader)
    )


def filter_excluded(
    restrictions: Iterable[RoleRestriction], *, concept_code: str | None = None
) -> list[RoleRestriction]:
    """Drop non-projectable, generic, and concept-specific unsupported restrictions."""
    return [
        restriction
        for restriction in restrictions
        if axes.is_projectable_role(restriction)
        and not axes.is_generic_filler(restriction.role_code, restriction.filler_code)
        and not axes.is_unsupported_filler(
            concept_code, restriction.role_code, restriction.filler_code
        )
    ]


def most_specific(fillers: set[str], is_ancestor: IsAncestor) -> set[str]:
    """Keep only specificity leaves: drop any filler strictly broader than another.

    Unrelated or mutually broader fillers are retained, and a single filler is returned
    unchanged.
    """
    return {
        f
        for f in fillers
        if not any(_is_strictly_broader(f, other, is_ancestor) for other in fillers)
    }


def _r101_axis(r: RoleRestriction, parent_morphology: str | None) -> str | None:
    if r.role_code != axes.PRIMARY_SITE_ROLE:
        return None
    if axes.is_lineage_generic(r.anchoring_genus):
        return axes.ASSOCIATED_LINEAGE_AXIS
    if r.filler_code in primary_subsites_for_morphology(parent_morphology):
        return axes.PRIMARY_SUBSITE_AXIS
    return None


def _reviewed_source_axis(
    r: RoleRestriction, parent_morphology: str | None
) -> str | None:
    if r.role_code == "R100" and r.filler_code == organ_for_morphology(
        parent_morphology
    ):
        return axes.PRIMARY_SITE_AXIS
    if (
        r.role_code == "R126"
        and r.anchoring_genus is not None
        and (r.anchoring_genus, r.filler_code) in axes.ASSOCIATED_PRIOR_DISEASE
    ):
        return "op:AssociatedPriorDisease"
    return None


def route_axis(r: RoleRestriction, parent_morphology: str | None = None) -> str:
    """Map each restriction to its target axis.

    Routings that are not a plain role → axis lookup, so the same role does not
    always reach the same axis:

    * R101 with lineage-generic ``anchoring_genus`` → ``ASSOCIATED_LINEAGE_AXIS``
    * R101 whose filler is a known subsite of ``parent_morphology`` →
      ``PRIMARY_SUBSITE_AXIS``
    * R100 whose filler is the organ routed from ``parent_morphology`` →
      ``PRIMARY_SITE_AXIS``
    * R126 whose (genus, filler) pair is in ``axes.ASSOCIATED_PRIOR_DISEASE`` →
      ``op:AssociatedPriorDisease``
    * R88 with a known stage-system filler code → ``STAGE_SYSTEM_AXIS`` (keyed on
      the filler alone, unlike the four above)

    Otherwise: known defining roles route to their univocal ``op:`` axis, and
    unknown roles keep their source code and are flagged for review downstream.
    """
    if contextual := _r101_axis(r, parent_morphology):
        return contextual
    if reviewed := _reviewed_source_axis(r, parent_morphology):
        return reviewed
    if r.role_code == "R88" and r.filler_code in STAGE_SYSTEM_CODES:
        return axes.STAGE_SYSTEM_AXIS
    return normalized_axis_for_role(r.role_code) or r.role_code


def _primary_site_semantic_route(
    restriction: RoleRestriction,
    semantic_type_of: Callable[[str], str | None] | None,
) -> tuple[str, SemanticRoute, str | None]:
    if semantic_type_of is None:
        return axes.PRIMARY_SITE_AXIS, "semantic-evidence-not-requested", None
    semantic_type = semantic_type_of(restriction.filler_code)
    if semantic_type is None:
        return axes.PRIMARY_SITE_AXIS, "missing-p106", None
    if semantic_type == axes.ORGAN_SEMANTIC_TYPE:
        return axes.PRIMARY_SITE_AXIS, "p106-organ", semantic_type
    return axes.ASSOCIATED_REGION_AXIS, "p106-non-organ-anatomy", semantic_type


def _semantic_route(
    restriction: RoleRestriction,
    parent_morphology: str | None,
    semantic_type_of: Callable[[str], str | None] | None,
) -> tuple[str, SemanticRoute, str | None]:
    if restriction.filler_code in primary_subsites_for_morphology(parent_morphology):
        return axes.PRIMARY_SUBSITE_AXIS, "reviewed-primary-subsite", None
    contextual = _r101_axis(restriction, parent_morphology)
    if contextual is not None:
        route = (
            "reviewed-lineage"
            if contextual == axes.ASSOCIATED_LINEAGE_AXIS
            else "reviewed-primary-subsite"
        )
        return contextual, route, None
    reviewed = _reviewed_source_axis(restriction, parent_morphology)
    if reviewed is not None:
        return reviewed, "reviewed-contextual-override", None
    if restriction.role_code != axes.PRIMARY_SITE_ROLE:
        axis_name = route_axis(restriction, parent_morphology)
        route = (
            "unknown-role" if axis_name == restriction.role_code else "role-contract"
        )
        return axis_name, route, None
    return _primary_site_semantic_route(restriction, semantic_type_of)


def _expand_routed_occurrences(
    restriction: RoleRestriction,
    *,
    normalized_axis: str,
    semantic_route: SemanticRoute,
    semantic_type: str | None,
) -> tuple[RoutedOccurrence, ...]:
    facts = restriction.source_definition_ids
    occurrences = restriction.source_occurrence_ids
    if not occurrences:
        return (
            RoutedOccurrence(
                restriction=restriction,
                normalized_axis=normalized_axis,
                semantic_route=semantic_route,
                semantic_type=semantic_type,
                source_fact_id=facts[0] if len(facts) == 1 else None,
                source_occurrence_id=None,
            ),
        )
    if len(facts) == 1:
        fact_by_occurrence = (facts[0],) * len(occurrences)
    elif len(facts) == len(occurrences):
        fact_by_occurrence = facts
    else:
        raise ValueError("source occurrence-to-fact binding is ambiguous")
    return tuple(
        RoutedOccurrence(
            restriction=restriction,
            normalized_axis=normalized_axis,
            semantic_route=semantic_route,
            semantic_type=semantic_type,
            source_fact_id=fact_id,
            source_occurrence_id=occurrence_id,
        )
        for fact_id, occurrence_id in zip(fact_by_occurrence, occurrences, strict=True)
    )


def _comparison_groups(
    occurrences: tuple[RoutedOccurrence, ...],
    *,
    location_only: bool,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    by_axis: dict[str, set[str]] = defaultdict(set)
    for occurrence in occurrences:
        if _collapse_eligible(occurrence) and (
            not location_only or occurrence.normalized_axis in _LOCATION_AXES
        ):
            by_axis[occurrence.normalized_axis].add(occurrence.restriction.filler_code)
    return tuple(
        (axis_name, tuple(sorted(fillers)))
        for axis_name, fillers in sorted(by_axis.items())
        if len(fillers) > 1
    )


def build_routed_plan(
    restrictions: Iterable[RoleRestriction],
    *,
    semantic_type_of: Callable[[str], str | None] | None = None,
    parent_morphologies: Iterable[str] = (),
    concept_code: str | None,
    source_identity: str | None,
    collapse_policy: CollapseVetoPolicy,
) -> RoutedPlan:
    """Route every surviving restriction once before planning any reduction."""
    morphology_fillers = tuple(dict.fromkeys(parent_morphologies))
    parent_morphology = morphology_fillers[0] if morphology_fillers else None
    included = tuple(filter_excluded(restrictions, concept_code=concept_code))
    routed_rows: list[RoutedOccurrence] = []
    for restriction in included:
        axis_name, route_name, semantic_type = _semantic_route(
            restriction, parent_morphology, semantic_type_of
        )
        routed_rows.extend(
            _expand_routed_occurrences(
                restriction,
                normalized_axis=axis_name,
                semantic_route=route_name,
                semantic_type=semantic_type,
            )
        )
    routed = tuple(routed_rows)
    applicable_vetoes = collapse_policy.applicable_vetoes(
        included,
        source_identity=source_identity,
        concept_code=concept_code,
        route_axis=lambda row: _semantic_route(
            row, parent_morphology, semantic_type_of
        )[0],
    )
    protected_pairs = frozenset(
        (entry.normalized_axis, entry.broader_code) for entry in applicable_vetoes
    )
    policy_decisions = tuple(
        (entry.occurrence_id, entry.atomic_decision_identity)
        for entry in applicable_vetoes
    )
    return RoutedPlan(
        occurrences=routed,
        parent_morphologies=morphology_fillers,
        specificity_groups=_comparison_groups(routed, location_only=False),
        comparison_groups=_comparison_groups(routed, location_only=True),
        protected_pairs=protected_pairs,
        policy_decisions=policy_decisions,
        source_identity=source_identity,
    )


def _collapse_eligible(occurrence: RoutedOccurrence) -> bool:
    return occurrence.semantic_route not in {"missing-p106", "unknown-role"} and (
        occurrence.normalized_axis != axes.ASSOCIATED_LINEAGE_AXIS
    )


def _relation_kind(
    axis_name: str,
    broader: str,
    narrower: str,
    is_ancestor: IsAncestor,
    is_part_of: IsPartOf,
) -> SpecificityRelationKind | None:
    forward_isa, reverse_isa = _directed_relation(broader, narrower, is_ancestor)
    forward_r82, reverse_r82 = _directed_r82_relation(
        axis_name, broader, narrower, is_part_of
    )
    if all((forward_isa, reverse_isa)) or all((forward_r82, reverse_r82)):
        raise ValueError(
            "specificity relation contains a cycle or mutually broader pair"
        )
    if forward_isa:
        return "is-a"
    if forward_r82:
        return "r82"
    return None


def _directed_relation(
    broader: str, narrower: str, relation: Callable[[str, str], bool]
) -> tuple[bool, bool]:
    distinct = broader != narrower
    return (
        distinct and relation(broader, narrower),
        distinct and relation(narrower, broader),
    )


def _directed_r82_relation(
    axis_name: str,
    broader: str,
    narrower: str,
    is_part_of: IsPartOf,
) -> tuple[bool, bool]:
    if axis_name not in _LOCATION_AXES:
        return False, False
    return is_part_of(narrower, broader), is_part_of(broader, narrower)


def _eligible_fillers(
    fillers: set[str], occurrences: tuple[RoutedOccurrence, ...]
) -> set[str]:
    return {
        filler
        for filler in fillers
        if all(
            _collapse_eligible(row)
            for row in occurrences
            if row.restriction.filler_code == filler
        )
    }


def _specificity_relations(
    axis_name: str,
    fillers: set[str],
    is_ancestor: IsAncestor,
    is_part_of: IsPartOf,
) -> dict[tuple[str, str], SpecificityRelationKind]:
    relations: dict[tuple[str, str], SpecificityRelationKind] = {}
    for broader in sorted(fillers):
        for narrower in sorted(fillers - {broader}):
            kind = _relation_kind(axis_name, broader, narrower, is_ancestor, is_part_of)
            if kind is not None:
                relations[(broader, narrower)] = kind
    return relations


def _collapsed_fillers(
    eligible_fillers: set[str],
    relations: dict[tuple[str, str], SpecificityRelationKind],
    protected_fillers: set[str],
    source_identity: str | None,
) -> dict[str, CollapseDecision]:
    outgoing: dict[str, list[tuple[str, SpecificityRelationKind]]] = defaultdict(list)
    for (broader, narrower), kind in relations.items():
        outgoing[broader].append((narrower, kind))
    collapsed: dict[str, CollapseDecision] = {}
    for broader in sorted(eligible_fillers - protected_fillers):
        decision = _collapse_decision(
            _terminal_paths(broader, outgoing), source_identity
        )
        if decision is not None:
            collapsed[broader] = decision
    return collapsed


SpecificityPath = tuple[tuple[str, str, SpecificityRelationKind], ...]


def _terminal_paths(
    broader: str,
    outgoing: dict[str, list[tuple[str, SpecificityRelationKind]]],
) -> list[SpecificityPath]:
    if not outgoing[broader]:
        return [()]
    return [
        ((broader, narrower, kind), *suffix)
        for narrower, kind in sorted(outgoing[broader])
        for suffix in _terminal_paths(narrower, outgoing)
    ]


def _collapse_decision(
    candidate_paths: list[SpecificityPath], source_identity: str | None
) -> CollapseDecision | None:
    paths = [path for path in candidate_paths if path]
    terminals = {path[-1][1] for path in paths}
    if len(terminals) != 1:
        return None
    path = min(paths, key=lambda value: (len(value), value))
    relation_kind = _path_disposition_kind(path)
    specificity_path = _mixed_path_evidence(path, relation_kind, source_identity)
    return CollapseDecision(
        retained_filler=next(iter(terminals)),
        relation_kind=relation_kind,
        specificity_path=specificity_path,
    )


def _path_disposition_kind(path: SpecificityPath) -> R101DispositionKind:
    kinds = {edge[2] for edge in path}
    if kinds == {"is-a"}:
        return "collapsed-is-a"
    if kinds == {"r82"}:
        return "collapsed-r82"
    return "collapsed-mixed"


def _mixed_path_evidence(
    path: SpecificityPath,
    relation_kind: R101DispositionKind,
    source_identity: str | None,
) -> tuple[SpecificityPathEdge, ...]:
    if relation_kind != "collapsed-mixed":
        return ()
    if source_identity is None:
        raise ValueError("mixed specificity path lacks source identity")
    return tuple(
        SpecificityPathEdge(
            kind=kind,
            broader_code=broader,
            narrower_code=narrower,
            source_identity=source_identity,
        )
        for broader, narrower, kind in path
    )


def _selected_fillers(
    axis_name: str,
    occurrences: tuple[RoutedOccurrence, ...],
    is_ancestor: IsAncestor,
    is_part_of: IsPartOf,
    protected_pairs: frozenset[tuple[str, str]],
    source_identity: str | None,
) -> tuple[set[str], dict[str, CollapseDecision]]:
    fillers = {row.restriction.filler_code for row in occurrences}
    eligible_fillers = _eligible_fillers(fillers, occurrences)
    if len(eligible_fillers) < _MIN_COMPARISON_FILLERS:
        return fillers, {}
    protected_fillers = {
        filler
        for protected_axis, filler in protected_pairs
        if protected_axis == axis_name
    }
    relations = _specificity_relations(
        axis_name, eligible_fillers, is_ancestor, is_part_of
    )
    _require_acyclic_specificity(eligible_fillers, relations)
    collapsed = _collapsed_fillers(
        eligible_fillers, relations, protected_fillers, source_identity
    )
    return fillers - collapsed.keys(), collapsed


def _require_acyclic_specificity(
    fillers: set[str],
    relations: dict[tuple[str, str], SpecificityRelationKind],
) -> None:
    descendants: dict[str, set[str]] = defaultdict(set)
    for broader, narrower in relations:
        descendants[broader].add(narrower)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(filler: str) -> None:
        if filler in visiting:
            raise ValueError("specificity relation contains a cycle")
        if filler in visited:
            return
        visiting.add(filler)
        for narrower in descendants[filler]:
            visit(narrower)
        visiting.remove(filler)
        visited.add(filler)

    for filler in fillers:
        visit(filler)


def _constituent_from_routed(
    axis_name: str,
    filler: str,
    occurrences: tuple[RoutedOccurrence, ...],
    retained_count: int,
    collapsed: dict[str, CollapseDecision],
    policy_protected_axis: bool,
) -> Constituent:
    rows = tuple(row for row in occurrences if row.restriction.filler_code == filler)
    source_roles, source_definition_ids, source_occurrence_ids = _source_bindings(rows)
    unknown = _has_unknown_route(rows)
    known_retained_count = _known_retained_count(occurrences, collapsed)
    routed_exempt = axis_name in _REVIEW_EXEMPT_AXES
    needs_review, group = _review_fields(
        axis_name,
        retained_count=retained_count,
        known_retained_count=known_retained_count,
        unknown=unknown,
        routed_exempt=routed_exempt,
        policy_protected_axis=policy_protected_axis,
    )
    chosen_over_broader = any(
        decision.retained_filler == filler for decision in collapsed.values()
    )
    return Constituent(
        axis=axis_name,
        filler_code=filler,
        axis_source="role",
        source_roles=source_roles,
        most_specific=chosen_over_broader,
        needs_review=needs_review,
        axis_ambiguity_group_id=group,
        source_definition_ids=source_definition_ids,
        source_occurrence_ids=source_occurrence_ids,
    )


def _source_bindings(
    rows: tuple[RoutedOccurrence, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    source_roles = tuple(sorted({row.restriction.role_code for row in rows}))
    source_definition_ids = tuple(
        sorted({row.source_fact_id for row in rows if row.source_fact_id is not None})
    )
    source_occurrence_ids = tuple(
        sorted(
            {
                row.source_occurrence_id
                for row in rows
                if row.source_occurrence_id is not None
            }
        )
    )
    return source_roles, source_definition_ids, source_occurrence_ids


def _has_unknown_route(rows: tuple[RoutedOccurrence, ...]) -> bool:
    return any(row.semantic_route in {"missing-p106", "unknown-role"} for row in rows)


def _known_retained_count(
    occurrences: tuple[RoutedOccurrence, ...],
    collapsed: dict[str, CollapseDecision],
) -> int:
    return len(
        {
            row.restriction.filler_code
            for row in occurrences
            if row.restriction.filler_code not in collapsed
            and row.semantic_route not in {"missing-p106", "unknown-role"}
        }
    )


def _review_fields(
    axis_name: str,
    *,
    retained_count: int,
    known_retained_count: int,
    unknown: bool,
    routed_exempt: bool,
    policy_protected_axis: bool,
) -> tuple[bool, str | None]:
    return (
        _needs_review(
            known_retained_count,
            unknown=unknown,
            routed_exempt=routed_exempt,
            policy_protected_axis=policy_protected_axis,
        ),
        _relationship_group(
            axis_name,
            retained_count=retained_count,
            known_retained_count=known_retained_count,
            unknown=unknown,
            routed_exempt=routed_exempt,
            policy_protected_axis=policy_protected_axis,
        ),
    )


def _needs_review(
    known_retained_count: int,
    *,
    unknown: bool,
    routed_exempt: bool,
    policy_protected_axis: bool,
) -> bool:
    if unknown:
        return True
    if known_retained_count <= 1:
        return False
    return not routed_exempt or policy_protected_axis


def _relationship_group(
    axis_name: str,
    *,
    retained_count: int,
    known_retained_count: int,
    unknown: bool,
    routed_exempt: bool,
    policy_protected_axis: bool,
) -> str | None:
    if retained_count <= 1:
        return None
    if policy_protected_axis:
        return axis_name
    if routed_exempt and axis_name != axes.ASSOCIATED_LINEAGE_AXIS:
        return axis_name
    if _requires_ambiguity_group(unknown, routed_exempt, known_retained_count):
        return axis_name
    return None


def _requires_ambiguity_group(
    unknown: bool, routed_exempt: bool, known_retained_count: int
) -> bool:
    return not unknown and not routed_exempt and known_retained_count > 1


def _disposition_reduction(
    occurrence: RoutedOccurrence,
    occurrence_id: str,
    collapsed: dict[str, CollapseDecision],
    policy_decisions: dict[str, str],
) -> tuple[R101DispositionKind, str, tuple[SpecificityPathEdge, ...]]:
    filler = occurrence.restriction.filler_code
    if occurrence_id in policy_decisions:
        return "retained-policy-veto", filler, ()
    if filler in collapsed:
        decision = collapsed[filler]
        return (
            decision.relation_kind,
            decision.retained_filler,
            decision.specificity_path,
        )
    kind = (
        "retained-unknown"
        if occurrence.semantic_route in {"missing-p106", "unknown-role"}
        else "retained-routed"
    )
    return cast("R101DispositionKind", kind), filler, ()


def _disposition(
    occurrence: RoutedOccurrence,
    collapsed: dict[str, CollapseDecision],
    policy_decisions: dict[str, str],
) -> OccurrenceDisposition | None:
    occurrence_id = occurrence.source_occurrence_id
    fact_id = occurrence.source_fact_id
    if occurrence_id is None or fact_id is None:
        if occurrence.restriction.source_kind == "synthetic":
            return None
        raise ValueError(
            "stated restriction requires source occurrence and fact identities"
        )
    filler = occurrence.restriction.filler_code
    kind, retained, specificity_path = _disposition_reduction(
        occurrence, occurrence_id, collapsed, policy_decisions
    )
    return OccurrenceDisposition(
        kind=kind,
        source_occurrence_id=occurrence_id,
        source_fact_id=fact_id,
        normalized_axis=occurrence.normalized_axis,
        source_filler=filler,
        retained_filler=retained,
        semantic_route=occurrence.semantic_route,
        semantic_type=occurrence.semantic_type,
        r82_part=retained if kind == "collapsed-r82" else None,
        r82_whole=filler if kind == "collapsed-r82" else None,
        specificity_path=specificity_path,
        policy_decision_identity=policy_decisions.get(occurrence_id),
    )


def _select_axis_partition(
    axis_name: str,
    occurrences: tuple[RoutedOccurrence, ...],
    plan: RoutedPlan,
    is_ancestor: IsAncestor,
    part_of: IsPartOf,
    policy_decisions: dict[str, str],
) -> tuple[list[Constituent], list[OccurrenceDisposition]]:
    retained, collapsed = _selected_fillers(
        axis_name,
        occurrences,
        is_ancestor,
        part_of,
        plan.protected_pairs,
        plan.source_identity,
    )
    protected = any(pair_axis == axis_name for pair_axis, _ in plan.protected_pairs)
    constituents = [
        _constituent_from_routed(
            axis_name,
            filler,
            occurrences,
            len(retained),
            collapsed,
            protected,
        )
        for filler in sorted(retained)
    ]
    dispositions = [
        disposition
        for occurrence in occurrences
        if (disposition := _disposition(occurrence, collapsed, policy_decisions))
        is not None
    ]
    return constituents, dispositions


def _reduce_routed_plan(
    plan: RoutedPlan,
    is_ancestor: IsAncestor,
    *,
    is_part_of: IsPartOf | None = None,
) -> RoutedSelection:
    """Reduce only within final routed partitions and disposition every occurrence."""
    part_of = is_part_of or (lambda _part, _whole: False)
    by_axis: dict[str, list[RoutedOccurrence]] = defaultdict(list)
    for occurrence in plan.occurrences:
        by_axis[occurrence.normalized_axis].append(occurrence)
    constituents: list[Constituent] = []
    dispositions: list[OccurrenceDisposition] = []
    policy_decisions = dict(plan.policy_decisions)
    for axis_name, rows in sorted(by_axis.items()):
        axis_constituents, axis_dispositions = _select_axis_partition(
            axis_name,
            tuple(rows),
            plan,
            is_ancestor,
            part_of,
            policy_decisions,
        )
        constituents.extend(axis_constituents)
        dispositions.extend(axis_dispositions)
    _append_morphology(constituents, plan.parent_morphologies)
    return RoutedSelection(
        constituents=tuple(
            sorted(constituents, key=lambda row: (row.axis, row.filler_code))
        ),
        dispositions=tuple(
            sorted(dispositions, key=lambda row: row.source_occurrence_id)
        ),
        synthetic_occurrence_count=sum(
            occurrence.restriction.source_kind == "synthetic"
            for occurrence in plan.occurrences
        ),
    )


def diagnose_historical_collapse_dispositions(
    plan: RoutedPlan,
    is_ancestor: IsAncestor,
    *,
    purpose: DiagnosticReductionPurpose,
    is_part_of: IsPartOf | None = None,
) -> HistoricalCollapseDiagnostic:
    """Reconstruct historical mixed-chain dispositions without enabling emission."""
    if purpose is not DiagnosticReductionPurpose.HISTORICAL_MIXED_CHAIN_RECONSTRUCTION:
        raise TypeError(
            "purpose must be "
            "DiagnosticReductionPurpose.HISTORICAL_MIXED_CHAIN_RECONSTRUCTION"
        )
    selected = _reduce_routed_plan(plan, is_ancestor, is_part_of=is_part_of)
    return HistoricalCollapseDiagnostic(dispositions=selected.dispositions)


def _projection_decision_record(
    decision: ProjectionDecision,
    rows: tuple[RoutedOccurrence, ...],
) -> ProjectionDecisionRecord:
    source_definition_ids = tuple(
        sorted({row.source_fact_id for row in rows if row.source_fact_id is not None})
    )
    source_occurrence_ids = tuple(
        sorted(
            {
                row.source_occurrence_id
                for row in rows
                if row.source_occurrence_id is not None
            }
        )
    )
    return ProjectionDecisionRecord(
        axis=decision.axis,
        filler_code=decision.filler_code,
        outcome=decision.outcome,
        review_bearing=decision.review_bearing,
        axis_range_status=decision.axis_range_status,
        atomicity_status=decision.atomicity_status,
        reasons=decision.reasons,
        source_definition_ids=source_definition_ids,
        source_occurrence_ids=source_occurrence_ids,
    )


def _occurrences_by_projection_key(
    plan: RoutedPlan,
) -> dict[tuple[str, str], list[RoutedOccurrence]]:
    result: dict[tuple[str, str], list[RoutedOccurrence]] = defaultdict(list)
    for occurrence in plan.occurrences:
        result[(occurrence.normalized_axis, occurrence.restriction.filler_code)].append(
            occurrence
        )
    for filler in plan.parent_morphologies:
        result[(axes.MORPHOLOGY_AXIS, filler)]
    return result


def _require_complete_assessments(
    expected: set[tuple[str, str]],
    supplied: set[tuple[str, str]],
) -> None:
    if missing := sorted(expected - supplied):
        axis_name, filler = missing[0]
        raise ValueError(f"missing projection assessment for {axis_name}/{filler}")
    if extra := sorted(supplied - expected):
        axis_name, filler = extra[0]
        raise ValueError(f"extraneous projection assessment for {axis_name}/{filler}")


def _retained_policy_decisions(
    plan: RoutedPlan,
    occurrences: tuple[RoutedOccurrence, ...],
) -> tuple[tuple[str, str], ...]:
    retained_occurrence_ids = {
        occurrence.source_occurrence_id
        for occurrence in occurrences
        if occurrence.source_occurrence_id is not None
    }
    return tuple(
        decision
        for decision in plan.policy_decisions
        if decision[0] in retained_occurrence_ids
    )


def _projection_decisions(
    by_key: Mapping[tuple[str, str], list[RoutedOccurrence]],
    assessments: Mapping[tuple[str, str], ProjectionAssessment],
) -> tuple[ProjectionDecisionRecord, ...]:
    return tuple(
        _projection_decision_record(
            decide_projection(assessments[key]),
            tuple(by_key[key]),
        )
        for key in sorted(by_key)
    )


def _retained_parent_morphologies(
    plan: RoutedPlan, accepted: set[tuple[str, str]]
) -> tuple[str, ...]:
    return tuple(
        filler
        for filler in plan.parent_morphologies
        if (axes.MORPHOLOGY_AXIS, filler) in accepted
    )


def _assessed_plan(
    plan: RoutedPlan,
    assessments: Mapping[tuple[str, str], ProjectionAssessment],
) -> tuple[RoutedPlan, tuple[ProjectionDecisionRecord, ...]]:
    by_key = _occurrences_by_projection_key(plan)
    _require_complete_assessments(set(by_key), set(assessments))
    decisions = _projection_decisions(by_key, assessments)
    accepted = {
        (decision.axis, decision.filler_code)
        for decision in decisions
        if decision.outcome == "accepted"
    }
    occurrences = tuple(
        occurrence
        for occurrence in plan.occurrences
        if (occurrence.normalized_axis, occurrence.restriction.filler_code) in accepted
    )
    assessed = RoutedPlan(
        occurrences=occurrences,
        parent_morphologies=_retained_parent_morphologies(plan, accepted),
        specificity_groups=_comparison_groups(occurrences, location_only=False),
        comparison_groups=_comparison_groups(occurrences, location_only=True),
        protected_pairs=frozenset(
            pair for pair in plan.protected_pairs if pair in accepted
        ),
        policy_decisions=_retained_policy_decisions(plan, occurrences),
        source_identity=plan.source_identity,
    )
    return assessed, decisions


def select_assessed_routed_plan(
    plan: RoutedPlan,
    is_ancestor: IsAncestor,
    *,
    assessments: Mapping[tuple[str, str], ProjectionAssessment],
    is_part_of: IsPartOf | None = None,
) -> RoutedSelection:
    """Apply complete validity decisions after routing and before reduction."""
    assessed, decisions = _assessed_plan(plan, assessments)
    selected = _reduce_routed_plan(assessed, is_ancestor, is_part_of=is_part_of)
    return RoutedSelection(
        constituents=selected.constituents,
        dispositions=selected.dispositions,
        synthetic_occurrence_count=selected.synthetic_occurrence_count,
        projection_decisions=decisions,
    )


_REVIEW_EXEMPT_AXES: frozenset[str] = frozenset(
    {
        axes.ASSOCIATED_LINEAGE_AXIS,
        axes.ASSOCIATED_REGION_AXIS,
        axes.STAGE_SYSTEM_AXIS,
    }
)

# D23: stage-SYSTEM fillers use the same R88 role but are routed to
# ``op:StageSystem`` (design §4.2, SME-approved). These are the staging
# manual/version codes (AJCC v6-v9, FIGO, Toronto) vs. stage VALUES
# (Stage I-IV). Known codes extracted from the golden set.
STAGE_CLASSIFICATION_VERSION = "ncit-26.07d-stage-kind-v1"
STAGE_SYSTEM_CLASSIFICATIONS = MappingProxyType(
    {
        "C132248": ("AJCC", "8"),
        "C140961": ("AJCC", "7"),
        "C141685": ("VALG", "limited-extensive"),
        "C180901": ("AJCC", "9"),
        "C186617": ("FIGO", "2018"),
        "C186618": ("FIGO", "2009"),
        "C198023": ("Toronto", "2 Tier 1"),
        "C198024": ("Toronto", "2 Tier 2"),
        "C206211": ("FIGO", "2023"),
        "C90529": ("AJCC", "6"),
        "C90530": ("AJCC", "7"),
    }
)
STAGE_SYSTEM_CODES: frozenset[str] = frozenset(STAGE_SYSTEM_CLASSIFICATIONS)


def _append_morphology(
    constituents: list[Constituent], parent_morphologies: Iterable[str]
) -> None:
    for parent_morphology in parent_morphologies:
        constituents.append(
            Constituent(
                axis=axes.MORPHOLOGY_AXIS,
                filler_code=parent_morphology,
                axis_source="parent",
            )
        )
