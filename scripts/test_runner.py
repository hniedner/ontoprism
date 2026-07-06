#!/usr/bin/env python3
"""Run the test suites by type and print a colored, grouped summary table.

A lightweight wrapper (not fairdata's heavy runner — see DECISIONS D9) that invokes
each sanctioned suite through its real command and renders one row per test *type*,
grouped Backend / Frontend, with a TOTAL. Exits non-zero if any suite failed.

    pdm run test-summary            # all types (unit/api/security/integration/e2e)
    pdm run test-summary --fast     # skip slow suites (live-store integration, e2e)

Coverage is a separate gated flow (`pdm run test-ci` / `test:coverage`), not shown here.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.table import Table

_REPO_ROOT = str(Path(__file__).resolve().parents[1])
# pytest exit 5 = "no tests collected" (e.g. a marker selected nothing) — not a failure.
_PYTEST_NO_TESTS = 5


@dataclass
class Suite:
    name: str
    group: str  # "Backend" | "Frontend"
    kind: str  # display label for the Type column
    runner: str  # "pytest" | "vitest" | "playwright"
    cmd: list[str]
    slow: bool = False  # skipped under --fast


@dataclass
class Result:
    suite: Suite
    passed: int
    failed: int
    errors: int
    skipped: int
    duration_s: float
    ok: bool


_PYTEST_COUNT = {
    "passed": re.compile(r"(\d+) passed"),
    "failed": re.compile(r"(\d+) failed"),
    "errors": re.compile(r"(\d+) errors?"),
    "skipped": re.compile(r"(\d+) skipped"),
}
_VITEST_TESTS = re.compile(
    r"Tests\s+(?:(\d+) failed \| )?(\d+) passed(?: \| (\d+) skipped)?"
)
# A raw per-test failure/error line (authoritative even if a merged summary is clean).
_RAW_FAILURE = re.compile(r"^(?:FAILED|ERROR) ", re.MULTILINE)
# ANSI SGR escapes — stripped before parsing (vitest/playwright emit color even piped).
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _count(pattern: re.Pattern[str], text: str) -> int:
    match = pattern.search(text)
    return int(match.group(1)) if match else 0


def parse_pytest(text: str) -> tuple[int, int, int, int]:
    """Return (passed, failed, errors, skipped) from pytest output."""
    return (
        _count(_PYTEST_COUNT["passed"], text),
        _count(_PYTEST_COUNT["failed"], text),
        _count(_PYTEST_COUNT["errors"], text),
        _count(_PYTEST_COUNT["skipped"], text),
    )


def parse_playwright(text: str) -> tuple[int, int, int, int]:
    """Return (passed, failed, errors, skipped) from Playwright output."""
    return (
        _count(_PYTEST_COUNT["passed"], text),
        _count(_PYTEST_COUNT["failed"], text),
        0,
        _count(_PYTEST_COUNT["skipped"], text),
    )


def parse_vitest(text: str) -> tuple[int, int, int, int]:
    """Return (passed, failed, errors, skipped) from vitest output (no errors tier)."""
    match = _VITEST_TESTS.search(text)
    if not match:
        return (0, 0, 0, 0)
    failed = int(match.group(1)) if match.group(1) else 0
    skipped = int(match.group(3)) if match.group(3) else 0
    return (int(match.group(2)), failed, 0, skipped)


_PARSERS = {
    "pytest": parse_pytest,
    "vitest": parse_vitest,
    "playwright": parse_playwright,
}


def _pytest(name: str, kind: str, marker: str, *, slow: bool = False) -> Suite:
    cmd = ["pytest", "ontolib/tests", "backend/tests", "-m", marker]
    return Suite(name, "Backend", kind, "pytest", cmd, slow=slow)


def suites(fast: bool) -> list[Suite]:
    """All suites (a subset when *fast*)."""
    all_suites = [
        _pytest("backend unit", "unit", "unit"),
        _pytest("backend api", "api", "api"),
        _pytest("backend security", "security", "security"),
        _pytest("backend integration", "integration", "integration", slow=True),
        Suite(
            "frontend vitest",
            "Frontend",
            "vitest",
            "vitest",
            ["npm", "--prefix", "frontend", "run", "test:unit", "--", "--run"],
        ),
        Suite(
            "frontend e2e",
            "Frontend",
            "playwright",
            "playwright",
            ["npm", "--prefix", "frontend", "run", "test:e2e"],
            slow=True,
        ),
    ]
    return [s for s in all_suites if not (fast and s.slow)]


def run_suite(suite: Suite) -> Result:
    start = time.perf_counter()
    # suite.cmd is a fixed, module-defined command list — never user/network input.
    proc = subprocess.run(  # noqa: S603
        suite.cmd, cwd=_REPO_ROOT, capture_output=True, text=True, check=False
    )
    duration = time.perf_counter() - start
    output = _ANSI.sub("", proc.stdout + proc.stderr)
    passed, failed, errors, skipped = _PARSERS[suite.runner](output)
    # Verdict: a good return code AND no failures/errors — and a raw FAILED/ERROR line
    # flips it red even if a merged summary somehow read clean (worker-drop guard).
    ok = (
        proc.returncode in (0, _PYTEST_NO_TESTS)
        and failed == 0
        and errors == 0
        and not _RAW_FAILURE.search(output)
    )
    return Result(suite, passed, failed, errors, skipped, duration, ok)


def _cell(value: int, color: str) -> str:
    return f"[{color}]{value}[/{color}]" if value else "0"


def _render(results: list[Result]) -> None:
    console = Console()
    table = Table(title="Test summary", header_style="bold")
    for col in ("Suite", "Type"):
        table.add_column(col)
    for col in ("Total", "Pass", "Fail", "Error", "Skip", "Time"):
        table.add_column(col, justify="right")
    table.add_column("Status")

    totals = [0, 0, 0, 0, 0]  # total, pass, fail, error, skip
    last_group = None
    for r in sorted(results, key=lambda x: (x.suite.group != "Backend", x.suite.name)):
        if r.suite.group != last_group:
            table.add_section()
            last_group = r.suite.group
        total = r.passed + r.failed + r.errors + r.skipped
        for i, v in enumerate((total, r.passed, r.failed, r.errors, r.skipped)):
            totals[i] += v
        table.add_row(
            f"[dim]{r.suite.group}[/dim] · {r.suite.name}",
            r.suite.kind,
            str(total),
            _cell(r.passed, "green"),
            _cell(r.failed, "red"),
            _cell(r.errors, "red"),
            _cell(r.skipped, "yellow"),
            f"{r.duration_s:.1f}s",
            "[green]PASS[/green]" if r.ok else "[red]FAIL[/red]",
        )
    table.add_section()
    table.add_row(
        "[bold]TOTAL[/bold]",
        "",
        f"[bold]{totals[0]}[/bold]",
        _cell(totals[1], "green"),
        _cell(totals[2], "red"),
        _cell(totals[3], "red"),
        _cell(totals[4], "yellow"),
        "",
        "",
    )
    console.print(table)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fast", action="store_true", help="skip the slow suites (integration, e2e)"
    )
    args = parser.parse_args()

    results = [run_suite(s) for s in suites(args.fast)]
    _render(results)
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
