#!/usr/bin/env python3
"""Run the test suites by type and print a colored summary table.

A lightweight wrapper (not fairdata's heavy runner — see DECISIONS D9) over the
sanctioned suites: it invokes each through its real command and renders one row per
suite (pass/fail/skip counts + timing). Exits non-zero if any suite failed.

    pdm run test-summary            # unit/api + integration + frontend
    pdm run test-summary --fast     # skip the slow live-store integration suite
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

_REPO_ROOT = __file__.rsplit("/scripts/", 1)[0]


@dataclass
class Suite:
    name: str
    kind: str
    cmd: list[str]


@dataclass
class Result:
    suite: Suite
    passed: int
    failed: int
    skipped: int
    duration_s: float
    ok: bool


_PYTEST_COUNT = {
    "passed": re.compile(r"(\d+) passed"),
    "failed": re.compile(r"(\d+) failed"),
    "skipped": re.compile(r"(\d+) skipped"),
}
_VITEST_TESTS = re.compile(r"Tests\s+(?:(\d+) failed \| )?(\d+) passed")


def _count(pattern: re.Pattern[str], text: str) -> int:
    match = pattern.search(text)
    return int(match.group(1)) if match else 0


def parse_pytest(text: str) -> tuple[int, int, int]:
    """Return (passed, failed, skipped) from pytest output."""
    return (
        _count(_PYTEST_COUNT["passed"], text),
        _count(_PYTEST_COUNT["failed"], text),
        _count(_PYTEST_COUNT["skipped"], text),
    )


def parse_vitest(text: str) -> tuple[int, int, int]:
    """Return (passed, failed, skipped) from vitest output."""
    match = _VITEST_TESTS.search(text)
    if not match:
        return (0, 0, 0)
    failed = int(match.group(1)) if match.group(1) else 0
    return (int(match.group(2)), failed, 0)


def _suites(fast: bool) -> list[Suite]:
    # No extra -q: pyproject addopts already sets it, and -qq drops the summary line.
    pytest_unit = ["pytest", "ontolib/tests", "backend/tests", "-m", "not integration"]
    suites = [Suite("backend unit/api", "unit", pytest_unit)]
    if not fast:
        suites.append(
            Suite(
                "integration",
                "integration",
                ["pytest", "ontolib/tests", "backend/tests", "-m", "integration"],
            )
        )
    suites.append(
        Suite(
            "frontend vitest",
            "frontend",
            ["npm", "--prefix", "frontend", "run", "test:unit", "--", "--run"],
        )
    )
    return suites


def run_suite(suite: Suite) -> Result:
    start = time.perf_counter()
    # suite.cmd is a fixed, module-defined command list — never user/network input.
    proc = subprocess.run(  # noqa: S603
        suite.cmd, cwd=_REPO_ROOT, capture_output=True, text=True, check=False
    )
    duration = time.perf_counter() - start
    output = proc.stdout + proc.stderr
    parser = parse_vitest if suite.kind == "frontend" else parse_pytest
    passed, failed, skipped = parser(output)
    return Result(suite, passed, failed, skipped, duration, proc.returncode == 0)


def _render(results: list[Result]) -> None:
    console = Console()
    table = Table(title="Test summary", header_style="bold")
    table.add_column("Suite")
    table.add_column("Type")
    table.add_column("Pass", justify="right")
    table.add_column("Fail", justify="right")
    table.add_column("Skip", justify="right")
    table.add_column("Time", justify="right")
    table.add_column("Status")
    for r in results:
        status = "[green]PASS[/green]" if r.ok else "[red]FAIL[/red]"
        table.add_row(
            r.suite.name,
            r.suite.kind,
            str(r.passed),
            f"[red]{r.failed}[/red]" if r.failed else "0",
            str(r.skipped),
            f"{r.duration_s:.1f}s",
            status,
        )
    console.print(table)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fast", action="store_true", help="skip the live-store integration suite"
    )
    args = parser.parse_args()

    results = [run_suite(s) for s in _suites(args.fast)]
    _render(results)
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
