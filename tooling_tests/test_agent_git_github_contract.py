"""Read-only contract: the live GitHub pulls query agent-git trusts for deletion."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from scripts.validation.run_agent_git import _github_squash_merged_tip

pytestmark = pytest.mark.integration

# PR #424 squash-merged this branch into main at this head (read 2026-09-22).
_MERGED_BRANCH = "feat/m0-r0-1-recovery-followon"
_MERGED_HEAD = "4c5a132ad3046393a719c98fd98410fcef884fb2"


class _Result:
    def __init__(self, stdout: str) -> None:
        self.returncode = 0
        self.stdout = stdout


def _require_github_cli() -> None:
    """Skip locally without an authenticated gh; in GitHub Actions, fail instead."""
    gh = shutil.which("gh")
    authenticated = (
        gh is not None
        and subprocess.run(  # noqa: S603 - resolved gh executable, fixed arguments
            [gh, "auth", "status"], capture_output=True, check=False
        ).returncode
        == 0
    )
    if authenticated:
        return
    if os.environ.get("GITHUB_ACTIONS") == "true":
        pytest.fail("GitHub CLI must be authenticated in CI (GH_TOKEN)")
    pytest.skip("authenticated GitHub CLI is not available")


def test_real_github_answer_proves_a_known_squash_merge(tmp_path: Path) -> None:
    _require_github_cli()

    # Both branches report PR #424's head as their tip on purpose: if the query
    # ignored the head filter, the unknown branch would then "match" #424.
    def run(arguments: list[str], **kwargs: object) -> object:
        if arguments[:2] == ["git", "rev-parse"]:
            return _Result(f"{_MERGED_HEAD}\n")
        return subprocess.run(arguments, **kwargs)  # noqa: PLW1510, S603

    merged = _github_squash_merged_tip(
        _MERGED_BRANCH, f"refs/heads/{_MERGED_BRANCH}", tmp_path, run
    )
    unknown = _github_squash_merged_tip(
        "no/such-branch-ever", "refs/heads/no/such-branch-ever", tmp_path, run
    )

    assert merged == _MERGED_HEAD
    assert unknown is None
