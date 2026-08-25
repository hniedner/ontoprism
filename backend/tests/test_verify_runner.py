"""Contracts for the shell-free authoritative verification runner."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from scripts.validation.docker_selectors import DOCKER_SELECTOR_VARIABLES
from scripts.validation.run_verify import run_verify


class _Runner:
    def __init__(self, *, fail_at: int | None = None) -> None:
        self.fail_at = fail_at
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(
        self, arguments: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((arguments, kwargs))
        return subprocess.CompletedProcess(
            arguments,
            1 if self.fail_at == len(self.calls) else 0,
        )


@pytest.mark.unit
def test_verify_runner_uses_portable_tools_and_runs_exact_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _Runner()
    monkeypatch.setattr(
        "scripts.validation.run_verify.os.environ",
        {
            "SAFE": "retained",
            "DOCKER_HOST": "unix:///deliberate.sock",
        },
    )

    assert run_verify(runner=runner) == 0

    assert [command for command, _options in runner.calls] == [
        [
            sys.executable,
            "scripts/validation/validate_opencode_config.py",
            "--root",
            ".",
        ],
        [sys.executable, "-m", "pre_commit", "run", "--all-files"],
        [
            str(Path(sys.executable).parent / "pytest"),
            "ontolib/tests",
            "backend/tests",
            "-m",
            "not integration",
            "--cov",
            "--cov-report=term-missing",
            "--cov-report=xml",
            "-n",
            "auto",
        ],
        [
            sys.executable,
            "scripts/run_safe_integration.py",
            "ontolib/tests",
            "backend/tests",
            "-m",
            "integration and not full_store and not full_build and not slow",
            "--cov=ontolib/src",
            "--cov=backend/src",
            "--cov-branch",
            "--cov-report=",
        ],
        [
            str(Path(sys.executable).parent / "coverage"),
            "combine",
            ".coverage.unit",
            ".coverage.integration",
        ],
        [
            sys.executable,
            "scripts/validation/strict_coverage_gate.py",
            "python",
            "--coverage-data",
            ".coverage",
            "--root",
            ".",
        ],
        ["npm", "--prefix", "frontend", "run", "test:coverage"],
    ]
    expected_coverage_files = (
        None,
        None,
        ".coverage.unit",
        ".coverage.integration",
        None,
        None,
        None,
    )
    for (_command, options), coverage_file in zip(
        runner.calls, expected_coverage_files, strict=True
    ):
        expected_environment = {"SAFE": "retained"}
        if coverage_file is not None:
            expected_environment["COVERAGE_FILE"] = coverage_file
        assert options == {
            "check": False,
            "cwd": Path(__file__).resolve().parents[2],
            "env": expected_environment,
            "shell": False,
            "text": True,
        }


@pytest.mark.unit
def test_verify_runner_reports_ignored_docker_selector_overrides(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _Runner()
    monkeypatch.setattr(
        "scripts.validation.run_verify.os.environ",
        {"DOCKER_HOST": "unix:///deliberate.sock"},
    )

    assert run_verify(runner=runner) == 0
    assert len(runner.calls) == 7
    assert capsys.readouterr().err == (
        "default-context verification ignores Docker selectors: DOCKER_HOST\n"
    )


@pytest.mark.unit
def test_verify_runner_stops_at_first_failed_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _Runner(fail_at=2)
    for selector in DOCKER_SELECTOR_VARIABLES:
        monkeypatch.delenv(selector, raising=False)

    assert run_verify(runner=runner) == 1
    assert len(runner.calls) == 2
