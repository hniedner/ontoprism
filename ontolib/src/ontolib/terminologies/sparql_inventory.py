"""Executable inventory of production SPARQL shapes and transport operations."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from pathlib import Path

_SOURCE_ROOTS = (
    "ontolib/src",
    "backend/src",
    "scripts",
)
_SPARQL_TOKEN = re.compile(
    r"\b(?:SELECT|ASK|CONSTRUCT|DESCRIBE|INSERT|DELETE|CLEAR|LOAD|GRAPH)\b",
    re.IGNORECASE,
)
_STATIC_SQL = re.compile(
    r"\b(?:FROM|JOIN|UPDATE|INSERT\s+INTO|DELETE\s+FROM)\s+[a-z_][a-z0-9_.]*",
    re.IGNORECASE,
)
_TRANSPORT_METHODS = frozenset(
    {"ask", "load", "select", "select_once", "select_raw", "update"}
)
_SPARQL_FILE_MARKERS = (
    "SparqlHttpClient",
    "SPARQL",
)


class SparqlInventorySummary(TypedDict):
    schema_version: int
    query_shape_count: int
    query_shapes_sha256: str
    transport_operation_count: int
    transport_operations_sha256: str


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _node_digest(node: ast.AST) -> str:
    return hashlib.sha256(
        ast.dump(node, annotate_fields=True, include_attributes=False).encode()
    ).hexdigest()


def _contains_sparql(node: ast.AST) -> bool:
    return any(
        isinstance(candidate, ast.Constant)
        and isinstance(candidate.value, str)
        and _SPARQL_TOKEN.search(candidate.value) is not None
        and _STATIC_SQL.search(candidate.value) is None
        for candidate in ast.walk(node)
    )


def _assignment_name(node: ast.Assign | ast.AnnAssign) -> str | None:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    names = [target.id for target in targets if isinstance(target, ast.Name)]
    return names[0] if len(names) == 1 else None


class _InventoryVisitor(ast.NodeVisitor):
    def __init__(self, relative_path: str, *, collect_transport: bool) -> None:
        self.relative_path = relative_path
        self.collect_transport = collect_transport
        self.qualifiers: list[str] = []
        self.query_shapes: list[tuple[str, str]] = []
        self.transport_operations: list[tuple[str, str]] = []
        self._operation_ordinals: dict[tuple[str, str], int] = {}
        self._mapping_receivers: list[set[str]] = [set()]

    @property
    def qualifier(self) -> str:
        return ".".join(self.qualifiers) if self.qualifiers else "<module>"

    def _visit_definition(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    ) -> None:
        self.qualifiers.append(node.name)
        mapping_receivers = (
            _mapping_parameter_names(node)
            if not isinstance(node, ast.ClassDef)
            else set()
        )
        self._mapping_receivers.append(mapping_receivers)
        if not isinstance(node, ast.ClassDef) and _contains_sparql(node):
            key = f"{self.relative_path}:{self.qualifier}"
            self.query_shapes.append((key, _node_digest(node)))
        self.generic_visit(node)
        self._mapping_receivers.pop()
        self.qualifiers.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_definition(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_definition(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_definition(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._visit_assignment(node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._visit_assignment(node)
        self.generic_visit(node)

    def _visit_assignment(self, node: ast.Assign | ast.AnnAssign) -> None:
        value = node.value
        name = _assignment_name(node)
        if name is not None and isinstance(value, (ast.Dict, ast.DictComp)):
            self._mapping_receivers[-1].add(name)
        if value is None or name is None or not _contains_sparql(value):
            return
        key = f"{self.relative_path}:{self.qualifier}:{name}"
        self.query_shapes.append((key, _node_digest(node)))

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        if (
            self.collect_transport
            and isinstance(function, ast.Attribute)
            and function.attr in _TRANSPORT_METHODS
            and not self._is_mapping_update(function)
        ):
            ordinal_key = (self.qualifier, function.attr)
            ordinal = self._operation_ordinals.get(ordinal_key, 0)
            self._operation_ordinals[ordinal_key] = ordinal + 1
            key = f"{self.relative_path}:{self.qualifier}:{function.attr}[{ordinal}]"
            self.transport_operations.append((key, _node_digest(node)))
        self.generic_visit(node)

    def _is_mapping_update(self, function: ast.Attribute) -> bool:
        return (
            function.attr == "update"
            and isinstance(function.value, ast.Name)
            and function.value.id in self._mapping_receivers[-1]
        )


def _mapping_parameter_names(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> set[str]:
    parameters = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
    return {
        parameter.arg
        for parameter in parameters
        if parameter.annotation is not None
        and _annotation_is_mapping(parameter.annotation)
    }


def _annotation_is_mapping(annotation: ast.AST) -> bool:
    root = annotation.value if isinstance(annotation, ast.Subscript) else annotation
    name = root.id if isinstance(root, ast.Name) else None
    return name in {"dict", "Mapping", "MutableMapping"}


def _python_sources(root: Path) -> list[Path]:
    return sorted(
        path
        for source_root in _SOURCE_ROOTS
        for path in (root / source_root).rglob("*.py")
        if "__pycache__" not in path.parts
    )


def summarize_sparql_inventory(root: Path) -> SparqlInventorySummary:
    """Hash every production SPARQL shape and transport call under *root*."""
    query_shapes: list[tuple[str, str]] = []
    transport_operations: list[tuple[str, str]] = []
    for path in _python_sources(root):
        source = path.read_text(encoding="utf-8")
        relative_path = path.relative_to(root).as_posix()
        tree = ast.parse(source, filename=relative_path)
        visitor = _InventoryVisitor(
            relative_path,
            collect_transport=(
                any(marker in source for marker in _SPARQL_FILE_MARKERS)
                or _contains_sparql(tree)
            ),
        )
        visitor.visit(tree)
        query_shapes.extend(visitor.query_shapes)
        transport_operations.extend(visitor.transport_operations)
    query_shapes.sort()
    transport_operations.sort()
    return {
        "schema_version": 1,
        "query_shape_count": len(query_shapes),
        "query_shapes_sha256": _digest(query_shapes),
        "transport_operation_count": len(transport_operations),
        "transport_operations_sha256": _digest(transport_operations),
    }
