#!/usr/bin/env python3
"""Run narrowly scoped repository tests for an OpenCode agent."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import tomllib
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

SHELL_METACHARACTERS = frozenset("&;|><`$\n\r")
SAFE_FLAGS = frozenset({"-q", "-v", "-x"})
SAFE_K_EXPRESSION = re.compile(r"[A-Za-z0-9_ .()\-]+")
SAFE_VITEST_NAME = re.compile(r"[A-Za-z0-9_ .():,\-/]+")
MAX_VITEST_NAME_LENGTH = 200
MAXFAIL = re.compile(r"--maxfail=([1-9][0-9]*)")
OWNED_TEST_ROOTS = (PurePosixPath("backend/tests"), PurePosixPath("ontolib/tests"))
FRONTEND_TEST_ROOTS = (PurePosixPath("frontend/src"),)
FRONTEND_TEST_NAME = re.compile(r".+\.(?:test|spec)\.(?:js|jsx|ts|tsx)$")
MAX_FRONTEND_FAILURE_NAMES = 10
MAX_FRONTEND_FAILURE_NAME_LENGTH = 160
MAX_FRONTEND_DIAGNOSTIC_BYTES = 16_384
MAX_FRONTEND_DIAGNOSTIC_CHARS = 1_999
MAX_FRONTEND_DIAGNOSTIC_LINES = 12
FRONTEND_TIMEOUT_SECONDS = 300
PROCESS_DRAIN_TIMEOUT_SECONDS = 5
INVENTORY_TIMEOUT_SECONDS = 10
ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|authorization|auth|token|password|secret)\b"
    r"\s*[:=]\s*(?:bearer\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s]+)"
)
BEARER_CREDENTIAL = re.compile(r"(?i)\bbearer\s+[^\s]+")
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
TEST_IDENTIFIER = re.compile(r"test_[A-Za-z0-9_]+")
NONINTEGRATION_MARKERS = (
    "not integration and not mutating_integration and not full_store"
)
FULL_STORE_MARKERS = "integration and full_store"
type AgentTestMode = Literal["pytest", "frontend", "safe-integration", "full-store"]


class AgentTestInputError(ValueError):
    """The requested focused test cannot be safely executed."""


@dataclass(frozen=True)
class FrontendTestPath:
    """Validated repository and Vitest-relative names for one frontend test."""

    repo_relative: str
    vitest_path: str


@dataclass(frozen=True)
class AgentTestInvocation:
    """A validated fixed-cwd test invocation with mode-consistent frontend paths."""

    arguments: tuple[str, ...]
    cwd: Path
    mode: AgentTestMode
    frontend_tests: tuple[FrontendTestPath, ...] = ()

    def __post_init__(self) -> None:
        if bool(self.frontend_tests) != (self.mode == "frontend"):
            raise ValueError("frontend test paths must match frontend mode")


@dataclass(frozen=True)
class SafeIntegrationEntry:
    """One validated safe-integration registry entry."""

    path: PurePosixPath
    tests: frozenset[str] | None


class CommandResult(Protocol):
    @property
    def returncode(self) -> int: ...

    @property
    def stdout(self) -> bytes | None: ...

    @property
    def stderr(self) -> bytes | None: ...


class CommandRunner(Protocol):
    def __call__(
        self,
        arguments: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        shell: Literal[False],
        stdout: int | None,
        stderr: int | None,
        timeout: int | None,
        start_new_session: bool,
    ) -> CommandResult: ...


def _subprocess_runner(
    arguments: tuple[str, ...],
    *,
    cwd: Path,
    env: dict[str, str],
    shell: Literal[False],
    stdout: int | None,
    stderr: int | None,
    timeout: int | None,
    start_new_session: bool,
) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.Popen(  # noqa: S603 -- validated fixed argv, never shell input
        arguments,
        cwd=cwd,
        env=env,
        shell=shell,
        stdout=stdout,
        stderr=stderr,
        start_new_session=start_new_session,
    )
    try:
        process_stdout, process_stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        try:
            drained_stdout, drained_stderr = process.communicate(
                timeout=PROCESS_DRAIN_TIMEOUT_SECONDS
            )
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                process.kill()
            drained_stdout, drained_stderr = process.communicate()
        raise subprocess.TimeoutExpired(
            arguments,
            timeout or 0,
            output=drained_stdout,
            stderr=drained_stderr,
        ) from exc
    return subprocess.CompletedProcess(
        arguments, process.returncode, process_stdout, process_stderr
    )


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
    candidate, resolved = _resolve_owned_node(node, root, OWNED_TEST_ROOTS)
    if candidate.suffix != ".py" or not resolved.is_file():
        raise AgentTestInputError("test node must name a Python test path")


def _tracked_frontend_paths(root: Path) -> frozenset[str]:
    git = shutil.which("git")
    if git is None:
        raise AgentTestInputError("tracked frontend test inventory is unavailable")
    try:
        result = subprocess.run(  # noqa: S603 -- resolved executable and fixed argv
            (git, "ls-files", "-z", "--", "frontend/src"),
            cwd=root,
            shell=False,
            check=False,
            capture_output=True,
            timeout=INVENTORY_TIMEOUT_SECONDS,
        )
        output = result.stdout.decode("utf-8", errors="strict")
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as exc:
        raise AgentTestInputError(
            "tracked frontend test inventory is unavailable"
        ) from exc
    if result.returncode != 0:
        raise AgentTestInputError("tracked frontend test inventory is unavailable")
    return frozenset(path for path in output.split("\0") if path)


def _validate_frontend_path(
    node: str, root: Path, tracked_paths: frozenset[str]
) -> FrontendTestPath:
    _reject_shell_syntax(node)
    candidate, resolved = _resolve_owned_node(node, root, FRONTEND_TEST_ROOTS)
    if candidate.as_posix() not in tracked_paths:
        raise AgentTestInputError("frontend test path must be tracked")
    if FRONTEND_TEST_NAME.fullmatch(candidate.name) is None:
        raise AgentTestInputError("frontend test filename is unsupported")
    current = root
    for part in candidate.parts:
        current /= part
        if current.is_symlink():
            raise AgentTestInputError("frontend test path must not use a symlink")
    if not resolved.is_file():
        raise AgentTestInputError("frontend test path must name a file")
    frontend = (root / "frontend").resolve()
    return FrontendTestPath(
        repo_relative=candidate.as_posix(),
        vitest_path=resolved.relative_to(frontend).as_posix(),
    )


def _build_frontend_invocation(
    arguments: list[str],
    root: Path,
    *,
    tracked_paths: frozenset[str] | None = None,
) -> AgentTestInvocation:
    if not arguments:
        raise AgentTestInputError("frontend test path is required")
    filter_arguments: tuple[str, ...] = ()
    path_arguments = arguments
    if "-t" in arguments:
        filter_index = arguments.index("-t")
        if filter_index == 0 or len(arguments) != filter_index + 2:
            raise AgentTestInputError("unsupported frontend test option")
        name = arguments[-1]
        _reject_shell_syntax(name)
        if (
            len(name) > MAX_VITEST_NAME_LENGTH
            or name.startswith("-")
            or SAFE_VITEST_NAME.fullmatch(name) is None
        ):
            raise AgentTestInputError("frontend test name is unsupported")
        filter_arguments = ("-t", name)
        path_arguments = arguments[:filter_index]
        if len(path_arguments) != 1:
            raise AgentTestInputError(
                "frontend name filter requires exactly one test path"
            )
    elif any(argument.startswith("-") for argument in arguments):
        raise AgentTestInputError("unsupported frontend test option")
    inventory = (
        tracked_paths if tracked_paths is not None else _tracked_frontend_paths(root)
    )
    validated = tuple(
        _validate_frontend_path(node, root, inventory) for node in path_arguments
    )
    requested = tuple(path.repo_relative for path in validated)
    if len(set(requested)) != len(requested):
        raise AgentTestInputError("frontend test paths must be unique")
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
    return AgentTestInvocation(
        (
            str(executable.resolve()),
            "run",
            *(path.vitest_path for path in validated),
            *filter_arguments,
        ),
        frontend,
        "frontend",
        validated,
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


def _validated_pytest_arguments(arguments: list[str], root: Path) -> list[str]:
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
            _validate_node(argument, root)
            validated.append(argument)
            node_count += 1
        index += 1
    if node_count == 0:
        raise AgentTestInputError("at least one test node is required")
    return validated


def _build_full_store_invocation(
    arguments: list[str], root: Path
) -> AgentTestInvocation:
    validated = _validated_pytest_arguments(arguments, root)
    return AgentTestInvocation(
        (
            "pytest",
            "--require-full-store",
            *validated,
            "-m",
            FULL_STORE_MARKERS,
        ),
        root,
        "full-store",
    )


def build_pytest_invocation(
    arguments: list[str],
    root: Path,
    *,
    tracked_frontend_paths: frozenset[str] | None = None,
) -> AgentTestInvocation:
    """Validate agent arguments and return a permitted subprocess shape."""
    resolved_root = root.resolve()
    if arguments[:1] == ["--frontend"]:
        return _build_frontend_invocation(
            arguments[1:], resolved_root, tracked_paths=tracked_frontend_paths
        )
    if arguments[:1] == ["--safe-integration"]:
        return _build_safe_integration_invocation(arguments[1:], resolved_root)
    if arguments[:1] == ["--full-store"]:
        return _build_full_store_invocation(arguments[1:], resolved_root)
    validated = _validated_pytest_arguments(arguments, resolved_root)
    return AgentTestInvocation(
        ("pytest", *validated, "-m", NONINTEGRATION_MARKERS),
        resolved_root,
        "pytest",
    )


def _parse_vitest_report(payload: str) -> dict[str, object]:
    try:
        report = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AgentTestInputError("frontend test report is invalid") from exc
    if not isinstance(report, dict):
        raise AgentTestInputError("frontend test report is invalid")
    return report


def _vitest_counts(report: dict[str, object]) -> tuple[int, int]:
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
    return passed, failed


def _sanitize_failure_name(name: str) -> str:
    without_ansi = ANSI_ESCAPE.sub("", name)
    printable = "".join(
        character if character.isprintable() else " " for character in without_ansi
    )
    normalized = " ".join(printable.split())
    return normalized[:MAX_FRONTEND_FAILURE_NAME_LENGTH]


def _vitest_assertions(report: dict[str, object]) -> tuple[dict[object, object], ...]:
    results = report.get("testResults")
    if not isinstance(results, list):
        raise AgentTestInputError("frontend test report is invalid")
    assertions: list[dict[object, object]] = []
    for result in results:
        if not isinstance(result, dict):
            raise AgentTestInputError("frontend test report is invalid")
        result_assertions = result.get("assertionResults")
        if not isinstance(result_assertions, list) or not all(
            isinstance(assertion, dict) for assertion in result_assertions
        ):
            raise AgentTestInputError("frontend test report is invalid")
        assertions.extend(result_assertions)
    return tuple(assertions)


def _unexecuted_requested_frontend_files(
    report: dict[str, object], requested: tuple[FrontendTestPath, ...]
) -> frozenset[FrontendTestPath]:
    results = report.get("testResults")
    if not isinstance(results, list):
        raise AgentTestInputError("frontend test report is invalid")
    executed: set[FrontendTestPath] = set()
    for result in results:
        if not isinstance(result, dict):
            raise AgentTestInputError("frontend test report is invalid")
        name = result.get("name")
        assertions = result.get("assertionResults")
        if not isinstance(name, str) or not isinstance(assertions, list):
            raise AgentTestInputError("frontend test report is invalid")
        if not all(isinstance(assertion, dict) for assertion in assertions):
            raise AgentTestInputError("frontend test report is invalid")
        if any(
            assertion.get("status") in {"passed", "failed"} for assertion in assertions
        ):
            normalized = PurePosixPath(name.replace("\\", "/"))
            for path in requested:
                requested_parts = PurePosixPath(path.repo_relative).parts
                if normalized.parts[-len(requested_parts) :] == requested_parts:
                    executed.add(path)
    return frozenset(set(requested) - executed)


def _vitest_failure_names(report: dict[str, object], failed: int) -> tuple[str, ...]:
    names: list[str] = []
    for assertion in _vitest_assertions(report):
        status = assertion.get("status")
        if not isinstance(status, str):
            raise AgentTestInputError("frontend test report is invalid")
        if status != "failed":
            continue
        full_name = assertion.get("fullName")
        if not isinstance(full_name, str):
            raise AgentTestInputError("frontend test report is invalid")
        sanitized = _sanitize_failure_name(full_name)
        if not sanitized:
            raise AgentTestInputError("frontend test report is invalid")
        names.append(sanitized)
    if len(names) > failed or len(set(names)) != len(names):
        raise AgentTestInputError("frontend test report is invalid")
    return tuple(sorted(names))


def _print_vitest_failure_summary(failed: int, names: tuple[str, ...]) -> None:
    print(f"frontend tests failed: {failed}", file=sys.stderr)
    shown = names[:MAX_FRONTEND_FAILURE_NAMES]
    for name in shown:
        print(f"- {name}", file=sys.stderr)
    omitted_names = len(names) - len(shown)
    if omitted_names:
        print(
            f"- ... {omitted_names} more named frontend failures",
            file=sys.stderr,
        )
    unnamed = failed - len(names)
    if unnamed:
        print(f"- {unnamed} unnamed frontend failures", file=sys.stderr)


def _sanitize_diagnostic_stream(stream: bytes | None) -> str:
    if not stream:
        return ""
    return stream[-MAX_FRONTEND_DIAGNOSTIC_BYTES:].decode("utf-8", errors="replace")


def _sanitize_frontend_diagnostic(
    stdout: bytes | None,
    stderr: bytes | None,
    *,
    root: Path,
    temporary: Path,
) -> str:
    text = "\n".join(
        part
        for part in (
            _sanitize_diagnostic_stream(stdout),
            _sanitize_diagnostic_stream(stderr),
        )
        if part
    )
    if not text:
        return ""
    text = ANSI_ESCAPE.sub("", text)
    text = "".join(
        character
        if character == "\n" or (character.isprintable() and character != "\r")
        else ""
        for character in text
    )
    text = text.replace(str(root), "<WORKTREE>")
    text = text.replace(str(temporary), "<TEMP>")
    text = SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )
    text = BEARER_CREDENTIAL.sub("Bearer [REDACTED]", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    normalized = "\n".join(line for line in lines if line)
    bounded_lines = "\n".join(normalized.splitlines()[-MAX_FRONTEND_DIAGNOSTIC_LINES:])
    return bounded_lines[-MAX_FRONTEND_DIAGNOSTIC_CHARS:]


def _print_frontend_diagnostic(diagnostic: str) -> None:
    if diagnostic:
        print("frontend diagnostic:", file=sys.stderr)
        print(diagnostic, file=sys.stderr)


def _no_frontend_tests_result(child_status: int, diagnostic: str) -> int:
    if child_status == 0:
        print("no frontend test matched the request", file=sys.stderr)
        return 4
    print("frontend test process failed before tests executed", file=sys.stderr)
    _print_frontend_diagnostic(diagnostic)
    return child_status


def _frontend_result(
    report_path: Path,
    child_status: int,
    *,
    diagnostic: str,
    requested: tuple[FrontendTestPath, ...],
) -> int:
    try:
        payload = report_path.read_text(encoding="utf-8")
        report = _parse_vitest_report(payload)
        passed, failed = _vitest_counts(report)
        names = _vitest_failure_names(report, failed)
        unexecuted = _unexecuted_requested_frontend_files(report, requested)
    except OSError, UnicodeDecodeError, AgentTestInputError:
        print("frontend test report is invalid", file=sys.stderr)
        _print_frontend_diagnostic(diagnostic)
        return 3
    if passed + failed == 0:
        return _no_frontend_tests_result(child_status, diagnostic)
    if unexecuted:
        missing = ", ".join(sorted(path.repo_relative for path in unexecuted))
        _print_frontend_diagnostic(
            "\n".join(
                part
                for part in (
                    diagnostic,
                    f"frontend test report omitted or did not execute: {missing}",
                )
                if part
            )
        )
        print(
            "frontend test report did not execute every requested file",
            file=sys.stderr,
        )
        return child_status or 4
    if failed == 0:
        if child_status != 0:
            print(
                "frontend test process failed without a failed test",
                file=sys.stderr,
            )
            _print_frontend_diagnostic(diagnostic)
        return child_status
    if child_status == 0:
        print(
            "frontend test report contradicts process status",
            file=sys.stderr,
        )
        return 3
    _print_vitest_failure_summary(failed, names)
    return child_status


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
    if environment.get("ONTOPRISM_TEST_PARTITION_LANE") is not None:
        environment["ONTOPRISM_TEST_PARTITION_NESTED_BYPASS"] = "1"
    return environment


def run_agent_test(
    arguments: list[str],
    root: Path,
    *,
    runner: CommandRunner | None = None,
    tracked_frontend_paths: frozenset[str] | None = None,
) -> int:
    """Execute a validated repository test command directly, never through a shell."""
    execute: CommandRunner = runner or _subprocess_runner
    invocation = build_pytest_invocation(
        arguments, root, tracked_frontend_paths=tracked_frontend_paths
    )
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
            result = execute(
                command,
                cwd=invocation.cwd,
                env=_controlled_environment(),
                shell=False,
                stdout=subprocess.PIPE if invocation.mode == "frontend" else None,
                stderr=subprocess.PIPE if invocation.mode == "frontend" else None,
                timeout=(
                    FRONTEND_TIMEOUT_SECONDS if invocation.mode == "frontend" else None
                ),
                start_new_session=invocation.mode == "frontend",
            )
        except subprocess.TimeoutExpired as exc:
            label = "frontend test" if invocation.mode == "frontend" else "test process"
            print(f"{label} timed out", file=sys.stderr)
            if invocation.mode == "frontend":
                _print_frontend_diagnostic(
                    _sanitize_frontend_diagnostic(
                        exc.output,
                        exc.stderr,
                        root=root.resolve(),
                        temporary=Path(temporary),
                    )
                )
            return 3
        except FileNotFoundError, PermissionError:
            print("required test executable is unavailable", file=sys.stderr)
            return 3
        except OSError:
            print("test process could not start", file=sys.stderr)
            return 3
        if invocation.mode != "frontend":
            return result.returncode
        diagnostic = _sanitize_frontend_diagnostic(
            result.stdout,
            result.stderr,
            root=root.resolve(),
            temporary=Path(temporary),
        )
        return _frontend_result(
            report_path,
            result.returncode,
            diagnostic=diagnostic,
            requested=invocation.frontend_tests,
        )


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    try:
        return run_agent_test(sys.argv[1:], root)
    except AgentTestInputError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
