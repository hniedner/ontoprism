"""Behavioral guards for embedding publication source preflight/stability."""

import shutil
import subprocess

import pytest
from scripts.data_build import (
    _code_commit,
    _require_ncit_source,
    _require_stable_cadsr_source,
    _require_stable_ncit_source,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("version", "count", "message"),
    [
        (None, 204_373, "no owl:versionInfo"),
        ("wrong", 204_373, "version does not match"),
        ("26.02d", 4_752, "count does not match"),
    ],
)
def test_ncit_source_preflight_rejects_unpublishable_release(
    version: str | None, count: int, message: str
) -> None:
    with pytest.raises(RuntimeError, match=message):
        _require_ncit_source(
            version,
            count,
            expected_version="26.02d",
            expected_count=204_373,
        )


@pytest.mark.unit
def test_ncit_source_stability_detects_same_count_content_drift() -> None:
    with pytest.raises(RuntimeError, match="source changed"):
        _require_stable_ncit_source(
            ("26.02d", 204_373, "before"),
            ("26.02d", 204_373, "after"),
        )


@pytest.mark.unit
def test_cadsr_source_stability_detects_file_drift() -> None:
    with pytest.raises(RuntimeError, match="source changed"):
        _require_stable_cadsr_source(("before", 79_827), ("after", 79_827))


@pytest.mark.unit
def test_code_commit_requires_clean_repo_and_returns_exact_head(tmp_path) -> None:  # type: ignore[no-untyped-def]
    git = shutil.which("git")
    assert git is not None
    subprocess.run([git, "init", "-q", str(tmp_path)], check=True)  # noqa: S603
    (tmp_path / "tracked.txt").write_text("clean")
    subprocess.run([git, "-C", str(tmp_path), "add", "."], check=True)  # noqa: S603
    subprocess.run(  # noqa: S603
        [
            git,
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "test",
        ],
        check=True,
    )
    expected = subprocess.run(  # noqa: S603
        [git, "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert _code_commit(tmp_path) == expected
    (tmp_path / "tracked.txt").write_text("dirty")
    with pytest.raises(RuntimeError, match="clean worktree"):
        _code_commit(tmp_path)
