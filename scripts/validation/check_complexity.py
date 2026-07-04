#!/usr/bin/env python3
"""Pre-commit hook to enforce cyclomatic complexity at assessment threshold (CC >= 8).

Uses radon to check cyclomatic complexity of Python functions/methods.
Any function with CC >= 8 is blocked unless listed in the baseline file.
The baseline is currently empty — all original violations have been refactored.

Threshold: CC >= 8 (matches Lizard ccn-medium from external code quality assessment).
Baseline: scripts/radon_cc8_baseline.txt
"""

import json
import subprocess
import sys
from pathlib import Path

THRESHOLD = 8
BASELINE_PATH = Path(__file__).parent / "radon_cc8_baseline.txt"
SCAN_DIRS = [
    ("ontolib/src", "ontolib"),
    ("backend/src", "backend"),
]


def load_baseline() -> set[str]:
    """Load grandfathered function signatures from baseline file."""
    if not BASELINE_PATH.exists():
        return set()
    return {
        line.strip()
        for line in BASELINE_PATH.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }


def run_radon(src_dir: str, package: str) -> dict:
    """Run radon CC analysis on a package directory."""
    import shutil  # noqa: PLC0415

    radon_path = shutil.which("radon")
    if not radon_path:
        return {}

    result = subprocess.run(  # noqa: S603
        [
            radon_path,
            "cc",
            "--min",
            "B",
            "--show-complexity",
            "--no-assert",
            "--json",
            package,
        ],
        capture_output=True,
        text=True,
        cwd=src_dir,
        check=False,
    )
    if not result.stdout.strip():
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


def check_complexity() -> list[str]:
    """Find new functions exceeding CC threshold that aren't in baseline."""
    baseline = load_baseline()
    violations = []

    for src_dir, package in SCAN_DIRS:
        if not Path(src_dir).exists():
            continue

        data = run_radon(src_dir, package)

        for filepath, blocks in sorted(data.items()):
            for block in blocks:
                cc = block.get("complexity", 0)
                if cc < THRESHOLD:
                    continue

                name = block.get("name", "")
                classname = block.get("classname", "")
                fullname = f"{classname}.{name}" if classname else name
                key = f"{filepath}:{fullname}"

                if key not in baseline:
                    lineno = block.get("lineno", 0)
                    violations.append(
                        f"  {src_dir}/{filepath}:{lineno} "
                        f"{fullname} (CC={cc}, threshold={THRESHOLD})"
                    )

    return violations


def main() -> int:
    """Check for new cyclomatic complexity violations and report them."""
    violations = check_complexity()

    if violations:
        print(f"Cyclomatic complexity violations (CC >= {THRESHOLD}):")
        print(f"  {len(violations)} new function(s) exceed threshold")
        print()
        for v in sorted(violations):
            print(v)
        print()
        print("To fix: reduce complexity by extracting helper functions,")
        print("simplifying conditionals, or using early returns.")
        print()
        print(
            f"Baseline: {BASELINE_PATH} ({len(load_baseline())} grandfathered entries)"
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
