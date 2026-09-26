"""Source-level tripwire that no overlay writer names the stated NCIt graph.

The behavioural guarantee for decomposition publication lives in
``ontolib/tests/decomposition/test_publication_integration.py`` (a sentinel added to the
stated graph, and its triple count, survive a real publication). This text check
additionally covers the xref and legacy writers, which that contract does not
exercise.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]


def _read(path: str) -> str:
    return (_ROOT / path).read_text(encoding="utf-8")


def _assignment_expression(path: str, name: str) -> ast.expr:
    module = ast.parse(_read(path), filename=path)
    matches: list[ast.expr] = []
    for node in module.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == name for target in targets
        ):
            continue
        assert node.value is not None, f"{name} has no value in {path}"
        matches.append(node.value)
    assert len(matches) == 1, f"expected one {name} assignment in {path}"
    return matches[0]


def _constant_string(expression: ast.expr) -> str:
    assert isinstance(expression, ast.Constant), ast.dump(expression)
    assert isinstance(expression.value, str), ast.dump(expression)
    return expression.value


def _function_source(path: str, function_name: str) -> str:
    source = _read(path)
    module = ast.parse(source, filename=path)
    matches = [
        node
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    ]
    assert len(matches) == 1, f"expected one {function_name} in {path}"
    segment = ast.get_source_segment(source, matches[0])
    assert segment is not None
    return segment


@pytest.mark.unit
def test_current_overlay_writers_cannot_target_the_stated_graph() -> None:
    stated_iri = _constant_string(
        _assignment_expression(
            "ontolib/src/ontolib/terminologies/ncit/owl_load.py", "STATED_GRAPH_IRI"
        )
    )
    publication_update = _function_source(
        "ontolib/src/ontolib/decomposition/publication.py",
        "build_replacement_update",
    )

    assert "public = vocab.DECOMPOSED_GRAPH_IRI" in publication_update
    assert "GRAPH <{public}>" in publication_update
    assert "STATED_GRAPH_IRI" not in publication_update
    assert stated_iri not in publication_update

    graph_agnostic_writer = _read("ontolib/src/ontolib/decomposition/legacy_writer.py")
    xref_writer_modules = (
        "ontolib/src/ontolib/repositories/xref/publication.py",
        "ontolib/src/ontolib/repositories/xref/ttl_writer.py",
    )
    for path, source in (
        ("legacy_writer.py", graph_agnostic_writer),
        *((path, _read(path)) for path in xref_writer_modules),
    ):
        assert "STATED_GRAPH_IRI" not in source, path
        assert stated_iri not in source, path
