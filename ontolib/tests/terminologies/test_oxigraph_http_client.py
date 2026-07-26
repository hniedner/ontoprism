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
    parse_ask_result,
    safe_iri,
)

_TTL = b"@prefix ex: <http://e/> . ex:a ex:b ex:c ."


@pytest.mark.unit
def test_safe_iri_builds_namespaced_uri() -> None:
    assert safe_iri("C3262", NCIT_NS) == f"{NCIT_NS}C3262"


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    ["C1> ?x", "C1}", "a b", "C1<", 'C1"', "C1\n"],
)
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
@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"results": {"bindings": []}}, "missing head object"),
        ({"head": [], "results": {"bindings": []}}, "missing head object"),
        ({"head": {}, "results": {"bindings": []}}, "missing variable list"),
        (
            {"head": {"vars": {}}, "results": {"bindings": []}},
            "missing variable list",
        ),
        (
            {"head": {"vars": [None]}, "results": {"bindings": []}},
            "missing variable list",
        ),
        ({"head": {"vars": []}}, "missing results object"),
        ({"head": {"vars": []}, "results": []}, "missing results object"),
        ({"head": {"vars": []}, "results": {}}, "missing bindings array"),
        (
            {"head": {"vars": []}, "results": {"bindings": {}}},
            "missing bindings array",
        ),
        ([], "root is not an object"),
    ],
)
def test_flatten_bindings_rejects_malformed_select_envelope(
    data: object,
    message: str,
) -> None:
    with pytest.raises(StorageError, match=message):
        flatten_bindings(data)


@pytest.mark.unit
def test_flatten_bindings_rejects_mixed_result_forms() -> None:
    data = {
        "head": {"vars": []},
        "results": {"bindings": []},
        "boolean": False,
    }
    with pytest.raises(StorageError, match="both SELECT and ASK"):
        flatten_bindings(data)


@pytest.mark.unit
@pytest.mark.parametrize(
    "bindings",
    [
        [None],
        [{"part": None}],
        [{1: {"value": "invalid variable"}}],
        [{"part": {}}],
        [{"part": {"value": None}}],
    ],
)
def test_flatten_bindings_rejects_malformed_rows_and_cells(
    bindings: list[object],
) -> None:
    with pytest.raises(StorageError, match="malformed SPARQL SELECT response"):
        flatten_bindings(
            {"head": {"vars": ["part"]}, "results": {"bindings": bindings}}
        )


@pytest.mark.unit
def test_flatten_bindings_rejects_undeclared_variable() -> None:
    data = {
        "head": {"vars": ["part"]},
        "results": {"bindings": [{"whole": {"type": "uri", "value": f"{NCIT_NS}C1"}}]},
    }
    with pytest.raises(StorageError, match="undeclared variable"):
        flatten_bindings(data)


@pytest.mark.unit
@pytest.mark.parametrize("cell_type", [None, "", "unknown"])
def test_flatten_bindings_rejects_invalid_cell_type(cell_type: object) -> None:
    data = {
        "head": {"vars": ["part"]},
        "results": {
            "bindings": [{"part": {"type": cell_type, "value": f"{NCIT_NS}C1"}}]
        },
    }
    with pytest.raises(StorageError, match="invalid binding type"):
        flatten_bindings(data)


@pytest.mark.unit
@pytest.mark.parametrize("value", [True, False])
def test_parse_ask_result_returns_boolean(value: bool) -> None:
    assert parse_ask_result({"head": {}, "boolean": value}) is value


@pytest.mark.unit
@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"boolean": False}, "missing head object"),
        ({"head": [], "boolean": False}, "missing head object"),
        ({"head": {}}, "missing boolean result"),
        ({"head": {}, "boolean": "false"}, "missing boolean result"),
        ({"head": {}, "boolean": 0}, "missing boolean result"),
        ([], "missing boolean result"),
    ],
)
def test_parse_ask_result_rejects_malformed_envelope(
    data: object,
    message: str,
) -> None:
    with pytest.raises(StorageError, match=message):
        parse_ask_result(data)


@pytest.mark.unit
def test_parse_ask_result_rejects_mixed_result_forms() -> None:
    data = {
        "head": {},
        "boolean": False,
        "results": {"bindings": []},
    }
    with pytest.raises(StorageError, match="both ASK and SELECT"):
        parse_ask_result(data)


@pytest.mark.unit
async def test_select_against_closed_port_raises_storage_error() -> None:
    # Port 1 has no listener → connection refused → retried → StorageError.
    async with OxigraphHttpClient("http://localhost:1", connect_timeout=0.5) as client:
        with pytest.raises(StorageError, match="transport error"):
            await client.select("ASK {}")


@pytest.mark.unit
async def test_load_against_closed_port_raises_storage_error() -> None:
    async with OxigraphHttpClient("http://localhost:1", connect_timeout=0.5) as client:
        with pytest.raises(StorageError, match="transport error"):
            await client.load(_TTL, content_type="text/turtle")
