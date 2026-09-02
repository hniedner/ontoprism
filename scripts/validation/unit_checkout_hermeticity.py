"""Reject fixed gitignored inputs from hermetic unit tests."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_TEST_ROOTS = ("ontolib/tests", "backend/tests")
_IGNORED_ROOTS = frozenset({"data", "tmp"})
_PATH_READ_METHODS = frozenset({"open", "read_bytes", "read_text"})
_INPUT_KEYWORD_TERMS = ("input", "source", "manifest")


@dataclass(frozen=True, slots=True)
class Violation:
    path: str
    line: int
    message: str


def _marker_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Attribute):
        return None
    if (
        isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
        and node.value.attr == "mark"
    ):
        return node.attr
    return None


def _module_markers(tree: ast.Module) -> set[str]:
    markers: set[str] = set()
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = (
            statement.targets
            if isinstance(statement, ast.Assign)
            else [statement.target]
        )
        if not any(
            isinstance(target, ast.Name) and target.id == "pytestmark"
            for target in targets
        ):
            continue
        value = statement.value
        if value is not None:
            markers.update(
                marker
                for node in ast.walk(value)
                if (marker := _marker_name(node)) is not None
            )
    return markers


def _unit_nodes(tree: ast.Module) -> tuple[ast.AST, ...]:
    markers = _module_markers(tree)
    if "full_store" in markers:
        return ()
    if "unit" in markers:
        return (tree,)
    selected: list[ast.AST] = []
    for node in tree.body:
        is_container = isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        )
        if is_container and any(
            _marker_name(decorator) == "unit" for decorator in node.decorator_list
        ):
            selected.append(node)
    return tuple(selected)


def _ignored_literal(value: str) -> bool:
    normalized = value.replace("\\", "/").removeprefix("./")
    root, separator, _remainder = normalized.partition("/")
    return bool(separator) and root in _IGNORED_ROOTS


def _ignored_literal_or_root(value: str) -> bool:
    normalized = value.replace("\\", "/").removeprefix("./")
    return _ignored_literal(value) or normalized in _IGNORED_ROOTS


def _path_segments(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return [*_path_segments(node.left), *_path_segments(node.right)]
    return [node]


def _contains_name(node: ast.AST, name: str) -> bool:
    return any(
        isinstance(child, ast.Name) and child.id == name for child in ast.walk(node)
    )


def _repo_anchors(tree: ast.AST) -> set[str]:
    anchors: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value_names = {
                child.id
                for child in ast.walk(node.value)
                if isinstance(child, ast.Name)
            }
            if not (_contains_name(node.value, "__file__") or value_names & anchors):
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in anchors:
                    anchors.add(target.id)
                    changed = True
    return anchors


def _is_repo_expression(node: ast.AST, anchors: set[str]) -> bool:
    return _contains_name(node, "__file__") or any(
        isinstance(child, ast.Name) and child.id in anchors for child in ast.walk(node)
    )


def _segmented_ignored_path(node: ast.BinOp, anchors: set[str]) -> str | None:
    if not isinstance(node.op, ast.Div) or not _is_repo_expression(node, anchors):
        return None
    values = [
        segment.value
        for segment in _path_segments(node)
        if isinstance(segment, ast.Constant) and isinstance(segment.value, str)
    ]
    for index, value in enumerate(values):
        if value in _IGNORED_ROOTS:
            return "/".join(values[index:])
    return None


def _is_input_context(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    current = node
    while isinstance(parents.get(current), (ast.Attribute, ast.BinOp)):
        current = parents[current]
    parent = parents.get(current)
    if isinstance(parent, ast.keyword):
        return parent.arg is not None and any(
            term in parent.arg.lower() for term in _INPUT_KEYWORD_TERMS
        )
    if isinstance(parent, ast.Expr):
        return True
    if isinstance(parent, ast.Call):
        if current in parent.args:
            return True
        if (
            parent.func is current
            and isinstance(current, ast.Attribute)
            and current.attr in _PATH_READ_METHODS
        ):
            return True
        return any(
            keyword.value is current
            and keyword.arg is not None
            and any(term in keyword.arg.lower() for term in _INPUT_KEYWORD_TERMS)
            for keyword in parent.keywords
        )
    return False


def fixed_ignored_path_violations(
    source: str, *, filename: str
) -> tuple[Violation, ...]:
    """Return fixed ``data``/``tmp`` path uses in one parsed source document."""
    tree = ast.parse(source, filename=filename)
    anchors = _repo_anchors(tree)
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    violations: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp):
            path = _segmented_ignored_path(node, anchors)
            if path is not None and _is_input_context(node, parents):
                violations.add((node.lineno, path))
        elif isinstance(node, ast.Call) and node.args:
            function = node.func
            is_path = (isinstance(function, ast.Name) and function.id == "Path") or (
                isinstance(function, ast.Attribute)
                and function.attr == "Path"
                and isinstance(function.value, ast.Name)
                and function.value.id == "pathlib"
            )
            first = node.args[0]
            if (
                is_path
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and _ignored_literal_or_root(first.value)
                and _is_input_context(node, parents)
            ):
                violations.add((node.lineno, first.value))
            is_open = isinstance(function, ast.Name) and function.id == "open"
            if (
                is_open
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and _ignored_literal_or_root(first.value)
            ):
                violations.add((node.lineno, first.value))
        if isinstance(node, ast.Call):
            is_open = isinstance(node.func, ast.Name) and node.func.id == "open"
            for keyword in node.keywords:
                if (
                    keyword.arg is not None
                    and (
                        (is_open and keyword.arg == "file")
                        or any(
                            term in keyword.arg.lower() for term in _INPUT_KEYWORD_TERMS
                        )
                    )
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                    and _ignored_literal_or_root(keyword.value.value)
                ):
                    violations.add((node.lineno, keyword.value.value))
    return tuple(
        Violation(filename, line, f"fixed gitignored input path: {path}")
        for line, path in sorted(violations)
    )


def unit_test_surface_violations(root: Path) -> tuple[Violation, ...]:
    """Discover and inspect explicitly marked non-full-store unit tests."""
    violations: list[Violation] = []
    for test_root in _TEST_ROOTS:
        for path in sorted((root / test_root).rglob("test_*.py")):
            relative = path.relative_to(root).as_posix()
            source = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source, filename=relative)
            except SyntaxError as error:
                violations.append(
                    Violation(
                        relative,
                        error.lineno or 0,
                        f"unable to parse unit-test candidate: {error.msg}",
                    )
                )
                continue
            unit_nodes = _unit_nodes(tree)
            if not unit_nodes:
                continue
            discovered = fixed_ignored_path_violations(source, filename=relative)
            module_unit = isinstance(unit_nodes[0], ast.Module)
            full_store_ranges = [
                (node.lineno, node.end_lineno or node.lineno)
                for node in tree.body
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                )
                and any(
                    _marker_name(decorator) == "full_store"
                    for decorator in node.decorator_list
                )
            ]
            unit_ranges = [
                (
                    getattr(node, "lineno", 0),
                    getattr(node, "end_lineno", None) or getattr(node, "lineno", 0),
                )
                for node in unit_nodes
                if not isinstance(node, ast.Module)
            ]
            violations.extend(
                violation
                for violation in discovered
                if (
                    (
                        module_unit
                        and not any(
                            start <= violation.line <= end
                            for start, end in full_store_ranges
                        )
                    )
                    or any(start <= violation.line <= end for start, end in unit_ranges)
                )
            )
    return tuple(
        sorted(
            set(violations),
            key=lambda item: (item.path, item.line, item.message),
        )
    )
