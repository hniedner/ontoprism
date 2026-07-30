"""External-store and double-fidelity contracts for hierarchy scopes."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ontolib.decomposition.scope import ScopeHierarchyError, enumerate_scope_codes
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

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
        _query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> Sequence[Mapping[str, str | None]]:
        assert required_variables in ({"child", "parent"}, {"overflowChild"})
        return self._responses.pop(0)


async def test_scope_double_matches_disposable_oxigraph_for_defined_genus_dag(
    isolated_oxigraph_url: str,
) -> None:
    async with OxigraphHttpClient(isolated_oxigraph_url) as client:
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


@pytest.mark.mutating_integration
async def test_scope_fails_closed_when_named_genus_follows_bounded_prefix(
    isolated_oxigraph_url: str,
) -> None:
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
    async with OxigraphHttpClient(isolated_oxigraph_url) as client:
        await client.load(
            turtle.encode(),
            content_type="text/turtle",
            graph_iri=STATED_GRAPH_IRI,
            replace=False,
        )
        with pytest.raises(ScopeHierarchyError, match="bounded genus positions"):
            await enumerate_scope_codes(client, "C99501")
