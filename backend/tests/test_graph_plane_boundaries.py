"""Source-level guards that overlay writers and readers stay on their own graph planes.

These read production source text. A behavioural replacement against a disposable
QLever (publish, then assert the stated graph is unchanged) is tracked in #339.
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


def _qualified_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name(node.value)
        return f"{prefix}.{node.attr}" if prefix is not None else None
    return None


def _static_string(expression: ast.expr, references: dict[str, str]) -> str:
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        return expression.value
    if isinstance(expression, ast.JoinedStr):
        parts: list[str] = []
        for value in expression.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
                continue
            if isinstance(value, ast.FormattedValue):
                name = _qualified_name(value.value)
                assert name in references, f"unresolved static string reference: {name}"
                parts.append(references[name])
                continue
            raise AssertionError(
                f"unsupported static string component: {ast.dump(value)}"
            )
        return "".join(parts)
    raise AssertionError(f"{ast.dump(expression)} is not a static string expression")


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
def test_current_read_planes_are_distinct_in_production_source() -> None:
    projection_reader = _read("ontolib/src/ontolib/decomposition/read_queries.py")
    assert "STATED_GRAPH_IRI" not in projection_reader
    assert "DECOMPOSED_GRAPH_IRI" in projection_reader

    stated_readers = (
        "ontolib/src/ontolib/decomposition/stated_queries.py",
        "ontolib/src/ontolib/decomposition/scope.py",
        "ontolib/src/ontolib/decomposition/walker.py",
    )
    assert stated_readers
    for path in stated_readers:
        assert "STATED_GRAPH_IRI" in _read(path), path


@pytest.mark.unit
def test_current_authored_ncit_graph_iris_are_explicit_technical_debt() -> None:
    decomposed = _static_string(
        _assignment_expression(
            "ontolib/src/ontolib/decomposition/vocab.py", "DECOMPOSED_GRAPH_IRI"
        ),
        {},
    )
    xref = _static_string(
        _assignment_expression(
            "ontolib/src/ontolib/repositories/xref/vocab.py",
            "NCIT_UPSTREAM_XREF_GRAPH_IRI",
        ),
        {},
    )
    showcase = _static_string(
        _assignment_expression(
            "ontolib/src/ontolib/decomposition/enhanced_showcase.py",
            "SHOWCASE_GRAPH_IRI",
        ),
        {"vocab.DECOMPOSED_GRAPH_IRI": decomposed},
    )

    assert {
        "decomposition": decomposed,
        "upstream-xref": xref,
        "enhanced-showcase": showcase,
    } == {
        "decomposition": (
            "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-decomposed.owl"
        ),
        "upstream-xref": (
            "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-upstream-xref.owl"
        ),
        "enhanced-showcase": (
            "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-decomposed.owl/"
            "enhanced-ncit-showcase"
        ),
    }

    assert 'f"{vocab.DECOMPOSED_GRAPH_IRI}/staging/{digest}"' in _function_source(
        "ontolib/src/ontolib/decomposition/publication.py", "staging_graph_iri"
    )
    assert (
        'f"{SHOWCASE_GRAPH_IRI}/staging/{hashlib.sha256(run_id.encode()).hexdigest()}"'
        in _function_source(
            "ontolib/src/ontolib/decomposition/enhanced_showcase.py",
            "showcase_staging_graph_iri",
        )
    )
    xref_publication = _read("ontolib/src/ontolib/repositories/xref/publication.py")
    assert "/generation/{component}/{generation_id}" in xref_publication
    assert "/active/{component}" in xref_publication


@pytest.mark.unit
def test_current_overlay_writers_cannot_target_the_stated_graph() -> None:
    stated_iri = _static_string(
        _assignment_expression(
            "ontolib/src/ontolib/terminologies/ncit/owl_load.py", "STATED_GRAPH_IRI"
        ),
        {},
    )
    publication_update = _function_source(
        "ontolib/src/ontolib/decomposition/publication.py",
        "build_replacement_update",
    )
    showcase_update = _function_source(
        "ontolib/src/ontolib/decomposition/enhanced_showcase.py",
        "build_showcase_replacement_update",
    )

    assert "public = vocab.DECOMPOSED_GRAPH_IRI" in publication_update
    assert "GRAPH <{public}>" in publication_update
    assert "GRAPH <{SHOWCASE_GRAPH_IRI}>" in showcase_update
    for update in (publication_update, showcase_update):
        assert "STATED_GRAPH_IRI" not in update
        assert stated_iri not in update

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
