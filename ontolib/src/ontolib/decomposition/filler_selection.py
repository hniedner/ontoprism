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
from ontolib.decomposition.site_resolution import organ_for_morphology

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
    """Map each restriction to its target axis.

    * R101 with lineage-generic ``anchoring_genus`` → ``ASSOCIATED_LINEAGE_AXIS``
    * R88 with a known stage-system filler code → ``STAGE_SYSTEM_AXIS``
    * Everything else keeps its role code as the axis.
    """
    if r.role_code == axes.PRIMARY_SITE_ROLE and axes.is_lineage_generic(
        r.anchoring_genus
    ):
        return axes.ASSOCIATED_LINEAGE_AXIS
    if r.role_code == "R88" and r.filler_code in _STAGE_SYSTEM_CODES:
        return axes.STAGE_SYSTEM_AXIS
    return r.role_code


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
_STAGE_SYSTEM_CODES: frozenset[str] = frozenset(
    {
        "C132248",  # AJCC v8 Stage
        "C180901",  # AJCC v9 Stage
        "C186617",  # FIGO 2018 Stage
        "C186618",  # FIGO 2009 Stage
        "C198024",  # Toronto Classification v2 Stage, Tier 2
        "C90529",  # AJCC v6 Stage
        "C90530",  # AJCC v7 Stage
    }
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
    organ_ambiguous = len(organ) > 1
    region_ambiguous = len(region) > 1
    for filler in organ:
        result.append(
            Constituent(
                axis=axes.PRIMARY_SITE_ROLE,
                filler_code=filler,
                axis_source="role",
                most_specific=_is_most_specific(filler, fillers, is_ancestor),
                needs_review=organ_ambiguous,
            )
        )
    for filler in region:
        result.append(
            Constituent(
                axis=axes.ASSOCIATED_REGION_AXIS,
                filler_code=filler,
                axis_source="role",
                most_specific=_is_most_specific(filler, fillers, is_ancestor),
                needs_review=False,
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
    is_routed = axis_name in _REVIEW_EXEMPT_AXES
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


def _resolve_r101_with_organ_lookup(
    leaves: set[str],
    fillers: set[str],
    is_ancestor: IsAncestor,
    parent_morphology: str | None,
    axis_name: str = "",
) -> list[Constituent] | None:
    """When the morphology has a known D23 organ mapping, prefer it over
    generic or data-quality-issue R101 candidates.

    Short-circuits the generic semantic-type ranking when the known organ
    is among the surviving leaves. Returns ``None`` to fall through to
    existing logic when no mapping applies.
    """
    if (
        axis_name != axes.PRIMARY_SITE_ROLE
        or parent_morphology is None
        or len(leaves) <= 1
    ):
        return None
    organ = organ_for_morphology(parent_morphology)
    if organ is None or organ not in leaves:
        return None
    return [
        Constituent(
            axis=axes.PRIMARY_SITE_ROLE,
            filler_code=organ,
            axis_source="role",
            most_specific=_is_most_specific(organ, fillers, is_ancestor),
            needs_review=False,
        )
    ]


def _iter_axis_constituents(
    by_axis: dict[str, set[str]],
    is_ancestor: IsAncestor,
    semantic_type_of: Callable[[str], str | None] | None,
    parent_morphology: str | None = None,
) -> list[Constituent]:
    result: list[Constituent] = []
    for axis_name, fillers in by_axis.items():
        result.extend(
            _constituents_for_axis(
                axis_name, fillers, is_ancestor, semantic_type_of, parent_morphology
            )
        )
    return result


def _constituents_for_axis(
    axis_name: str,
    fillers: set[str],
    is_ancestor: IsAncestor,
    semantic_type_of: Callable[[str], str | None] | None,
    parent_morphology: str | None,
) -> list[Constituent]:
    leaves = _resolved_leaves(axis_name, fillers, is_ancestor)

    resolved = _resolve_r101_with_organ_lookup(
        leaves, fillers, is_ancestor, parent_morphology, axis_name
    )
    if resolved is not None:
        return resolved

    if _is_r101_semantic_split(axis_name, leaves, semantic_type_of):
        narrowed = cast("Callable[[str], str | None]", semantic_type_of)
        split = _r101_semantic_type_constituents(leaves, fillers, is_ancestor, narrowed)
        if split:
            return split

    return _standard_constituents(axis_name, leaves, fillers, is_ancestor)


def _resolved_leaves(
    axis_name: str, fillers: set[str], is_ancestor: IsAncestor
) -> set[str]:
    if axis_name == axes.ASSOCIATED_LINEAGE_AXIS:
        return set(fillers)
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
) -> list[Constituent]:
    """Turn a concept's stated role restrictions into its selected constituents.

    Excludes ``Excludes_*`` axioms, groups the rest by routed axis (D20 refinement 1),
    collapses each axis to its most-specific filler(s), applies D20 refinement 2
    (semantic-type ranking on residual R101 leaves), and assigns D19 relationship-group
    ids to co-equal non-nested leaves on routed axes. Output is sorted (axis, filler)
    for deterministic, diffable results.
    """
    by_axis = _group_by_routed_axis(restrictions)
    constituents = _iter_axis_constituents(
        by_axis, is_ancestor, semantic_type_of, parent_morphology
    )
    _append_morphology(constituents, parent_morphology)
    return sorted(constituents, key=lambda c: (c.axis, c.filler_code))
