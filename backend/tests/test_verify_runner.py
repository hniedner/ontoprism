"""Contracts for the shell-free authoritative verification runner."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
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
def test_verify_runner_clears_docker_selectors_and_runs_exact_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _Runner()
    monkeypatch.setattr(
        "scripts.validation.run_verify.os.environ",
        {
            "SAFE": "retained",
            "DOCKER_HOST": "unix:///stale.sock",
            "DOCKER_CONTEXT": "stale",
            "DOCKER_TLS_VERIFY": "1",
            "DOCKER_CERT_PATH": "/stale",
        },
    )

    assert run_verify(runner=runner) == 0

    assert [command for command, _options in runner.calls] == [
        ["/opt/homebrew/bin/pdm", "run", "validate-opencode-config"],
        [
            "/opt/homebrew/bin/pdm",
            "run",
            "pre-commit",
            "run",
            "--all-files",
        ],
        ["/opt/homebrew/bin/pdm", "run", "test-ci"],
        ["/opt/homebrew/bin/npm", "--prefix", "frontend", "run", "test:coverage"],
    ]
    for _command, options in runner.calls:
        assert options == {
            "check": False,
            "cwd": Path(__file__).resolve().parents[2],
            "env": {"SAFE": "retained"},
            "shell": False,
            "text": True,
        }


@pytest.mark.unit
def test_verify_runner_stops_at_first_failed_gate() -> None:
    runner = _Runner(fail_at=2)

    assert run_verify(runner=runner) == 1
    assert len(runner.calls) == 2
