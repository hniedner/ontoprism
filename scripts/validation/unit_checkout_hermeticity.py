"""Reject untracked repository inputs from hermetic unit tests."""

from __future__ import annotations

import ast
import ntpath
import posixpath
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_TEST_ROOTS = ("ontolib/tests", "backend/tests")
_PATH_READ_METHODS = frozenset({"open", "read_bytes", "read_text"})
_INPUT_TERMS = ("input", "source", "manifest")
_INPUT_CALL_TERMS = (*_INPUT_TERMS, "load", "parse", "read")
_OUTPUT_TERMS = (
    "output",
    "destination",
    "target",
    "cache",
    "directory",
)
_WRITE_METHODS = frozenset({"mkdir", "touch", "unlink", "write_bytes", "write_text"})
_GIT_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class Violation:
    path: str
    line: int
    message: str


class GitRunner(Protocol):
    def __call__(
        self, arguments: tuple[str, ...], *, cwd: Path, timeout: float
    ) -> bytes: ...


@dataclass(frozen=True, slots=True)
class _ResolvedPath:
    value: str = ""
    pytest_owned: bool = False
    invalid: bool = False
    explicit: bool = False


def _run_git(arguments: tuple[str, ...], *, cwd: Path, timeout: float) -> bytes:
    return subprocess.check_output(  # noqa: S603 - fixed inventory commands
        arguments, cwd=cwd, timeout=timeout
    )


def _marker_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        return _marker_name(node.func)
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


def _decorator_markers(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> set[str]:
    return {
        marker
        for decorator in node.decorator_list
        if (marker := _marker_name(decorator)) is not None
    }


def mixed_test_marker_violations(
    source: str, *, filename: str
) -> tuple[Violation, ...]:
    """Reject test nodes marked as both unit and a real-boundary contract."""
    tree = ast.parse(source, filename=filename)
    module_markers = _module_markers(tree)
    violations: list[Violation] = []

    def inspect_function(
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        inherited: set[str],
        identity: str,
    ) -> None:
        if not node.name.startswith("test_"):
            return
        effective = inherited | _decorator_markers(node)
        boundary_markers = effective & {"integration", "full_store"}
        if "unit" in effective and boundary_markers:
            conflicts = ", ".join(sorted(boundary_markers))
            violations.append(
                Violation(
                    filename,
                    node.lineno,
                    f"{identity} is both unit and {conflicts}",
                )
            )

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inspect_function(node, module_markers, node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            class_markers = module_markers | _decorator_markers(node)
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    inspect_function(
                        child,
                        class_markers,
                        f"{node.name}::{child.name}",
                    )
    return tuple(violations)


def _inventory_error(error: BaseException) -> tuple[Violation, ...]:
    return (
        Violation(
            "<git inventory>",
            0,
            "unable to inventory tracked checkout: " + type(error).__name__,
        ),
    )


def _decode_inventory(payload: bytes) -> frozenset[str]:
    return frozenset(entry for entry in payload.decode("utf-8").split("\0") if entry)


def _tracked_test_inventory(root: Path, runner: GitRunner) -> frozenset[str]:
    payload = runner(
        ("git", "ls-files", "-z", "--", *_TEST_ROOTS),
        cwd=root,
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    return frozenset(
        path
        for path in _decode_inventory(payload)
        if Path(path).name.startswith("test_") and path.endswith(".py")
    )


def _all_tracked_inventory(root: Path, runner: GitRunner) -> frozenset[str]:
    return _decode_inventory(
        runner(
            ("git", "ls-files", "-z"),
            cwd=root,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    )


def _read_failure(relative: str, error: BaseException) -> Violation:
    return Violation(
        relative,
        0,
        "unable to read tracked test candidate: " + type(error).__name__,
    )


def mixed_test_marker_surface_violations(
    root: Path, *, runner: GitRunner = _run_git
) -> tuple[Violation, ...]:
    """Inspect tracked repository tests without importing test modules."""
    try:
        test_paths = _tracked_test_inventory(root, runner)
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        return _inventory_error(error)

    violations: list[Violation] = []
    for relative in sorted(test_paths):
        try:
            source = (root / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            violations.append(_read_failure(relative, error))
            continue
        try:
            violations.extend(mixed_test_marker_violations(source, filename=relative))
        except SyntaxError as error:
            violations.append(
                Violation(
                    relative,
                    error.lineno or 0,
                    f"unable to parse test marker candidate: {error.msg}",
                )
            )
    return tuple(violations)


def _unit_nodes(tree: ast.Module) -> tuple[ast.AST, ...]:
    markers = _module_markers(tree)
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


def _path_constructor_aliases(tree: ast.Module) -> set[str]:
    aliases = {"Path"}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "pathlib":
            for name in node.names:
                if name.name == "Path":
                    aliases.add(name.asname or name.name)
    return aliases


def _assignments(tree: ast.Module) -> dict[str, ast.AST]:
    assignments: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                assignments[target.id] = node.value
    return assignments


def _literal_path(value: str, *, explicit: bool = False) -> _ResolvedPath:
    if "://" in value:
        return _ResolvedPath()
    normalized = value.replace("\\", "/")
    if (
        posixpath.isabs(normalized)
        or ntpath.isabs(value)
        or normalized == ".."
        or normalized.startswith("../")
    ):
        return _ResolvedPath(normalized, invalid=True, explicit=explicit)
    normalized = posixpath.normpath(normalized).removeprefix("./")
    if normalized in {"", "."}:
        return _ResolvedPath()
    return _ResolvedPath(normalized, explicit=explicit)


def _combine(base: _ResolvedPath, child: _ResolvedPath) -> _ResolvedPath:
    if base.invalid or child.invalid:
        return _ResolvedPath(
            child.value or base.value,
            invalid=True,
            explicit=base.explicit or child.explicit,
        )
    if base.pytest_owned:
        return _ResolvedPath(pytest_owned=True)
    if child.pytest_owned:
        return child
    if not base.value:
        return child
    if not child.value:
        return base
    return _literal_path(
        f"{base.value}/{child.value}",
        explicit=base.explicit or child.explicit,
    )


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id.lower()
    if isinstance(node, ast.Attribute):
        return node.attr.lower()
    return ""


class _PathResolver:
    def __init__(self, tree: ast.Module, filename: str) -> None:
        self._assignments = _assignments(tree)
        self._constructors = _path_constructor_aliases(tree)
        self._filename = filename

    def resolve(
        self, node: ast.AST, resolving: frozenset[str] = frozenset()
    ) -> _ResolvedPath | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            result = _literal_path(node.value)
        elif isinstance(node, ast.Name):
            result = self._resolve_name(node, resolving)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            result = self._resolve_division(node, resolving)
        elif isinstance(node, ast.Attribute):
            result = self._resolve_attribute(node, resolving)
        elif isinstance(node, ast.Subscript):
            result = self._resolve_subscript(node, resolving)
        elif isinstance(node, ast.Call):
            result = self._resolve_call(node, resolving)
        else:
            result = None
        return result

    def _resolve_name(
        self, node: ast.Name, resolving: frozenset[str]
    ) -> _ResolvedPath | None:
        if node.id in {"tmp_path", "tmpdir"}:
            return _ResolvedPath(pytest_owned=True)
        if node.id == "__file__":
            return _literal_path(self._filename)
        if node.id in resolving or node.id not in self._assignments:
            return None
        return self.resolve(self._assignments[node.id], resolving | {node.id})

    def _resolve_division(
        self, node: ast.BinOp, resolving: frozenset[str]
    ) -> _ResolvedPath | None:
        left = self.resolve(node.left, resolving)
        right = self.resolve(node.right, resolving)
        if left is None or right is None:
            return None
        combined = _combine(left, right)
        return _ResolvedPath(
            combined.value,
            combined.pytest_owned,
            combined.invalid,
            explicit=True,
        )

    def _resolve_attribute(
        self, node: ast.Attribute, resolving: frozenset[str]
    ) -> _ResolvedPath | None:
        base = self.resolve(node.value, resolving)
        if base is None:
            return None
        if node.attr != "parent" or base.pytest_owned or base.invalid:
            return base
        return _literal_path(posixpath.dirname(base.value), explicit=base.explicit)

    def _resolve_subscript(
        self, node: ast.Subscript, resolving: frozenset[str]
    ) -> _ResolvedPath | None:
        if not isinstance(node.value, ast.Attribute) or node.value.attr != "parents":
            return None
        base = self.resolve(node.value.value, resolving)
        if base is None or base.pytest_owned or base.invalid:
            return base
        if not isinstance(node.slice, ast.Constant) or not isinstance(
            node.slice.value, int
        ):
            return None
        value = base.value
        for _unused in range(node.slice.value + 1):
            value = posixpath.dirname(value)
        return _literal_path(value, explicit=base.explicit)

    def _resolve_call(
        self, node: ast.Call, resolving: frozenset[str]
    ) -> _ResolvedPath | None:
        if isinstance(node.func, ast.Name) and node.func.id in self._constructors:
            return self._join_arguments(node.args, resolving, explicit=True)
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "Path"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "pathlib"
        ):
            return self._join_arguments(node.args, resolving, explicit=True)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "joinpath":
            base = self.resolve(node.func.value, resolving)
            return self._join_arguments(node.args, resolving, base=base, explicit=True)
        if self._is_os_path_join(node.func):
            return self._join_arguments(node.args, resolving, explicit=True)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "resolve":
            return self.resolve(node.func.value, resolving)
        return None

    def _join_arguments(
        self,
        arguments: list[ast.expr],
        resolving: frozenset[str],
        *,
        base: _ResolvedPath | None = None,
        explicit: bool = False,
    ) -> _ResolvedPath | None:
        result = base or _ResolvedPath(explicit=explicit)
        for argument in arguments:
            part = self.resolve(argument, resolving)
            if part is None:
                return None
            result = _combine(result, part)
        return _ResolvedPath(
            result.value,
            result.pytest_owned,
            result.invalid,
            explicit=result.explicit or explicit,
        )

    @staticmethod
    def _is_os_path_join(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "join"
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "path"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "os"
        )


def _is_write_mode(call: ast.Call) -> bool:
    mode_node: ast.AST | None = call.args[1] if len(call.args) > 1 else None
    for keyword in call.keywords:
        if keyword.arg == "mode":
            mode_node = keyword.value
    if not isinstance(mode_node, ast.Constant) or not isinstance(mode_node.value, str):
        return False
    mode = mode_node.value
    return "+" not in mode and any(flag in mode for flag in "wax")


def _candidate_calls(tree: ast.Module) -> list[tuple[ast.AST, int, bool]]:
    candidates: list[tuple[ast.AST, int, bool]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            candidates.extend(_call_candidates(node))
    return candidates


def _call_candidates(node: ast.Call) -> list[tuple[ast.AST, int, bool]]:
    name = _call_name(node.func)
    if name in _WRITE_METHODS or any(term in name for term in _OUTPUT_TERMS):
        return []
    if name == "open":
        return _open_candidates(node)
    if isinstance(node.func, ast.Attribute) and name in _PATH_READ_METHODS:
        return [(node.func.value, node.lineno, True)]
    candidates: list[tuple[ast.AST, int, bool]]
    if any(term in name for term in _INPUT_CALL_TERMS):
        candidates = [(argument, node.lineno, False) for argument in node.args]
    else:
        candidates = [
            (argument, node.lineno, False)
            for argument in node.args
            if isinstance(argument, (ast.Call, ast.BinOp))
        ]
    candidates.extend(_keyword_candidates(node, name))
    return candidates


def _open_candidates(node: ast.Call) -> list[tuple[ast.AST, int, bool]]:
    if _is_write_mode(node):
        return []
    candidates: list[tuple[ast.AST, int, bool]] = []
    if isinstance(node.func, ast.Attribute):
        candidates.append((node.func.value, node.lineno, True))
    elif node.args:
        candidates.append((node.args[0], node.lineno, True))
    candidates.extend(
        (keyword.value, node.lineno, True)
        for keyword in node.keywords
        if keyword.arg == "file"
    )
    return candidates


def _keyword_candidates(
    node: ast.Call, call_name: str
) -> list[tuple[ast.AST, int, bool]]:
    if call_name in {"model_construct", "model_validate"}:
        return []
    candidates: list[tuple[ast.AST, int, bool]] = []
    input_call = any(term in call_name for term in _INPUT_CALL_TERMS)
    for keyword in node.keywords:
        if keyword.arg is None:
            continue
        keyword_name = keyword.arg.lower()
        if any(term in keyword_name for term in _OUTPUT_TERMS):
            continue
        is_input = any(term in keyword_name for term in _INPUT_TERMS)
        is_path = "path" in keyword_name or "manifest" in keyword_name
        if is_input and (is_path or input_call):
            candidates.append((keyword.value, node.lineno, is_path))
    return candidates


def _looks_like_path(value: str) -> bool:
    if not value or any(character in value for character in "\n\r\x1b"):
        return False
    if " " in value:
        return False
    extension = posixpath.splitext(value)[1].lower()
    return (
        "/" in value
        or "\\" in value
        or value.startswith(".")
        or extension
        in {
            ".bytes",
            ".csv",
            ".json",
            ".jsonc",
            ".lock",
            ".md",
            ".nt",
            ".owl",
            ".py",
            ".toml",
            ".tsv",
            ".ttl",
            ".txt",
            ".xls",
            ".xlsx",
            ".xml",
            ".yaml",
            ".yml",
            ".zip",
        }
    )


def fixed_untracked_input_violations(
    source: str,
    *,
    filename: str,
    tracked_paths: frozenset[str],
) -> tuple[Violation, ...]:
    """Return statically resolvable checkout inputs absent from Git inventory."""
    tree = ast.parse(source, filename=filename)
    resolver = _PathResolver(tree, filename)
    violations: set[tuple[int, str, str]] = set()
    for expression, line, force_path in _candidate_calls(tree):
        resolved = resolver.resolve(expression)
        if resolved is None or resolved.pytest_owned or not resolved.value:
            continue
        if not (force_path or resolved.explicit or _looks_like_path(resolved.value)):
            continue
        if resolved.invalid:
            message = f"invalid fixed checkout input path: {resolved.value}"
        elif resolved.value not in tracked_paths:
            message = f"fixed untracked checkout input path: {resolved.value}"
        else:
            continue
        violations.add((line, resolved.value, message))
    return tuple(
        Violation(filename, line, message)
        for line, _path, message in sorted(violations)
    )


def _node_ranges(
    tree: ast.Module, unit_nodes: tuple[ast.AST, ...]
) -> list[tuple[int, int]]:
    ranges = [
        (
            getattr(node, "lineno", 0),
            getattr(node, "end_lineno", None) or getattr(node, "lineno", 0),
        )
        for node in unit_nodes
        if not isinstance(node, ast.Module)
    ]
    if ranges:
        ranges.extend(
            (node.lineno, node.end_lineno or node.lineno)
            for node in tree.body
            if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            )
        )
    return ranges


def unit_test_surface_violations(
    root: Path, *, runner: GitRunner = _run_git
) -> tuple[Violation, ...]:
    """Inspect tracked, explicitly unit-marked tests and their checkout inputs."""
    try:
        test_paths = _tracked_test_inventory(root, runner)
        tracked_paths = _all_tracked_inventory(root, runner)
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        return _inventory_error(error)

    violations: list[Violation] = []
    for relative in sorted(test_paths):
        try:
            source = (root / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            violations.append(_read_failure(relative, error))
            continue
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
        discovered = fixed_untracked_input_violations(
            source, filename=relative, tracked_paths=tracked_paths
        )
        if isinstance(unit_nodes[0], ast.Module):
            violations.extend(discovered)
            continue
        ranges = _node_ranges(tree, unit_nodes)
        violations.extend(
            violation
            for violation in discovered
            if any(start <= violation.line <= end for start, end in ranges)
        )
    return tuple(
        sorted(
            set(violations),
            key=lambda item: (item.path, item.line, item.message),
        )
    )
