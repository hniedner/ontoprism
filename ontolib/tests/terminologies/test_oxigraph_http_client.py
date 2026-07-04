"""Unit tests for the Oxigraph SPARQL client's pure logic and error behavior.

No mocks: pure helpers are exercised on real SPARQL-JSON data, and the transport
error path is driven against a genuinely closed port.
"""

import pytest

from ontolib.core.exceptions import StorageError
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.oxigraph_http_client import (
    OxigraphHttpClient,
    flatten_bindings,
    safe_iri,
)


@pytest.mark.unit
def test_safe_iri_builds_namespaced_uri() -> None:
    assert safe_iri("C3262", NCIT_NS) == f"{NCIT_NS}C3262"


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["C1> ?x", "C1}", "a b", "C1<", 'C1"'])
def test_safe_iri_rejects_injection(bad: str) -> None:
    with pytest.raises(ValueError, match="Unsafe concept code"):
        safe_iri(bad, NCIT_NS)


@pytest.mark.unit
def test_flatten_bindings_keeps_values_and_omits_unbound() -> None:
    data = {
        "head": {"vars": ["rel", "target"]},
        "results": {
            "bindings": [
                {
                    "rel": {"type": "uri", "value": f"{NCIT_NS}R105"},
                    "target": {"type": "uri", "value": f"{NCIT_NS}C12922"},
                },
                {"rel": {"type": "uri", "value": f"{NCIT_NS}R100"}},
            ]
        },
    }
    rows = flatten_bindings(data)
    assert rows[0] == {"rel": f"{NCIT_NS}R105", "target": f"{NCIT_NS}C12922"}
    # Unbound optional is omitted, not empty-stringed.
    assert rows[1] == {"rel": f"{NCIT_NS}R100"}


@pytest.mark.unit
def test_flatten_bindings_empty_result() -> None:
    assert flatten_bindings({"head": {"vars": []}, "results": {"bindings": []}}) == []


@pytest.mark.unit
async def test_select_against_closed_port_raises_storage_error() -> None:
    # Port 1 has no listener → connection refused → retried → StorageError.
    async with OxigraphHttpClient("http://localhost:1", connect_timeout=0.5) as client:
        with pytest.raises(StorageError, match="transport error"):
            await client.select("ASK {}")
