#!/usr/bin/env python3
"""Run a narrowly scoped repository pytest node for an OpenCode agent."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

SHELL_METACHARACTERS = frozenset("&;|><`$\n\r")
SAFE_FLAGS = frozenset({"-q", "-v", "-x"})
SAFE_K_EXPRESSION = re.compile(r"[A-Za-z0-9_ .()\-]+")
SAFE_VITEST_NAME = re.compile(r"[A-Za-z0-9_ .():,\-/]+")
MAXFAIL = re.compile(r"--maxfail=([1-9][0-9]*)")
OWNED_TEST_ROOTS = (PurePosixPath("backend/tests"), PurePosixPath("ontolib/tests"))
FRONTEND_TEST_ROOTS = (PurePosixPath("frontend/src"), PurePosixPath("frontend/tests"))
FRONTEND_TEST_NAME = re.compile(r".+\.(?:test|spec)\.(?:js|jsx|ts|tsx)$")
FRONTEND_ARGUMENTS_WITH_NAME = 3
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
TEST_IDENTIFIER = re.compile(r"test_[A-Za-z0-9_]+")
NONINTEGRATION_MARKERS = (
    "not integration and not mutating_integration and not full_store"
)


class AgentTestInputError(ValueError):
    """The requested focused test cannot be safely executed."""


@dataclass(frozen=True)
class AgentTestInvocation:
    """A validated fixed-cwd test invocation."""

    arguments: tuple[str, ...]
    cwd: Path
    mode: str


@dataclass(frozen=True)
class SafeIntegrationEntry:
    """One validated safe-integration registry entry."""

    path: PurePosixPath
    tests: frozenset[str] | None


class CommandResult(Protocol):
    returncode: int


class CommandRunner(Protocol):
    def __call__(
        self, arguments: tuple[str, ...], **kwargs: object
    ) -> CommandResult: ...


def _reject_shell_syntax(argument: str) -> None:
    if not argument or any(character in argument for character in SHELL_METACHARACTERS):
        raise AgentTestInputError("test arguments must not contain shell syntax")


def _resolve_owned_node(
    node: str, root: Path, owned_roots: tuple[PurePosixPath, ...]
) -> tuple[PurePosixPath, Path]:
    path_text = node.split("::", 1)[0]
    candidate = PurePosixPath(path_text)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise AgentTestInputError("test path must stay within repository test roots")
    owned_root = next(
        (owned for owned in owned_roots if candidate.is_relative_to(owned)),
        None,
    )
    if owned_root is None:
        raise AgentTestInputError("test path must stay within repository test roots")
    resolved_root = (root / owned_root).resolve()
    resolved_path = (root / candidate).resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise AgentTestInputError(
            "test path must stay within repository test roots"
        ) from exc
    if not resolved_path.exists():
        raise AgentTestInputError("test path does not exist")
    return candidate, resolved_path


def _validate_node(node: str, root: Path) -> None:
    _resolve_owned_node(node, root, OWNED_TEST_ROOTS)


def _build_frontend_invocation(arguments: list[str], root: Path) -> AgentTestInvocation:
    if not arguments:
        raise AgentTestInputError("frontend test path is required")
    node = arguments[0]
    _reject_shell_syntax(node)
    candidate, _ = _resolve_owned_node(node, root, FRONTEND_TEST_ROOTS)
    if FRONTEND_TEST_NAME.fullmatch(candidate.name) is None:
        raise AgentTestInputError("frontend test filename is unsupported")
    extra: tuple[str, ...] = ()
    if len(arguments) == FRONTEND_ARGUMENTS_WITH_NAME and arguments[1] == "-t":
        name = arguments[2]
        _reject_shell_syntax(name)
        if name.startswith("-") or SAFE_VITEST_NAME.fullmatch(name) is None:
            raise AgentTestInputError("frontend test name is unsupported")
        extra = ("-t", name)
    elif len(arguments) != 1:
        raise AgentTestInputError("unsupported frontend test option")
    frontend = (root / "frontend").resolve()
    node_modules = (frontend / "node_modules").resolve()
    executable = frontend / "node_modules/.bin/vitest"
    try:
        executable.resolve(strict=True).relative_to(node_modules)
    except (OSError, ValueError) as exc:
        raise AgentTestInputError(
            "repository Vitest executable is unavailable"
        ) from exc
    if not os.access(executable, os.X_OK):
        raise AgentTestInputError("repository Vitest executable is unavailable")
    relative_test = (root / candidate).resolve().relative_to(frontend).as_posix()
    return AgentTestInvocation(
        (str(executable.resolve()), "run", relative_test, *extra), frontend, "frontend"
    )


def _invalid_registry() -> AgentTestInputError:
    return AgentTestInputError("safe integration registry is invalid")


def _parse_registry_entry(entry: object, root: Path) -> SafeIntegrationEntry:
    if not isinstance(entry, dict) or not set(entry) <= {
        "path",
        "fixtures",
        "tests",
    }:
        raise _invalid_registry()
    if not {"path", "fixtures"} <= set(entry):
        raise _invalid_registry()
    path = entry["path"]
    fixtures = entry.get("fixtures")
    if (
        not isinstance(path, str)
        or "::" in path
        or not isinstance(fixtures, list)
        or not fixtures
        or not all(
            isinstance(fixture, str) and IDENTIFIER.fullmatch(fixture)
            for fixture in fixtures
        )
    ):
        raise _invalid_registry()
    try:
        candidate, resolved = _resolve_owned_node(path, root, OWNED_TEST_ROOTS)
    except AgentTestInputError as exc:
        raise _invalid_registry() from exc
    if not resolved.is_file():
        raise _invalid_registry()
    tests = entry.get("tests")
    if tests is not None and (
        not isinstance(tests, list)
        or not tests
        or not all(
            isinstance(test, str) and TEST_IDENTIFIER.fullmatch(test) for test in tests
        )
    ):
        raise _invalid_registry()
    return SafeIntegrationEntry(
        candidate,
        frozenset(tests) if isinstance(tests, list) else None,
    )


def _load_safe_integration_registry(root: Path) -> tuple[SafeIntegrationEntry, ...]:
    manifest = root / "test_support/integration_mutators.toml"
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise _invalid_registry() from exc
    if set(data) != {"mutator"} or not isinstance(data["mutator"], list):
        raise _invalid_registry()
    entries = [_parse_registry_entry(entry, root) for entry in data["mutator"]]
    if not entries:
        raise _invalid_registry()
    return tuple(entries)


def _registered_safe_integration(node: str, root: Path) -> bool:
    if "::" not in node:
        return False
    candidate, _ = _resolve_owned_node(node, root, OWNED_TEST_ROOTS)
    selector = node.split("::", 2)[1]
    entries = _load_safe_integration_registry(root)
    return any(
        entry.path == candidate and (entry.tests is None or selector in entry.tests)
        for entry in entries
    )


def _build_safe_integration_invocation(
    arguments: list[str], root: Path
) -> AgentTestInvocation:
    if not arguments:
        raise AgentTestInputError("safe integration node is required")
    node = arguments[0]
    _reject_shell_syntax(node)
    if not _registered_safe_integration(node, root):
        raise AgentTestInputError("safe integration node is not registered")
    flags = arguments[1:]
    if any(flag not in {"-q", "-v"} for flag in flags):
        raise AgentTestInputError("unsupported safe integration option")
    return AgentTestInvocation(
        (
            "pdm",
            "run",
            "python",
            "scripts/run_safe_integration.py",
            node,
            *flags,
        ),
        root,
        "safe-integration",
    )


def build_pytest_invocation(arguments: list[str], root: Path) -> AgentTestInvocation:
    """Validate agent arguments and return a permitted subprocess shape."""
    resolved_root = root.resolve()
    if arguments[:1] == ["--frontend"]:
        return _build_frontend_invocation(arguments[1:], resolved_root)
    if arguments[:1] == ["--safe-integration"]:
        return _build_safe_integration_invocation(arguments[1:], resolved_root)
    validated: list[str] = []
    node_count = 0
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        _reject_shell_syntax(argument)
        if argument in SAFE_FLAGS or MAXFAIL.fullmatch(argument):
            validated.append(argument)
        elif argument == "-k":
            if index + 1 >= len(arguments):
                raise AgentTestInputError("missing -k expression")
            expression = arguments[index + 1]
            _reject_shell_syntax(expression)
            if SAFE_K_EXPRESSION.fullmatch(expression) is None:
                raise AgentTestInputError("unsupported -k expression")
            validated.extend((argument, expression))
            index += 1
        elif argument.startswith("-"):
            raise AgentTestInputError("unsupported pytest option")
        else:
            _validate_node(argument, resolved_root)
            validated.append(argument)
            node_count += 1
        index += 1
    if node_count == 0:
        raise AgentTestInputError("at least one test node is required")
    return AgentTestInvocation(
        ("pytest", *validated, "-m", NONINTEGRATION_MARKERS),
        resolved_root,
        "pytest",
    )


def parse_vitest_execution_count(payload: str) -> int:
    """Return passed plus failed tests from the fixed Vitest JSON reporter."""
    try:
        report = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AgentTestInputError("frontend test report is invalid") from exc
    if not isinstance(report, dict):
        raise AgentTestInputError("frontend test report is invalid")
    passed = report.get("numPassedTests")
    failed = report.get("numFailedTests")
    if (
        not isinstance(passed, int)
        or isinstance(passed, bool)
        or passed < 0
        or not isinstance(failed, int)
        or isinstance(failed, bool)
        or failed < 0
    ):
        raise AgentTestInputError("frontend test report is invalid")
    return passed + failed


def _controlled_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in (
        "NODE_OPTIONS",
        "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS",
        "PYTHONPATH",
    ):
        environment.pop(name, None)
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def run_agent_test(
    arguments: list[str],
    root: Path,
    *,
    runner: CommandRunner = subprocess.run,
) -> int:
    """Execute a validated pytest command directly, never through a shell."""
    invocation = build_pytest_invocation(arguments, root)
    with tempfile.TemporaryDirectory(prefix="ontoprism-agent-test-") as temporary:
        report_path = Path(temporary) / "vitest-report.json"
        command = invocation.arguments
        if invocation.mode == "frontend":
            command = (
                *command,
                "--reporter=json",
                f"--outputFile={report_path}",
            )
        try:
            result = runner(
                command,
                cwd=invocation.cwd,
                env=_controlled_environment(),
                shell=False,
                check=False,
            )
        except (FileNotFoundError, PermissionError):
            print("required test executable is unavailable", file=sys.stderr)
            return 3
        except OSError:
            print("test process could not start", file=sys.stderr)
            return 3
        if result.returncode != 0 or invocation.mode != "frontend":
            return result.returncode
        try:
            payload = report_path.read_text(encoding="utf-8")
            executed = parse_vitest_execution_count(payload)
        except (OSError, UnicodeDecodeError, AgentTestInputError):
            print("frontend test report is invalid", file=sys.stderr)
            return 3
        if executed == 0:
            print("no frontend test matched the request", file=sys.stderr)
            return 4
        return 0


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    try:
        return run_agent_test(sys.argv[1:], root)
    except AgentTestInputError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
