"""Pure assembly of SPARQL result rows into decomposition models.

Kept separate from the async query execution (which lives in the caller / #5b's
orchestrator) so every parsing rule is unit-tested without a store. The integration
layer only wires ``client.select(build_*_query(...))`` into these helpers.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ontolib.decomposition.models import RoleRestriction
from ontolib.terminologies.namespaces import NCIT_NS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

Row = Mapping[str, str | None]
_NCIT_CONCEPT_CODE = re.compile(r"C[0-9]+")


@dataclass(frozen=True, slots=True, kw_only=True)
class PartOfPair:
    """One NCIt R82 edge from an anatomic part to its containing whole."""

    part: str
    whole: str

    def __post_init__(self) -> None:
        for binding, code in (("part", self.part), ("whole", self.whole)):
            if _NCIT_CONCEPT_CODE.fullmatch(code) is None:
                raise ValueError(f"{binding} is not an NCIt concept code: {code!r}")


@dataclass(frozen=True, slots=True, kw_only=True)
class AncestorPair:
    """One directed ancestor-descendant relationship."""

    ancestor: str
    descendant: str


def _code(iri: str | None) -> str | None:
    """Local NCIt code from a Thesaurus IRI (``…Thesaurus.owl#C6135`` -> ``C6135``)."""
    if not iri:
        return None
    return iri.rsplit("#", 1)[-1]


def _required_binding(row: Row, binding: str) -> str:
    value = row.get(binding)
    if not value:
        raise ValueError(f"SPARQL result row is missing required {binding!r} binding")
    return value


def _required_code(row: Row, binding: str) -> str:
    code = _code(_required_binding(row, binding))
    if not code:
        raise ValueError(f"SPARQL result row is missing required {binding!r} binding")
    return code


def roles_from_rows(rows: Iterable[Row]) -> list[RoleRestriction]:
    """Parse ``?rel``/``?relLabel``/``?target`` rows into role restrictions.

    ``relLabel`` is optional; the query guarantees ``rel`` and ``target``, so missing
    required bindings abort extraction rather than silently dropping a restriction.
    """
    restrictions: list[RoleRestriction] = []
    for row in rows:
        role_code = _required_code(row, "rel")
        filler_code = _required_code(row, "target")
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
    return sorted({_required_binding(row, "semanticType") for row in rows})


def ancestor_pairs_from_rows(rows: Iterable[Row]) -> set[AncestorPair]:
    """Parse required ``?ancestor``/``?descendant`` rows into directed pairs."""
    pairs: set[AncestorPair] = set()
    for row in rows:
        pairs.add(
            AncestorPair(
                ancestor=_required_code(row, "ancestor"),
                descendant=_required_code(row, "descendant"),
            )
        )
    return pairs


def make_is_ancestor(pairs: set[AncestorPair]) -> Callable[[str, str], bool]:
    """Build an ``is_ancestor(a, b)`` predicate from a set of ancestor pairs."""
    return lambda a, b: AncestorPair(ancestor=a, descendant=b) in pairs


def concepts_from_rows(rows: Iterable[Row]) -> list[str]:
    """Parse ``?concept`` rows (e.g. ``build_in_scope_concepts_query``) into codes.

    Preserves row order (the query's ``ORDER BY`` makes it the paging order). The query
    guarantees ``concept``; a missing binding aborts paging rather than ending it early.
    """
    codes: list[str] = []
    for row in rows:
        codes.append(_required_code(row, "concept"))
    return codes


def _add_role_if_new(
    row: Row,
    roles: list[RoleRestriction],
    seen: set[tuple[str, str]],
) -> None:
    role_code = _required_code(row, "role")
    filler_code = _required_code(row, "target")
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
    genus = _required_code(row, "member")
    if genus not in seen:
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
        _required_binding(row, "member")
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
        code = _required_binding(row, "code")
        st = _required_binding(row, "st")
        result.setdefault(code, []).append(st)
    return result


def _required_ncit_code(row: Row, binding: str) -> str:
    iri = row.get(binding)
    if not iri:
        raise ValueError("R82 result row is missing required part/whole binding")
    if not iri.startswith(NCIT_NS):
        raise ValueError(f"R82 result {binding} is not an NCIt IRI")
    code = iri.removeprefix(NCIT_NS)
    if not code:
        raise ValueError("R82 result row is missing required part/whole binding")
    return code


def part_of_pairs_from_rows(rows: Iterable[Row]) -> list[PartOfPair]:
    """Parse required ``?part``/``?whole`` IRI bindings into typed R82 edges."""
    pairs: list[PartOfPair] = []
    for row in rows:
        pairs.append(
            PartOfPair(
                part=_required_ncit_code(row, "part"),
                whole=_required_ncit_code(row, "whole"),
            )
        )
    return pairs
