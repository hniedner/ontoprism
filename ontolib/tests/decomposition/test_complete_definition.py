from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ontolib.decomposition.complete_definition import (
    CompleteDefinitionError,
    build_complete_definition_query,
    definition_facts_from_rows,
    read_complete_definition,
    trace_curated_projection,
)
from ontolib.decomposition.models import (
    CompleteDefinition,
    Constituent,
    Decomposition,
    GenusDefinitionFact,
    RestrictionDefinitionFact,
)
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI

if TYPE_CHECKING:
    from collections.abc import Collection


def _iri(code: str) -> str:
    return f"{NCIT_NS}{code}"


@pytest.mark.unit
def test_complete_definition_query_is_bounded_and_stated_only() -> None:
    query = build_complete_definition_query("C6135")

    assert f"GRAPH <{STATED_GRAPH_IRI}>" in query
    assert f"<{_iri('C6135')}> owl:equivalentClass ?expression" in query
    assert "rdf:rest*" not in query
    assert "rdf:rest+" not in query
    assert "?position" in query
    assert "?childExpression" in query
    assert "?overflow" in query


@pytest.mark.unit
def test_complete_definition_query_rejects_unsafe_code() -> None:
    with pytest.raises(ValueError, match=r"[Uu]nsafe"):
        build_complete_definition_query("C6135> } UNION {")


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
        (
            {
                "expression": "_:expression",
                "position": "-1",
                "member": _iri("C100"),
                "role": None,
                "target": None,
            },
            "position",
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
    ],
)
def test_row_parser_rejects_incomplete_or_foreign_facts(
    row: dict[str, str | None], message: str
) -> None:
    with pytest.raises(CompleteDefinitionError, match=message):
        definition_facts_from_rows("C1", depth=0, rows=[row])


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

    traced = trace_curated_projection(
        [
            Constituent(
                axis="op:AssociatedRegion",
                filler_code="C200",
                axis_source="role",
            )
        ],
        complete,
    )

    source_ids = set(traced[0].source_definition_ids)
    assert {
        fact.role_code
        for fact in facts
        if isinstance(fact, RestrictionDefinitionFact) and fact.fact_id in source_ids
    } == {"R101"}


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
    valid = GenusDefinitionFact(
        fact_id="a" * 64,
        anchor_code="C1",
        group_id="b" * 64,
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
            group=item["group"],
            needs_review=item["needs_review"],
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
        assert required_variables == {"expression", "position", "member", "overflow"}
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
