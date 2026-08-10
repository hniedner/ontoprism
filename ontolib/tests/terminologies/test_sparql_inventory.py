from __future__ import annotations

import json
from pathlib import Path

import pytest

from ontolib.terminologies.sparql_inventory import summarize_sparql_inventory

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
