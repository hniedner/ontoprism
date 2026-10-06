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


@pytest.fixture
def coverage_repo(tmp_path: Path) -> Path:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-b", "main")
    (tmp_path / "README.md").write_text("baseline\n")
    git("add", "README.md")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "baseline",
    )
    git("switch", "-c", "feature")
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


def run_gate(repo: Path, xml: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--coverage-xml", str(xml)],
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
