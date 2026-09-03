"""External-store and double-fidelity contracts for hierarchy scopes."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ontolib.decomposition.scope import (
    ScopeHierarchyError,
    enumerate_scope_codes,
    read_scope_hierarchy_edges,
)
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.client import ncit_sparql_client
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

pytestmark = pytest.mark.integration


def _iri(code: str) -> str:
    return f"{NCIT_NS}{code}"


class _ScopeRowsDouble:
    def __init__(self) -> None:
        self._responses: list[list[dict[str, str]]] = [
            [
                {"child": _iri("C99501"), "parent": _iri("C99500")},
                {"child": _iri("C99502"), "parent": _iri("C99501")},
                {"child": _iri("C99504"), "parent": _iri("C99500")},
            ],
            [{"child": _iri("C99503"), "parent": _iri("C99502")}],
            [],
            [],
            [],
            [],
            [],
            [],
        ]

    async def select_once(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> Sequence[Mapping[str, str | None]]:
        del query
        assert required_variables in ({"child", "parent"}, {"overflowChild"})
        return self._responses.pop(0)


async def test_scope_double_matches_disposable_qlever_for_defined_genus_dag(
    isolated_qlever_url: str,
) -> None:
    async with ncit_sparql_client(isolated_qlever_url) as client:
        real_neoplasm = await enumerate_scope_codes(client, "C99501")
        real_disease = await enumerate_scope_codes(client, "C99500")
    double_neoplasm = await enumerate_scope_codes(_ScopeRowsDouble(), "C99501")
    double_disease = await enumerate_scope_codes(_ScopeRowsDouble(), "C99500")

    assert real_neoplasm == double_neoplasm == ("C99502", "C99503")
    assert (
        real_disease
        == double_disease
        == (
            "C99501",
            "C99502",
            "C99503",
            "C99504",
        )
    )
    assert "C99505" not in real_disease


async def test_scope_hierarchy_snapshot_matches_disposable_qlever_and_double(
    isolated_qlever_url: str,
) -> None:
    async with ncit_sparql_client(isolated_qlever_url) as client:
        real = await read_scope_hierarchy_edges(client)
    doubled = await read_scope_hierarchy_edges(_ScopeRowsDouble())

    expected = {
        ("C99501", "C99500"),
        ("C99502", "C99501"),
        ("C99503", "C99502"),
        ("C99504", "C99500"),
    }
    assert {(edge.child, edge.parent) for edge in doubled} == expected
    assert expected <= {(edge.child, edge.parent) for edge in real}


@pytest.mark.mutating_integration
async def test_scope_fails_closed_when_named_genus_follows_bounded_prefix(
    isolated_qlever_url: str,
    preserved_stated_graph: None,
) -> None:
    del preserved_stated_graph
    restrictions = "\n".join(
        (
            "[ a <http://www.w3.org/2002/07/owl#Restriction> ; "
            f"<http://www.w3.org/2002/07/owl#onProperty> <{NCIT_NS}R101> ; "
            f"<http://www.w3.org/2002/07/owl#someValuesFrom> <{NCIT_NS}C12400> ]"
        )
        for _ in range(7)
    )
    turtle = (
        f"<{NCIT_NS}C99506> "
        "<http://www.w3.org/2002/07/owl#equivalentClass> [ "
        "<http://www.w3.org/2002/07/owl#intersectionOf> ( "
        f"{restrictions} <{NCIT_NS}C99502> ) ] ."
    )
    async with ncit_sparql_client(isolated_qlever_url) as client:
        await client.load(
            turtle.encode(),
            content_type="text/turtle",
            graph_iri=STATED_GRAPH_IRI,
            replace=False,
        )
        with pytest.raises(ScopeHierarchyError, match="bounded genus positions"):
            await enumerate_scope_codes(client, "C99501")
