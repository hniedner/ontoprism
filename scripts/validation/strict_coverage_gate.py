#!/usr/bin/env python3
"""Enforce independent strict aggregate line and branch coverage floors."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

STRICT_FLOOR_PERCENT = 90
REPO_ROOT = Path(__file__).resolve().parents[2]


class CoverageGateError(ValueError):
    """Coverage evidence is missing, malformed, or below the strict floor."""


@dataclass(frozen=True, slots=True)
class AggregateCoverage:
    """Independent aggregate line and branch counts for one production scope."""

    scope: str
    covered_lines: int
    total_lines: int
    covered_branches: int
    total_branches: int


def _validate_counts(aggregate: AggregateCoverage) -> None:
    for label, covered, total in (
        ("lines", aggregate.covered_lines, aggregate.total_lines),
        ("branches", aggregate.covered_branches, aggregate.total_branches),
    ):
        if total <= 0:
            raise CoverageGateError(f"{aggregate.scope} has no measurable {label}")
        if covered < 0:
            raise CoverageGateError(f"{aggregate.scope} has negative covered {label}")
        if covered > total:
            raise CoverageGateError(
                f"{aggregate.scope} covered {label} exceeds total {label}"
            )


def _metric_text(covered: int, total: int) -> str:
    return f"{covered * 100.0 / total:.2f}% ({covered}/{total})"


def enforce_strict_coverage(
    aggregates: Sequence[AggregateCoverage],
) -> str:
    """Return an independent-metric report or raise when either metric is <= 90%."""
    if not aggregates:
        raise CoverageGateError("no aggregate coverage scopes were provided")
    report: list[str] = []
    failures: list[str] = []
    for aggregate in aggregates:
        _validate_counts(aggregate)
        line_text = _metric_text(aggregate.covered_lines, aggregate.total_lines)
        branch_text = _metric_text(aggregate.covered_branches, aggregate.total_branches)
        line_passes = (
            aggregate.covered_lines * 100 > STRICT_FLOOR_PERCENT * aggregate.total_lines
        )
        branch_passes = (
            aggregate.covered_branches * 100
            > STRICT_FLOOR_PERCENT * aggregate.total_branches
        )
        status = "PASS (> 90%)" if line_passes and branch_passes else "FAIL"
        report.append(
            f"{aggregate.scope}: lines {line_text}; branches {branch_text}; {status}"
        )
        if not line_passes:
            failures.append(f"{aggregate.scope}: lines {line_text} must be > 90%")
        if not branch_passes:
            failures.append(f"{aggregate.scope}: branches {branch_text} must be > 90%")
    if failures:
        raise CoverageGateError("\n".join(failures))
    return "\n".join(report)


def _summary_integer(
    summary: Mapping[str, object],
    key: str,
    *,
    context: str,
) -> int:
    value = summary.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise CoverageGateError(
            f"{context} coverage summary is missing integer {key!r}"
        )
    return value


def coverage_py_aggregate(
    raw: Mapping[str, object],
    *,
    scope: str,
) -> AggregateCoverage:
    """Parse one Coverage.py JSON total, rejecting unknown result shapes."""
    totals = raw.get("totals")
    if not isinstance(totals, Mapping):
        raise CoverageGateError(f"{scope} coverage summary is missing totals")
    return AggregateCoverage(
        scope=scope,
        covered_lines=_summary_integer(
            totals, "covered_lines", context=f"{scope} line"
        ),
        total_lines=_summary_integer(totals, "num_statements", context=f"{scope} line"),
        covered_branches=_summary_integer(
            totals, "covered_branches", context=f"{scope} branch"
        ),
        total_branches=_summary_integer(
            totals, "num_branches", context=f"{scope} branch"
        ),
    )


def python_scope_coverage(
    data_file: Path,
    *,
    root: Path,
    scope: str,
) -> AggregateCoverage:
    """Read native Coverage.py totals for exactly one source directory."""
    from coverage import Coverage  # noqa: PLC0415

    data_file = data_file.resolve()
    root = root.resolve()
    if not data_file.is_file():
        raise CoverageGateError(f"coverage data file does not exist: {data_file}")
    config = root / "pyproject.toml"
    coverage = Coverage(
        data_file=str(data_file),
        config_file=str(config) if config.is_file() else False,
    )
    coverage.load()
    scope_root = root / scope
    source_paths = sorted(scope_root.rglob("*.py")) if scope_root.is_dir() else []
    if not source_paths:
        raise CoverageGateError(
            f"coverage scope has no Python source files: {scope_root}"
        )
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "coverage.json"
        coverage.json_report(
            morfs=[str(path) for path in source_paths],
            outfile=str(output),
            pretty_print=True,
        )
        raw = json.loads(output.read_text(encoding="utf-8"))
    return coverage_py_aggregate(raw, scope=scope)


def frontend_scope_coverage(summary_file: Path) -> AggregateCoverage:
    """Read Vitest/Istanbul aggregate totals for the configured frontend library."""
    if not summary_file.is_file():
        raise CoverageGateError(
            f"coverage summary does not exist: {summary_file.resolve()}"
        )
    try:
        raw = json.loads(summary_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CoverageGateError("frontend coverage summary is invalid JSON") from error
    if not isinstance(raw, Mapping):
        raise CoverageGateError("frontend coverage summary is not an object")
    total = raw.get("total")
    if not isinstance(total, Mapping):
        raise CoverageGateError("frontend coverage summary is missing total")
    lines = total.get("lines")
    branches = total.get("branches")
    if not isinstance(lines, Mapping) or not isinstance(branches, Mapping):
        raise CoverageGateError(
            "frontend coverage summary is missing line or branch totals"
        )
    return AggregateCoverage(
        scope="frontend/src/lib",
        covered_lines=_summary_integer(lines, "covered", context="frontend line"),
        total_lines=_summary_integer(lines, "total", context="frontend line"),
        covered_branches=_summary_integer(
            branches, "covered", context="frontend branch"
        ),
        total_branches=_summary_integer(branches, "total", context="frontend branch"),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    python = subparsers.add_parser("python")
    python.add_argument("--coverage-data", type=Path, default=Path(".coverage"))
    python.add_argument("--root", type=Path, default=REPO_ROOT)
    frontend = subparsers.add_parser("frontend")
    frontend.add_argument(
        "--coverage-summary",
        type=Path,
        default=Path("coverage/coverage-summary.json"),
    )
    return parser


def _python_aggregates(args: argparse.Namespace) -> tuple[AggregateCoverage, ...]:
    return tuple(
        python_scope_coverage(
            args.coverage_data,
            root=args.root,
            scope=scope,
        )
        for scope in ("ontolib/src", "backend/src")
    )


def _frontend_aggregates(args: argparse.Namespace) -> tuple[AggregateCoverage, ...]:
    return (frontend_scope_coverage(args.coverage_summary),)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        handlers = {
            "python": _python_aggregates,
            "frontend": _frontend_aggregates,
        }
        aggregates = handlers[args.command](args)
        print(enforce_strict_coverage(aggregates))
    except (CoverageGateError, OSError) as error:
        print(f"strict coverage gate failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
