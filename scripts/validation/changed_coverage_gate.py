"""Apply diff-cover's native branch-aware changed-line gate to Python production."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def _git(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — fixed git operations, no shell
        ["git", *arguments],  # noqa: S607 — git is required on PATH
        capture_output=True,
        text=True,
        check=False,
    )


def comparison_base(explicit: str | None) -> str:
    """Resolve an explicit base, main for milestones, or a unique milestone tip."""
    if explicit is not None:
        return explicit
    current = _git("rev-parse", "--abbrev-ref", "HEAD")
    if current.returncode:
        raise ValueError(f"Cannot identify current branch: {current.stderr.strip()}")
    if re.fullmatch(r"feat/m[0-9]+-.+", current.stdout.strip()):
        fetched = _git("fetch", "origin", "main")
        if fetched.returncode:
            print(
                "Warning: git fetch origin main failed; using existing origin/main "
                f"ref. {fetched.stderr.strip()}",
                file=sys.stderr,
            )
        return "origin/main"
    branches = _git(
        "for-each-ref",
        "--merged=HEAD",
        "--format=%(refname:short)",
        "refs/heads/feat/m*",
    )
    if branches.returncode:
        raise ValueError(f"Cannot list milestone candidates: {branches.stderr.strip()}")
    candidates = branches.stdout.splitlines()
    if len(candidates) != 1:
        raise ValueError(
            "Expected one local ancestor milestone branch; candidates: "
            f"{', '.join(candidates) or '(none)'}. Specify --compare-branch."
        )
    return candidates[0]


def gate(coverage_xml: Path, compare_branch: str) -> int:
    """Delegate scoring and threshold enforcement; label empty reports explicitly."""
    with tempfile.TemporaryDirectory(prefix="ontoprism-diff-cover-") as directory:
        report = Path(directory) / "report.json"
        result = subprocess.run(  # noqa: S603 — fixed tool, paths passed as arguments
            [
                sys.executable,
                "-m",
                "diff_cover.diff_cover_tool",
                str(coverage_xml),
                "--compare-branch",
                compare_branch,
                "--branch-coverage",
                "--fail-under=95",
                "--include",
                "ontolib/src/**",
                "backend/src/**",
                "--format",
                f"json:{report}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and report.is_file():
            summary = json.loads(report.read_text())
            if summary["total_num_lines"] == 0:
                print(
                    "Changed Python production coverage: not applicable "
                    "(no measured changed lines)"
                )
                return 0
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-xml", type=Path)
    parser.add_argument("--coverage-data", type=Path, default=Path(".coverage"))
    parser.add_argument("--compare-branch")
    args = parser.parse_args()
    try:
        base = comparison_base(args.compare_branch)
    except ValueError as error:
        parser.error(str(error))
    print(f"Changed coverage base: {base}", flush=True)
    if args.coverage_xml is not None:
        return gate(args.coverage_xml, base)
    # CI combines its four partitions before XML generation: partially covered
    # branches from separate reports cannot be unioned correctly by diff-cover.
    from coverage import Coverage  # noqa: PLC0415

    if not args.coverage_data.is_file():
        parser.error(f"coverage data missing: {args.coverage_data}")
    with tempfile.TemporaryDirectory(prefix="ontoprism-changed-coverage-") as directory:
        xml = Path(directory) / "coverage.xml"
        coverage = Coverage(data_file=str(args.coverage_data))
        coverage.load()
        coverage.xml_report(outfile=str(xml))
        return gate(xml, base)


if __name__ == "__main__":
    raise SystemExit(main())
