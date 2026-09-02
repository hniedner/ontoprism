#!/usr/bin/env python3
"""Run authoritative gates against the operator-selected default Docker context."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal, Protocol

from .docker_selectors import DOCKER_SELECTOR_VARIABLES

_ROOT = Path(__file__).resolve().parents[2]


def _gates(pdm_executable: str) -> tuple[tuple[str, ...], ...]:
    return (
        (
            sys.executable,
            "scripts/validation/validate_opencode_config.py",
            "--root",
            ".",
        ),
        (sys.executable, "-m", "pre_commit", "run", "--all-files"),
        (pdm_executable, "run", "test-ci"),
        ("npm", "--prefix", "frontend", "run", "test:coverage"),
        (
            pdm_executable,
            "run",
            "python",
            "-m",
            "scripts.validation.frontend_coverage_hierarchy",
        ),
    )


class CommandRunner(Protocol):
    def __call__(
        self,
        arguments: list[str],
        *,
        check: Literal[False],
        cwd: Path,
        env: dict[str, str],
        shell: Literal[False],
        text: Literal[True],
    ) -> subprocess.CompletedProcess[str]: ...


def _subprocess_runner(
    arguments: list[str],
    *,
    check: Literal[False],
    cwd: Path,
    env: dict[str, str],
    shell: Literal[False],
    text: Literal[True],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        arguments,
        check=check,
        cwd=cwd,
        env=env,
        shell=shell,
        text=text,
    )


def run_verify(
    *,
    runner: CommandRunner = _subprocess_runner,
    pdm_executable: str | None = None,
) -> int:
    """Run fixed gates after removing Docker selectors to use the default context."""
    resolved_pdm = pdm_executable or shutil.which("pdm")
    if resolved_pdm is None:
        raise RuntimeError("pdm executable is required to run verification")
    environment = dict(os.environ)
    selectors = [
        variable for variable in DOCKER_SELECTOR_VARIABLES if variable in environment
    ]
    if selectors:
        print(
            "default-context verification ignores Docker selectors: "
            + ", ".join(selectors),
            file=sys.stderr,
        )
    for variable in DOCKER_SELECTOR_VARIABLES:
        environment.pop(variable, None)
    for command in _gates(resolved_pdm):
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
