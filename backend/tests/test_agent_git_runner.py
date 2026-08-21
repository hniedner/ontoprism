from __future__ import annotations

import re
import subprocess
from typing import TYPE_CHECKING

import pytest
from scripts.validation.run_agent_git import (
    AgentGitInputError,
    AgentGitProcessError,
    run_agent_git,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "arguments",
    [
        ["switch-existing", "--discard-changes"],
        ["switch-existing", "../escape"],
        ["switch-existing", "bad..name"],
        ["switch-existing", "bad@{1}"],
        ["switch-existing", "bad\nmain"],
        ["switch-new", "feature", "--start", "main"],
        ["delete-force", "feature"],
        ["delete-merged", "main"],
        ["merge-no-ff", "feature", "--strategy=ours"],
    ],
)
def test_agent_git_rejects_unsafe_operations(
    arguments: list[str], tmp_path: Path
) -> None:
    with pytest.raises(AgentGitInputError):
        run_agent_git(arguments, tmp_path)


def git(repository: Path, *arguments: str) -> None:
    subprocess.run(  # noqa: S603 - fixed Git test helper
        ["/usr/bin/git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


def test_agent_git_runs_safe_branch_lifecycle_in_disposable_repository(
    tmp_path: Path,
) -> None:
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "tracked.txt").write_text("main\n")
    git(tmp_path, "add", "tracked.txt")
    git(tmp_path, "commit", "-m", "initial")

    assert run_agent_git(["switch-new", "feat/safe"], tmp_path) == 0
    (tmp_path / "tracked.txt").write_text("feature\n")
    git(tmp_path, "commit", "-am", "feature")
    assert run_agent_git(["switch-existing", "main"], tmp_path) == 0
    assert run_agent_git(["merge-no-ff", "feat/safe"], tmp_path) == 0
    assert run_agent_git(["delete-merged", "feat/safe"], tmp_path) == 0

    branches = subprocess.run(
        ["/usr/bin/git", "branch", "--format=%(refname:short)"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert branches == ["main"]


def test_agent_git_reports_conflicted_merge_as_unknown_repository_state(
    tmp_path: Path,
) -> None:
    message = (
        "Git merge failed and may have changed repository state; inspect git status"
    )
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test User")
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("base\n")
    git(tmp_path, "add", "tracked.txt")
    git(tmp_path, "commit", "-m", "initial")
    git(tmp_path, "switch", "-c", "feat/conflict")
    tracked.write_text("feature\n")
    git(tmp_path, "commit", "-am", "feature")
    git(tmp_path, "switch", "main")
    tracked.write_text("main\n")
    git(tmp_path, "commit", "-am", "main")

    try:
        with pytest.raises(
            AgentGitProcessError,
            match=f"^{re.escape(message)}$",
        ):
            run_agent_git(["merge-no-ff", "feat/conflict"], tmp_path)
        assert (tmp_path / ".git" / "MERGE_HEAD").is_file()
    finally:
        if (tmp_path / ".git" / "MERGE_HEAD").is_file():
            git(tmp_path, "merge", "--abort")

    assert not (tmp_path / ".git" / "MERGE_HEAD").exists()
    status = subprocess.run(
        ["/usr/bin/git", "status", "--porcelain"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""


class Result:
    def __init__(self, returncode: int, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


def scripted_runner(results: list[object]) -> object:
    remaining = iter(results)

    def run(_arguments: object, **_kwargs: object) -> object:
        result = next(remaining)
        if isinstance(result, BaseException):
            raise result
        return result

    return run


def test_agent_git_bounds_every_process(tmp_path: Path) -> None:
    timeouts: list[object] = []

    def runner(_arguments: object, **kwargs: object) -> Result:
        timeouts.append(kwargs.get("timeout"))
        return Result(0)

    assert run_agent_git(["switch-existing", "feat/x"], tmp_path, runner=runner) == 0
    assert timeouts == [10, 10]


def test_delete_merged_distinguishes_not_merged_from_operational_error(
    tmp_path: Path,
) -> None:
    not_merged = scripted_runner([Result(0), Result(0, "main\n"), Result(1)])
    with pytest.raises(AgentGitInputError, match="not merged"):
        run_agent_git(["delete-merged", "feat/x"], tmp_path, runner=not_merged)

    operational = scripted_runner([Result(0), Result(0, "main\n"), Result(2)])
    with pytest.raises(AgentGitProcessError, match="merge ancestry check failed"):
        run_agent_git(["delete-merged", "feat/x"], tmp_path, runner=operational)

    signaled = scripted_runner([Result(0), Result(0, "main\n"), Result(-9)])
    with pytest.raises(AgentGitProcessError, match="merge ancestry check failed"):
        run_agent_git(["delete-merged", "feat/x"], tmp_path, runner=signaled)


def test_agent_git_classifies_decode_start_timeout_and_nonzero(
    tmp_path: Path,
) -> None:
    undecodable = UnicodeDecodeError("utf-8", b"\xffsecret", 0, 1, "invalid")
    with pytest.raises(AgentGitProcessError, match="produced undecodable output"):
        run_agent_git(
            ["switch-existing", "feat/x"],
            tmp_path,
            runner=scripted_runner([undecodable]),
        )

    mutation_decode = scripted_runner([Result(0), undecodable])
    with pytest.raises(
        AgentGitProcessError, match="outcome is unknown; inspect git status"
    ):
        run_agent_git(["switch-existing", "feat/x"], tmp_path, runner=mutation_decode)

    with pytest.raises(AgentGitProcessError, match="executable is unavailable"):
        run_agent_git(
            ["switch-existing", "feat/x"],
            tmp_path,
            runner=scripted_runner([FileNotFoundError("secret")]),
        )

    timeout = subprocess.TimeoutExpired(["git"], 10)
    with pytest.raises(AgentGitProcessError, match="timed out"):
        run_agent_git(
            ["switch-existing", "feat/x"],
            tmp_path,
            runner=scripted_runner([timeout]),
        )


@pytest.mark.parametrize(
    ("arguments", "results", "message"),
    [
        (
            ["switch-existing", "feat/x"],
            [Result(0), Result(9)],
            "Git switch failed and may have changed repository state; "
            "inspect git status",
        ),
        (
            ["switch-new", "feat/x"],
            [Result(0), Result(9)],
            "Git branch creation failed and may have changed repository state; "
            "inspect git status",
        ),
        (
            ["merge-no-ff", "feat/x"],
            [Result(0), Result(9)],
            "Git merge failed and may have changed repository state; "
            "inspect git status",
        ),
        (
            ["delete-merged", "feat/x"],
            [Result(0), Result(0, "main\n"), Result(0), Result(9)],
            "Git branch deletion failed and may have changed repository state; "
            "inspect git status",
        ),
    ],
)
def test_mutating_nonzero_reports_operation_specific_unknown_state(
    tmp_path: Path,
    arguments: list[str],
    results: list[object],
    message: str,
) -> None:
    with pytest.raises(AgentGitProcessError, match=f"^{re.escape(message)}$"):
        run_agent_git(arguments, tmp_path, runner=scripted_runner(results))
