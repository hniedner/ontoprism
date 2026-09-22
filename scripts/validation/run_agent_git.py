#!/usr/bin/env python3
"""Run fixed local Git operations for implementers and fixed remote pull/push
for orchestrators, without a shell. delete-merged may also run one read-only
GitHub CLI pull-request query to recognise a squash-merged branch.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, assert_never

UNSAFE_REF_CHARACTERS = frozenset("&;|><`$\\\n\r\t")
UNSAFE_MESSAGE_CHARACTERS = frozenset("&;|><`$\\")
PROTECTED_BRANCHES = frozenset({"main", "master"})
REPOSITORY = "hniedner/ontoprism"
ACCEPTED_ORIGIN_URLS = frozenset(
    {
        "https://github.com/hniedner/ontoprism",
        "https://github.com/hniedner/ontoprism.git",
        "git@github.com:hniedner/ontoprism.git",
    }
)
OPERATION_ARGUMENT_COUNT = 2
COMMIT_ARGUMENT_COUNT = 3
MAX_COMMIT_MESSAGE_LENGTH = 200
PROCESS_TIMEOUT_SECONDS = 10
GITHUB_READ_TIMEOUT_SECONDS = 30
MUTATION_TIMEOUT_SECONDS = 600
OperationClass = Literal["read", "github-read", "local-mutation", "remote-mutation"]
CommandKind = Literal["branch", "commit", "remote"]


@dataclass(frozen=True)
class OperationClassSpec:
    timeout: int
    decode_error: str
    timeout_error: str
    os_error: str


@dataclass(frozen=True)
class OperationSpec:
    command_kind: CommandKind
    failure: str


OPERATION_CLASS_SPECS: dict[OperationClass, OperationClassSpec] = {
    "read": OperationClassSpec(
        timeout=PROCESS_TIMEOUT_SECONDS,
        decode_error="Git produced undecodable output",
        timeout_error="Git operation timed out",
        os_error="Git process could not start",
    ),
    "github-read": OperationClassSpec(
        timeout=GITHUB_READ_TIMEOUT_SECONDS,
        decode_error="GitHub produced undecodable output",
        timeout_error="GitHub pull-request query timed out",
        os_error="GitHub CLI could not start",
    ),
    "local-mutation": OperationClassSpec(
        timeout=MUTATION_TIMEOUT_SECONDS,
        decode_error="Git operation outcome is unknown; inspect git status",
        timeout_error="Git operation outcome is unknown; inspect git status",
        os_error="Git operation outcome is unknown; inspect git status",
    ),
    "remote-mutation": OperationClassSpec(
        timeout=MUTATION_TIMEOUT_SECONDS,
        decode_error=(
            "Git operation outcome is unknown; inspect repository and remote state "
            "before retrying"
        ),
        timeout_error=(
            "Git operation outcome is unknown; inspect repository and remote state "
            "before retrying"
        ),
        os_error=(
            "Git operation outcome is unknown; inspect repository and remote state "
            "before retrying"
        ),
    ),
}
OPERATION_SPECS: dict[str, OperationSpec] = {
    "switch-existing": OperationSpec(
        "branch",
        "Git switch failed and may have changed repository state; inspect git status",
    ),
    "switch-new": OperationSpec(
        "branch",
        "Git branch creation failed and may have changed repository state; "
        "inspect git status",
    ),
    "delete-merged": OperationSpec(
        "branch",
        # Git exits nonzero before changing anything (a signal death is reported
        # separately). The pre-checks cannot see every refusal: commits not on the
        # branch's upstream (when one is set, `-d` checks it instead of HEAD), a
        # paused rebase or bisect, or a ref lock held elsewhere.
        "Git refused to delete the branch; nothing was deleted. Causes include "
        "commits not on its upstream, a rebase or bisect in some worktree "
        "(git worktree list), or a held ref lock",
    ),
    "merge-no-ff": OperationSpec(
        "branch",
        "Git merge failed and may have changed repository state; inspect git status",
    ),
    "commit-staged": OperationSpec(
        "commit",
        "Git commit failed and may have changed repository state; inspect git status",
    ),
    "pull-origin": OperationSpec(
        "remote",
        "Git pull failed; inspect repository and remote state before retrying",
    ),
    "push-origin": OperationSpec(
        "remote",
        "Git push failed; inspect repository and remote state before retrying",
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


def operation_class_for(command_kind: CommandKind) -> OperationClass:
    if command_kind in ("branch", "commit"):
        return "local-mutation"
    if command_kind == "remote":
        return "remote-mutation"
    assert_never(command_kind)


def _subprocess_runner(arguments: list[str], **kwargs: object) -> CommandResult:
    # Arguments are the fixed, validated Git or GitHub CLI invocation built above;
    # never shell input.
    return subprocess.run(  # noqa: S603, PLW1510
        arguments,
        **kwargs,  # type: ignore[arg-type,return-value]
    )


def _invoke(
    arguments: list[str],
    root: Path,
    runner: CommandRunner,
    *,
    operation_class: OperationClass,
) -> CommandResult:
    spec = OPERATION_CLASS_SPECS[operation_class]
    kwargs: dict[str, object] = {
        "cwd": root,
        "capture_output": True,
        "text": True,
        "shell": False,
        "check": False,
        "timeout": spec.timeout,
    }
    if operation_class == "remote-mutation":
        environment = os.environ.copy()
        environment.update({"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"})
        kwargs.update({"stdin": subprocess.DEVNULL, "env": environment})
    try:
        result = runner(arguments, **kwargs)
    except UnicodeDecodeError as exc:
        raise AgentGitProcessError(spec.decode_error) from exc
    except subprocess.TimeoutExpired as exc:
        raise AgentGitProcessError(spec.timeout_error) from exc
    except OSError as exc:
        raise AgentGitProcessError(spec.os_error) from exc
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
    result = _invoke(
        ["git", "check-ref-format", f"refs/heads/{name}"],
        root,
        runner,
        operation_class="read",
    )
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
    result = _invoke(
        ["git", "show-ref", "--verify", "--quiet", full_ref],
        root,
        runner,
        operation_class="read",
    )
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
    current = _invoke(
        ["git", "branch", "--show-current"],
        root,
        runner,
        operation_class="read",
    )
    _require_success(current, "Git current branch could not be determined")
    branch = current.stdout.strip()
    if not branch:
        raise AgentGitInputError("Git operation requires an attached branch")
    if branch in PROTECTED_BRANCHES:
        raise AgentGitInputError("Git operation cannot target a protected branch")


def _prepare_remote_command(
    operation: str, branch: str, root: Path, runner: CommandRunner
) -> list[str]:
    _validate_branch(branch, root, runner)
    if operation == "push-origin" and branch in PROTECTED_BRANCHES:
        raise AgentGitInputError("Git push cannot target a protected branch")
    origin = _invoke(
        ["git", "remote", "get-url", "origin"],
        root,
        runner,
        operation_class="read",
    )
    _require_success(origin, "Git origin could not be determined")
    if origin.stdout.strip() not in ACCEPTED_ORIGIN_URLS:
        raise AgentGitInputError("Git origin is not the ONTOPRISM repository")
    current = _invoke(
        ["git", "branch", "--show-current"],
        root,
        runner,
        operation_class="read",
    )
    _require_success(current, "Git current branch could not be determined")
    current_branch = current.stdout.strip()
    if not current_branch:
        raise AgentGitInputError("Git operation requires an attached branch")
    if current_branch != branch:
        raise AgentGitInputError("requested branch is not the current branch")
    status = _invoke(
        ["git", "status", "--porcelain"],
        root,
        runner,
        operation_class="read",
    )
    _require_success(status, "Git worktree status could not be determined")
    if status.stdout:
        raise AgentGitInputError("Git remote operation requires a clean worktree")
    full_ref = f"refs/heads/{branch}"
    if operation == "pull-origin":
        return ["git", "pull", "--ff-only", "origin", full_ref]
    return [
        "git",
        "push",
        "--set-upstream",
        "origin",
        f"{full_ref}:{full_ref}",
    ]


def _prepare_branch_command(
    operation: str, branch: str, root: Path, runner: CommandRunner
) -> list[str]:
    _validate_branch(branch, root, runner)
    if operation == "switch-existing":
        # Switching to main is safe: commit, merge and push refuse protected branches,
        # so the only operation that changes main is pull-origin, fast-forward only.
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
    return _prepare_delete_command(branch, root, runner)


def _prepare_delete_command(
    branch: str, root: Path, runner: CommandRunner
) -> list[str]:
    if branch in PROTECTED_BRANCHES:
        raise AgentGitInputError("protected branch cannot be deleted")
    full_ref = _require_local_branch(branch, root, runner)
    current = _invoke(
        ["git", "branch", "--show-current"],
        root,
        runner,
        operation_class="read",
    )
    _require_success(current)
    if current.stdout.strip() == branch:
        raise AgentGitInputError("current branch cannot be deleted")
    _require_not_checked_out(full_ref, root, runner)
    merged = _invoke(
        ["git", "merge-base", "--is-ancestor", full_ref, "HEAD"],
        root,
        runner,
        operation_class="read",
    )
    if merged.returncode == 0:
        return ["git", "branch", "-d", branch]
    if merged.returncode != 1:
        raise AgentGitProcessError("Git merge ancestry check failed")
    merged_tip = _github_squash_merged_tip(branch, full_ref, root, runner)
    if merged_tip is not None:
        # Re-read the tip after the (slow) GitHub query so a commit added meanwhile
        # is refused. `branch -D` still refuses a branch that a worktree checked out
        # after `_require_not_checked_out`, and removes the branch's configuration.
        if _branch_tip(full_ref, root, runner) != merged_tip:
            raise AgentGitInputError(
                "branch changed after the GitHub merge check; it was not deleted"
            )
        return ["git", "branch", "-D", branch]
    raise AgentGitInputError(
        "branch is not merged into HEAD or squash-merged into main"
    )


def _require_not_checked_out(full_ref: str, root: Path, runner: CommandRunner) -> None:
    # Git would refuse too, but only as a failed mutation, which the wrapper cannot
    # tell apart from other deletion failures.
    listed = _invoke(
        ["git", "worktree", "list", "--porcelain", "-z"],
        root,
        runner,
        operation_class="read",
    )
    _require_success(listed, "Git worktree list failed")
    if f"branch {full_ref}" in listed.stdout.split("\0"):
        raise AgentGitInputError(
            "branch is checked out in another worktree; it was not deleted"
        )


def _branch_tip(full_ref: str, root: Path, runner: CommandRunner) -> str:
    tip = _invoke(["git", "rev-parse", full_ref], root, runner, operation_class="read")
    _require_success(tip, "Git branch tip could not be read")
    return tip.stdout.strip()


def _github_squash_merged_tip(
    branch: str, full_ref: str, root: Path, runner: CommandRunner
) -> str | None:
    """Return the local tip if GitHub merged a PR into main whose head is exactly it.

    A squash merge leaves no ancestry, so the merged PR's recorded head SHA is the
    proof that nothing on the local branch is lost by deleting it. Only the first
    100 closed PRs from the branch into main are examined; a miss refuses deletion.
    """
    local_tip = _branch_tip(full_ref, root, runner)
    owner = REPOSITORY.split("/", maxsplit=1)[0]
    answer = _invoke(
        [
            "gh",
            "api",
            f"repos/{REPOSITORY}/pulls?state=closed&base=main"
            f"&head={owner}:{branch}&per_page=100",
        ],
        root,
        runner,
        operation_class="github-read",
    )
    if answer.returncode != 0:
        raise AgentGitProcessError(
            f"GitHub pull-request query failed (gh exit {answer.returncode})"
        )
    try:
        pulls = json.loads(answer.stdout)
    except json.JSONDecodeError as exc:
        raise AgentGitProcessError("GitHub pull-request query was malformed") from exc
    if not isinstance(pulls, list):
        raise AgentGitProcessError("GitHub pull-request query was malformed")
    for pull in pulls:
        head = pull.get("head") if isinstance(pull, dict) else None
        base = pull.get("base") if isinstance(pull, dict) else None
        if not isinstance(head, dict) or not isinstance(base, dict):
            raise AgentGitProcessError("GitHub pull-request query was malformed")
        if (
            pull.get("merged_at")
            and head.get("sha") == local_tip
            and base.get("ref") == "main"
        ):
            return local_tip
    return None


def run_agent_git(
    arguments: list[str],
    root: Path,
    *,
    runner: CommandRunner | None = None,
) -> int:
    """Run fixed local Git operations for implementers and fixed remote pull/push
    for orchestrators, without a shell. delete-merged may also run one read-only
    GitHub CLI pull-request query to recognise a squash-merged branch.
    """
    runner = runner or _subprocess_runner
    if not arguments:
        raise AgentGitInputError("Git operation is unsupported")
    operation = arguments[0]
    operation_spec = OPERATION_SPECS.get(operation)
    if operation_spec is None:
        raise AgentGitInputError("Git operation is unsupported")
    resolved_root = root.resolve()
    if operation_spec.command_kind == "commit":
        if len(arguments) != COMMIT_ARGUMENT_COUNT or arguments[1] != "--message":
            raise AgentGitInputError(
                "commit-staged requires exactly --message and one value"
            )
        message = arguments[2]
        _validate_commit_message(message)
        _require_mutable_current_branch(resolved_root, runner)
        command = ["git", "commit", "-m", message]
    elif operation_spec.command_kind == "branch":
        if len(arguments) != OPERATION_ARGUMENT_COUNT:
            raise AgentGitInputError("Git operation requires exactly one branch")
        command = _prepare_branch_command(
            operation, arguments[1], resolved_root, runner
        )
    elif operation_spec.command_kind == "remote":
        if len(arguments) != OPERATION_ARGUMENT_COUNT:
            raise AgentGitInputError("Git operation requires exactly one branch")
        command = _prepare_remote_command(
            operation, arguments[1], resolved_root, runner
        )
    else:
        assert_never(operation_spec.command_kind)
    operation_class = operation_class_for(operation_spec.command_kind)
    result = _invoke(command, resolved_root, runner, operation_class=operation_class)
    if result.returncode < 0:
        # Killed by a signal: Git may have applied the change before it died, so
        # report the same unknown outcome as a timeout.
        raise AgentGitProcessError(OPERATION_CLASS_SPECS[operation_class].timeout_error)
    _require_success(
        result,
        operation_spec.failure,
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
