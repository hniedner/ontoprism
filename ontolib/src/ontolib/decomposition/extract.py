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


def concepts_from_rows(rows: Iterable[Row]) -> list[str]:
    """Parse ``?concept`` rows (e.g. ``build_in_scope_concepts_query``) into codes.

    Preserves row order (the query's ``ORDER BY`` makes it the paging order); rows
    missing a concept or with an empty code (an IRI ending in ``#``) are skipped.
    """
    codes: list[str] = []
    for row in rows:
        code = _code(row.get("concept"))
        if code:
            codes.append(code)
    return codes


def _add_role_if_new(
    row: Row,
    roles: list[RoleRestriction],
    seen: set[tuple[str, str]],
) -> None:
    role_code = _code(row.get("role"))
    filler_code = _code(row.get("target"))
    if not role_code or not filler_code:
        return
    key = (role_code, filler_code)
    if key not in seen:
        seen.add(key)
        roles.append(
            RoleRestriction(
                role_code=role_code,
                filler_code=filler_code,
                role_label=row.get("roleLabel"),
            )
        )


def _add_genus_if_new(
    row: Row,
    genuses: list[str],
    seen: set[str],
) -> None:
    genus = _code(row.get("member"))
    if genus and genus not in seen:
        seen.add(genus)
        genuses.append(genus)


def genus_walk_rows_to_roles_and_genuses(
    rows: Iterable[Row],
) -> tuple[list[RoleRestriction], list[str]]:
    roles: list[RoleRestriction] = []
    genuses: list[str] = []
    seen_roles: set[tuple[str, str]] = set()
    seen_genuses: set[str] = set()

    for row in rows:
        if row.get("type") == "http://www.w3.org/2002/07/owl#Restriction":
            _add_role_if_new(row, roles, seen_roles)
        else:
            _add_genus_if_new(row, genuses, seen_genuses)

    return roles, genuses


def semantic_type_of_from_rows(
    rows: Iterable[Row],
) -> dict[str, list[str]]:
    """Parse ``?code``/``?st`` batch rows into ``{code: [semantic_types]}``.

    A concept may carry multiple semantic types; all are collected per code.
    """
    result: dict[str, list[str]] = {}
    for row in rows:
        code = row.get("code")
        st = row.get("st")
        if code and st:
            result.setdefault(code, []).append(st)
    return result


def part_of_pairs_from_rows(rows: Iterable[Row]) -> list[tuple[str, str]]:
    """Parse ``?whole``/``?part`` rows into ``(whole, part)`` pairs.

    ``?whole`` is a code string from ``REPLACE(STR(?descendant), ...)`` while
    ``?part`` is a full IRI from ``owl:someValuesFrom ?part`` — both are
    normalised via ``_code()`` so the output is consistently ``(code, code)``.
    """
    pairs: list[tuple[str, str]] = []
    for row in rows:
        whole = _code(row.get("whole"))
        part = _code(row.get("part"))
        if whole and part:
            pairs.append((whole, part))
    return pairs
