from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ontolib.decomposition.complete_definition import (
    CompleteDefinitionError,
    UnsupportedDefinitionConstructorError,
    build_complete_definition_query,
    definition_facts_from_rows,
    read_complete_definition,
    trace_curated_projection,
)
from ontolib.decomposition.models import (
    CompleteDefinition,
    Constituent,
    Decomposition,
    DefinitionGroup,
    GenusDefinitionFact,
    RestrictionDefinitionFact,
    SourceDefinitionOccurrence,
    canonical_definition_fact_id,
    canonical_definition_group_id,
    canonical_source_occurrence_id,
)
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI

if TYPE_CHECKING:
    from collections.abc import Callable, Collection


def _iri(code: str) -> str:
    return f"{NCIT_NS}{code}"


@pytest.mark.unit
def test_complete_definition_query_is_bounded_and_stated_only() -> None:
    query = build_complete_definition_query("C6135", nesting_depth=0)
    nested_query = build_complete_definition_query("C6135", nesting_depth=1)

    assert f"GRAPH <{STATED_GRAPH_IRI}>" in query
    assert f"<{_iri('C6135')}> owl:equivalentClass ?expression" in query
    assert "rdf:rest*" in query
    assert "rdf:rest+" not in query
    assert "?cell != rdf:nil" not in query
    assert "{ ?cell rdf:first ?cellWitness }" in query
    assert "{ ?cell rdf:rest ?cellWitness }" in query
    assert "BIND(0 AS ?nestingDepth)" in query
    assert "BIND(0 AS ?requestedNestingDepth)" in query
    assert "?cell" in query
    assert "?next" in query
    assert "?childExpression" in query
    assert "?nestedExpression" in query
    assert "?unionList" in query
    assert "?parentExpression" in query
    assert "?pathMember1" not in query
    assert f"<{_iri('C6135')}> owl:equivalentClass ?rootExpression" in nested_query
    assert "?pathMember1" in nested_query
    assert "?pathMember2" not in nested_query
    assert f"<{_iri('C6135')}> owl:equivalentClass ?expression" in nested_query
    assert "UNION" in nested_query
    assert "BIND(1 AS ?nestingDepth)" in nested_query
    assert "BIND(1 AS ?requestedNestingDepth)" in nested_query
    assert len(query) < 5_000


@pytest.mark.unit
def test_complete_definition_query_rejects_unsafe_code() -> None:
    with pytest.raises(ValueError, match=r"[Uu]nsafe"):
        build_complete_definition_query("C6135> } UNION {")


@pytest.mark.unit
def test_complete_definition_query_rejects_out_of_range_nesting_depth() -> None:
    with pytest.raises(ValueError, match="nesting depth"):
        build_complete_definition_query("C6135", nesting_depth=-1)
    with pytest.raises(ValueError, match="nesting depth"):
        build_complete_definition_query("C6135", nesting_depth=5)


@pytest.mark.unit
def test_union_definition_member_is_typed_as_unsupported() -> None:
    rows = _definition_rows("_:expression", ("_:union", None, None, False))
    rows[0]["unionList"] = "_:union-list"

    with pytest.raises(UnsupportedDefinitionConstructorError, match="owl:unionOf"):
        definition_facts_from_rows("C5136", depth=0, rows=rows)


@pytest.mark.unit
async def test_complete_definition_queries_only_proven_nested_levels() -> None:
    calls: list[int] = []
    outer = {
        "expression": "_:outer",
        "parentExpression": None,
        "nestingDepth": "0",
        "list": "_:outer-cell",
        "cell": "_:outer-cell",
        "next": "http://www.w3.org/1999/02/22-rdf-syntax-ns#nil",
        "member": "_:inner",
        "role": None,
        "target": None,
        "childExpression": None,
        "nestedExpression": "_:inner",
    }
    inner = {
        "expression": "_:inner",
        "parentExpression": "_:outer",
        "nestingDepth": "1",
        "list": "_:inner-cell",
        "cell": "_:inner-cell",
        "next": "http://www.w3.org/1999/02/22-rdf-syntax-ns#nil",
        "member": _iri("C35501"),
        "role": None,
        "target": None,
        "childExpression": None,
        "nestedExpression": None,
    }

    async def select(
        query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str | None]]:
        assert required_variables == {"expression", "list", "cell"}
        depth = next(
            depth
            for depth in range(5)
            if f"BIND({depth} AS ?requestedNestingDepth)" in query
        )
        calls.append(depth)
        return [outer] if depth == 0 else [outer, inner]

    complete = await read_complete_definition(select, "C27262")

    assert calls == [0, 1]
    assert len(complete.groups) == 2


@pytest.mark.unit
async def test_complete_definition_nested_query_bound_has_live_reject_branch() -> None:
    calls: list[int] = []

    async def select(
        query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str | None]]:
        del required_variables
        depth = next(
            depth
            for depth in range(5)
            if f"BIND({depth} AS ?requestedNestingDepth)" in query
        )
        calls.append(depth)
        expression = f"_:expression-{depth}"
        nested = f"_:expression-{depth + 1}"
        return [
            {
                "expression": expression,
                "parentExpression": (f"_:expression-{depth - 1}" if depth else None),
                "nestingDepth": str(depth),
                "list": f"_:cell-{depth}",
                "cell": f"_:cell-{depth}",
                "next": "http://www.w3.org/1999/02/22-rdf-syntax-ns#nil",
                "member": nested,
                "role": None,
                "target": None,
                "childExpression": None,
                "nestedExpression": nested,
            }
        ]

    with pytest.raises(CompleteDefinitionError, match="nesting depth bound"):
        await read_complete_definition(select, "C27262")

    assert calls == [0, 1, 2, 3, 4]


@pytest.mark.unit
async def test_complete_definition_rejects_a_mismatched_query_level() -> None:
    async def select(
        query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str | None]]:
        del query, required_variables
        return [
            _linked_definition_rows(1)[0]
            | {"requestedNestingDepth": "4", "nestingDepth": "0"}
        ]

    with pytest.raises(CompleteDefinitionError, match="requested nesting depth"):
        await read_complete_definition(select, "C27262")


@pytest.mark.unit
def test_rows_preserve_multiple_axioms_genera_restrictions_and_groups() -> None:
    rows = [
        {
            "expression": "_:expression-a",
            "position": "0",
            "member": _iri("C100"),
            "childExpression": "_:defined-c100",
            "role": None,
            "target": None,
            "overflow": None,
        },
        {
            "expression": "_:expression-a",
            "position": "1",
            "member": "_:restriction-1",
            "childExpression": None,
            "role": _iri("R101"),
            "target": _iri("C200"),
            "overflow": None,
        },
        {
            "expression": "_:expression-b",
            "position": "0",
            "member": _iri("C300"),
            "childExpression": None,
            "role": None,
            "target": None,
            "overflow": None,
        },
    ]

    facts = definition_facts_from_rows("C1", depth=2, rows=rows)

    assert len(facts) == 3
    assert len({fact.group_id for fact in facts}) == 2
    assert len({fact.fact_id for fact in facts}) == 3
    assert facts == tuple(sorted(facts, key=lambda fact: fact.fact_id))
    genus = next(
        fact
        for fact in facts
        if isinstance(fact, GenusDefinitionFact) and fact.genus_code == "C100"
    )
    assert genus.anchor_code == "C1"
    assert genus.depth == 2
    assert genus.is_defined is True
    restriction = next(
        fact for fact in facts if isinstance(fact, RestrictionDefinitionFact)
    )
    assert restriction.role_code == "R101"
    assert restriction.filler_code == "C200"


@pytest.mark.unit
async def test_mutually_nested_groups_fail_closed_instead_of_recursing() -> None:
    """A 2-cycle in `nestedExpression` must raise, not blow the stack.

    `_linked_group_depths` has a pinned cycle guard, but `_canonical_group_ids`
    walks the same graph a second time to canonicalise the ids. Without its own
    guard the recursion is unbounded, so the failure mode is `RecursionError`
    rather than a typed `CompleteDefinitionError`.
    """
    rows = [
        {
            "expression": "_:a",
            "parentExpression": "_:b",
            "nestingDepth": "1",
            "position": "0",
            "member": "_:b",
            "childExpression": None,
            "nestedExpression": "_:b",
            "role": None,
            "target": None,
            "overflow": "false",
        },
        {
            "expression": "_:b",
            "parentExpression": "_:a",
            "nestingDepth": "1",
            "position": "0",
            "member": "_:a",
            "childExpression": None,
            "nestedExpression": "_:a",
            "role": None,
            "target": None,
            "overflow": "false",
        },
    ]

    async def select(
        query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str | None]]:
        del query, required_variables
        return rows

    with pytest.raises(CompleteDefinitionError, match="cycle"):
        await read_complete_definition(select, "C27262")


@pytest.mark.unit
async def test_nested_intersection_is_a_stable_proof_bearing_group_tree() -> None:
    rows = [
        {
            "expression": "_:outer-a",
            "parentExpression": None,
            "nestingDepth": "0",
            "position": "0",
            "member": _iri("C35501"),
            "childExpression": None,
            "nestedExpression": None,
            "role": None,
            "target": None,
            "overflow": "false",
        },
        {
            "expression": "_:outer-a",
            "parentExpression": None,
            "nestingDepth": "0",
            "position": "1",
            "member": "_:inner-a",
            "childExpression": None,
            "nestedExpression": "_:inner-a",
            "role": None,
            "target": None,
            "overflow": "false",
        },
        {
            "expression": "_:inner-a",
            "parentExpression": "_:outer-a",
            "nestingDepth": "1",
            "position": "0",
            "member": "_:restriction-a",
            "childExpression": None,
            "nestedExpression": None,
            "role": _iri("R140"),
            "target": _iri("C36715"),
            "overflow": "false",
        },
        {
            "expression": "_:inner-a",
            "parentExpression": "_:outer-a",
            "nestingDepth": "1",
            "position": "1",
            "member": "_:restriction-b",
            "childExpression": None,
            "nestedExpression": None,
            "role": _iri("R141"),
            "target": _iri("C13271"),
            "overflow": "false",
        },
        {
            "expression": "_:outer-a",
            "parentExpression": None,
            "nestingDepth": "0",
            "position": "2",
            "member": "_:restriction-c",
            "childExpression": None,
            "nestedExpression": None,
            "role": _iri("R101"),
            "target": _iri("C12431"),
            "overflow": "false",
        },
    ]

    async def select(
        query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str | None]]:
        assert "?nestedExpression" in query
        assert required_variables == {
            "expression",
            "list",
            "cell",
        }
        return rows

    complete = await read_complete_definition(select, "C27262")

    assert len(complete.groups) == 2
    assert len(complete.root_group_ids) == 1
    outer = next(
        group for group in complete.groups if group.group_id in complete.root_group_ids
    )
    assert len(outer.child_group_ids) == 1
    inner = next(
        group for group in complete.groups if group.group_id == outer.child_group_ids[0]
    )
    assert inner.child_group_ids == ()
    assert {fact.group_id for fact in complete.facts} == {
        outer.group_id,
        inner.group_id,
    }
    assert {
        (fact.role_code, fact.filler_code)
        for fact in complete.facts
        if isinstance(fact, RestrictionDefinitionFact)
        and fact.group_id == inner.group_id
    } == {("R140", "C36715"), ("R141", "C13271")}

    renamed_and_reordered = [
        {
            key: (
                value.replace("_:outer-a", "_:outer-b").replace(
                    "_:inner-a", "_:inner-b"
                )
                if isinstance(value, str)
                else value
            )
            for key, value in row.items()
        }
        for row in reversed(rows)
    ]

    async def select_renamed(
        query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str | None]]:
        return renamed_and_reordered

    renamed = await read_complete_definition(select_renamed, "C27262")
    assert renamed == complete
    assert renamed.identity == complete.identity


@pytest.mark.unit
def test_definition_group_rejects_unknown_children_and_cycles() -> None:
    root = DefinitionGroup(
        group_id="a" * 64,
        anchor_code="C1",
        depth=0,
        child_group_ids=("b" * 64,),
    )
    child = DefinitionGroup(
        group_id="b" * 64,
        anchor_code="C1",
        depth=0,
        child_group_ids=("a" * 64,),
    )

    with pytest.raises(ValueError, match="cycle"):
        CompleteDefinition(
            root_code="C1",
            facts=(),
            groups=(root, child),
            root_group_ids=(root.group_id,),
        )
    with pytest.raises(ValueError, match="unknown child"):
        CompleteDefinition(
            root_code="C1",
            facts=(),
            groups=(replace(root, child_group_ids=("c" * 64,)),),
            root_group_ids=(root.group_id,),
        )


@pytest.mark.unit
def test_row_parser_fails_closed_on_bounded_list_overflow() -> None:
    with pytest.raises(CompleteDefinitionError, match="list bound"):
        definition_facts_from_rows(
            "C1",
            depth=0,
            rows=[
                {
                    "expression": "_:expression",
                    "position": "64",
                    "member": "_:overflow",
                    "childExpression": None,
                    "role": None,
                    "target": None,
                    "overflow": "true",
                }
            ],
        )


def _linked_definition_rows(length: int) -> list[dict[str, str | None]]:
    return [
        {
            "expression": "_:expression",
            "parentExpression": None,
            "list": "_:cell-0",
            "cell": f"_:cell-{position}",
            "next": (
                f"_:cell-{position + 1}"
                if position + 1 < length
                else "http://www.w3.org/1999/02/22-rdf-syntax-ns#nil"
            ),
            "member": _iri(f"C{position + 1}"),
            "role": None,
            "target": None,
            "childExpression": None,
            "nestedExpression": None,
        }
        for position in range(length)
    ]


@pytest.mark.unit
async def test_linked_list_reader_accepts_bound_and_rejects_overflow() -> None:
    async def at_bound(
        query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str | None]]:
        return _linked_definition_rows(64)

    complete = await read_complete_definition(at_bound, "C900")
    assert len(complete.facts) == 64

    async def over_bound(
        query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str | None]]:
        return _linked_definition_rows(65)

    with pytest.raises(CompleteDefinitionError, match="member list bound"):
        await read_complete_definition(over_bound, "C900")


@pytest.mark.unit
async def test_linked_list_rejects_duplicate_source_bindings() -> None:
    rows = _linked_definition_rows(1)
    rows[0]["childExpression"] = "_:definition-a"
    rows.append(dict(rows[0], childExpression="_:definition-b"))

    async def select(
        query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str | None]]:
        return rows if _iri("C900") in query else []

    with pytest.raises(CompleteDefinitionError, match=r"duplicate.*RDF list cell"):
        await read_complete_definition(select, "C900")


@pytest.mark.unit
async def test_linked_list_rejects_an_identical_duplicate_source_row() -> None:
    rows = _linked_definition_rows(1)
    rows.append(dict(rows[0]))

    async def select(
        query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str | None]]:
        del query, required_variables
        return rows

    with pytest.raises(CompleteDefinitionError, match=r"duplicate.*RDF list cell"):
        await read_complete_definition(select, "C900")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda rows: rows[0].update(member=None),
            "member",
        ),
        (
            lambda rows: rows[0].update(next=None),
            "next",
        ),
        (
            lambda rows: rows[0].update(next="_:missing"),
            "missing cell",
        ),
        (
            lambda rows: rows[1].update(next="_:cell-0"),
            "cycle",
        ),
        (
            lambda rows: rows.append(dict(rows[0], member=_iri("C999"))),
            "conflicting members",
        ),
        (
            lambda rows: rows[1].update(list="_:different-list"),
            "conflicting RDF lists",
        ),
        (
            lambda rows: rows.append(
                dict(
                    rows[0],
                    cell="_:disconnected",
                    next="http://www.w3.org/1999/02/22-rdf-syntax-ns#nil",
                )
            ),
            "disconnected cells",
        ),
    ],
)
async def test_linked_list_reader_fails_closed_on_malformed_graph(
    mutate: Callable[[list[dict[str, str | None]]], None],
    message: str,
) -> None:
    rows = _linked_definition_rows(2)
    mutate(rows)

    async def select(
        query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str | None]]:
        return rows

    with pytest.raises(CompleteDefinitionError, match=message):
        await read_complete_definition(select, "C900")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            [
                {
                    "expression": "_:outer",
                    "parentExpression": "_:inner",
                    "list": "_:outer-cell",
                    "cell": "_:outer-cell",
                    "next": "http://www.w3.org/1999/02/22-rdf-syntax-ns#nil",
                    "member": "_:inner",
                    "role": None,
                    "target": None,
                    "childExpression": None,
                    "nestedExpression": "_:inner",
                },
                {
                    "expression": "_:inner",
                    "parentExpression": "_:outer",
                    "list": "_:inner-cell",
                    "cell": "_:inner-cell",
                    "next": "http://www.w3.org/1999/02/22-rdf-syntax-ns#nil",
                    "member": _iri("C1"),
                    "role": None,
                    "target": None,
                    "childExpression": None,
                    "nestedExpression": None,
                },
            ],
            "groups contain a cycle",
        ),
        (
            [
                {
                    "expression": "_:inner",
                    "parentExpression": "_:missing",
                    "list": "_:inner-cell",
                    "cell": "_:inner-cell",
                    "next": "http://www.w3.org/1999/02/22-rdf-syntax-ns#nil",
                    "member": _iri("C1"),
                    "role": None,
                    "target": None,
                    "childExpression": None,
                    "nestedExpression": None,
                }
            ],
            "missing its parent",
        ),
    ],
)
async def test_linked_group_reader_rejects_cyclic_or_missing_parent_graphs(
    rows: list[dict[str, str | None]],
    message: str,
) -> None:
    async def select(
        query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str | None]]:
        return rows

    with pytest.raises(CompleteDefinitionError, match=message):
        await read_complete_definition(select, "C900")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            [
                {
                    "expression": "_:outer",
                    "parentExpression": None,
                    "nestingDepth": "0",
                    "position": "0",
                    "member": "_:inner",
                    "nestedExpression": "_:inner",
                    "role": None,
                    "target": None,
                    "overflow": "false",
                }
            ],
            "missing nested group",
        ),
        (
            [
                {
                    "expression": "_:too-deep",
                    "parentExpression": "_:parent",
                    "nestingDepth": "5",
                    "position": "0",
                    "member": _iri("C1"),
                    "nestedExpression": None,
                    "role": None,
                    "target": None,
                    "overflow": "false",
                }
            ],
            "nesting depth bound",
        ),
    ],
)
async def test_nested_intersection_fails_closed_on_incomplete_or_too_deep_shape(
    rows: list[dict[str, str | None]], message: str
) -> None:
    async def select(
        query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str | None]]:
        return rows

    with pytest.raises(CompleteDefinitionError, match=message):
        await read_complete_definition(select, "C1")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("row", "message"),
    [
        (
            {
                "expression": None,
                "position": "0",
                "member": _iri("C100"),
                "role": None,
                "target": None,
            },
            "expression",
        ),
        (
            {
                "expression": "_:expression",
                "position": "0",
                "member": "_:restriction",
                "role": _iri("R101"),
                "target": None,
            },
            "target",
        ),
        (
            {
                "expression": "_:expression",
                "position": "0",
                "member": "_:restriction",
                "role": None,
                "target": _iri("C200"),
            },
            "role",
        ),
        (
            {
                "expression": "_:expression",
                "position": "not-an-integer",
                "member": _iri("C100"),
                "role": None,
                "target": None,
            },
            "integer",
        ),
        # The bound itself, not a downstream symptom: with the guard removed, a
        # negative position is caught later by "definition list has a missing
        # position", which also matches a loose "position" regex.
        (
            {
                "expression": "_:expression",
                "position": "-1",
                "member": _iri("C100"),
                "role": None,
                "target": None,
            },
            "position exceeds list bound",
        ),
        (
            {
                "expression": "_:expression",
                "position": "64",
                "member": _iri("C100"),
                "role": None,
                "target": None,
            },
            "position exceeds list bound",
        ),
        (
            {
                "expression": "_:expression",
                "position": "999",
                "member": _iri("C100"),
                "role": None,
                "target": None,
            },
            "position exceeds list bound",
        ),
        (
            {
                "expression": "_:expression",
                "position": "0",
                "member": "https://example.org/not-ncit",
                "role": None,
                "target": None,
            },
            "NCIt",
        ),
        (
            {
                "expression": "_:expression",
                "position": "0",
                "member": _iri("R101"),
                "role": None,
                "target": None,
            },
            "NCIt C",
        ),
        (
            {
                "expression": "_:expression",
                "position": "0",
                "member": "_:nested",
                "nestedExpression": "_:nested",
                "role": _iri("R101"),
                "target": _iri("C200"),
            },
            "nested definition member",
        ),
    ],
)
def test_row_parser_rejects_incomplete_or_foreign_facts(
    row: dict[str, str | None], message: str
) -> None:
    with pytest.raises(CompleteDefinitionError, match=message):
        definition_facts_from_rows("C1", depth=0, rows=[row])


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            [
                {
                    "expression": "_:positional",
                    "position": "0",
                    "member": _iri("C1"),
                    "role": None,
                    "target": None,
                },
                *_linked_definition_rows(1),
            ],
            "mixes linked and positional",
        ),
        (
            [
                {
                    "expression": "_:root",
                    "position": "0",
                    "member": _iri("C1"),
                    "role": None,
                    "target": None,
                    "parentExpression": "_:unexpected",
                    "nestingDepth": "0",
                }
            ],
            "root definition group unexpectedly has a parent",
        ),
        (
            [
                {
                    "expression": "_:nested",
                    "position": "0",
                    "member": _iri("C1"),
                    "role": None,
                    "target": None,
                    "parentExpression": None,
                    "nestingDepth": "1",
                }
            ],
            "nested definition group is missing its parent",
        ),
        (
            [
                {
                    "expression": "_:root",
                    "position": "0",
                    "member": _iri("C1"),
                    "role": None,
                    "target": None,
                    "parentExpression": None,
                    "nestingDepth": "not-an-integer",
                }
            ],
            "nesting depth is not an integer",
        ),
        (
            [
                {
                    "expression": "_:root",
                    "position": "0",
                    "member": _iri("C1"),
                    "role": None,
                    "target": None,
                    "parentExpression": None,
                    "nestingDepth": "0",
                },
                {
                    "expression": "_:root",
                    "position": "1",
                    "member": _iri("C2"),
                    "role": None,
                    "target": None,
                    "parentExpression": "_:parent",
                    "nestingDepth": "1",
                },
            ],
            "conflicting nesting depths",
        ),
    ],
)
def test_row_parser_rejects_inconsistent_transport_and_group_metadata(
    rows: list[dict[str, str | None]],
    message: str,
) -> None:
    with pytest.raises(CompleteDefinitionError, match=message):
        definition_facts_from_rows("C1", depth=0, rows=rows)


@pytest.mark.unit
def test_complete_definition_identity_and_projection_loss_are_deterministic() -> None:
    facts = definition_facts_from_rows(
        "C1",
        depth=0,
        rows=[
            {
                "expression": "_:expression",
                "position": "0",
                "member": _iri("C100"),
                "childExpression": None,
                "role": None,
                "target": None,
            },
            {
                "expression": "_:expression",
                "position": "1",
                "member": "_:restriction",
                "childExpression": None,
                "role": _iri("R101"),
                "target": _iri("C200"),
            },
        ],
    )
    complete = CompleteDefinition(root_code="C1", facts=facts)
    restriction = next(
        fact for fact in facts if isinstance(fact, RestrictionDefinitionFact)
    )
    constituents = trace_curated_projection(
        [
            Constituent(
                axis="R101",
                filler_code="C200",
                axis_source="role",
                source_definition_ids=(restriction.fact_id,),
            )
        ],
        complete,
    )

    assert constituents[0].source_definition_ids == (restriction.fact_id,)
    decomposition = Decomposition(
        code="C1",
        semantic_type="Neoplastic Process",
        constituents=constituents,
        complete_definition=complete,
    )
    assert decomposition.complete_fact_count == 2
    assert decomposition.projected_fact_count == 1
    assert decomposition.projection_loss_count == 1
    assert len(complete.identity) == 64
    assert (
        CompleteDefinition(root_code="C1", facts=tuple(reversed(facts))).identity
        == complete.identity
    )

    with pytest.raises(ValueError, match="unknown complete-definition fact"):
        Decomposition(
            code="C1",
            semantic_type=None,
            constituents=[
                replace(
                    constituents[0],
                    source_definition_ids=("f" * 64,),
                )
            ],
            complete_definition=complete,
        )
    with pytest.raises(ValueError, match="unrelated restriction"):
        Decomposition(
            code="C1",
            semantic_type=None,
            constituents=[
                replace(
                    constituents[0],
                    filler_code="C999",
                    source_definition_ids=(restriction.fact_id,),
                )
            ],
            complete_definition=complete,
        )
    with pytest.raises(ValueError, match="different source role"):
        Decomposition(
            code="C1",
            semantic_type=None,
            constituents=[
                replace(
                    constituents[0],
                    source_roles=("R105",),
                    source_definition_ids=(restriction.fact_id,),
                )
            ],
            complete_definition=complete,
        )


@pytest.mark.unit
def test_routed_axis_trace_does_not_claim_an_unrelated_role_with_same_filler() -> None:
    facts = definition_facts_from_rows(
        "C1",
        depth=0,
        rows=[
            *_definition_rows(
                "_:site",
                ("_:site-restriction", _iri("R101"), _iri("C200"), False),
            ),
            *_definition_rows(
                "_:cell",
                ("_:cell-restriction", _iri("R105"), _iri("C200"), False),
            ),
        ],
    )
    complete = CompleteDefinition(root_code="C1", facts=facts)
    site_restriction = next(
        fact
        for fact in facts
        if isinstance(fact, RestrictionDefinitionFact) and fact.role_code == "R101"
    )

    traced = trace_curated_projection(
        [
            Constituent(
                axis="op:AssociatedRegion",
                filler_code="C200",
                axis_source="role",
                source_roles=("R101",),
                source_definition_ids=(site_restriction.fact_id,),
            )
        ],
        complete,
    )

    assert traced[0].source_definition_ids == (site_restriction.fact_id,)


@pytest.mark.unit
def test_definition_fact_types_reject_impossible_shapes() -> None:
    with pytest.raises(ValueError, match="genus_code"):
        GenusDefinitionFact(
            fact_id="a" * 64,
            anchor_code="C1",
            group_id="b" * 64,
            depth=0,
            genus_code="R101",
            is_defined=False,
        )
    with pytest.raises(ValueError, match="role_code"):
        RestrictionDefinitionFact(
            fact_id="a" * 64,
            anchor_code="C1",
            group_id="b" * 64,
            depth=0,
            role_code="C100",
            filler_code="C200",
        )


@pytest.mark.unit
def test_complete_definition_types_reject_invalid_identity_and_link_shapes() -> None:
    group_id = canonical_definition_group_id("C1", ("genus:C2:primitive",))
    valid = GenusDefinitionFact(
        fact_id=canonical_definition_fact_id(
            "C1", group_id, "genus", "C2", "primitive"
        ),
        anchor_code="C1",
        group_id=group_id,
        depth=0,
        genus_code="C2",
        is_defined=False,
    )
    with pytest.raises(ValueError, match="fact_id"):
        replace(valid, fact_id="not-a-digest")
    with pytest.raises(ValueError, match="group_id"):
        replace(valid, group_id="f" * 63)
    with pytest.raises(ValueError, match="anchor_code"):
        replace(valid, anchor_code="R1")
    with pytest.raises(ValueError, match="depth"):
        replace(valid, depth=-1)
    with pytest.raises(ValueError, match="unique"):
        CompleteDefinition(root_code="C1", facts=(valid, valid))
    with pytest.raises(ValueError, match="not canonical"):
        CompleteDefinition(
            root_code="C1",
            facts=(replace(valid, fact_id="f" * 64),),
        )
    with pytest.raises(ValueError, match="require a complete definition"):
        Decomposition(
            code="C1",
            semantic_type=None,
            constituents=[
                Constituent(
                    axis="R101",
                    filler_code="C2",
                    axis_source="role",
                    source_definition_ids=("a" * 64,),
                )
            ],
        )
    with pytest.raises(ValueError, match="root"):
        Decomposition(
            code="C2",
            semantic_type=None,
            complete_definition=CompleteDefinition(root_code="C1", facts=(valid,)),
        )


@pytest.mark.unit
def test_complete_definition_group_model_enforces_graph_invariants() -> None:
    child_id = canonical_definition_group_id("C1", ())
    root_id = canonical_definition_group_id(
        "C1", (f"group:{child_id}", "genus:C2:primitive")
    )
    other_root_id = canonical_definition_group_id(
        "C1", (f"group:{child_id}", "genus:C3:primitive")
    )
    unknown_id = "d" * 64
    root = DefinitionGroup(
        group_id=root_id,
        anchor_code="C1",
        depth=0,
        child_group_ids=(child_id,),
    )
    other_root = DefinitionGroup(
        group_id=other_root_id,
        anchor_code="C1",
        depth=0,
        child_group_ids=(child_id,),
    )
    child = DefinitionGroup(
        group_id=child_id,
        anchor_code="C1",
        depth=0,
    )
    root_fact = GenusDefinitionFact(
        fact_id=canonical_definition_fact_id("C1", root_id, "genus", "C2", "primitive"),
        anchor_code="C1",
        group_id=root_id,
        depth=0,
        genus_code="C2",
        is_defined=False,
    )
    other_root_fact = GenusDefinitionFact(
        fact_id=canonical_definition_fact_id(
            "C1", other_root_id, "genus", "C3", "primitive"
        ),
        anchor_code="C1",
        group_id=other_root_id,
        depth=0,
        genus_code="C3",
        is_defined=False,
    )

    with pytest.raises(ValueError, match="non-negative"):
        replace(child, depth=-1)
    with pytest.raises(ValueError, match="unique"):
        CompleteDefinition(
            root_code="C1",
            facts=(),
            groups=(child, child),
            root_group_ids=(child_id,),
        )
    with pytest.raises(ValueError, match="unknown child"):
        CompleteDefinition(
            root_code="C1",
            facts=(),
            groups=(replace(root, child_group_ids=(unknown_id,)),),
            root_group_ids=(root_id,),
        )
    with pytest.raises(ValueError, match="unknown group"):
        CompleteDefinition(
            root_code="C1",
            facts=(),
            groups=(child,),
            root_group_ids=(unknown_id,),
        )

    inferred_roots = CompleteDefinition(
        root_code="C1",
        facts=(root_fact,),
        groups=(child, root),
    )
    assert inferred_roots.root_group_ids == (root_id,)
    with pytest.raises(ValueError, match="parentless"):
        CompleteDefinition(
            root_code="C1",
            facts=(root_fact,),
            groups=(child, root),
            root_group_ids=(root_id, child_id),
        )

    reconvergent = CompleteDefinition(
        root_code="C1",
        facts=(root_fact, other_root_fact),
        groups=(child, other_root, root),
        root_group_ids=(root_id, other_root_id),
    )
    assert reconvergent.root_group_ids == tuple(sorted((root_id, other_root_id)))

    with pytest.raises(ValueError, match="share an anchor and DAG depth"):
        CompleteDefinition(
            root_code="C1",
            facts=(),
            groups=(root, replace(child, anchor_code="C2")),
            root_group_ids=(root_id,),
        )
    with pytest.raises(ValueError, match="no reachable root"):
        CompleteDefinition(
            root_code="C1",
            facts=(),
            groups=(
                replace(root, child_group_ids=()),
                replace(other_root, child_group_ids=()),
            ),
            root_group_ids=(root_id,),
        )


@pytest.mark.unit
def test_complete_definition_fact_model_enforces_group_membership() -> None:
    group_id = "a" * 64
    fact = RestrictionDefinitionFact(
        fact_id="b" * 64,
        anchor_code="C1",
        group_id=group_id,
        depth=0,
        role_code="R101",
        filler_code="C2",
    )
    with pytest.raises(ValueError, match="non-negative"):
        replace(fact, depth=-1)
    with pytest.raises(ValueError, match="unknown group"):
        CompleteDefinition(
            root_code="C1",
            facts=(fact,),
            groups=(
                DefinitionGroup(
                    group_id="c" * 64,
                    anchor_code="C1",
                    depth=0,
                ),
            ),
        )
    with pytest.raises(ValueError, match="anchors/depths"):
        CompleteDefinition(
            root_code="C1",
            facts=(fact,),
            groups=(
                DefinitionGroup(
                    group_id=group_id,
                    anchor_code="C2",
                    depth=0,
                ),
            ),
        )
    with pytest.raises(ValueError, match="cannot span anchors"):
        CompleteDefinition(
            root_code="C1",
            facts=(
                fact,
                replace(fact, fact_id="c" * 64, anchor_code="C2"),
            ),
        )


def _definition_rows(
    expression: str,
    *members: tuple[str, str | None, str | None, bool],
) -> list[dict[str, str | None]]:
    return [
        {
            "expression": expression,
            "position": str(position),
            "member": member,
            "role": role,
            "target": target,
            "childExpression": "_:defined" if is_defined else None,
            "overflow": "false",
        }
        for position, (member, role, target, is_defined) in enumerate(members)
    ]


@pytest.mark.unit
def test_row_parser_rejects_conflicts_gaps_and_collapses_duplicate_groups() -> None:
    conflict = _definition_rows(
        "_:one", (_iri("C1"), None, None, False)
    ) + _definition_rows("_:one", (_iri("C2"), None, None, False))
    with pytest.raises(CompleteDefinitionError, match="conflicting"):
        definition_facts_from_rows("C9", depth=0, rows=conflict)

    gap = _definition_rows("_:gap", (_iri("C1"), None, None, False))
    gap[0]["position"] = "1"
    with pytest.raises(CompleteDefinitionError, match="missing position"):
        definition_facts_from_rows("C9", depth=0, rows=gap)

    duplicate_groups = [
        *_definition_rows("_:one", (_iri("C1"), None, None, False)),
        *_definition_rows("_:two", (_iri("C1"), None, None, False)),
    ]
    assert len(definition_facts_from_rows("C9", depth=0, rows=duplicate_groups)) == 1


@pytest.mark.unit
def test_row_parser_rejects_an_identical_duplicate_position_binding() -> None:
    rows = _definition_rows(
        "_:expression", ("_:restriction", _iri("R101"), _iri("C2"), False)
    )

    with pytest.raises(CompleteDefinitionError, match=r"duplicate.*position"):
        definition_facts_from_rows("C9", depth=0, rows=[*rows, dict(rows[0])])


@pytest.mark.unit
def test_definition_identity_ignores_semantically_irrelevant_intersection_order() -> (
    None
):
    members = (
        (_iri("C1"), None, None, False),
        ("_:restriction", _iri("R101"), _iri("C2"), False),
    )
    canonical = definition_facts_from_rows(
        "C9",
        depth=0,
        rows=_definition_rows("_:one", *members),
    )
    reordered_duplicate = definition_facts_from_rows(
        "C9",
        depth=0,
        rows=[
            *_definition_rows("_:one", *members),
            *_definition_rows("_:two", *reversed(members)),
        ],
    )

    assert reordered_duplicate == canonical
    assert (
        CompleteDefinition(root_code="C9", facts=reordered_duplicate).identity
        == CompleteDefinition(root_code="C9", facts=canonical).identity
    )

    repeated_member = definition_facts_from_rows(
        "C9",
        depth=0,
        rows=_definition_rows("_:one", members[0], members[0]),
    )
    single_member = definition_facts_from_rows(
        "C9",
        depth=0,
        rows=_definition_rows("_:one", members[0]),
    )
    assert CompleteDefinition(root_code="C9", facts=repeated_member).identity == (
        CompleteDefinition(root_code="C9", facts=single_member).identity
    )


@pytest.mark.unit
async def test_repeated_restrictions_keep_stable_source_occurrences() -> None:
    rows = _definition_rows(
        "_:expression-a",
        (_iri("C1"), None, None, False),
        ("_:restriction-a", _iri("R101"), _iri("C2"), False),
        ("_:restriction-b", _iri("R101"), _iri("C2"), False),
    )

    async def select(
        query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str | None]]:
        del query, required_variables
        return rows

    complete = await read_complete_definition(select, "C9")
    restriction = next(
        fact for fact in complete.facts if isinstance(fact, RestrictionDefinitionFact)
    )

    assert len(complete.facts) == 2
    assert complete.occurrences == (
        SourceDefinitionOccurrence(
            occurrence_id=complete.occurrences[0].occurrence_id,
            root_code="C9",
            source_fact_id=restriction.fact_id,
            source_group_id=restriction.group_id,
            anchor_code="C9",
            depth=0,
            role_code="R101",
            filler_code="C2",
            structural_path=(0, 1),
            member_position=1,
        ),
        SourceDefinitionOccurrence(
            occurrence_id=complete.occurrences[1].occurrence_id,
            root_code="C9",
            source_fact_id=restriction.fact_id,
            source_group_id=restriction.group_id,
            anchor_code="C9",
            depth=0,
            role_code="R101",
            filler_code="C2",
            structural_path=(0, 2),
            member_position=2,
        ),
    )
    assert (
        complete.occurrences[0].occurrence_id != complete.occurrences[1].occurrence_id
    )

    renamed = [
        {
            key: value.replace("_:expression-a", "_:expression-z")
            if isinstance(value, str)
            else value
            for key, value in row.items()
        }
        for row in reversed(rows)
    ]

    async def select_renamed(
        query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str | None]]:
        del query, required_variables
        return renamed

    assert await read_complete_definition(select_renamed, "C9") == complete

    canonical_only = CompleteDefinition(
        root_code=complete.root_code,
        facts=complete.facts,
        groups=complete.groups,
        root_group_ids=complete.root_group_ids,
    )
    assert complete.identity == canonical_only.identity

    traced = trace_curated_projection(
        [
            Constituent(
                axis="R101",
                filler_code="C2",
                axis_source="role",
                source_definition_ids=(restriction.fact_id,),
                source_occurrence_ids=tuple(
                    occurrence.occurrence_id for occurrence in complete.occurrences
                ),
            )
        ],
        complete,
    )
    assert traced[0].source_definition_ids == (restriction.fact_id,)
    assert traced[0].source_occurrence_ids == tuple(
        sorted(occurrence.occurrence_id for occurrence in complete.occurrences)
    )


@pytest.mark.unit
async def test_linked_repeated_restrictions_at_distinct_cells_are_preserved() -> None:
    rows = _linked_definition_rows(3)
    rows[1].update(
        member="_:restriction-a",
        role=_iri("R101"),
        target=_iri("C2"),
    )
    rows[2].update(
        member="_:restriction-b",
        role=_iri("R101"),
        target=_iri("C2"),
    )

    async def select(
        query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str | None]]:
        del query, required_variables
        return rows

    complete = await read_complete_definition(select, "C9")

    assert [occurrence.member_position for occurrence in complete.occurrences] == [1, 2]
    assert {
        (occurrence.role_code, occurrence.filler_code)
        for occurrence in complete.occurrences
    } == {("R101", "C2")}
    assert len({occurrence.occurrence_id for occurrence in complete.occurrences}) == 2


@pytest.mark.unit
def test_decomposition_validates_source_occurrence_links() -> None:
    group_id = canonical_definition_group_id("C1", ("restriction:R101:C2",))
    fact_id = canonical_definition_fact_id("C1", group_id, "restriction", "R101", "C2")
    occurrence = SourceDefinitionOccurrence(
        occurrence_id=canonical_source_occurrence_id("C1", fact_id, (0, 0)),
        root_code="C1",
        source_fact_id=fact_id,
        source_group_id=group_id,
        anchor_code="C1",
        depth=0,
        role_code="R101",
        filler_code="C2",
        structural_path=(0, 0),
        member_position=0,
    )
    complete = CompleteDefinition(
        root_code="C1",
        facts=(
            RestrictionDefinitionFact(
                fact_id=fact_id,
                anchor_code="C1",
                group_id=group_id,
                depth=0,
                role_code="R101",
                filler_code="C2",
            ),
        ),
        occurrences=(occurrence,),
    )

    with pytest.raises(ValueError, match="unknown source occurrence"):
        Decomposition(
            code="C1",
            semantic_type=None,
            constituents=(
                Constituent(
                    axis="R101",
                    filler_code="C2",
                    axis_source="role",
                    source_definition_ids=(fact_id,),
                    source_occurrence_ids=("b" * 64,),
                ),
            ),
            complete_definition=complete,
        )


@pytest.mark.unit
def test_complete_record_matches_structural_golden_contract() -> None:
    golden_path = Path(__file__).parent / "golden" / "complete-definition.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    rows: list[dict[str, str | None]] = []
    for group_index, members in enumerate(golden["definition_groups"]):
        expression = f"_:expression-{group_index}"
        for position, member in enumerate(members):
            is_genus = member["kind"] == "genus"
            rows.append(
                {
                    "expression": expression,
                    "position": str(position),
                    "member": (
                        _iri(member["code"])
                        if is_genus
                        else f"_:restriction-{group_index}-{position}"
                    ),
                    "role": None if is_genus else _iri(member["role"]),
                    "target": None if is_genus else _iri(member["filler"]),
                    "childExpression": (
                        "_:defined" if is_genus and member["is_defined"] else None
                    ),
                    "overflow": "false",
                }
            )
    facts = definition_facts_from_rows(golden["root_code"], depth=0, rows=rows)
    complete = CompleteDefinition(root_code=golden["root_code"], facts=facts)
    projection = [
        Constituent(
            axis=item["axis"],
            filler_code=item["filler"],
            axis_source=item["axis_source"],
            source_roles=(item["source_role"],),
            group=item["group"],
            needs_review=item["needs_review"],
            source_definition_ids=tuple(
                fact.fact_id
                for fact in facts
                if isinstance(fact, RestrictionDefinitionFact)
                and fact.role_code == item["source_role"]
                and fact.filler_code == item["filler"]
            ),
        )
        for item in golden["curated_projection"]
    ]
    traced = trace_curated_projection(projection, complete)
    decomposition = Decomposition(
        code=golden["root_code"],
        semantic_type="Neoplastic Process",
        constituents=traced,
        complete_definition=complete,
    )

    expected = golden["expected"]
    assert decomposition.complete_fact_count == expected["definition_fact_count"]
    assert len({fact.group_id for fact in facts}) == expected["definition_group_count"]
    assert decomposition.projected_fact_count == expected["projected_fact_count"]
    assert decomposition.projection_loss_count == expected["projection_loss_count"]
    assert all(len(item.source_definition_ids) == 1 for item in traced)
    assert traced[0].group == "region-1"
    assert traced[1].needs_review is True


@pytest.mark.unit
async def test_complete_definition_walks_every_defined_genus_once() -> None:
    calls: list[str] = []
    rows_by_code = {
        "C1": _definition_rows(
            "_:c1",
            (_iri("C2"), None, None, True),
            ("_:r1", _iri("R101"), _iri("C100"), False),
        ),
        "C2": _definition_rows(
            "_:c2",
            (_iri("C3"), None, None, False),
            ("_:r2", _iri("R105"), _iri("C200"), False),
        ),
    }

    async def select(
        query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str | None]]:
        assert required_variables == {"expression", "list", "cell"}
        code = next(code for code in rows_by_code if f"#{code}>" in query)
        calls.append(code)
        return rows_by_code[code]

    complete = await read_complete_definition(select, "C1")

    assert calls == ["C1", "C2"]
    assert complete.root_code == "C1"
    assert len(complete.facts) == 4
    assert {fact.depth for fact in complete.facts} == {0, 1}
    assert {fact.anchor_code for fact in complete.facts} == {"C1", "C2"}


@pytest.mark.unit
async def test_complete_definition_reconverged_dag_queries_shared_genus_once() -> None:
    calls: list[str] = []
    rows_by_code = {
        "C1": _definition_rows(
            "_:c1",
            (_iri("C2"), None, None, True),
            (_iri("C3"), None, None, True),
        ),
        "C2": _definition_rows("_:c2", (_iri("C4"), None, None, True)),
        "C3": _definition_rows("_:c3", (_iri("C4"), None, None, True)),
        "C4": _definition_rows("_:c4", (_iri("C5"), None, None, False)),
    }

    async def select(
        query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str | None]]:
        code = next(code for code in rows_by_code if f"#{code}>" in query)
        calls.append(code)
        return rows_by_code[code]

    complete = await read_complete_definition(select, "C1")

    assert calls == ["C1", "C2", "C3", "C4"]
    c4_facts = [fact for fact in complete.facts if fact.anchor_code == "C4"]
    assert len(c4_facts) == 1


@pytest.mark.unit
async def test_complete_definition_fails_closed_at_depth_or_node_bound() -> None:
    rows = _definition_rows("_:c1", (_iri("C2"), None, None, True))

    async def select(
        query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str | None]]:
        return rows

    with pytest.raises(CompleteDefinitionError, match="depth bound"):
        await read_complete_definition(select, "C1", max_depth=0)
    with pytest.raises(CompleteDefinitionError, match="node bound"):
        await read_complete_definition(select, "C1", max_nodes=1)
    with pytest.raises(ValueError, match="non-negative"):
        await read_complete_definition(select, "C1", max_depth=-1)
    with pytest.raises(ValueError, match="positive"):
        await read_complete_definition(select, "C1", max_nodes=0)


@pytest.mark.unit
async def test_complete_definition_propagates_store_failure() -> None:
    async def select(
        query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str | None]]:
        raise RuntimeError("store unavailable")

    with pytest.raises(RuntimeError, match="store unavailable"):
        await read_complete_definition(select, "C1")
