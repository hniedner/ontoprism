from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from ontolib.terminologies.sparql_inventory import (
    _contains_sparql,
    summarize_sparql_inventory,
)

ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = ROOT / "scripts" / "validation" / "sparql-inventory.json"


@pytest.mark.unit
def test_production_sparql_inventory_matches_machine_readable_contract() -> None:
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    actual = summarize_sparql_inventory(ROOT)

    assert actual == expected
    assert actual["query_shape_count"] >= 30
    assert actual["transport_operation_count"] >= 30


@pytest.mark.unit
def test_inventory_detects_query_shapes_without_engine_named_imports(
    tmp_path: Path,
) -> None:
    source = tmp_path / "ontolib" / "src" / "query_builder.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        'def build_query() -> str:\n    return "SELECT ?s WHERE { ?s ?p ?o }"\n',
        encoding="utf-8",
    )

    initial = summarize_sparql_inventory(tmp_path)
    source.write_text(
        "def build_query() -> str:\n"
        '    return "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1"\n',
        encoding="utf-8",
    )
    changed = summarize_sparql_inventory(tmp_path)

    assert initial["query_shape_count"] == changed["query_shape_count"] == 1
    assert initial["query_shapes_sha256"] != changed["query_shapes_sha256"]


@pytest.mark.unit
def test_inventory_ignores_static_sql_and_mapping_update(tmp_path: Path) -> None:
    source = tmp_path / "scripts" / "report.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "def read_rows(session, values):\n"
        "    statement = 'SELECT status FROM decomp_run'\n"
        "    values.update({'status': 'failed'})\n"
        "    return session.execute(statement)\n",
        encoding="utf-8",
    )

    summary = summarize_sparql_inventory(tmp_path)

    assert summary["query_shape_count"] == 0
    assert summary["transport_operation_count"] == 0


@pytest.mark.unit
def test_inventory_ignores_delete_prefixed_hyphen_suffixed_operation_name(
    tmp_path: Path,
) -> None:
    source = tmp_path / "scripts" / "runner.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        'FAILURES = {"delete-merged": "Git branch deletion failed"}\n',
        encoding="utf-8",
    )

    summary = summarize_sparql_inventory(tmp_path)

    assert summary["query_shape_count"] == 0
    assert summary["transport_operation_count"] == 0


@pytest.mark.unit
def test_inventory_ignores_governance_permission_action_messages(
    tmp_path: Path,
) -> None:
    source = tmp_path / "scripts" / "validation" / "permissions.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        'ASK_ACTION = "a" + "sk"\n'
        "def error(role: str) -> str:\n"
        '    return f"{role} action must be allow, deny, or {ASK_ACTION}"\n',
        encoding="utf-8",
    )

    summary = summarize_sparql_inventory(tmp_path)

    assert summary["query_shape_count"] == 0
    assert summary["transport_operation_count"] == 0


@pytest.mark.unit
def test_permission_action_validator_is_not_a_sparql_query_shape() -> None:
    source = (
        ROOT / "scripts" / "validation" / "validate_opencode_config.py"
    ).read_text(encoding="utf-8")
    module = ast.parse(source)
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "validate_permission_actions"
    )

    assert not _contains_sparql(function)


@pytest.mark.unit
def test_inventory_does_not_mistake_from_named_sparql_for_sql(tmp_path: Path) -> None:
    source = tmp_path / "scripts" / "named_graph.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "def query():\n"
        "    return 'SELECT ?s FROM NAMED <urn:graph> "
        "WHERE { GRAPH ?g { ?s ?p ?o } }'\n",
        encoding="utf-8",
    )

    assert summarize_sparql_inventory(tmp_path)["query_shape_count"] == 1


@pytest.mark.unit
def test_inventory_detects_typed_sparql_transport_not_dict_update(
    tmp_path: Path,
) -> None:
    source = tmp_path / "ontolib" / "src" / "transport.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "async def execute(client: SparqlHttpClient, values: dict[str, str]):\n"
        "    query = 'SELECT ?s WHERE { ?s ?p ?o }'\n"
        "    values.update({'query': query})\n"
        "    await client.select(query)\n"
        "    await client.ask('ASK { ?s ?p ?o }')\n"
        "    await client.update('DELETE WHERE { ?s ?p ?o }')\n"
        "    await client.load(query)\n",
        encoding="utf-8",
    )

    summary = summarize_sparql_inventory(tmp_path)

    assert summary["query_shape_count"] == 2
    assert summary["transport_operation_count"] == 4
