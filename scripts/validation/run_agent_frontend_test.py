#!/usr/bin/env python3
"""Run fixed tracked Vitest files without exposing npm or Vitest arguments."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TEST_SUFFIXES = (".test.ts", ".test.js", ".spec.ts", ".spec.js")
SAFE_PATH_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-[]"
)
TEST_TIMEOUT_SECONDS = 300
INVENTORY_TIMEOUT_SECONDS = 10


class AgentFrontendTestInputError(ValueError):
    """The requested frontend test files cannot be safely executed."""


@dataclass(frozen=True)
class FrontendTestInvocation:
    """A validated fixed frontend test command."""

    arguments: tuple[str, ...]
    cwd: Path


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
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603 -- argv is fixed around validated tracked paths
        arguments,
        cwd=cwd,
        shell=shell,
        check=check,
        capture_output=capture_output,
        timeout=timeout,
        env=env,
    )


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


def _validate_test_path(
    argument: str, root: Path, tracked_paths: frozenset[str]
) -> str:
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
    return candidate.relative_to(PurePosixPath("frontend")).as_posix()


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
    tests = tuple(
        _validate_test_path(argument, resolved_root, inventory)
        for argument in arguments
    )
    return FrontendTestInvocation(
        (
            "npm",
            "--prefix",
            "frontend",
            "run",
            "test:unit",
            "--",
            "--run",
            *tests,
        ),
        resolved_root,
    )


def run_agent_frontend_test(
    arguments: list[str],
    root: Path = REPOSITORY_ROOT,
    *,
    tracked_paths: frozenset[str] | None = None,
    runner: CommandRunner = _subprocess_runner,
) -> int:
    """Run one validated invocation and preserve Vitest's process status."""
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
    return result.returncode


def main() -> int:
    """CLI entry point rooted at this script's repository checkout."""
    return run_agent_frontend_test(sys.argv[1:], REPOSITORY_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
