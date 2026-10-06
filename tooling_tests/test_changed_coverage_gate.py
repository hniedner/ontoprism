"""Real diff-cover contracts using disposable git history and Cobertura reports."""
# ruff: noqa: S603, S607 — fixed commands in a disposable test repository

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts/validation/changed_coverage_gate.py"
)
pytestmark = pytest.mark.unit


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(repo: Path) -> None:
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "fixture",
    )


@pytest.fixture
def coverage_repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-b", "main")
    (tmp_path / "README.md").write_text("baseline\n")
    git(tmp_path, "add", "README.md")
    commit(tmp_path)
    git(tmp_path, "branch", "feat/m16-sites-and-versions")
    git(tmp_path, "switch", "-c", "feature")
    return tmp_path


def report(repo: Path, filename: str, condition: str) -> Path:
    source = repo / filename
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("if value:\n    raise ValueError(value)\n")
    subprocess.run(["git", "add", filename], cwd=repo, check=True)
    xml = repo / "coverage.xml"
    xml.write_text(
        "<coverage><sources><source>.</source></sources><packages><package>"
        f'<classes><class filename="{filename}"><lines>'
        f'<line number="1" hits="1" branch="true" condition-coverage="{condition}"/>'
        '<line number="2" hits="1"/>'
        "</lines></class></classes></package></packages></coverage>"
    )
    return xml


def run_gate(repo: Path, xml: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--coverage-xml", str(xml), *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("scope", ["ontolib/src", "backend/src"])
def test_changed_partial_branch_fails_even_when_line_was_executed(
    coverage_repo: Path,
    scope: str,
) -> None:
    result = run_gate(
        coverage_repo, report(coverage_repo, f"{scope}/guard.py", "50% (1/2)")
    )
    assert result.returncode == 1
    assert "50%" in result.stdout


def test_changed_fully_covered_guard_passes(coverage_repo: Path) -> None:
    result = run_gate(
        coverage_repo, report(coverage_repo, "ontolib/src/guard.py", "100% (2/2)")
    )
    assert result.returncode == 0
    assert "100%" in result.stdout


def test_test_only_diff_is_not_applicable(coverage_repo: Path) -> None:
    result = run_gate(
        coverage_repo, report(coverage_repo, "tooling_tests/test_guard.py", "50% (1/2)")
    )
    assert result.returncode == 0
    assert "not applicable" in result.stdout
    assert "100%" not in result.stdout


def test_missing_report_is_an_error_not_a_pass(coverage_repo: Path) -> None:
    result = run_gate(coverage_repo, coverage_repo / "absent.xml")
    assert result.returncode != 0
    assert "not applicable" not in result.stdout


def test_issue_compares_only_changes_after_ancestor_milestone(
    coverage_repo: Path,
) -> None:
    repo = coverage_repo
    git(repo, "switch", "feat/m16-sites-and-versions")
    xml = report(repo, "ontolib/src/guard.py", "50% (1/2)")
    commit(repo)
    git(repo, "switch", "-c", "fix/sites-465")
    (repo / "README.md").write_text("issue documentation\n")
    git(repo, "add", "README.md")
    # A local milestone that is NOT an ancestor must not cause ambiguity.
    git(repo, "switch", "-c", "feat/m17-future")
    commit(repo)
    git(repo, "switch", "fix/sites-465")

    result = run_gate(repo, xml)

    assert result.returncode == 0, result.stderr
    assert "base: feat/m16-sites-and-versions" in result.stdout
    assert "not applicable" in result.stdout
    assert "warning" not in result.stderr.lower()


@pytest.fixture
def milestone_remote(coverage_repo: Path, tmp_path_factory) -> tuple[Path, Path]:
    repo = coverage_repo
    remote = tmp_path_factory.mktemp("remote") / "origin.git"
    git(repo, "init", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "origin", "main")
    git(repo, "switch", "feat/m16-sites-and-versions")
    xml = report(repo, "ontolib/src/guard.py", "50% (1/2)")
    commit(repo)
    return repo, xml


def test_milestone_fetches_latest_origin_main(
    milestone_remote, tmp_path_factory
) -> None:
    repo, xml = milestone_remote
    # Advance main from another checkout, leaving this checkout's origin/main stale.
    publisher = tmp_path_factory.mktemp("publisher")
    git(publisher, "clone", str(repo), ".")
    git(
        publisher,
        "remote",
        "set-url",
        "origin",
        git(repo, "remote", "get-url", "origin"),
    )
    git(publisher, "push", "origin", "HEAD:main")
    assert git(repo, "rev-parse", "origin/main") != git(repo, "rev-parse", "HEAD")

    result = run_gate(repo, xml)

    assert result.returncode == 0, result.stderr
    assert "base: origin/main" in result.stdout
    assert "not applicable" in result.stdout
    assert git(repo, "rev-parse", "origin/main") == git(repo, "rev-parse", "HEAD")


def test_milestone_offline_warns_and_enforces_cached_base(milestone_remote) -> None:
    repo, xml = milestone_remote
    git(repo, "remote", "set-url", "origin", str(repo / "missing-remote.git"))

    result = run_gate(repo, xml)

    assert result.returncode == 1
    assert "base: origin/main" in result.stdout
    assert "50%" in result.stdout
    assert "warning" in result.stderr.lower()
    assert "fetch" in result.stderr.lower()
    assert "existing origin/main" in result.stderr


@pytest.mark.parametrize(
    "candidates", [(), ("feat/m16-sites-and-versions", "feat/m17-next")]
)
def test_issue_requires_unambiguous_milestone(coverage_repo: Path, candidates) -> None:
    repo = coverage_repo
    git(repo, "branch", "-d", "feat/m16-sites-and-versions")
    for candidate in candidates:
        git(repo, "branch", candidate)
    xml = report(repo, "ontolib/src/guard.py", "100% (2/2)")

    result = run_gate(repo, xml)

    assert result.returncode != 0
    assert "--compare-branch" in result.stderr
    for candidate in candidates or ("none",):
        assert candidate in result.stderr
    assert "not applicable" not in result.stdout


def test_explicit_base_overrides_milestone_default_without_fetch(
    milestone_remote,
) -> None:
    repo, xml = milestone_remote
    git(repo, "remote", "set-url", "origin", str(repo / "missing-remote.git"))

    result = run_gate(repo, xml, "--compare-branch", "main")

    assert result.returncode == 1
    assert "base: main" in result.stdout
    assert "50%" in result.stdout
    assert "warning" not in result.stderr.lower()


def test_explicit_base_overrides_missing_milestone(coverage_repo: Path) -> None:
    repo = coverage_repo
    git(repo, "branch", "-d", "feat/m16-sites-and-versions")
    xml = report(repo, "ontolib/src/guard.py", "100% (2/2)")

    result = run_gate(repo, xml, "--compare-branch", "main")

    assert result.returncode == 0, result.stderr
    assert "base: main" in result.stdout


def test_offline_without_cached_main_fails_loudly(coverage_repo: Path) -> None:
    repo = coverage_repo
    git(repo, "switch", "feat/m16-sites-and-versions")
    xml = report(repo, "ontolib/src/guard.py", "100% (2/2)")

    result = run_gate(repo, xml)

    assert result.returncode != 0
    assert "base: origin/main" in result.stdout
    assert "warning" in result.stderr.lower()
    assert "origin/main" in result.stderr
    assert "not applicable" not in result.stdout
