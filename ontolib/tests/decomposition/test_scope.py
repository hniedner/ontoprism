"""Hierarchy-scope unit contracts over NCIt's stated named-class DAG."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ontolib.decomposition.scope import (
    HierarchyEdge,
    ScopeHierarchyError,
    build_scope_edge_queries,
    build_scope_overflow_query,
    descendant_codes,
    enumerate_scope_codes,
)
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

pytestmark = pytest.mark.unit


def _iri(code: str) -> str:
    return f"{NCIT_NS}{code}"


class _ScopeClient:
    def __init__(
        self,
        rows: Sequence[Sequence[Mapping[str, str | None]]],
        *,
        overflow: Sequence[Mapping[str, str | None]] = (),
    ) -> None:
        self._rows = iter(rows)
        self._overflow = list(overflow)
        self.requirements: list[frozenset[str]] = []

    async def select_once(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> Sequence[Mapping[str, str | None]]:
        self.requirements.append(frozenset(required_variables))
        if "?overflowChild" in query:
            return self._overflow
        return next(self._rows)


def test_descendant_closure_combines_subclass_and_definition_genus_edges() -> None:
    edges = {
        HierarchyEdge(child="C3262", parent="C2991"),
        HierarchyEdge(child="C7062", parent="C3262"),
        HierarchyEdge(child="C9305", parent="C7062"),
        HierarchyEdge(child="C6135", parent="C9305"),
        HierarchyEdge(child="C100012", parent="C2991"),
        # A cycle must terminate without duplicating the worklist.
        HierarchyEdge(child="C7062", parent="C9305"),
        HierarchyEdge(child="C12400", parent="C12219"),
    }

    neoplasm = descendant_codes("C3262", edges)
    disease = descendant_codes("C2991", edges)

    assert neoplasm == ("C6135", "C7062", "C9305")
    assert disease == ("C100012", "C3262", "C6135", "C7062", "C9305")
    assert set(neoplasm) < set(disease)
    assert "C12400" not in disease


@pytest.mark.parametrize("root_code", ["R101", "Cnotnumeric", ""])
def test_descendant_closure_rejects_non_concept_roots(root_code: str) -> None:
    with pytest.raises(ScopeHierarchyError, match="invalid root NCIt code"):
        descendant_codes(root_code, ())


def test_scope_queries_are_bounded_to_the_stated_named_class_dag() -> None:
    queries = build_scope_edge_queries()
    overflow = build_scope_overflow_query()

    assert len(queries) == 7  # direct named subclasses + six genus positions
    assert all(f"GRAPH <{STATED_GRAPH_IRI}>" in query for query in (*queries, overflow))
    assert "rdfs:subClassOf ?parent" in queries[0]
    assert "owl:equivalentClass ?expression" in queries[1]
    assert "rdf:rest*" not in "\n".join(queries)
    assert "rdf:rest*/rdf:first ?parent" in overflow
    assert overflow.count("rdf:rest ?overflowRest") == 6
    assert "?overflowChild" in overflow


async def test_scope_enumeration_uses_both_edge_kinds_and_requires_bound_rows() -> None:
    client = _ScopeClient(
        [
            [{"child": _iri("C3262"), "parent": _iri("C2991")}],
            [{"child": _iri("C9305"), "parent": _iri("C3262")}],
            [{"child": _iri("C6135"), "parent": _iri("C9305")}],
            [],
            [],
            [],
            [],
        ]
    )

    assert await enumerate_scope_codes(client, "C3262") == (
        "C6135",
        "C9305",
    )
    assert client.requirements == [frozenset({"child", "parent"})] * 7 + [
        frozenset({"overflowChild"})
    ]


async def test_scope_enumeration_fails_closed_on_named_genus_overflow() -> None:
    client = _ScopeClient(
        [[], [], [], [], [], [], []],
        overflow=[{"overflowChild": _iri("C999")}],
    )

    with pytest.raises(ScopeHierarchyError, match="bounded genus positions"):
        await enumerate_scope_codes(client, "C3262")


@pytest.mark.parametrize(
    ("child", "message"),
    [
        (None, "invalid child NCIt IRI"),
        ("https://example.test/C1", "invalid child NCIt IRI"),
        (_iri("R101"), "invalid child NCIt code"),
    ],
)
async def test_scope_enumeration_rejects_malformed_edge_bindings(
    child: str | None,
    message: str,
) -> None:
    client = _ScopeClient(
        [
            [{"child": child, "parent": _iri("C3262")}],
            [],
            [],
            [],
            [],
            [],
            [],
        ]
    )

    with pytest.raises(ScopeHierarchyError, match=message):
        await enumerate_scope_codes(client, "C3262")
