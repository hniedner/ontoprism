from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest
from scripts.validation.run_agent_git import AgentGitInputError, run_agent_git

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
