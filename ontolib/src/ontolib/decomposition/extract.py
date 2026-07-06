"""Pure assembly of SPARQL result rows into decomposition models.

Kept separate from the async query execution (which lives in the caller / #5b's
orchestrator) so every parsing rule is unit-tested without a store. The integration
layer only wires ``client.select(build_*_query(...))`` into these helpers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ontolib.decomposition.models import RoleRestriction

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

Row = dict[str, str | None]


def _code(iri: str | None) -> str | None:
    """Local NCIt code from a Thesaurus IRI (``…Thesaurus.owl#C6135`` -> ``C6135``)."""
    if not iri:
        return None
    return iri.rsplit("#", 1)[-1]


def roles_from_rows(rows: Iterable[Row]) -> list[RoleRestriction]:
    """Parse ``?rel``/``?relLabel``/``?target`` rows into role restrictions.

    Rows missing a role or a filler are skipped (an incomplete binding is not a usable
    restriction).
    """
    restrictions: list[RoleRestriction] = []
    for row in rows:
        role_code = _code(row.get("rel"))
        filler_code = _code(row.get("target"))
        # Skip incomplete rows and any empty code (an IRI ending in ``#``), consistent
        # with ancestor_pairs_from_rows below.
        if not role_code or not filler_code:
            continue
        restrictions.append(
            RoleRestriction(
                role_code=role_code,
                filler_code=filler_code,
                role_label=row.get("relLabel"),
            )
        )
    return restrictions


def semantic_types_from_rows(rows: Iterable[Row]) -> list[str]:
    """All distinct ``?semanticType`` literals, sorted (deterministic).

    NCIt concepts can carry several semantic types; the caller must consider all of
    them, so this returns the full set rather than an arbitrary first row.
    """
    return sorted({v for row in rows if (v := row.get("semanticType"))})


def ancestor_pairs_from_rows(rows: Iterable[Row]) -> set[tuple[str, str]]:
    """Parse ``?ancestor``/``?descendant`` rows into ``(ancestor, descendant)``."""
    pairs: set[tuple[str, str]] = set()
    for row in rows:
        ancestor = _code(row.get("ancestor"))
        descendant = _code(row.get("descendant"))
        if ancestor and descendant:
            pairs.add((ancestor, descendant))
    return pairs


def make_is_ancestor(pairs: set[tuple[str, str]]) -> Callable[[str, str], bool]:
    """Build an ``is_ancestor(a, b)`` predicate from a set of ancestor pairs."""
    return lambda a, b: (a, b) in pairs
