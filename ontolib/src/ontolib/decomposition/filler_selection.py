"""Filler selection — choose the intended constituent(s) per axis (design §6).

Working from the *stated* graph already eliminates most ancestor bleed; most-specific
selection is defense-in-depth for any axis that still returns multiple fillers. The
selection is a pure function of the fillers and an injected ``is_ancestor`` predicate,
so it is fully unit-testable without a store.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import cast

from ontolib.decomposition import axes
from ontolib.decomposition.models import Constituent, RoleRestriction

# ``is_ancestor(a, b)`` is True when concept *a* is a (proper) superclass of *b*.
IsAncestor = Callable[[str, str], bool]


def filter_excluded(restrictions: Iterable[RoleRestriction]) -> list[RoleRestriction]:
    """Drop ``Excludes_*`` negative axioms, keeping only defining role restrictions."""
    return [r for r in restrictions if axes.is_defining_role(r)]


def most_specific(fillers: set[str], is_ancestor: IsAncestor) -> set[str]:
    """Keep only the hierarchy leaves: drop any filler that is an ancestor of another
    filler in the set. Fillers with no ancestor relationship are all kept (genuine
    multi-filler axis), and a single filler is returned unchanged.
    """
    return {
        f
        for f in fillers
        if not any(other != f and is_ancestor(f, other) for other in fillers)
    }


def route_axis(r: RoleRestriction) -> str:
    """Map each ``R101`` restriction whose ``anchoring_genus`` is lineage-generic to
    ``ASSOCIATED_LINEAGE_AXIS``; everything else keeps its role code as the axis."""
    if r.role_code == axes.PRIMARY_SITE_ROLE and axes.is_lineage_generic(
        r.anchoring_genus
    ):
        return axes.ASSOCIATED_LINEAGE_AXIS
    return r.role_code


_R101_ROUTED_AXES = frozenset(
    {axes.ASSOCIATED_LINEAGE_AXIS, axes.ASSOCIATED_REGION_AXIS}
)


def _is_most_specific(filler: str, fillers: set[str], is_ancestor: IsAncestor) -> bool:
    """True when *filler* was chosen over an ancestor present in *fillers*."""
    return any(o != filler and is_ancestor(o, filler) for o in fillers)


def _is_r101_semantic_split(
    axis_name: str,
    leaves: set[str],
    semantic_type_of: Callable[[str], str | None] | None,
) -> bool:
    return (
        axis_name == axes.PRIMARY_SITE_ROLE
        and semantic_type_of is not None
        and len(leaves) > 1
    )


def _r101_semantic_type_constituents(
    leaves: set[str],
    fillers: set[str],
    is_ancestor: IsAncestor,
    semantic_type_of: Callable[[str], str | None],
) -> list[Constituent]:
    organ = {f for f in leaves if semantic_type_of(f) == axes.ORGAN_SEMANTIC_TYPE}
    region = leaves - organ
    if not region:
        return []

    result: list[Constituent] = []
    for filler in organ:
        result.append(
            Constituent(
                axis=axes.PRIMARY_SITE_ROLE,
                filler_code=filler,
                axis_source="role",
                most_specific=_is_most_specific(filler, fillers, is_ancestor),
            )
        )
    region_ambiguous = len(region) > 1
    for filler in region:
        result.append(
            Constituent(
                axis=axes.ASSOCIATED_REGION_AXIS,
                filler_code=filler,
                axis_source="role",
                most_specific=_is_most_specific(filler, fillers, is_ancestor),
                needs_review=not region_ambiguous,
                group=axes.ASSOCIATED_REGION_AXIS if region_ambiguous else None,
            )
        )
    return result


def _standard_constituents(
    axis_name: str,
    leaves: set[str],
    fillers: set[str],
    is_ancestor: IsAncestor,
) -> list[Constituent]:
    ambiguous = len(leaves) > 1
    is_routed = axis_name in _R101_ROUTED_AXES
    return [
        Constituent(
            axis=axis_name,
            filler_code=filler,
            axis_source="role",
            most_specific=_is_most_specific(filler, fillers, is_ancestor),
            needs_review=not is_routed and ambiguous,
            group=axis_name if is_routed and ambiguous else None,
        )
        for filler in leaves
    ]


def _group_by_routed_axis(
    restrictions: Iterable[RoleRestriction],
) -> dict[str, set[str]]:
    by_axis: dict[str, set[str]] = defaultdict(set)
    for r in filter_excluded(restrictions):
        by_axis[route_axis(r)].add(r.filler_code)
    return by_axis


def _iter_axis_constituents(
    by_axis: dict[str, set[str]],
    is_ancestor: IsAncestor,
    semantic_type_of: Callable[[str], str | None] | None,
) -> list[Constituent]:
    result: list[Constituent] = []
    for axis_name, fillers in by_axis.items():
        leaves = most_specific(fillers, is_ancestor) or set(fillers)

        if _is_r101_semantic_split(axis_name, leaves, semantic_type_of):
            narrowed = cast("Callable[[str], str | None]", semantic_type_of)
            split = _r101_semantic_type_constituents(
                leaves, fillers, is_ancestor, narrowed
            )
            if split:
                result.extend(split)
                continue
        result.extend(_standard_constituents(axis_name, leaves, fillers, is_ancestor))
    return result


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
) -> list[Constituent]:
    """Turn a concept's stated role restrictions into its selected constituents.

    Excludes ``Excludes_*`` axioms, groups the rest by routed axis (D20 refinement 1),
    collapses each axis to its most-specific filler(s), applies D20 refinement 2
    (semantic-type ranking on residual R101 leaves), and assigns D19 relationship-group
    ids to co-equal non-nested leaves on routed axes. Output is sorted (axis, filler)
    for deterministic, diffable results.
    """
    by_axis = _group_by_routed_axis(restrictions)
    constituents = _iter_axis_constituents(by_axis, is_ancestor, semantic_type_of)
    _append_morphology(constituents, parent_morphology)
    return sorted(constituents, key=lambda c: (c.axis, c.filler_code))
