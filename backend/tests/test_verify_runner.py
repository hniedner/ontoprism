"""Contracts for the shell-free authoritative verification runner."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
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
    pdm_executable = shutil.which("pdm")
    assert pdm_executable is not None
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
        [pdm_executable, "run", "test-ci"],
        ["npm", "--prefix", "frontend", "run", "test:coverage"],
    ]
    for _command, options in runner.calls:
        assert options == {
            "check": False,
            "cwd": Path(__file__).resolve().parents[2],
            "env": {"SAFE": "retained"},
            "shell": False,
            "text": True,
        }

    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    scripts = pyproject["tool"]["pdm"]["scripts"]
    assert scripts["verify"] == "python -m scripts.validation.run_verify"
    assert "test-ci" in scripts
    assert "pdm run verify" not in scripts["test-ci"]["shell"]


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
    assert len(runner.calls) == 4
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
