#!/usr/bin/env python3
"""Run narrowly scoped local Git branch operations for the implementer agent."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Protocol

UNSAFE_REF_CHARACTERS = frozenset("&;|><`$\\\n\r\t")
PROTECTED_BRANCHES = frozenset({"main", "master"})
OPERATION_ARGUMENT_COUNT = 2
PROCESS_TIMEOUT_SECONDS = 10
MUTATION_FAILURE_MESSAGES = {
    "switch-existing": (
        "Git switch failed and may have changed repository state; inspect git status"
    ),
    "switch-new": (
        "Git branch creation failed and may have changed repository state; "
        "inspect git status"
    ),
    "delete-merged": (
        "Git branch deletion failed and may have changed repository state; "
        "inspect git status"
    ),
    "merge-no-ff": (
        "Git merge failed and may have changed repository state; inspect git status"
    ),
}


class AgentGitInputError(ValueError):
    """The requested Git operation is outside the safe agent contract."""


class AgentGitProcessError(RuntimeError):
    """A fixed, sanitized Git subprocess failure."""


class CommandResult(Protocol):
    returncode: int
    stdout: str


class CommandRunner(Protocol):
    def __call__(self, arguments: list[str], **kwargs: object) -> CommandResult: ...


def _invoke(
    arguments: list[str],
    root: Path,
    runner: CommandRunner,
    *,
    mutating: bool = False,
) -> CommandResult:
    try:
        result = runner(
            arguments,
            cwd=root,
            capture_output=True,
            text=True,
            shell=False,
            check=False,
            timeout=PROCESS_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, PermissionError) as exc:
        raise AgentGitProcessError("required Git executable is unavailable") from exc
    except UnicodeDecodeError as exc:
        message = (
            "Git operation outcome is unknown; inspect git status"
            if mutating
            else "Git produced undecodable output"
        )
        raise AgentGitProcessError(message) from exc
    except subprocess.TimeoutExpired as exc:
        message = (
            "Git operation timed out; inspect git status"
            if mutating
            else "Git operation timed out"
        )
        raise AgentGitProcessError(message) from exc
    except OSError as exc:
        raise AgentGitProcessError("Git process could not start") from exc
    return result


def _validate_branch(name: str, root: Path, runner: CommandRunner) -> None:
    if (
        not name
        or name.startswith("-")
        or ".." in name
        or "@{" in name
        or any(character in name for character in UNSAFE_REF_CHARACTERS)
    ):
        raise AgentGitInputError("branch name is invalid")
    result = _invoke(["git", "check-ref-format", "--branch", name], root, runner)
    if result.returncode != 0:
        raise AgentGitInputError("branch name is invalid")


def _require_success(
    result: CommandResult, message: str = "Git operation failed"
) -> None:
    if result.returncode != 0:
        raise AgentGitProcessError(message)


def run_agent_git(
    arguments: list[str],
    root: Path,
    *,
    runner: CommandRunner = subprocess.run,
) -> int:
    """Validate and run one fixed local branch operation without a shell."""
    if len(arguments) != OPERATION_ARGUMENT_COUNT:
        raise AgentGitInputError("Git operation requires exactly one branch")
    operation, branch = arguments
    resolved_root = root.resolve()
    if operation not in {
        "switch-existing",
        "switch-new",
        "delete-merged",
        "merge-no-ff",
    }:
        raise AgentGitInputError("Git operation is unsupported")
    _validate_branch(branch, resolved_root, runner)
    if operation == "delete-merged":
        if branch in PROTECTED_BRANCHES:
            raise AgentGitInputError("protected branch cannot be deleted")
        current = _invoke(["git", "branch", "--show-current"], resolved_root, runner)
        _require_success(current)
        if current.stdout.strip() == branch:
            raise AgentGitInputError("current branch cannot be deleted")
        merged = _invoke(
            ["git", "merge-base", "--is-ancestor", branch, "HEAD"],
            resolved_root,
            runner,
        )
        if merged.returncode == 1:
            raise AgentGitInputError("branch is not merged into HEAD")
        if merged.returncode != 0:
            raise AgentGitProcessError("Git merge ancestry check failed")
        command = ["git", "branch", "-d", branch]
    elif operation == "switch-existing":
        command = ["git", "switch", branch]
    elif operation == "switch-new":
        command = ["git", "switch", "-c", branch]
    else:
        command = ["git", "merge", "--no-ff", branch]
    _require_success(
        _invoke(command, resolved_root, runner, mutating=True),
        MUTATION_FAILURE_MESSAGES[operation],
    )
    return 0


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    try:
        return run_agent_git(sys.argv[1:], root)
    except AgentGitInputError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except AgentGitProcessError as exc:
        print(str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
