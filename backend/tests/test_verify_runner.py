"""Contracts for the shell-free authoritative verification runner."""

from __future__ import annotations

import inspect
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from scripts.validation.docker_selectors import DOCKER_SELECTOR_VARIABLES
from scripts.validation.run_verify import run_verify
from scripts.validation.validate_python_runtime import main as validate_python_runtime


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
def test_verify_runner_docstring_describes_default_context_selection() -> None:
    assert inspect.getdoc(run_verify) == (
        "Run fixed gates after removing Docker selectors to use the default context."
    )


@pytest.mark.unit
def test_verify_runner_uses_portable_tools_and_runs_exact_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdm_executable = "/test/bin/pdm"
    runner = _Runner()
    monkeypatch.setattr(
        "scripts.validation.run_verify.os.environ",
        {
            "SAFE": "retained",
            "DOCKER_HOST": "unix:///deliberate.sock",
        },
    )

    assert run_verify(runner=runner, pdm_executable=pdm_executable) == 0

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
        [
            pdm_executable,
            "run",
            "python",
            "-m",
            "scripts.validation.frontend_coverage_hierarchy",
        ],
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
    assert scripts["verify"] == {
        "composite": [
            "validate-python-runtime",
            "python -m scripts.validation.run_verify",
        ]
    }
    assert scripts["validate-python-runtime"] == (
        "python -m scripts.validation.validate_python_runtime"
    )
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

    assert run_verify(runner=runner, pdm_executable="/test/bin/pdm") == 0
    assert len(runner.calls) == 5
    assert capsys.readouterr().err == (
        "default-context verification ignores Docker selectors: DOCKER_HOST\n"
    )


@pytest.mark.unit
@pytest.mark.parametrize(("fail_at", "expected_calls"), [(2, 2), (5, 5)])
def test_verify_runner_stops_at_first_failed_gate_including_hierarchy_report(
    monkeypatch: pytest.MonkeyPatch,
    fail_at: int,
    expected_calls: int,
) -> None:
    runner = _Runner(fail_at=fail_at)
    for selector in DOCKER_SELECTOR_VARIABLES:
        monkeypatch.delenv(selector, raising=False)

    assert run_verify(runner=runner, pdm_executable="/test/bin/pdm") == 1
    assert len(runner.calls) == expected_calls


@pytest.mark.unit
def test_verify_runner_discovers_pdm_only_when_verification_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.validation.run_verify.shutil.which", lambda _name: None
    )

    with pytest.raises(RuntimeError, match="pdm executable"):
        run_verify(runner=_Runner())


@pytest.mark.unit
def test_operational_runtime_validator_accepts_only_python_3147(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert validate_python_runtime((3, 14, 7)) == 0
    assert capsys.readouterr().err == ""

    assert validate_python_runtime((3, 14, 6)) == 1
    assert capsys.readouterr().err == (
        "OntoPrism operational workflows require Python 3.14.7; "
        "executing interpreter is 3.14.6.\n"
    )
