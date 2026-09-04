#!/usr/bin/env python3
"""Run fixed tracked Vitest files without exposing npm or Vitest arguments."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol, Self

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TEST_SUFFIXES = (".test.ts", ".test.js", ".spec.ts", ".spec.js")
TEST_ROOT = PurePosixPath("frontend/src")
SAFE_PATH_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-[]"
)
TEST_TIMEOUT_SECONDS = 300
INVENTORY_TIMEOUT_SECONDS = 10


class AgentFrontendTestInputError(ValueError):
    """The requested frontend test files cannot be safely executed."""


@dataclass(frozen=True)
class RepoRelativeTrackedTestPath:
    """A validated tracked test path rooted at the repository."""

    value: str


@dataclass(frozen=True)
class FrontendRelativeTestPath:
    """A validated test path rooted at the frontend directory."""

    value: str


@dataclass(frozen=True, init=False)
class FrontendTestInvocation:
    """The sole fixed npm command assembled from validated typed test paths."""

    _tests: tuple[FrontendRelativeTestPath, ...]
    cwd: Path

    @classmethod
    def _from_validated_tests(
        cls, tests: tuple[FrontendRelativeTestPath, ...], cwd: Path
    ) -> Self:
        instance = object.__new__(cls)
        object.__setattr__(instance, "_tests", tests)
        object.__setattr__(instance, "cwd", cwd)
        return instance

    @property
    def arguments(self) -> tuple[str, ...]:
        return (
            "npm",
            "--prefix",
            "frontend",
            "run",
            "test:unit",
            "--",
            "--run",
            "--reporter=json",
            *(test.value for test in self._tests),
        )

    @property
    def tests(self) -> tuple[FrontendRelativeTestPath, ...]:
        return self._tests


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
        shell: Literal[False],
        check: Literal[False],
        capture_output: Literal[True],
        timeout: int,
        env: dict[str, str],
        start_new_session: Literal[True],
    ) -> CommandResult: ...


def _subprocess_runner(
    arguments: tuple[str, ...],
    *,
    cwd: Path,
    shell: Literal[False],
    check: Literal[False],
    capture_output: Literal[True],
    timeout: int,
    env: dict[str, str],
    start_new_session: Literal[True],
) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.Popen(  # noqa: S603 -- fixed argv around validated paths
        arguments,
        cwd=cwd,
        shell=shell,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=start_new_session,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise subprocess.TimeoutExpired(arguments, timeout) from exc
    _ = check  # protocol fixes check=False
    _ = capture_output  # protocol fixes capture_output=True
    return subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)


def _tracked_frontend_paths(root: Path) -> frozenset[str]:
    git = shutil.which("git")
    if git is None:
        raise AgentFrontendTestInputError(
            "tracked frontend test inventory is unavailable"
        )
    try:
        result = subprocess.run(  # noqa: S603 -- resolved executable, fixed argv
            (git, "ls-files", "-z", "--", "frontend"),
            cwd=root,
            shell=False,
            check=False,
            capture_output=True,
            timeout=INVENTORY_TIMEOUT_SECONDS,
        )
        stdout = result.stdout.decode("utf-8", errors="strict")
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as exc:
        raise AgentFrontendTestInputError(
            "tracked frontend test inventory is unavailable"
        ) from exc
    if result.returncode != 0:
        raise AgentFrontendTestInputError(
            "tracked frontend test inventory is unavailable"
        )
    return frozenset(path for path in stdout.split("\0") if path)


def _lexical_test_path(argument: str) -> PurePosixPath:
    if (
        not argument
        or argument.startswith("-")
        or any(character not in SAFE_PATH_CHARACTERS for character in argument)
    ):
        raise AgentFrontendTestInputError("frontend test path is invalid")
    candidate = PurePosixPath(argument)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise AgentFrontendTestInputError("frontend test path must stay in frontend")
    if not candidate.is_relative_to(PurePosixPath("frontend")):
        raise AgentFrontendTestInputError("frontend test path must stay in frontend")
    if not candidate.is_relative_to(TEST_ROOT):
        raise AgentFrontendTestInputError(
            "frontend test path must stay in frontend/src"
        )
    return candidate


def _validate_test_path(
    argument: str, root: Path, tracked_paths: frozenset[str]
) -> RepoRelativeTrackedTestPath:
    candidate = _lexical_test_path(argument)
    normalized = candidate.as_posix()
    if normalized not in tracked_paths:
        raise AgentFrontendTestInputError("frontend test path must be tracked")
    if not normalized.endswith(TEST_SUFFIXES):
        raise AgentFrontendTestInputError("frontend test filename is unsupported")

    path = root / candidate
    current = root
    for part in candidate.parts:
        current /= part
        if current.is_symlink():
            raise AgentFrontendTestInputError(
                "frontend test path must not use a symlink"
            )
    frontend_root = (root / "frontend").resolve()
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(frontend_root)
    except (OSError, ValueError) as exc:
        raise AgentFrontendTestInputError(
            "frontend test path must name an existing file"
        ) from exc
    if not resolved.is_file():
        raise AgentFrontendTestInputError("frontend test path must name a file")
    return RepoRelativeTrackedTestPath(normalized)


def build_frontend_test_invocation(
    arguments: list[str],
    root: Path = REPOSITORY_ROOT,
    *,
    tracked_paths: frozenset[str] | None = None,
) -> FrontendTestInvocation:
    """Validate test files and build the only supported npm argv."""
    if not arguments:
        raise AgentFrontendTestInputError("at least one frontend test path is required")
    resolved_root = root.resolve()
    inventory = (
        _tracked_frontend_paths(resolved_root)
        if tracked_paths is None
        else tracked_paths
    )
    repository_tests = tuple(
        _validate_test_path(argument, resolved_root, inventory)
        for argument in arguments
    )
    tests = tuple(
        FrontendRelativeTestPath(
            PurePosixPath(test.value).relative_to(PurePosixPath("frontend")).as_posix()
        )
        for test in repository_tests
    )
    return FrontendTestInvocation._from_validated_tests(tests, resolved_root)


def _successful_report_executed_requested_files(
    stdout: str, tests: tuple[FrontendRelativeTestPath, ...]
) -> bool:
    try:
        report_line = next(
            line for line in reversed(stdout.splitlines()) if line.strip()
        )
        report = json.loads(report_line)
        passed = report["numPassedTests"]
        results = report["testResults"]
    except StopIteration, json.JSONDecodeError, KeyError, TypeError:
        return False
    if not isinstance(passed, int) or isinstance(passed, bool) or passed < 1:
        return False
    if not isinstance(results, list):
        return False
    result_names: set[str] = set()
    for result in results:
        if not isinstance(result, dict):
            continue
        name = result.get("name")
        if isinstance(name, str):
            result_names.add(name.replace("\\", "/"))
    return all(
        any(name.endswith(f"/frontend/{test.value}") for name in result_names)
        for test in tests
    )


def run_agent_frontend_test(
    arguments: list[str],
    root: Path = REPOSITORY_ROOT,
    *,
    tracked_paths: frozenset[str] | None = None,
    runner: CommandRunner = _subprocess_runner,
) -> int:
    """Preserve completed Vitest status; return 2 for wrapper contract failures."""
    try:
        invocation = build_frontend_test_invocation(
            arguments, root, tracked_paths=tracked_paths
        )
        with tempfile.TemporaryDirectory(prefix="ontoprism-agent-frontend-") as home:
            environment = {
                "PATH": os.environ.get("PATH", os.defpath),
                "HOME": home,
                "NPM_CONFIG_CACHE": home,
                "NPM_CONFIG_USERCONFIG": os.devnull,
            }
            result = runner(
                invocation.arguments,
                cwd=invocation.cwd,
                shell=False,
                check=False,
                capture_output=True,
                timeout=TEST_TIMEOUT_SECONDS,
                env=environment,
                start_new_session=True,
            )
            stdout = (result.stdout or b"").decode("utf-8", errors="strict")
            stderr = (result.stderr or b"").decode("utf-8", errors="strict")
    except AgentFrontendTestInputError as exc:
        print(f"agent-frontend-test: {exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("agent-frontend-test: frontend test timed out", file=sys.stderr)
        return 2
    except OSError:
        print(
            "agent-frontend-test: frontend test process could not start",
            file=sys.stderr,
        )
        return 2
    except UnicodeDecodeError:
        print(
            "agent-frontend-test: frontend test produced invalid output",
            file=sys.stderr,
        )
        return 2

    if stdout:
        sys.stdout.write(stdout)
    if stderr:
        sys.stderr.write(stderr)
    if result.returncode == 0 and not _successful_report_executed_requested_files(
        stdout, invocation.tests
    ):
        print(
            "agent-frontend-test: Vitest did not execute every requested test file",
            file=sys.stderr,
        )
        return 2
    return result.returncode


def main() -> int:
    """CLI entry point rooted at this script's repository checkout."""
    return run_agent_frontend_test(sys.argv[1:], REPOSITORY_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
