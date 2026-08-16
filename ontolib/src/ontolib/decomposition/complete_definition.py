"""Bounded stated-OWL extraction for the complete decomposition record (#153)."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Awaitable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from ontolib.decomposition.models import (
    CompleteDefinition,
    Constituent,
    DefinitionFact,
    DefinitionGroup,
    GenusDefinitionFact,
    RestrictionDefinitionFact,
    SourceDefinitionOccurrence,
    canonical_definition_fact_id,
    canonical_definition_group_id,
    canonical_source_occurrence_id,
)
from ontolib.terminologies.namespaces import NCIT_NS, OWL_NS, RDF_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.sparql_transport import safe_iri

_MAX_INTERSECTION_MEMBERS = 64
_MAX_NESTING_DEPTH = 4
_PREFIXES = f"""
PREFIX rdf: <{RDF_NS}>
PREFIX owl: <{OWL_NS}>
"""

Row = Mapping[str, str | None]
Member = tuple[str, ...]
PositionedMember = tuple[Member, bool]
GroupedMembers = dict[str, dict[int, PositionedMember]]


class CompleteDefinitionError(ValueError):
    """The stated definition cannot be represented completely and deterministically."""


class UnsupportedDefinitionConstructorError(CompleteDefinitionError):
    """A valid OWL constructor is outside the decomposition representation."""


class SelectRows(Protocol):
    def __call__(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> Awaitable[Sequence[Row]]: ...


@dataclass(frozen=True, slots=True)
class _DefinitionSlice:
    facts: tuple[DefinitionFact, ...]
    groups: tuple[DefinitionGroup, ...]
    root_group_ids: tuple[str, ...]
    occurrences: tuple[SourceDefinitionOccurrence, ...]


def _expression_pattern(concept_iri: str, nesting_depth: int) -> str:
    if nesting_depth == 0:
        return (
            f"<{concept_iri}> owl:equivalentClass ?expression .\n"
            "BIND(0 AS ?nestingDepth)"
        )
    lines = [f"<{concept_iri}> owl:equivalentClass ?rootExpression ."]
    parent = "?rootExpression"
    for level in range(1, nesting_depth + 1):
        member = f"?pathMember{level}"
        expression = f"?pathExpression{level}"
        lines.extend(
            (
                f"{parent} owl:intersectionOf/rdf:rest*/rdf:first {member} .",
                f"FILTER(isBlank({member}))",
                f"{member} owl:equivalentClass? {expression} .",
                f"{expression} owl:intersectionOf ?pathList{level} .",
            )
        )
        if level == nesting_depth:
            lines.extend(
                (
                    f"BIND({parent} AS ?parentExpression)",
                    f"BIND({expression} AS ?expression)",
                    f"BIND({nesting_depth} AS ?nestingDepth)",
                )
            )
        parent = expression
    return "\n".join(lines)


def build_complete_definition_query(
    concept_code: str,
    *,
    nesting_depth: int = 0,
) -> str:
    """Read the proven prefix through one requested stated-OWL nesting level."""
    if nesting_depth < 0 or nesting_depth > _MAX_NESTING_DEPTH:
        raise ValueError(f"nesting depth must be between 0 and {_MAX_NESTING_DEPTH}")
    concept_iri = safe_iri(concept_code, NCIT_NS)
    expression_pattern = "\nUNION\n".join(
        "{\n" + _expression_pattern(concept_iri, depth) + "\n}"
        for depth in range(nesting_depth + 1)
    )
    return f"""{_PREFIXES}
SELECT DISTINCT ?expression ?parentExpression ?nestingDepth ?requestedNestingDepth
       ?list ?cell ?next ?member ?role ?target ?childExpression ?nestedExpression
       ?unionList
WHERE {{
    GRAPH <{STATED_GRAPH_IRI}> {{
        {{
        {expression_pattern}
        }}
        BIND({nesting_depth} AS ?requestedNestingDepth)
        ?expression owl:intersectionOf ?list .
        ?list rdf:rest* ?cell .
        {{ ?cell rdf:first ?cellWitness }}
        UNION
        {{ ?cell rdf:rest ?cellWitness }}
        OPTIONAL {{ ?cell rdf:first ?member }}
        OPTIONAL {{ ?cell rdf:rest ?next }}
        OPTIONAL {{
            ?member owl:onProperty ?role ;
                    owl:someValuesFrom ?target .
        }}
        OPTIONAL {{ ?member owl:equivalentClass ?childExpression }}
        OPTIONAL {{ ?member owl:unionOf ?unionList }}
        OPTIONAL {{
            FILTER(isBlank(?member))
            ?member owl:equivalentClass? ?nestedExpression .
            ?nestedExpression owl:intersectionOf ?nestedList .
        }}
    }}
}}
ORDER BY STR(?expression) STR(?cell)
"""


def _required(row: Row, binding: str) -> str:
    value = row.get(binding)
    if value is None or value == "":
        raise CompleteDefinitionError(f"complete-definition row is missing {binding!r}")
    return value


def _ncit_code(value: str, *, binding: str, prefix: str) -> str:
    if not value.startswith(NCIT_NS):
        raise CompleteDefinitionError(f"{binding} is not an NCIt IRI")
    code = value.removeprefix(NCIT_NS)
    if not code.startswith(prefix) or not code[len(prefix) :].isdigit():
        raise CompleteDefinitionError(f"{binding} is not an NCIt {prefix} code")
    return code


def _restriction_member(role: str | None, target: str | None) -> Member:
    if role is None:
        raise CompleteDefinitionError("restriction row is missing 'role' binding")
    if target is None:
        raise CompleteDefinitionError("restriction row is missing 'target' binding")
    return (
        "restriction",
        _ncit_code(role, binding="role", prefix="R"),
        _ncit_code(target, binding="target", prefix="C"),
    )


def _genus_member(row: Row) -> Member:
    return (
        "genus",
        _ncit_code(_required(row, "member"), binding="member", prefix="C"),
        ("defined" if row.get("childExpression") not in {None, ""} else "primitive"),
    )


def _require_supported_member(row: Row) -> None:
    if row.get("unionList") not in {None, ""}:
        raise UnsupportedDefinitionConstructorError(
            "complete definition contains unsupported owl:unionOf member"
        )


def _member_key(row: Row) -> Member:
    role = row.get("role")
    target = row.get("target")
    nested_expression = row.get("nestedExpression")
    if isinstance(nested_expression, str) and nested_expression:
        if role is not None or target is not None:
            raise CompleteDefinitionError(
                "nested definition member cannot also be a restriction"
            )
        return ("group", nested_expression)
    if role is not None or target is not None:
        return _restriction_member(role, target)
    _require_supported_member(row)
    return _genus_member(row)


def _definition_position(row: Row) -> int:
    if row.get("overflow") in {"true", "1"}:
        raise CompleteDefinitionError(
            f"definition exceeds the {_MAX_INTERSECTION_MEMBERS} member list bound"
        )
    try:
        position = int(_required(row, "position"))
    except ValueError as exc:
        raise CompleteDefinitionError("definition position is not an integer") from exc
    if position < 0 or position >= _MAX_INTERSECTION_MEMBERS:
        raise CompleteDefinitionError("definition position exceeds list bound")
    return position


def _linked_group_depths(
    expressions: set[str],
    parents: Mapping[str, set[str]],
) -> dict[str, int]:
    depths: dict[str, int] = {}
    visiting: set[str] = set()

    def depth(expression: str) -> int:
        existing = depths.get(expression)
        if existing is not None:
            return existing
        if expression in visiting:
            raise CompleteDefinitionError("nested definition groups contain a cycle")
        visiting.add(expression)
        parent_expressions = parents.get(expression, set())
        missing = parent_expressions - expressions
        if missing:
            raise CompleteDefinitionError(
                "nested definition group is missing its parent"
            )
        resolved = (
            max(depth(parent) + 1 for parent in parent_expressions)
            if parent_expressions
            else 0
        )
        visiting.remove(expression)
        if resolved > _MAX_NESTING_DEPTH:
            raise CompleteDefinitionError(
                f"definition exceeds nesting depth bound {_MAX_NESTING_DEPTH}"
            )
        depths[expression] = resolved
        return resolved

    for expression in expressions:
        depth(expression)
    return depths


def _linked_cell_signature(row: Row) -> tuple[str | None, ...]:
    return (row.get("next"), *_member_key(row))


def _record_linked_cell(expression_cells: dict[str, Row], cell: str, row: Row) -> None:
    previous = expression_cells.get(cell)
    if previous is None:
        expression_cells[cell] = row
        return
    if _linked_cell_signature(previous) != _linked_cell_signature(row):
        raise CompleteDefinitionError(
            "one RDF list cell resolved to conflicting members"
        )
    raise CompleteDefinitionError(
        "complete-definition response has a duplicate RDF list cell binding"
    )


def _collect_linked_rows(
    rows: list[Row],
) -> tuple[dict[str, str], dict[str, dict[str, Row]], dict[str, set[str]]]:
    lists: dict[str, str] = {}
    cells: dict[str, dict[str, Row]] = defaultdict(dict)
    parents: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        expression = _required(row, "expression")
        list_node = _required(row, "list")
        cell = _required(row, "cell")
        previous_list = lists.setdefault(expression, list_node)
        if previous_list != list_node:
            raise CompleteDefinitionError(
                "one definition expression resolved to conflicting RDF lists"
            )
        _record_linked_cell(cells[expression], cell, row)
        parent = row.get("parentExpression")
        if isinstance(parent, str) and parent:
            parents[expression].add(parent)
    return lists, cells, parents


def _normalize_linked_group(
    expression: str,
    *,
    list_node: str,
    cells: Mapping[str, Row],
    parents: set[str],
    nesting_depth: int,
) -> list[Row]:
    normalized: list[Row] = []
    rdf_nil = f"{RDF_NS}nil"
    current = list_node
    visited: set[str] = set()
    position = 0
    while current != rdf_nil:
        row, next_cell = _linked_cell(cells, current, visited, position)
        normalized.extend(
            _normalized_parent_rows(
                row,
                parents=parents,
                nesting_depth=nesting_depth,
                position=position,
            )
        )
        visited.add(current)
        current = next_cell
        position += 1
    if visited != cells.keys():
        raise CompleteDefinitionError("definition RDF list returned disconnected cells")
    return normalized


def _linked_cell(
    cells: Mapping[str, Row],
    current: str,
    visited: set[str],
    position: int,
) -> tuple[Row, str]:
    if current in visited:
        raise CompleteDefinitionError("definition RDF list contains a cycle")
    if position >= _MAX_INTERSECTION_MEMBERS:
        raise CompleteDefinitionError(
            f"definition exceeds the {_MAX_INTERSECTION_MEMBERS} member list bound"
        )
    row = cells.get(current)
    if row is None:
        raise CompleteDefinitionError("definition RDF list has a missing cell")
    _required(row, "member")
    return row, _required(row, "next")


def _normalized_parent_rows(
    row: Row,
    *,
    parents: set[str],
    nesting_depth: int,
    position: int,
) -> list[Row]:
    parent_expressions: tuple[str | None, ...] = (
        tuple(sorted(parents)) if parents else (None,)
    )
    return [
        dict(row)
        | {
            "parentExpression": parent,
            "nestingDepth": str(nesting_depth),
            "position": str(position),
            "overflow": "false",
        }
        for parent in parent_expressions
    ]


def _normalize_linked_rows(rows: list[Row]) -> list[Row]:
    lists, cells, parents = _collect_linked_rows(rows)
    depths = _linked_group_depths(set(lists), parents)
    return [
        row
        for expression, list_node in lists.items()
        for row in _normalize_linked_group(
            expression,
            list_node=list_node,
            cells=cells[expression],
            parents=parents.get(expression, set()),
            nesting_depth=depths[expression],
        )
    ]


def _normalize_definition_rows(rows: Iterable[Row]) -> list[Row]:
    materialized = list(rows)
    if not materialized:
        return []
    linked_count = sum(_is_linked_row(row) for row in materialized)
    if linked_count not in {0, len(materialized)}:
        raise CompleteDefinitionError(
            "complete-definition response mixes linked and positional rows"
        )
    return _normalize_linked_rows(materialized) if linked_count else materialized


def _is_linked_row(row: Row) -> bool:
    return any(binding in row for binding in ("cell", "list", "next"))


def _nesting_depth(row: Row) -> int:
    raw_depth = row.get("nestingDepth")
    if not isinstance(raw_depth, str) or not raw_depth:
        return 0
    try:
        nesting_depth = int(raw_depth)
    except ValueError as exc:
        raise CompleteDefinitionError(
            "definition nesting depth is not an integer"
        ) from exc
    if nesting_depth < 0 or nesting_depth > _MAX_NESTING_DEPTH:
        raise CompleteDefinitionError(
            f"definition exceeds nesting depth bound {_MAX_NESTING_DEPTH}"
        )
    return nesting_depth


def _add_grouped_row(
    grouped: GroupedMembers,
    parents: dict[str, set[str]],
    nesting_depths: dict[str, int],
    row: Row,
) -> None:
    expression = _required(row, "expression")
    nesting_depth = _nesting_depth(row)
    _record_group_parent(parents, expression, nesting_depth, row)
    _record_nesting_depth(nesting_depths, expression, nesting_depth)
    _record_group_position(grouped[expression], row)


def _record_group_parent(
    parents: dict[str, set[str]],
    expression: str,
    nesting_depth: int,
    row: Row,
) -> None:
    parent = row.get("parentExpression")
    if nesting_depth == 0 and parent not in {None, ""}:
        raise CompleteDefinitionError("root definition group unexpectedly has a parent")
    if nesting_depth > 0 and parent in {None, ""}:
        raise CompleteDefinitionError("nested definition group is missing its parent")
    if isinstance(parent, str) and parent:
        parents[expression].add(parent)


def _record_nesting_depth(
    nesting_depths: dict[str, int],
    expression: str,
    nesting_depth: int,
) -> None:
    previous_depth = nesting_depths.setdefault(expression, nesting_depth)
    if previous_depth != nesting_depth:
        raise CompleteDefinitionError(
            "one definition group resolved to conflicting nesting depths"
        )


def _record_group_position(
    positions: dict[int, PositionedMember],
    row: Row,
) -> None:
    position = _definition_position(row)
    member = _member_key(row)
    is_defined = member[0] == "genus" and member[2] == "defined"
    previous = positions.get(position)
    if previous is not None and previous[0] != member:
        raise CompleteDefinitionError(
            "one definition position resolved to conflicting members"
        )
    if previous is not None:
        raise CompleteDefinitionError(
            "complete-definition response has a duplicate position binding"
        )
    positions[position] = (
        member,
        is_defined,
    )


def _group_definition_rows(
    rows: Iterable[Row],
) -> tuple[GroupedMembers, dict[str, set[str]], dict[str, int]]:
    grouped: GroupedMembers = defaultdict(dict)
    parents: dict[str, set[str]] = defaultdict(set)
    nesting_depths: dict[str, int] = {}
    for row in _normalize_definition_rows(rows):
        _add_grouped_row(grouped, parents, nesting_depths, row)
    return grouped, parents, nesting_depths


def _ordered_members(
    positions: Mapping[int, PositionedMember],
) -> tuple[PositionedMember, ...]:
    ordered_positions = sorted(positions)
    if ordered_positions != list(range(len(ordered_positions))):
        raise CompleteDefinitionError("definition list has a missing position")
    return tuple(positions[position] for position in ordered_positions)


def _definition_fact(
    anchor_code: str,
    group_id: str,
    depth: int,
    member: Member,
    is_defined: bool,
) -> DefinitionFact:
    if member[0] == "genus":
        fact_id = canonical_definition_fact_id(
            anchor_code,
            group_id,
            "genus",
            *member[1:],
        )
        return GenusDefinitionFact(
            fact_id=fact_id,
            anchor_code=anchor_code,
            group_id=group_id,
            depth=depth,
            genus_code=member[1],
            is_defined=is_defined,
        )
    fact_id = canonical_definition_fact_id(
        anchor_code,
        group_id,
        "restriction",
        *member[1:],
    )
    return RestrictionDefinitionFact(
        fact_id=fact_id,
        anchor_code=anchor_code,
        group_id=group_id,
        depth=depth,
        role_code=member[1],
        filler_code=member[2],
    )


def _group_facts(
    anchor_code: str,
    *,
    group_id: str,
    depth: int,
    positions: Mapping[int, PositionedMember],
) -> list[DefinitionFact]:
    return [
        _definition_fact(anchor_code, group_id, depth, member, is_defined)
        for member, is_defined in positions.values()
        if member[0] != "group"
    ]


def _canonical_group_ids(
    anchor_code: str,
    grouped: GroupedMembers,
) -> dict[str, str]:
    group_ids: dict[str, str] = {}
    visiting: set[str] = set()

    def canonical_group_id(expression: str) -> str:
        existing = group_ids.get(expression)
        if existing is not None:
            return existing
        if expression in visiting:
            raise CompleteDefinitionError("nested definition groups contain a cycle")
        positions = grouped.get(expression)
        if positions is None:
            raise CompleteDefinitionError(
                "definition references a missing nested group"
            )
        visiting.add(expression)
        signature = [
            (
                f"group:{canonical_group_id(member[1])}"
                if member[0] == "group"
                else ":".join(member)
            )
            for member, _is_defined in _ordered_members(positions)
        ]
        visiting.remove(expression)
        canonical_id = canonical_definition_group_id(anchor_code, signature)
        group_ids[expression] = canonical_id
        return canonical_id

    for expression in grouped:
        canonical_group_id(expression)
    return group_ids


def _inbound_group_parents(grouped: GroupedMembers) -> dict[str, set[str]]:
    inbound_parents: dict[str, set[str]] = defaultdict(set)
    for expression, positions in grouped.items():
        for member, _is_defined in _ordered_members(positions):
            if member[0] == "group":
                inbound_parents[member[1]].add(expression)
    return inbound_parents


def _validate_group_metadata(
    grouped: GroupedMembers,
    declared_parents: Mapping[str, set[str]],
    nesting_depths: Mapping[str, int],
    inbound_parents: Mapping[str, set[str]],
) -> None:
    for expression in grouped:
        if declared_parents.get(expression, set()) != inbound_parents.get(
            expression, set()
        ):
            raise CompleteDefinitionError(
                "nested definition parent does not match its group membership"
            )
        if not inbound_parents.get(expression) and nesting_depths[expression] != 0:
            raise CompleteDefinitionError(
                "root definition group has a non-zero nesting depth"
            )


def _materialize_definition_slice(
    root_code: str,
    anchor_code: str,
    *,
    depth: int,
    grouped: GroupedMembers,
    group_ids: Mapping[str, str],
    inbound_parents: Mapping[str, set[str]],
) -> _DefinitionSlice:
    fact_by_id: dict[str, DefinitionFact] = {}
    group_by_id: dict[str, DefinitionGroup] = {}
    roots: set[str] = set()
    occurrences: list[SourceDefinitionOccurrence] = []
    group_paths = _structural_group_paths(grouped, group_ids, inbound_parents)
    for expression, positions in grouped.items():
        canonical_id = group_ids[expression]
        group = _materialized_group(
            anchor_code,
            depth,
            canonical_id,
            positions,
            group_ids,
        )
        _record_materialized_group(group_by_id, group)
        if not inbound_parents.get(expression):
            roots.add(canonical_id)
        for fact in _group_facts(
            anchor_code,
            group_id=canonical_id,
            depth=depth,
            positions=positions,
        ):
            _record_materialized_fact(fact_by_id, fact)
        for position, (member, _is_defined) in positions.items():
            if member[0] != "restriction":
                continue
            fact = _definition_fact(anchor_code, canonical_id, depth, member, False)
            structural_path = (*group_paths[expression], position)
            occurrences.append(
                SourceDefinitionOccurrence(
                    occurrence_id=canonical_source_occurrence_id(
                        root_code, fact.fact_id, structural_path
                    ),
                    root_code=root_code,
                    source_fact_id=fact.fact_id,
                    source_group_id=canonical_id,
                    anchor_code=anchor_code,
                    depth=depth,
                    role_code=member[1],
                    filler_code=member[2],
                    structural_path=structural_path,
                    member_position=position,
                )
            )
    return _DefinitionSlice(
        facts=tuple(sorted(fact_by_id.values(), key=lambda fact: fact.fact_id)),
        groups=tuple(sorted(group_by_id.values(), key=lambda group: group.group_id)),
        root_group_ids=tuple(sorted(roots)),
        occurrences=tuple(occurrences),
    )


def _structural_group_paths(
    grouped: GroupedMembers,
    group_ids: Mapping[str, str],
    inbound_parents: Mapping[str, set[str]],
) -> dict[str, tuple[int, ...]]:
    roots = sorted(
        (expression for expression in grouped if not inbound_parents.get(expression)),
        key=lambda expression: group_ids[expression],
    )
    paths: dict[str, tuple[int, ...]] = {}

    def visit(expression: str, path: tuple[int, ...]) -> None:
        previous = paths.get(expression)
        if previous is not None and previous <= path:
            return
        paths[expression] = path
        for position, (member, _is_defined) in sorted(grouped[expression].items()):
            if member[0] == "group":
                visit(member[1], (*path, position))

    for root_position, expression in enumerate(roots):
        visit(expression, (root_position,))
    return paths


def _materialized_group(
    anchor_code: str,
    depth: int,
    canonical_id: str,
    positions: Mapping[int, PositionedMember],
    group_ids: Mapping[str, str],
) -> DefinitionGroup:
    children = tuple(
        group_ids[member[1]]
        for member, _is_defined in _ordered_members(positions)
        if member[0] == "group"
    )
    return DefinitionGroup(
        group_id=canonical_id,
        anchor_code=anchor_code,
        depth=depth,
        child_group_ids=children,
    )


def _record_materialized_group(
    group_by_id: dict[str, DefinitionGroup],
    group: DefinitionGroup,
) -> None:
    previous = group_by_id.setdefault(group.group_id, group)
    if previous != group:
        raise CompleteDefinitionError(
            "one canonical definition group resolved to conflicting children"
        )


def _record_materialized_fact(
    fact_by_id: dict[str, DefinitionFact],
    fact: DefinitionFact,
) -> None:
    previous = fact_by_id.setdefault(fact.fact_id, fact)
    if previous != fact:
        raise CompleteDefinitionError(
            "one complete-definition fact identity resolved to conflicting facts"
        )


def _definition_slice_from_rows(
    anchor_code: str,
    *,
    depth: int,
    rows: Iterable[Row],
    root_code: str | None = None,
) -> _DefinitionSlice:
    grouped, declared_parents, nesting_depths = _group_definition_rows(rows)
    group_ids = _canonical_group_ids(anchor_code, grouped)
    inbound_parents = _inbound_group_parents(grouped)
    _validate_group_metadata(
        grouped,
        declared_parents,
        nesting_depths,
        inbound_parents,
    )
    return _materialize_definition_slice(
        root_code or anchor_code,
        anchor_code,
        depth=depth,
        grouped=grouped,
        group_ids=group_ids,
        inbound_parents=inbound_parents,
    )


def definition_facts_from_rows(
    anchor_code: str,
    *,
    depth: int,
    rows: Iterable[Row],
) -> tuple[DefinitionFact, ...]:
    """Parse one bounded definition response into canonical typed atomic facts."""
    return _definition_slice_from_rows(
        anchor_code,
        depth=depth,
        rows=rows,
    ).facts


def _validate_walk_bounds(max_depth: int, max_nodes: int) -> None:
    if max_depth < 0:
        raise ValueError("max_depth must be non-negative")
    if max_nodes < 1:
        raise ValueError("max_nodes must be positive")


def _defined_genera(facts: Iterable[DefinitionFact]) -> list[str]:
    return sorted(
        {
            fact.genus_code
            for fact in facts
            if isinstance(fact, GenusDefinitionFact) and fact.is_defined
        }
    )


def _schedule_defined_genus(
    *,
    root_code: str,
    genus_code: str,
    depth: int,
    max_depth: int,
    max_nodes: int,
    scheduled: set[str],
    queue: deque[tuple[str, int]],
) -> None:
    if genus_code in scheduled:
        return
    if depth >= max_depth:
        raise CompleteDefinitionError(
            f"{root_code} complete definition exceeds depth bound "
            f"{max_depth} at {genus_code}"
        )
    if len(scheduled) >= max_nodes:
        raise CompleteDefinitionError(
            f"{root_code} complete definition exceeds node bound {max_nodes}"
        )
    scheduled.add(genus_code)
    queue.append((genus_code, depth + 1))


async def read_complete_definition(
    select_fn: SelectRows,
    root_code: str,
    *,
    max_depth: int = 64,
    max_nodes: int = 4096,
) -> CompleteDefinition:
    """Walk the complete stated definition DAG without inferred hierarchy closure."""
    _validate_walk_bounds(max_depth, max_nodes)
    queue: deque[tuple[str, int]] = deque([(root_code, 0)])
    scheduled = {root_code}
    facts: list[DefinitionFact] = []
    groups: list[DefinitionGroup] = []
    root_group_ids: list[str] = []
    occurrences: list[SourceDefinitionOccurrence] = []
    while queue:
        anchor_code, depth = queue.popleft()
        rows = await _read_anchor_definition_rows(select_fn, anchor_code)
        definition_slice = _definition_slice_from_rows(
            anchor_code,
            depth=depth,
            rows=rows,
            root_code=root_code,
        )
        direct = definition_slice.facts
        facts.extend(direct)
        groups.extend(definition_slice.groups)
        root_group_ids.extend(definition_slice.root_group_ids)
        occurrences.extend(definition_slice.occurrences)
        for genus_code in _defined_genera(direct):
            _schedule_defined_genus(
                root_code=root_code,
                genus_code=genus_code,
                depth=depth,
                max_depth=max_depth,
                max_nodes=max_nodes,
                scheduled=scheduled,
                queue=queue,
            )
    return CompleteDefinition(
        root_code=root_code,
        facts=tuple(facts),
        groups=tuple(groups),
        root_group_ids=tuple(root_group_ids),
        occurrences=tuple(occurrences),
    )


async def _read_anchor_definition_rows(
    select_fn: SelectRows,
    anchor_code: str,
) -> list[Row]:
    rows: list[Row] = []
    for nesting_depth in range(_MAX_NESTING_DEPTH + 1):
        current = await select_fn(
            build_complete_definition_query(
                anchor_code,
                nesting_depth=nesting_depth,
            ),
            required_variables={
                "expression",
                "list",
                "cell",
            },
        )
        _validate_requested_nesting_depth(current, nesting_depth)
        rows = list(current)
        if not _level_requires_nested_query(current, nesting_depth):
            break
        if nesting_depth == _MAX_NESTING_DEPTH:
            raise CompleteDefinitionError(
                f"definition exceeds nesting depth bound {_MAX_NESTING_DEPTH}"
            )
    return rows


def _validate_requested_nesting_depth(rows: Iterable[Row], expected: int) -> None:
    requested_depths = {
        value
        for row in rows
        if (value := row.get("requestedNestingDepth")) not in {None, ""}
    }
    if requested_depths not in (set(), {str(expected)}):
        raise CompleteDefinitionError(
            "complete-definition response has a mismatched requested nesting depth"
        )


def _level_requires_nested_query(rows: Iterable[Row], depth: int) -> bool:
    return any(
        row.get("nestedExpression") not in {None, ""}
        for row in rows
        if _nesting_depth(row) == depth
    )


def _role_source_ids(
    constituent: Constituent,
    restrictions: Iterable[RestrictionDefinitionFact],
) -> tuple[str, ...]:
    # Constituent.__post_init__ guarantees source_roles is nonempty on every
    # role-derived constituent, and this is the only path that reaches here.
    return tuple(
        sorted(
            fact.fact_id
            for fact in restrictions
            if fact.filler_code == constituent.filler_code
            and fact.role_code in constituent.source_roles
        )
    )


def _role_occurrence_ids(
    constituent: Constituent,
    occurrences: Iterable[SourceDefinitionOccurrence],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            occurrence.occurrence_id
            for occurrence in occurrences
            if occurrence.filler_code == constituent.filler_code
            and occurrence.role_code in constituent.source_roles
        )
    )


def _parent_source_ids(
    constituent: Constituent,
    genera: Iterable[GenusDefinitionFact],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            fact.fact_id
            for fact in genera
            if fact.genus_code == constituent.filler_code
        )
    )


def _projection_source_ids(
    constituent: Constituent,
    restrictions: Iterable[RestrictionDefinitionFact],
    genera: Iterable[GenusDefinitionFact],
) -> tuple[str, ...]:
    if constituent.axis_source == "role":
        return _role_source_ids(constituent, restrictions)
    if constituent.axis_source == "parent":
        return _parent_source_ids(constituent, genera)
    return ()


def trace_curated_projection(
    constituents: Iterable[Constituent],
    complete: CompleteDefinition,
) -> list[Constituent]:
    """Attach complete-fact IDs without changing the curated projection's verdict."""
    restrictions = [
        fact for fact in complete.facts if isinstance(fact, RestrictionDefinitionFact)
    ]
    genera = [fact for fact in complete.facts if isinstance(fact, GenusDefinitionFact)]
    return [
        replace(
            constituent,
            source_definition_ids=_projection_source_ids(
                constituent,
                restrictions,
                genera,
            ),
            source_occurrence_ids=(
                _role_occurrence_ids(constituent, complete.occurrences)
                if constituent.axis_source == "role"
                else ()
            ),
        )
        for constituent in constituents
    ]
