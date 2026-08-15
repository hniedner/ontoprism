"""Filler selection — choose the intended constituent(s) per axis (design §6).

Working from the *stated* graph already eliminates most ancestor bleed; most-specific
selection is defense-in-depth for hierarchy-comparable axes that still return multiple
fillers. The selection is a pure function of the fillers and an injected
``is_ancestor`` predicate, so it is fully unit-testable without a store.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from types import MappingProxyType
from typing import cast

from ontolib.decomposition import axes
from ontolib.decomposition.axis_contracts import (
    AXIS_CONTRACTS,
    normalized_axis_for_role,
)
from ontolib.decomposition.models import Constituent, RoleRestriction
from ontolib.decomposition.site_resolution import (
    organ_for_morphology,
    primary_subsites_for_morphology,
)

# ``is_ancestor(a, b)`` means *a* is a proper superclass of *b*.
# R82 containment is supplied independently through ``IsPartOf``.
IsAncestor = Callable[[str, str], bool]
IsPartOf = Callable[[str, str], bool]


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


def _location_broader(is_ancestor: IsAncestor, is_part_of: IsPartOf) -> IsAncestor:
    def broader(ancestor: str, descendant: str) -> bool:
        return is_ancestor(ancestor, descendant) or is_part_of(descendant, ancestor)

    return broader


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


def _is_most_specific(filler: str, fillers: set[str], is_ancestor: IsAncestor) -> bool:
    """True when *filler* was chosen over a strictly broader filler."""
    return any(_is_strictly_broader(o, filler, is_ancestor) for o in fillers)


def _is_r101_semantic_split(
    axis_name: str,
    fillers: set[str],
    semantic_type_of: Callable[[str], str | None] | None,
) -> bool:
    return (
        axis_name == axes.PRIMARY_SITE_AXIS
        and semantic_type_of is not None
        and len(fillers) > 1
        and any(semantic_type_of(filler) is not None for filler in fillers)
    )


def _r101_semantic_type_constituents(
    fillers: set[str],
    is_ancestor: IsAncestor,
    is_part_of: IsPartOf,
    semantic_type_of: Callable[[str], str | None],
) -> list[Constituent]:
    organ_fillers = {
        filler
        for filler in fillers
        if semantic_type_of(filler) == axes.ORGAN_SEMANTIC_TYPE
    }
    region_fillers = fillers - organ_fillers
    organ = most_specific(organ_fillers, is_ancestor) or organ_fillers
    location_broader = _location_broader(is_ancestor, is_part_of)
    region = most_specific(region_fillers, location_broader) or region_fillers

    organ_ambiguous = len(organ) > 1
    organ_constituents = [
        Constituent(
            axis=axes.PRIMARY_SITE_AXIS,
            filler_code=filler,
            axis_source="role",
            source_roles=(axes.PRIMARY_SITE_ROLE,),
            most_specific=_is_most_specific(filler, fillers, is_ancestor),
            needs_review=organ_ambiguous,
        )
        for filler in organ
    ]
    return [
        *organ_constituents,
        *_associated_region_constituents(region, fillers, is_ancestor),
    ]


def _associated_region_constituents(
    regions: set[str],
    fillers: set[str],
    is_ancestor: IsAncestor,
) -> list[Constituent]:
    group = axes.ASSOCIATED_REGION_AXIS if len(regions) > 1 else None
    return [
        Constituent(
            axis=axes.ASSOCIATED_REGION_AXIS,
            filler_code=filler,
            axis_source="role",
            source_roles=(axes.PRIMARY_SITE_ROLE,),
            most_specific=_is_most_specific(filler, fillers, is_ancestor),
            needs_review=False,
            group=group,
        )
        for filler in regions
    ]


def _primary_subsite_constituents(
    subsites: set[str], fillers: set[str], is_ancestor: IsAncestor
) -> list[Constituent]:
    return [
        Constituent(
            axis=axes.PRIMARY_SUBSITE_AXIS,
            filler_code=filler,
            axis_source="role",
            source_roles=(axes.PRIMARY_SITE_ROLE,),
            most_specific=_is_most_specific(filler, fillers, is_ancestor),
        )
        for filler in subsites
    ]


def _source_roles_for_axis(axis_name: str) -> tuple[str, ...]:
    contract = AXIS_CONTRACTS.get(axis_name)
    if contract is not None and len(contract.source_roles) == 1:
        return contract.source_roles
    return (axis_name,) if axis_name.startswith("R") else ()


def _requires_review(axis_name: str, *, ambiguous: bool) -> bool:
    if axis_name not in AXIS_CONTRACTS:
        return True
    return ambiguous and axis_name not in _REVIEW_EXEMPT_AXES


def _standard_constituents(
    axis_name: str,
    leaves: set[str],
    fillers: set[str],
    is_ancestor: IsAncestor,
    source_roles: dict[tuple[str, str], tuple[str, ...]] | None = None,
) -> list[Constituent]:
    ambiguous = len(leaves) > 1
    is_routed = axis_name in _REVIEW_EXEMPT_AXES
    synthetic_group = is_routed and axis_name != axes.ASSOCIATED_LINEAGE_AXIS
    return [
        Constituent(
            axis=axis_name,
            filler_code=filler,
            axis_source="role",
            source_roles=(source_roles or {}).get(
                (axis_name, filler), _source_roles_for_axis(axis_name)
            ),
            most_specific=_is_most_specific(filler, fillers, is_ancestor),
            needs_review=_requires_review(axis_name, ambiguous=ambiguous),
            group=axis_name if synthetic_group and ambiguous else None,
        )
        for filler in leaves
    ]


def _group_by_routed_axis(
    restrictions: Iterable[RoleRestriction],
    parent_morphology: str | None = None,
    concept_code: str | None = None,
) -> tuple[dict[str, set[str]], dict[tuple[str, str], tuple[str, ...]]]:
    by_axis: dict[str, set[str]] = defaultdict(set)
    source_role_sets: dict[tuple[str, str], set[str]] = defaultdict(set)
    for r in filter_excluded(restrictions, concept_code=concept_code):
        axis_name = route_axis(r, parent_morphology)
        by_axis[axis_name].add(r.filler_code)
        key = (axis_name, r.filler_code)
        source_role_sets[key].add(r.role_code)
    source_roles = {
        key: tuple(sorted(roles)) for key, roles in source_role_sets.items()
    }
    return by_axis, source_roles


def comparison_filler_codes(
    restrictions: Iterable[RoleRestriction], *, concept_code: str | None = None
) -> list[str]:
    """Return fillers from routed-axis groups that use specificity comparison."""
    return sorted(
        {
            filler
            for axis_name, fillers in _group_by_routed_axis(
                restrictions, concept_code=concept_code
            )[0].items()
            if axis_name != axes.ASSOCIATED_LINEAGE_AXIS and len(fillers) > 1
            for filler in fillers
        }
    )


def _known_r101_organ(
    fillers: set[str],
    parent_morphology: str | None,
    axis_name: str,
) -> str | None:
    if (
        axis_name != axes.PRIMARY_SITE_AXIS
        or parent_morphology is None
        or len(fillers) <= 1
    ):
        return None
    organ = organ_for_morphology(parent_morphology)
    return organ if organ in fillers else None


def _resolve_r101_with_organ_lookup(
    fillers: set[str],
    is_ancestor: IsAncestor,
    parent_morphology: str | None,
    semantic_type_of: Callable[[str], str | None] | None,
    source_roles: dict[tuple[str, str], tuple[str, ...]],
    is_part_of: IsPartOf,
    axis_name: str = "",
) -> list[Constituent] | None:
    """Prefer the known D23 organ while preserving distinct D20 region facts."""
    organ = _known_r101_organ(fillers, parent_morphology, axis_name)
    if organ is None:
        return None
    primary = Constituent(
        axis=axes.PRIMARY_SITE_AXIS,
        filler_code=organ,
        axis_source="role",
        source_roles=source_roles.get(
            (axes.PRIMARY_SITE_AXIS, organ), (axes.PRIMARY_SITE_ROLE,)
        ),
        most_specific=_is_most_specific(organ, fillers, is_ancestor),
        needs_review=False,
    )
    if semantic_type_of is None:
        return [primary]
    # Retained deliberately, not dead by construction: exhaustive enumeration of
    # 144,072 closed-form inputs, 9.0M production-shaped pipeline runs, and 14,604
    # hermetic-suite helper executions all found this set empty. The emptiness is
    # data-contingent on the hand-maintained MORPHOLOGY_TO_ORGAN and
    # MORPHOLOGY_TO_PRIMARY_SUBSITES tables remaining disjoint (see
    # ontolib.decomposition.site_resolution), not structural, so deleting the branch
    # would silently drop subsites the moment those tables overlap.
    subsites = set(primary_subsites_for_morphology(parent_morphology)) & fillers
    regions = {
        filler
        for filler in fillers - {organ} - subsites
        if semantic_type_of(filler) != axes.ORGAN_SEMANTIC_TYPE
    }
    location_broader = _location_broader(is_ancestor, is_part_of)
    region_leaves = most_specific(regions, location_broader) or regions
    return [
        primary,
        *_primary_subsite_constituents(subsites, fillers, is_ancestor),
        *_associated_region_constituents(region_leaves, fillers, is_ancestor),
    ]


def _iter_axis_constituents(
    by_axis: dict[str, set[str]],
    source_roles: dict[tuple[str, str], tuple[str, ...]],
    is_ancestor: IsAncestor,
    semantic_type_of: Callable[[str], str | None] | None,
    parent_morphology: str | None = None,
    is_part_of: IsPartOf | None = None,
) -> list[Constituent]:
    part_of = is_part_of or (lambda _part, _whole: False)
    result: list[Constituent] = []
    for axis_name, fillers in by_axis.items():
        result.extend(
            _constituents_for_axis(
                axis_name,
                fillers,
                is_ancestor,
                semantic_type_of,
                parent_morphology,
                source_roles,
                part_of,
            )
        )
    return result


def _constituents_for_axis(
    axis_name: str,
    fillers: set[str],
    is_ancestor: IsAncestor,
    semantic_type_of: Callable[[str], str | None] | None,
    parent_morphology: str | None,
    source_roles: dict[tuple[str, str], tuple[str, ...]],
    is_part_of: IsPartOf,
) -> list[Constituent]:
    resolved = _resolve_r101_with_organ_lookup(
        fillers,
        is_ancestor,
        parent_morphology,
        semantic_type_of,
        source_roles,
        is_part_of,
        axis_name,
    )
    if resolved is not None:
        return resolved

    if _is_r101_semantic_split(axis_name, fillers, semantic_type_of):
        narrowed = cast("Callable[[str], str | None]", semantic_type_of)
        split = _r101_semantic_type_constituents(
            fillers, is_ancestor, is_part_of, narrowed
        )
        if split:
            return split

    leaves = _resolved_leaves(axis_name, fillers, is_ancestor, is_part_of)
    return _standard_constituents(axis_name, leaves, fillers, is_ancestor, source_roles)


def _resolved_leaves(
    axis_name: str,
    fillers: set[str],
    is_ancestor: IsAncestor,
    is_part_of: IsPartOf,
) -> set[str]:
    if axis_name == axes.ASSOCIATED_LINEAGE_AXIS:
        return set(fillers)
    if axis_name in {
        axes.PRIMARY_SITE_AXIS,
        axes.PRIMARY_SUBSITE_AXIS,
        axes.ASSOCIATED_REGION_AXIS,
        "op:AssociatedSite",
        "op:MetastaticSite",
    }:
        broader = _location_broader(is_ancestor, is_part_of)
        return most_specific(fillers, broader) or set(fillers)
    return most_specific(fillers, is_ancestor) or set(fillers)


def _append_morphology(
    constituents: list[Constituent], parent_morphology: str | None
) -> None:
    if parent_morphology is not None:
        constituents.append(
            Constituent(
                axis=axes.MORPHOLOGY_AXIS,
                filler_code=parent_morphology,
                axis_source="parent",
            )
        )


def select_constituents(
    restrictions: Iterable[RoleRestriction],
    is_ancestor: IsAncestor,
    *,
    parent_morphology: str | None = None,
    semantic_type_of: Callable[[str], str | None] | None = None,
    is_part_of: IsPartOf | None = None,
    concept_code: str | None = None,
) -> list[Constituent]:
    """Turn a concept's stated role restrictions into its selected constituents.

    Three independent suppressions drop restrictions before routing, and all three
    delete would-be constituents silently:

    * non-defining restrictions — ``Excludes_*`` negative axioms and the
      probabilistic ``May_Have_*`` roles (``axes.DROPPED_ROLES``). Neither is gated
      by a caller flag, but the ``Excludes_*`` test keys on ``role_label`` and so
      misses a restriction whose label did not resolve
    * generic fillers — ``axes.GENERIC_FILLERS_BY_ROLE``, the
      ``contracted-role-generic-v2`` audit set (D59)
    * concept-role fillers the projection does not support —
      ``axes.UNSUPPORTED_FILLERS_BY_CONCEPT_ROLE``,
      the ``ncit-26.07d-unsupported-filler-v1`` set

    The survivors are grouped by routed axis (D20 refinement 1), collapsed to their
    most-specific filler(s) on hierarchy-comparable axes, and all associated-lineage
    fillers are preserved.
    It then applies D20 refinement 2 (semantic-type ranking on residual R101 leaves) and
    assigns D19 relationship-group ids to ambiguous routed-axis values. Output is sorted
    (axis, filler) for deterministic, diffable results.
    """
    by_axis, source_roles = _group_by_routed_axis(
        restrictions, parent_morphology, concept_code
    )
    constituents = _iter_axis_constituents(
        by_axis,
        source_roles,
        is_ancestor,
        semantic_type_of,
        parent_morphology,
        is_part_of,
    )
    _append_morphology(constituents, parent_morphology)
    return sorted(constituents, key=lambda c: (c.axis, c.filler_code))
