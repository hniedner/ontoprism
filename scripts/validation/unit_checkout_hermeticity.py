"""Reject non-checkout inputs and mixed boundary markers in unit-test code.

The gate analyzes only statically resolvable paths. It inventories tracked tests plus
untracked ``test_*.py`` worktree files, rejects every untracked test module, and rejects
effective ``unit`` markers combined with any declared real-boundary marker.
"""

from __future__ import annotations

import ast
import ntpath
import os
import posixpath
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

_TEST_ROOTS = ("ontolib/tests", "backend/tests")
_PATH_READ_METHODS = frozenset({"open", "read_bytes", "read_text"})
_INPUT_TERMS = ("input", "source", "manifest")
_INPUT_CALL_TERMS = (*_INPUT_TERMS, "load", "parse", "read")
_OUTPUT_TERMS = ("output", "destination", "target", "cache", "directory")
_WRITE_METHODS = frozenset({"mkdir", "touch", "unlink", "write_bytes", "write_text"})
_BOUNDARY_MARKERS = frozenset(
    {"integration", "mutating_integration", "full_store", "full_build", "e2e"}
)
_SCRUBBED_GIT_ENVIRONMENT = frozenset(
    {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CEILING_DIRECTORIES"}
)
_GIT_TIMEOUT_SECONDS = 10.0
_DIAGNOSTIC_LIMIT = 240

ViolationKind = Literal[
    "input",
    "invalid_path",
    "untracked_test",
    "inventory_error",
    "read_error",
    "parse_error",
    "marker_error",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class Violation:
    kind: ViolationKind
    path: str
    line: int | None
    message: str


@dataclass(frozen=True, slots=True)
class UnitSurface:
    module_scope: bool
    nodes: tuple[ast.AST, ...]
    module_statements: tuple[ast.stmt, ...]


@dataclass(frozen=True, slots=True)
class _Candidate:
    expression: ast.AST
    line: int
    force_path: bool


class GitRunner(Protocol):
    def __call__(
        self,
        arguments: tuple[str, ...],
        *,
        cwd: Path,
        timeout: float,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[bytes]: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class _ResolvedPath:
    value: str = ""
    pytest_owned: bool = False
    invalid: bool = False
    explicit: bool = False
    at_floor: bool = False
    invalid_reason: str = ""


def _git_environment(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if environment is None else environment
    return {
        key: value
        for key, value in source.items()
        if key not in _SCRUBBED_GIT_ENVIRONMENT
    }


def _run_git(
    arguments: tuple[str, ...],
    *,
    cwd: Path,
    timeout: float,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(  # noqa: S603 - fixed inventory commands
        arguments,
        cwd=cwd,
        timeout=timeout,
        check=False,
        capture_output=True,
        env=_git_environment(env),
    )
    if result.returncode:
        raise subprocess.CalledProcessError(
            result.returncode,
            arguments,
            output=result.stdout,
            stderr=result.stderr,
        )
    return result


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
        if statement.value is not None:
            markers.update(
                marker
                for node in ast.walk(statement.value)
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
    """Reject effective unit markers combined with declared real-boundary markers."""
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
        boundary_markers = effective & _BOUNDARY_MARKERS
        if "unit" in effective and boundary_markers:
            conflicts = ", ".join(sorted(boundary_markers))
            violations.append(
                Violation(
                    kind="marker_error",
                    path=filename,
                    line=node.lineno,
                    message=f"{identity} is both unit and {conflicts}",
                )
            )

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inspect_function(node, module_markers, node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            class_markers = module_markers | _decorator_markers(node)
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    inspect_function(child, class_markers, f"{node.name}::{child.name}")
    return tuple(violations)


def _sanitize_diagnostic(value: str, root: Path) -> str:
    cleaned = value.replace(str(root), "<checkout>")
    cleaned = re.sub(
        r"(?i)\b(token|password|secret|authorization)=\S+",
        r"\1=<redacted>",
        cleaned,
    )
    cleaned = re.sub(r"(?<![<\w])/(?:[^\s;/]+/)*[^\s;]+", "<path>", cleaned)
    cleaned = re.sub(r"(?i)\b[A-Z]:[\\/][^\s;]+", "<path>", cleaned)
    cleaned = " ".join(cleaned.replace("\x1b", "").split())
    return cleaned[:_DIAGNOSTIC_LIMIT]


def _inventory_error(error: BaseException, root: Path) -> tuple[Violation, ...]:
    details = [type(error).__name__]
    returncode = getattr(error, "returncode", None)
    if isinstance(returncode, int):
        details.append(f"returncode={returncode}")
    stderr = getattr(error, "stderr", None)
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    if isinstance(stderr, str) and stderr:
        details.append(f"stderr={_sanitize_diagnostic(stderr, root)}")
    message = "unable to inventory tracked checkout: " + "; ".join(details)
    return (
        Violation(
            kind="inventory_error",
            path="<git inventory>",
            line=None,
            message=message[:320],
        ),
    )


def _git_payload(root: Path, runner: GitRunner, arguments: tuple[str, ...]) -> bytes:
    result = runner(
        arguments,
        cwd=root,
        timeout=_GIT_TIMEOUT_SECONDS,
        env=_git_environment(),
    )
    if result.returncode:
        raise subprocess.CalledProcessError(
            result.returncode,
            arguments,
            output=result.stdout,
            stderr=result.stderr,
        )
    return result.stdout


def _decode_inventory(payload: bytes) -> frozenset[str]:
    return frozenset(entry for entry in payload.decode("utf-8").split("\0") if entry)


def _test_inventory(
    root: Path, runner: GitRunner
) -> tuple[frozenset[str], frozenset[str]]:
    tracked = _decode_inventory(
        _git_payload(root, runner, ("git", "ls-files", "-z", "--", *_TEST_ROOTS))
    )
    untracked = _decode_inventory(
        _git_payload(
            root,
            runner,
            (
                "git",
                "ls-files",
                "-z",
                "--others",
                "--exclude-standard",
                "--",
                *_TEST_ROOTS,
            ),
        )
    )

    def tests(paths: frozenset[str]) -> frozenset[str]:
        return frozenset(
            path
            for path in paths
            if Path(path).name.startswith("test_") and path.endswith(".py")
        )

    return tests(tracked), tests(untracked)


def _all_tracked_inventory(root: Path, runner: GitRunner) -> frozenset[str]:
    return _decode_inventory(_git_payload(root, runner, ("git", "ls-files", "-z")))


def _read_failure(relative: str, error: BaseException, root: Path) -> Violation:
    detail = _sanitize_diagnostic(str(error), root)
    suffix = f"; error={detail}" if detail else ""
    return Violation(
        kind="read_error",
        path=relative,
        line=None,
        message=(f"unable to read test candidate: {type(error).__name__}{suffix}")[
            :320
        ],
    )


def mixed_test_marker_surface_violations(
    root: Path, *, runner: GitRunner = _run_git
) -> tuple[Violation, ...]:
    """Inspect tracked repository test modules without importing them."""
    try:
        test_paths, _untracked = _test_inventory(root, runner)
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        return _inventory_error(error, root)

    violations: list[Violation] = []
    for relative in sorted(test_paths):
        try:
            source = (root / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            violations.append(_read_failure(relative, error, root))
            continue
        try:
            violations.extend(mixed_test_marker_violations(source, filename=relative))
        except SyntaxError as error:
            violations.append(
                Violation(
                    kind="parse_error",
                    path=relative,
                    line=error.lineno,
                    message=f"unable to parse test marker candidate: {error.msg}",
                )
            )
    return tuple(violations)


def _unit_nodes(tree: ast.Module) -> UnitSurface:
    module_statements = tuple(
        node
        for node in tree.body
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    )
    if "unit" in _module_markers(tree):
        return UnitSurface(True, tuple(tree.body), module_statements)

    selected: list[ast.AST] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if "unit" in _decorator_markers(node):
                selected.append(node)
        elif isinstance(node, ast.ClassDef):
            if "unit" in _decorator_markers(node):
                selected.append(node)
            else:
                selected.extend(
                    child
                    for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and "unit" in _decorator_markers(child)
                )
    return UnitSurface(False, tuple(selected), module_statements)


def _path_constructor_aliases(tree: ast.Module) -> set[str]:
    aliases = {"Path"}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "pathlib":
            for name in node.names:
                if name.name == "Path":
                    aliases.add(name.asname or name.name)
    return aliases


@dataclass(frozen=True, slots=True)
class _Scope:
    parent: _Scope | None
    assignments: dict[str, ast.AST | None]


class _ScopeBuilder(ast.NodeVisitor):
    def __init__(self, tree: ast.Module) -> None:
        self.module = _Scope(None, {})
        self.current = self.module
        self.by_node: dict[ast.AST, _Scope] = {}
        self.visit(tree)

    def visit(self, node: ast.AST) -> None:
        self.by_node[node] = self.current
        super().visit(node)

    def _record(self, target: ast.expr, value: ast.AST) -> None:
        if not isinstance(target, ast.Name):
            return
        if target.id in self.current.assignments:
            self.current.assignments[target.id] = None
        else:
            self.current.assignments[target.id] = value

    def visit_Assign(self, node: ast.Assign) -> None:
        self.by_node[node] = self.current
        for target in node.targets:
            self._record(target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.by_node[node] = self.current
        if node.value is not None:
            self._record(node.target, node.value)
        self.generic_visit(node)

    def _visit_nested_scope(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    ) -> None:
        self.by_node[node] = self.current
        parent = self.current
        nested = _Scope(parent, {})
        self.current = nested
        for statement in node.body:
            self.visit(statement)
        self.current = parent

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_nested_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_nested_scope(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_nested_scope(node)


def _literal_path(value: str, *, explicit: bool = False) -> _ResolvedPath:
    if "://" in value:
        return _ResolvedPath()
    slash_normalized = value.replace("\\", "/")
    normalized = posixpath.normpath(slash_normalized).removeprefix("./")
    if posixpath.isabs(slash_normalized) or ntpath.isabs(value):
        return _ResolvedPath(value=normalized, invalid=True, explicit=explicit)
    if normalized == ".." or normalized.startswith("../"):
        return _ResolvedPath(value=normalized, invalid=True, explicit=explicit)
    if normalized in {"", "."}:
        return _ResolvedPath()
    return _ResolvedPath(value=normalized, explicit=explicit)


def _combine(base: _ResolvedPath, child: _ResolvedPath) -> _ResolvedPath:
    if base.invalid or child.invalid:
        return _ResolvedPath(
            value=child.value or base.value,
            invalid=True,
            explicit=base.explicit or child.explicit,
            invalid_reason=child.invalid_reason or base.invalid_reason,
        )
    if base.pytest_owned:
        return _ResolvedPath(pytest_owned=True)
    if child.pytest_owned:
        return child
    if not base.value:
        return _ResolvedPath(
            value=child.value,
            pytest_owned=child.pytest_owned,
            invalid=child.invalid,
            explicit=base.explicit or child.explicit,
            at_floor=False,
        )
    if not child.value:
        return base
    return _literal_path(
        f"{base.value}/{child.value}", explicit=base.explicit or child.explicit
    )


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id.lower()
    if isinstance(node, ast.Attribute):
        return node.attr.lower()
    return ""


class _PathResolver:
    def __init__(self, tree: ast.Module, filename: str) -> None:
        scopes = _ScopeBuilder(tree)
        self._module_scope = scopes.module
        self._scopes = scopes.by_node
        self._constructors = _path_constructor_aliases(tree)
        self._filename = filename

    def resolve(  # noqa: PLR0911 - one branch per supported AST form
        self,
        node: ast.AST,
        resolving: frozenset[tuple[int, str]] = frozenset(),
        scope: _Scope | None = None,
    ) -> _ResolvedPath | None:
        current_scope = scope or self._scopes.get(node, self._module_scope)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return _literal_path(node.value)
        if isinstance(node, ast.Name):
            return self._resolve_name(node, resolving, current_scope)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left = self.resolve(node.left, resolving, current_scope)
            right = self.resolve(node.right, resolving, current_scope)
            if left is None or right is None:
                return None
            combined = _combine(left, right)
            return _ResolvedPath(
                value=combined.value,
                pytest_owned=combined.pytest_owned,
                invalid=combined.invalid,
                explicit=True,
                at_floor=combined.at_floor,
                invalid_reason=combined.invalid_reason,
            )
        if isinstance(node, ast.Attribute):
            return self._resolve_attribute(node, resolving, current_scope)
        if isinstance(node, ast.Subscript):
            return self._resolve_subscript(node, resolving, current_scope)
        if isinstance(node, ast.Call):
            return self._resolve_call(node, resolving, current_scope)
        return None

    def _resolve_name(
        self, node: ast.Name, resolving: frozenset[tuple[int, str]], scope: _Scope
    ) -> _ResolvedPath | None:
        if node.id in {"tmp_path", "tmpdir"}:
            return _ResolvedPath(pytest_owned=True)
        if node.id == "__file__":
            return _literal_path(self._filename)
        candidate_scope: _Scope | None = scope
        while candidate_scope is not None:
            if node.id in candidate_scope.assignments:
                value = candidate_scope.assignments[node.id]
                identity = (id(candidate_scope), node.id)
                if value is None or identity in resolving:
                    return None
                return self.resolve(value, resolving | {identity}, candidate_scope)
            candidate_scope = candidate_scope.parent
        return None

    @staticmethod
    def _parent(base: _ResolvedPath) -> _ResolvedPath:
        if base.pytest_owned or base.invalid:
            return base
        if base.at_floor or not base.value:
            return _ResolvedPath(
                value=base.value or ".",
                invalid=True,
                explicit=base.explicit,
                invalid_reason="parent traversal above checkout root",
            )
        parent = posixpath.dirname(base.value)
        return _ResolvedPath(
            value=parent,
            explicit=base.explicit,
            at_floor=not parent,
        )

    def _resolve_attribute(
        self,
        node: ast.Attribute,
        resolving: frozenset[tuple[int, str]],
        scope: _Scope,
    ) -> _ResolvedPath | None:
        base = self.resolve(node.value, resolving, scope)
        if base is None or node.attr != "parent":
            return base
        return self._parent(base)

    def _resolve_subscript(
        self,
        node: ast.Subscript,
        resolving: frozenset[tuple[int, str]],
        scope: _Scope,
    ) -> _ResolvedPath | None:
        if not isinstance(node.value, ast.Attribute) or node.value.attr != "parents":
            return None
        base = self.resolve(node.value.value, resolving, scope)
        if base is None or base.pytest_owned or base.invalid:
            return base
        if not isinstance(node.slice, ast.Constant) or not isinstance(
            node.slice.value, int
        ):
            return None
        for _unused in range(node.slice.value + 1):
            base = self._parent(base)
            if base.invalid:
                break
        return base

    def _resolve_call(
        self,
        node: ast.Call,
        resolving: frozenset[tuple[int, str]],
        scope: _Scope,
    ) -> _ResolvedPath | None:
        if isinstance(node.func, ast.Name) and node.func.id in self._constructors:
            return self._join_arguments(node.args, resolving, scope, explicit=True)
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "Path"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "pathlib"
        ):
            return self._join_arguments(node.args, resolving, scope, explicit=True)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "joinpath":
            base = self.resolve(node.func.value, resolving, scope)
            return self._join_arguments(
                node.args, resolving, scope, base=base, explicit=True
            )
        if self._is_os_path_join(node.func):
            return self._join_arguments(node.args, resolving, scope, explicit=True)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "resolve":
            return self.resolve(node.func.value, resolving, scope)
        return None

    def _join_arguments(
        self,
        arguments: list[ast.expr],
        resolving: frozenset[tuple[int, str]],
        scope: _Scope,
        *,
        base: _ResolvedPath | None = None,
        explicit: bool = False,
    ) -> _ResolvedPath | None:
        result = base or _ResolvedPath(explicit=explicit)
        for argument in arguments:
            part = self.resolve(argument, resolving, scope)
            if part is None:
                return None
            result = _combine(result, part)
        return _ResolvedPath(
            value=result.value,
            pytest_owned=result.pytest_owned,
            invalid=result.invalid,
            explicit=result.explicit or explicit,
            at_floor=result.at_floor,
            invalid_reason=result.invalid_reason,
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
    argument_index = 0 if isinstance(call.func, ast.Attribute) else 1
    mode_node: ast.AST | None = (
        call.args[argument_index] if len(call.args) > argument_index else None
    )
    for keyword in call.keywords:
        if keyword.arg == "mode":
            mode_node = keyword.value
    if not isinstance(mode_node, ast.Constant) or not isinstance(mode_node.value, str):
        return False
    mode = mode_node.value
    return "+" not in mode and any(flag in mode for flag in "wax")


def _candidate_calls(tree: ast.Module) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            candidates.extend(_call_candidates(node))
    return candidates


def _call_candidates(node: ast.Call) -> list[_Candidate]:
    name = _call_name(node.func)
    if name in _WRITE_METHODS:
        return []
    if name == "open":
        return _open_candidates(node)
    if isinstance(node.func, ast.Attribute) and name in _PATH_READ_METHODS:
        return [_Candidate(node.func.value, node.lineno, True)]
    if any(term in name for term in _INPUT_CALL_TERMS):
        candidates = [
            _Candidate(argument, node.lineno, False) for argument in node.args
        ]
    else:
        candidates = [
            _Candidate(argument, node.lineno, False)
            for argument in node.args
            if isinstance(argument, (ast.Call, ast.BinOp))
        ]
    candidates.extend(_keyword_candidates(node, name))
    return candidates


def _open_candidates(node: ast.Call) -> list[_Candidate]:
    if _is_write_mode(node):
        return []
    candidates: list[_Candidate] = []
    if isinstance(node.func, ast.Attribute):
        candidates.append(_Candidate(node.func.value, node.lineno, True))
    elif node.args:
        candidates.append(_Candidate(node.args[0], node.lineno, True))
    candidates.extend(
        _Candidate(keyword.value, node.lineno, True)
        for keyword in node.keywords
        if keyword.arg == "file"
    )
    return candidates


def _keyword_candidates(node: ast.Call, call_name: str) -> list[_Candidate]:
    if call_name in {"model_construct", "model_validate"}:
        return []
    candidates: list[_Candidate] = []
    input_call = any(term in call_name for term in _INPUT_CALL_TERMS)
    for keyword in node.keywords:
        if keyword.arg is None:
            continue
        keyword_name = keyword.arg.lower()
        is_input = any(term in keyword_name for term in _INPUT_TERMS)
        is_output = any(term in keyword_name for term in _OUTPUT_TERMS)
        if is_output and not is_input:
            continue
        is_path = "path" in keyword_name or "manifest" in keyword_name
        if is_input and (is_path or input_call):
            candidates.append(_Candidate(keyword.value, node.lineno, is_path))
    return candidates


def _looks_like_path(value: str) -> bool:
    if not value or any(character in value for character in "\n\r\x1b<>"):
        return False
    extension = posixpath.splitext(value)[1].lower()
    known_extension = extension in {
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
    return (
        "/" in value
        or "\\" in value
        or (value.startswith(".") and " " not in value)
        or known_extension
    )


def _tracked_directories(tracked_paths: frozenset[str]) -> frozenset[str]:
    directories: set[str] = set()
    for tracked in tracked_paths:
        parent = posixpath.dirname(tracked)
        while parent:
            directories.add(parent)
            parent = posixpath.dirname(parent)
    return frozenset(directories)


def _fixed_untracked_input_violations(
    tree: ast.Module,
    *,
    filename: str,
    tracked_paths: frozenset[str],
) -> tuple[Violation, ...]:
    resolver = _PathResolver(tree, filename)
    tracked_directories = _tracked_directories(tracked_paths)
    violations: set[tuple[int, ViolationKind, str]] = set()
    for candidate in _candidate_calls(tree):
        resolved = resolver.resolve(candidate.expression)
        if resolved is None or resolved.pytest_owned or not resolved.value:
            continue
        if not (
            candidate.force_path
            or resolved.explicit
            or _looks_like_path(resolved.value)
        ):
            continue
        if resolved.invalid:
            kind: ViolationKind = "invalid_path"
            detail = resolved.invalid_reason or resolved.value
            message = f"invalid statically resolvable checkout input path: {detail}"
        elif (
            resolved.value not in tracked_paths
            and resolved.value not in tracked_directories
        ):
            kind = "input"
            message = (
                f"statically resolvable untracked checkout input path: {resolved.value}"
            )
        else:
            continue
        violations.add((candidate.line, kind, message))
    return tuple(
        Violation(kind=kind, path=filename, line=line, message=message)
        for line, kind, message in sorted(violations)
    )


def fixed_untracked_input_violations(
    source: str,
    *,
    filename: str,
    tracked_paths: frozenset[str],
) -> tuple[Violation, ...]:
    """Return statically resolvable checkout inputs absent from Git inventory."""
    tree = ast.parse(source, filename=filename)
    return _fixed_untracked_input_violations(
        tree, filename=filename, tracked_paths=tracked_paths
    )


def _node_ranges(surface: UnitSurface) -> list[tuple[int, int]]:
    return [
        (
            getattr(node, "lineno", 0),
            getattr(node, "end_lineno", None) or getattr(node, "lineno", 0),
        )
        for node in (*surface.nodes, *surface.module_statements)
        if hasattr(node, "lineno")
    ]


def unit_test_surface_violations(
    root: Path, *, runner: GitRunner = _run_git
) -> tuple[Violation, ...]:
    """Inspect unit-marked module statements and test nodes for checkout inputs."""
    try:
        tracked_tests, untracked_tests = _test_inventory(root, runner)
        tracked_paths = _all_tracked_inventory(root, runner)
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        return _inventory_error(error, root)

    violations: list[Violation] = [
        Violation(
            kind="untracked_test",
            path=relative,
            line=None,
            message="untracked test module is outside the checkout inventory",
        )
        for relative in sorted(untracked_tests)
    ]
    for relative in sorted(tracked_tests):
        try:
            source = (root / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            violations.append(_read_failure(relative, error, root))
            continue
        try:
            tree = ast.parse(source, filename=relative)
        except SyntaxError as error:
            violations.append(
                Violation(
                    kind="parse_error",
                    path=relative,
                    line=error.lineno,
                    message=f"unable to parse unit-test candidate: {error.msg}",
                )
            )
            continue
        surface = _unit_nodes(tree)
        if not surface.module_scope and not surface.nodes:
            continue
        discovered = _fixed_untracked_input_violations(
            tree, filename=relative, tracked_paths=tracked_paths
        )
        if surface.module_scope:
            violations.extend(discovered)
            continue
        ranges = _node_ranges(surface)
        violations.extend(
            violation
            for violation in discovered
            if violation.line is not None
            and any(start <= violation.line <= end for start, end in ranges)
        )
    return tuple(
        sorted(
            set(violations),
            key=lambda item: (item.path, item.line or 0, item.kind, item.message),
        )
    )
