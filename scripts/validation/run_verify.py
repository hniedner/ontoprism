#!/usr/bin/env python3
"""Run authoritative gates against the operator-selected default Docker context."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Literal, Protocol

from .docker_selectors import DOCKER_SELECTOR_VARIABLES

_ROOT = Path(__file__).resolve().parents[2]
_ENVIRONMENT_BIN = Path(sys.executable).parent
_GATES = (
    (
        (
            sys.executable,
            "scripts/validation/validate_opencode_config.py",
            "--root",
            ".",
        ),
        None,
    ),
    ((sys.executable, "-m", "pre_commit", "run", "--all-files"), None),
    (
        (
            str(_ENVIRONMENT_BIN / "pytest"),
            "ontolib/tests",
            "backend/tests",
            "-m",
            "not integration",
            "--cov",
            "--cov-report=term-missing",
            "--cov-report=xml",
            "-n",
            "auto",
        ),
        ".coverage.unit",
    ),
    (
        (
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
        ),
        ".coverage.integration",
    ),
    (
        (
            str(_ENVIRONMENT_BIN / "coverage"),
            "combine",
            ".coverage.unit",
            ".coverage.integration",
        ),
        None,
    ),
    (
        (
            sys.executable,
            "scripts/validation/strict_coverage_gate.py",
            "python",
            "--coverage-data",
            ".coverage",
            "--root",
            ".",
        ),
        None,
    ),
    (("npm", "--prefix", "frontend", "run", "test:coverage"), None),
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


def run_verify(*, runner: CommandRunner = _subprocess_runner) -> int:
    """Run fixed gates only when Docker will use its selected default context."""
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
    for command, coverage_file in _GATES:
        gate_environment = dict(environment)
        if coverage_file is not None:
            gate_environment["COVERAGE_FILE"] = coverage_file
        result = runner(
            list(command),
            check=False,
            cwd=_ROOT,
            env=gate_environment,
            shell=False,
            text=True,
        )
        if result.returncode != 0:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(run_verify())
