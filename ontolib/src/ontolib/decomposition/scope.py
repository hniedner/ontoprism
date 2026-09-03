"""Hierarchy-defined decomposition scopes over NCIt's stated named-class DAG."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from ontolib.terminologies.namespaces import NCIT_NS, OWL_NS, RDF_NS, RDFS_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.sparql_transport import safe_iri

if TYPE_CHECKING:
    from collections.abc import (
        Awaitable,
        Collection,
        Iterable,
        Mapping,
        Sequence,
    )

_PREFIXES = f"""
PREFIX owl: <{OWL_NS}>
PREFIX rdf: <{RDF_NS}>
PREFIX rdfs: <{RDFS_NS}>
"""
_GENUS_POSITIONS = 6


class ScopeSelectClient(Protocol):
    """Single-attempt reads needed to materialize one exact scope."""

    def select_once(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> Awaitable[Sequence[Mapping[str, str | None]]]: ...


class ScopeHierarchyError(RuntimeError):
    """The stated hierarchy cannot be converted into a trustworthy scope."""


@dataclass(frozen=True, slots=True)
class HierarchyEdge:
    """One named child-to-parent edge in NCIt's stated hierarchy."""

    child: str
    parent: str


def _rest_first_pattern(position: int, *, member: str = "?parent") -> str:
    if position == 0:
        return f"?list rdf:first {member} ."
    lines: list[str] = []
    previous = "?list"
    for index in range(position):
        current = f"?rest{index}"
        lines.append(f"{previous} rdf:rest {current} .")
        previous = current
    lines.append(f"{previous} rdf:first {member} .")
    return "\n".join(lines)


def _rest_prefix_pattern(length: int) -> str:
    lines: list[str] = []
    previous = "?list"
    for index in range(length):
        current = f"?overflowRest{index}"
        lines.append(f"{previous} rdf:rest {current} .")
        previous = current
    lines.append(f"{previous} rdf:rest*/rdf:first ?parent .")
    return "\n".join(lines)


def _named_ncit_filter(*variables: str) -> str:
    clauses = [
        f'isIRI({variable}) && STRSTARTS(STR({variable}), "{NCIT_NS}")'
        for variable in variables
    ]
    return f"FILTER({' && '.join(clauses)})"


def _definition_genus_query(position: int) -> str:
    return f"""{_PREFIXES}
SELECT DISTINCT ?child ?parent WHERE {{
  GRAPH <{STATED_GRAPH_IRI}> {{
    ?child owl:equivalentClass ?expression .
    ?expression owl:intersectionOf ?list .
    {_rest_first_pattern(position)}
    {_named_ncit_filter("?child", "?parent")}
  }}
}}
"""


def build_scope_edge_queries() -> tuple[str, ...]:
    """Return bounded queries for direct subclass and definition-genus edges."""
    subclass = f"""{_PREFIXES}
SELECT DISTINCT ?child ?parent WHERE {{
  GRAPH <{STATED_GRAPH_IRI}> {{
    ?child rdfs:subClassOf ?parent .
    {_named_ncit_filter("?child", "?parent")}
  }}
}}
"""
    genus = tuple(
        _definition_genus_query(position) for position in range(_GENUS_POSITIONS)
    )
    return (subclass, *genus)


def build_scope_overflow_query() -> str:
    """Detect any named genus after the bounded prefix instead of dropping it."""
    return f"""{_PREFIXES}
SELECT DISTINCT ?overflowChild WHERE {{
  GRAPH <{STATED_GRAPH_IRI}> {{
    ?overflowChild owl:equivalentClass ?expression .
    ?expression owl:intersectionOf ?list .
    {_rest_prefix_pattern(_GENUS_POSITIONS)}
    {_named_ncit_filter("?overflowChild", "?parent")}
  }}
}}
LIMIT 1
"""


def _concept_code(value: str | None, binding: str) -> str:
    if value is None or not value.startswith(NCIT_NS):
        raise ScopeHierarchyError(f"scope edge has invalid {binding} NCIt IRI")
    code = value.removeprefix(NCIT_NS)
    try:
        safe_iri(code, NCIT_NS)
    except ValueError as exc:
        raise ScopeHierarchyError(
            f"scope edge has invalid {binding} NCIt code"
        ) from exc
    if not code.startswith("C") or not code[1:].isdigit():
        raise ScopeHierarchyError(f"scope edge has invalid {binding} NCIt code")
    return code


def _edges_from_rows(
    rows: Iterable[Mapping[str, str | None]],
) -> set[HierarchyEdge]:
    return {
        HierarchyEdge(
            child=_concept_code(row.get("child"), "child"),
            parent=_concept_code(row.get("parent"), "parent"),
        )
        for row in rows
    }


def descendant_codes(
    root_code: str,
    edges: Iterable[HierarchyEdge],
) -> tuple[str, ...]:
    """Return every strict descendant, sorted and duplicate-free."""
    root_code = _concept_code(f"{NCIT_NS}{root_code}", "root")
    children: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        children[edge.parent].add(edge.child)
    reached = {root_code}
    frontier = deque([root_code])
    while frontier:
        unseen = children[frontier.popleft()] - reached
        reached.update(unseen)
        frontier.extend(unseen)
    reached.remove(root_code)
    return tuple(sorted(reached))


async def enumerate_scope_codes(
    client: ScopeSelectClient,
    root_code: str,
) -> tuple[str, ...]:
    """Materialize one hierarchy scope from complete bounded edge observations."""
    return descendant_codes(root_code, await read_scope_hierarchy_edges(client))


async def read_scope_hierarchy_edges(
    client: ScopeSelectClient,
) -> tuple[HierarchyEdge, ...]:
    """Read the complete bounded stated named-class hierarchy once."""
    edges: set[HierarchyEdge] = set()
    for query in build_scope_edge_queries():
        rows = await client.select_once(
            query,
            required_variables={"child", "parent"},
        )
        edges.update(_edges_from_rows(rows))
    overflow = await client.select_once(
        build_scope_overflow_query(),
        required_variables={"overflowChild"},
    )
    if overflow:
        raise ScopeHierarchyError(
            "stated hierarchy contains a named genus beyond the bounded genus positions"
        )
    return tuple(sorted(edges, key=lambda edge: (edge.child, edge.parent)))
