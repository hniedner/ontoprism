#!/usr/bin/env python3
"""Run the authoritative gates without shell parsing or Docker selector overrides."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Protocol

_ROOT = Path(__file__).resolve().parents[2]
_PDM = "/opt/homebrew/bin/pdm"
_NPM = "/opt/homebrew/bin/npm"
_DOCKER_SELECTOR_VARIABLES = (
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_TLS_VERIFY",
    "DOCKER_CERT_PATH",
    "PODMAN_COMPOSE_PROVIDER",
    "CONTAINER_HOST",
)
_GATES = (
    (_PDM, "run", "validate-opencode-config"),
    (_PDM, "run", "pre-commit", "run", "--all-files"),
    (_PDM, "run", "test-ci"),
    (_NPM, "--prefix", "frontend", "run", "test:coverage"),
)


class CommandRunner(Protocol):
    def __call__(
        self,
        arguments: list[str],
        *,
        check: bool,
        cwd: Path,
        env: dict[str, str],
        shell: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]: ...


def _subprocess_runner(
    arguments: list[str],
    *,
    check: bool,
    cwd: Path,
    env: dict[str, str],
    shell: bool,
    text: bool,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        arguments,
        check=check,
        cwd=cwd,
        env=env,
        shell=shell,
        text=text,
    )


def run_verify(*, runner: CommandRunner = _subprocess_runner) -> int:
    """Run each fixed gate, routing Docker through the selected Docker context."""
    environment = dict(os.environ)
    for variable in _DOCKER_SELECTOR_VARIABLES:
        environment.pop(variable, None)
    for command in _GATES:
        result = runner(
            list(command),
            check=False,
            cwd=_ROOT,
            env=environment,
            shell=False,
            text=True,
        )
        if result.returncode != 0:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(run_verify())
