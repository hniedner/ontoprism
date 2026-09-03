#!/usr/bin/env python3
"""Run narrowly scoped local Git operations for the implementer agent."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Protocol

UNSAFE_REF_CHARACTERS = frozenset("&;|><`$\\\n\r\t")
UNSAFE_MESSAGE_CHARACTERS = frozenset("&;|><`$\\")
PROTECTED_BRANCHES = frozenset({"main", "master"})
OPERATION_ARGUMENT_COUNT = 2
COMMIT_ARGUMENT_COUNT = 3
MAX_COMMIT_MESSAGE_LENGTH = 200
PROCESS_TIMEOUT_SECONDS = 10
MUTATION_TIMEOUT_SECONDS = 600
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
    "commit-staged": (
        "Git commit failed and may have changed repository state; inspect git status"
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


def _subprocess_runner(arguments: list[str], **kwargs: object) -> CommandResult:
    # Arguments are the fixed, validated Git invocation built above; never shell input.
    return subprocess.run(  # noqa: S603, PLW1510
        arguments,
        **kwargs,  # type: ignore[arg-type,return-value]
    )


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
            timeout=(MUTATION_TIMEOUT_SECONDS if mutating else PROCESS_TIMEOUT_SECONDS),
        )
    except UnicodeDecodeError as exc:
        message = (
            "Git operation outcome is unknown; inspect git status"
            if mutating
            else "Git produced undecodable output"
        )
        raise AgentGitProcessError(message) from exc
    except subprocess.TimeoutExpired as exc:
        message = (
            "Git operation outcome is unknown; inspect git status"
            if mutating
            else "Git operation timed out"
        )
        raise AgentGitProcessError(message) from exc
    except OSError as exc:
        message = (
            "Git operation outcome is unknown; inspect git status"
            if mutating
            else "Git process could not start"
        )
        raise AgentGitProcessError(message) from exc
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
    result = _invoke(["git", "check-ref-format", f"refs/heads/{name}"], root, runner)
    if result.returncode == 0:
        return
    if result.returncode == 1:
        raise AgentGitInputError("branch name is invalid")
    if result.returncode < 0:
        raise AgentGitProcessError(
            "Git branch validation was interrupted; retry the operation"
        )
    raise AgentGitProcessError("Git branch validation failed")


def _require_local_branch(name: str, root: Path, runner: CommandRunner) -> str:
    full_ref = f"refs/heads/{name}"
    result = _invoke(["git", "show-ref", "--verify", "--quiet", full_ref], root, runner)
    if result.returncode == 0:
        return full_ref
    if result.returncode == 1:
        raise AgentGitInputError("local branch does not exist")
    raise AgentGitProcessError("Git local branch check failed")


def _validate_commit_message(message: str) -> None:
    if (
        not message.strip()
        or message.lstrip().startswith("-")
        or len(message) > MAX_COMMIT_MESSAGE_LENGTH
        or any(character in message for character in UNSAFE_MESSAGE_CHARACTERS)
        or any(not character.isprintable() for character in message)
    ):
        raise AgentGitInputError("commit message is invalid")


def _require_success(
    result: CommandResult, message: str = "Git operation failed"
) -> None:
    if result.returncode != 0:
        raise AgentGitProcessError(message)


def _require_mutable_current_branch(root: Path, runner: CommandRunner) -> None:
    current = _invoke(["git", "branch", "--show-current"], root, runner)
    _require_success(current, "Git current branch could not be determined")
    branch = current.stdout.strip()
    if not branch:
        raise AgentGitInputError("Git operation requires an attached branch")
    if branch in PROTECTED_BRANCHES:
        raise AgentGitInputError("Git operation cannot target a protected branch")


def _prepare_branch_command(
    operation: str, branch: str, root: Path, runner: CommandRunner
) -> list[str]:
    _validate_branch(branch, root, runner)
    if operation == "switch-existing":
        if branch in PROTECTED_BRANCHES:
            raise AgentGitInputError("cannot switch to a protected branch")
        _require_local_branch(branch, root, runner)
        return ["git", "switch", branch]
    if operation == "switch-new":
        if branch in PROTECTED_BRANCHES:
            raise AgentGitInputError("cannot create a protected branch")
        return ["git", "switch", "-c", branch]
    if operation == "merge-no-ff":
        full_ref = _require_local_branch(branch, root, runner)
        _require_mutable_current_branch(root, runner)
        return ["git", "merge", "--no-ff", full_ref]
    if branch in PROTECTED_BRANCHES:
        raise AgentGitInputError("protected branch cannot be deleted")
    full_ref = _require_local_branch(branch, root, runner)
    current = _invoke(["git", "branch", "--show-current"], root, runner)
    _require_success(current)
    if current.stdout.strip() == branch:
        raise AgentGitInputError("current branch cannot be deleted")
    merged = _invoke(
        ["git", "merge-base", "--is-ancestor", full_ref, "HEAD"], root, runner
    )
    if merged.returncode == 1:
        raise AgentGitInputError("branch is not merged into HEAD")
    if merged.returncode != 0:
        raise AgentGitProcessError("Git merge ancestry check failed")
    return ["git", "branch", "-d", branch]


def run_agent_git(
    arguments: list[str],
    root: Path,
    *,
    runner: CommandRunner | None = None,
) -> int:
    """Validate and run one fixed local Git operation without a shell."""
    runner = runner or _subprocess_runner
    if not arguments:
        raise AgentGitInputError("Git operation is unsupported")
    operation = arguments[0]
    resolved_root = root.resolve()
    branch_operations = {
        "switch-existing",
        "switch-new",
        "delete-merged",
        "merge-no-ff",
    }
    if operation == "commit-staged":
        if len(arguments) != COMMIT_ARGUMENT_COUNT or arguments[1] != "--message":
            raise AgentGitInputError(
                "commit-staged requires exactly --message and one value"
            )
        message = arguments[2]
        _validate_commit_message(message)
        _require_mutable_current_branch(resolved_root, runner)
        command = ["git", "commit", "-m", message]
    elif operation in branch_operations:
        if len(arguments) != OPERATION_ARGUMENT_COUNT:
            raise AgentGitInputError("Git operation requires exactly one branch")
        command = _prepare_branch_command(
            operation, arguments[1], resolved_root, runner
        )
    else:
        raise AgentGitInputError("Git operation is unsupported")
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
