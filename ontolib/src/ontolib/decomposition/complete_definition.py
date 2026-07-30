"""Bounded stated-OWL extraction for the complete decomposition record (#153)."""

from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from collections.abc import Awaitable, Collection, Iterable, Mapping, Sequence
from dataclasses import replace
from typing import Protocol

from ontolib.decomposition import axes
from ontolib.decomposition.models import (
    CompleteDefinition,
    Constituent,
    DefinitionFact,
    GenusDefinitionFact,
    RestrictionDefinitionFact,
)
from ontolib.terminologies.namespaces import NCIT_NS, OWL_NS, RDF_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.oxigraph_http_client import safe_iri

_MAX_INTERSECTION_MEMBERS = 64
_PREFIXES = f"""
PREFIX rdf: <{RDF_NS}>
PREFIX owl: <{OWL_NS}>
"""

Row = Mapping[str, str | None]
GroupedMembers = dict[str, dict[int, tuple[tuple[str, ...], bool]]]
_ROUTED_SOURCE_ROLES = {
    axes.ASSOCIATED_LINEAGE_AXIS: axes.PRIMARY_SITE_ROLE,
    axes.ASSOCIATED_REGION_AXIS: axes.PRIMARY_SITE_ROLE,
    axes.STAGE_SYSTEM_AXIS: "R88",
}


class CompleteDefinitionError(ValueError):
    """The stated definition cannot be represented completely and deterministically."""


class SelectRows(Protocol):
    def __call__(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> Awaitable[Sequence[Row]]: ...


def _hop_pattern(hop: int) -> str:
    lines: list[str] = []
    for index in range(hop):
        previous = "?list" if index == 0 else f"?rest{index - 1}"
        current = f"?rest{index}"
        lines.append(f"{previous} rdf:rest {current} .")
    source = "?list" if hop == 0 else f"?rest{hop - 1}"
    lines.append(f"{source} rdf:first ?member .")
    return "\n".join(lines)


def build_complete_definition_query(concept_code: str) -> str:
    """Read every member of every direct stated definition with an overflow sentinel."""
    concept_iri = safe_iri(concept_code, NCIT_NS)
    branches: list[str] = []
    for position in range(_MAX_INTERSECTION_MEMBERS + 1):
        overflow = "true" if position == _MAX_INTERSECTION_MEMBERS else "false"
        branches.append(
            f"""{{
                <{concept_iri}> owl:equivalentClass ?expression .
                ?expression owl:intersectionOf ?list .
                {_hop_pattern(position)}
                BIND({position} AS ?position)
                BIND({overflow} AS ?overflow)
            }}"""
        )
    union = "\nUNION\n".join(branches)
    return f"""{_PREFIXES}
SELECT ?expression ?position ?member ?role ?target ?childExpression ?overflow WHERE {{
    GRAPH <{STATED_GRAPH_IRI}> {{
        {union}
        OPTIONAL {{
            ?member owl:onProperty ?role ;
                    owl:someValuesFrom ?target .
        }}
        OPTIONAL {{ ?member owl:equivalentClass ?childExpression }}
    }}
}}
ORDER BY STR(?expression) ?position
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


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _member_key(row: Row) -> tuple[str, ...]:
    role = row.get("role")
    target = row.get("target")
    if role is not None or target is not None:
        if role is None:
            raise CompleteDefinitionError("restriction row is missing 'role' binding")
        if target is None:
            raise CompleteDefinitionError("restriction row is missing 'target' binding")
        return (
            "restriction",
            _ncit_code(role, binding="role", prefix="R"),
            _ncit_code(target, binding="target", prefix="C"),
        )
    return (
        "genus",
        _ncit_code(_required(row, "member"), binding="member", prefix="C"),
    )


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


def _add_grouped_row(grouped: GroupedMembers, row: Row) -> None:
    expression = _required(row, "expression")
    position = _definition_position(row)
    member = _member_key(row)
    is_defined = row.get("childExpression") not in {None, ""}
    previous = grouped[expression].get(position)
    if previous is not None and previous[0] != member:
        raise CompleteDefinitionError(
            "one definition position resolved to conflicting members"
        )
    grouped[expression][position] = (
        member,
        is_defined or (previous[1] if previous else False),
    )


def _group_definition_rows(rows: Iterable[Row]) -> GroupedMembers:
    grouped: GroupedMembers = defaultdict(dict)
    for row in rows:
        _add_grouped_row(grouped, row)
    return grouped


def _group_signature(
    positions: Mapping[int, tuple[tuple[str, ...], bool]],
) -> tuple[tuple[str, ...], ...]:
    ordered_positions = sorted(positions)
    if ordered_positions != list(range(len(ordered_positions))):
        raise CompleteDefinitionError("definition list has a missing position")
    return tuple(sorted(positions[position][0] for position in ordered_positions))


def _definition_fact(
    anchor_code: str,
    group_id: str,
    depth: int,
    member: tuple[str, ...],
    is_defined: bool,
) -> DefinitionFact:
    fact_id = _digest(anchor_code, group_id, *member)
    if member[0] == "genus":
        return GenusDefinitionFact(
            fact_id=fact_id,
            anchor_code=anchor_code,
            group_id=group_id,
            depth=depth,
            genus_code=member[1],
            is_defined=is_defined,
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
    positions: Mapping[int, tuple[tuple[str, ...], bool]],
) -> list[DefinitionFact]:
    return [
        _definition_fact(anchor_code, group_id, depth, member, is_defined)
        for member, is_defined in positions.values()
    ]


def definition_facts_from_rows(
    anchor_code: str,
    *,
    depth: int,
    rows: Iterable[Row],
) -> tuple[DefinitionFact, ...]:
    """Parse one bounded direct-definition response into canonical typed facts."""
    grouped = _group_definition_rows(rows)
    facts: list[DefinitionFact] = []
    seen_group_signatures: set[tuple[tuple[str, ...], ...]] = set()
    for positions in grouped.values():
        signature = _group_signature(positions)
        if signature in seen_group_signatures:
            continue
        seen_group_signatures.add(signature)
        group_id = _digest(anchor_code, *(":".join(member) for member in signature))
        facts.extend(
            _group_facts(
                anchor_code,
                group_id=group_id,
                depth=depth,
                positions=positions,
            )
        )
    return tuple(sorted(facts, key=lambda fact: fact.fact_id))


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
    while queue:
        anchor_code, depth = queue.popleft()
        rows = await select_fn(
            build_complete_definition_query(anchor_code),
            required_variables={
                "expression",
                "position",
                "member",
                "overflow",
            },
        )
        direct = definition_facts_from_rows(
            anchor_code,
            depth=depth,
            rows=rows,
        )
        facts.extend(direct)
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
    return CompleteDefinition(root_code=root_code, facts=tuple(facts))


def _source_role(constituent: Constituent) -> str | None:
    if constituent.source_role is not None:
        return constituent.source_role
    if constituent.axis.startswith("R"):
        return constituent.axis
    return _ROUTED_SOURCE_ROLES.get(constituent.axis)


def _role_source_ids(
    constituent: Constituent,
    restrictions: Iterable[RestrictionDefinitionFact],
) -> tuple[str, ...]:
    source_role = _source_role(constituent)
    return tuple(
        sorted(
            fact.fact_id
            for fact in restrictions
            if fact.filler_code == constituent.filler_code
            and source_role == fact.role_code
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
        )
        for constituent in constituents
    ]
