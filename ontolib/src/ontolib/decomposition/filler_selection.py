"""Filler selection — choose the intended constituent(s) per axis (design §6).

Working from the *stated* graph already eliminates most ancestor bleed; most-specific
selection is defense-in-depth for any axis that still returns multiple fillers. The
selection is a pure function of the fillers and an injected ``is_ancestor`` predicate,
so it is fully unit-testable without a store.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable

from ontolib.decomposition.axes import MORPHOLOGY_AXIS, is_defining_role
from ontolib.decomposition.models import Constituent, RoleRestriction

# ``is_ancestor(a, b)`` is True when concept *a* is a (proper) superclass of *b*.
IsAncestor = Callable[[str, str], bool]


def filter_excluded(restrictions: Iterable[RoleRestriction]) -> list[RoleRestriction]:
    """Drop ``Excludes_*`` negative axioms, keeping only defining role restrictions."""
    return [r for r in restrictions if is_defining_role(r)]


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


def _axis_constituents(
    axis: str, fillers: set[str], is_ancestor: IsAncestor
) -> list[Constituent]:
    """Select the constituent(s) for one axis: collapse to leaves, flag review/most-
    specific per filler. A cyclic hierarchy (which would drop every filler) falls back
    to keeping all fillers so the axis is never silently lost."""
    leaves = most_specific(fillers, is_ancestor) or set(fillers)
    ambiguous = len(leaves) > 1  # multiple candidate leaves — needs curation
    return [
        Constituent(
            axis=axis,
            filler_code=filler,
            axis_source="role",
            # Per-filler: this filler was chosen over an ancestor present in the set.
            most_specific=any(o != filler and is_ancestor(o, filler) for o in fillers),
            needs_review=ambiguous,
        )
        for filler in leaves
    ]


def select_constituents(
    restrictions: Iterable[RoleRestriction],
    is_ancestor: IsAncestor,
    *,
    parent_morphology: str | None = None,
) -> list[Constituent]:
    """Turn a concept's stated role restrictions into its selected constituents.

    Excludes ``Excludes_*`` axioms, groups the rest by axis (role code), collapses each
    axis to its most-specific filler(s), flags a genuinely multi-leaf axis for review,
    and appends the morphology-from-parent constituent when one is supplied. Output is
    sorted (axis, filler) for deterministic, diffable results.
    """
    by_axis: dict[str, set[str]] = defaultdict(set)
    for r in filter_excluded(restrictions):
        by_axis[r.role_code].add(r.filler_code)

    constituents = [
        c
        for axis, fillers in by_axis.items()
        for c in _axis_constituents(axis, fillers, is_ancestor)
    ]

    if parent_morphology is not None:
        constituents.append(
            Constituent(
                axis=MORPHOLOGY_AXIS,
                filler_code=parent_morphology,
                axis_source="parent",
            )
        )

    return sorted(constituents, key=lambda c: (c.axis, c.filler_code))
