#!/usr/bin/env python3
"""Run the test suites by type and print a colored, grouped summary table.

The default `pdm run test` entry point (a lightweight wrapper, not fairdata's heavy
runner — see DECISIONS D9): it invokes each hermetic suite through its real command and
renders one row per test *type*, grouped Backend / Frontend, with a TOTAL, in the same
style as fairdata's runner. Exits non-zero if any suite failed.

    pdm run test            # hermetic suites: backend unit/api/security + frontend
    pdm run test --all      # also run the slow suites (live-store integration, e2e)

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
    slow: bool = False  # skipped unless --all


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


def _pytest(
    group: str, kind: str, marker: str, path: str, *, slow: bool = False
) -> Suite:
    cmd = ["pytest", path, "-m", marker]
    return Suite(f"{group} {kind}", group, kind, "pytest", cmd, slow=slow)


def suites(include_slow: bool) -> list[Suite]:
    """Hermetic suites by default; add integration + e2e when *include_slow*."""
    all_suites = [
        _pytest("ontolib", "unit", "unit", "ontolib/tests"),
        _pytest("ontolib", "integration", "integration", "ontolib/tests", slow=True),
        _pytest("backend", "unit", "unit", "backend/tests"),
        _pytest("backend", "api", "api", "backend/tests"),
        _pytest("backend", "security", "security", "backend/tests"),
        _pytest("backend", "integration", "integration", "backend/tests", slow=True),
        Suite(
            "frontend vitest",
            "frontend",
            "vitest",
            "vitest",
            ["npm", "--prefix", "frontend", "run", "test:unit", "--", "--run"],
        ),
        Suite(
            "frontend e2e",
            "frontend",
            "playwright",
            "playwright",
            ["npm", "--prefix", "frontend", "run", "test:e2e"],
            slow=True,
        ),
    ]
    return [s for s in all_suites if include_slow or not s.slow]


def run_suite(suite: Suite, console: Console) -> Result:
    """Run one suite, streaming its output live (so long suites show progress) while
    capturing it for the summary parse."""
    console.rule(f"[bold cyan]{suite.group} · {suite.kind}[/bold cyan]")
    start = time.perf_counter()
    # suite.cmd is a fixed, module-defined command list — never user/network input.
    proc = subprocess.Popen(  # noqa: S603
        suite.cmd,
        cwd=_REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    captured: list[str] = []
    # Popen(stdout=PIPE) always yields a stream; the guard keeps the type-checker happy.
    if proc.stdout is not None:
        for line in proc.stdout:
            sys.stdout.write(line)  # live echo — the developer sees tests progressing
            captured.append(line)
    sys.stdout.flush()
    returncode = proc.wait()
    duration = time.perf_counter() - start
    output = _ANSI.sub("", "".join(captured))
    passed, failed, errors, skipped = _PARSERS[suite.runner](output)
    # Verdict: a good return code AND no failures/errors — and a raw FAILED/ERROR line
    # flips it red even if a merged summary somehow read clean (worker-drop guard).
    ok = (
        returncode in (0, _PYTEST_NO_TESTS)
        and failed == 0
        and errors == 0
        and not _RAW_FAILURE.search(output)
    )
    return Result(suite, passed, failed, errors, skipped, duration, ok)


_NAME_W = 18
_COL_W = 7
# Column widths in order: name, then the six right-aligned metric columns.
_WIDTHS = (_NAME_W, _COL_W, _COL_W, _COL_W, _COL_W, _COL_W, _COL_W)
_TABLE_WIDTH = sum(_WIDTHS) + 3 * (len(_WIDTHS) - 1)
_HEADERS = ("Test Type", "Total", "Passed", "Failed", "Errors", "Skipped", "Time")
# Section order in the summary table (library first, then service, then UI).
_GROUP_ORDER = {"ontolib": 0, "backend": 1, "frontend": 2}


def _num(value: int, color: str | None = None) -> str:
    """A right-aligned metric cell, colored only when non-zero (padding preserved)."""
    cell = f"{value:>{_COL_W}}"
    return f"[{color}]{cell}[/{color}]" if value and color else cell


def _row(name: str, cells: list[str]) -> str:
    return " | ".join([f"{name:<{_NAME_W}}", *cells])


def _render(results: list[Result], total_duration: float) -> None:
    # Pin the width so the table lays out identically piped or in a terminal (never
    # soft-wrapped to the detected column count).
    console = Console(width=_TABLE_WIDTH)
    sep = "-+-".join("-" * w for w in _WIDTHS)

    console.print("=" * _TABLE_WIDTH)
    console.print("TEST RESULTS SUMMARY".center(_TABLE_WIDTH), style="bold")
    console.print("=" * _TABLE_WIDTH)
    console.print(
        _row(_HEADERS[0], [f"{h:>{_COL_W}}" for h in _HEADERS[1:]]), style="bold"
    )
    console.print(sep)

    totals = [0, 0, 0, 0, 0]  # total, pass, fail, error, skip
    last_group: str | None = None

    def _key(r: Result) -> tuple[int, str]:
        return (_GROUP_ORDER.get(r.suite.group, 99), r.suite.name)

    for r in sorted(results, key=_key):
        if r.suite.group != last_group:
            if last_group is not None:
                console.print(sep)
            console.print(r.suite.group.title(), style="bold blue")
            last_group = r.suite.group
        total = r.passed + r.failed + r.errors + r.skipped
        for i, v in enumerate((total, r.passed, r.failed, r.errors, r.skipped)):
            totals[i] += v
        cells = [
            _num(total),
            _num(r.passed, "green"),
            _num(r.failed, "red"),
            _num(r.errors, "red"),
            _num(r.skipped, "yellow"),
            f"{r.duration_s:>{_COL_W - 1}.1f}s",
        ]
        console.print(_row(f"  {r.suite.kind}", cells))

    console.print(sep)
    total_cells = [
        _num(totals[0]),
        _num(totals[1], "green"),
        _num(totals[2], "red"),
        _num(totals[3], "red"),
        _num(totals[4], "yellow"),
        f"{total_duration:>{_COL_W - 1}.1f}s",
    ]
    console.print(_row("TOTAL", total_cells), style="bold")
    console.print("=" * _TABLE_WIDTH)

    ok = totals[2] == 0 and totals[3] == 0
    console.print()
    if ok:
        console.print(
            f"✅ All tests passed! ({totals[1]} passed, {totals[4]} skipped "
            f"in {total_duration:.0f}s)",
            style="bold green",
        )
    else:
        console.print(
            f"❌ Tests failed! ({totals[2]} failed, {totals[3]} errors "
            f"in {total_duration:.0f}s)",
            style="bold red",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all",
        action="store_true",
        help="also run the slow suites (live-store integration, e2e)",
    )
    args = parser.parse_args()

    console = Console()
    start = time.perf_counter()
    results = [run_suite(s, console) for s in suites(args.all)]
    _render(results, time.perf_counter() - start)
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
