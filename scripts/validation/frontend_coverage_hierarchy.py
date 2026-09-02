#!/usr/bin/env python3
"""Render the fixed frontend hierarchy report with installed Vitest metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from scripts.validation.coverage_hierarchy import main as coverage_hierarchy_main

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


_ROOT = Path(__file__).resolve().parents[2]


def run_frontend_hierarchy(
    *,
    root: Path = _ROOT,
    hierarchy_main: Callable[[Sequence[str] | None], int] = coverage_hierarchy_main,
) -> int:
    """Render from fixed project paths using the installed Vitest version."""
    package_path = root / "frontend/node_modules/vitest/package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    if not isinstance(package, dict):
        raise ValueError("Vitest package metadata must be a JSON object")
    version = package.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("Vitest package metadata has no string version")
    return hierarchy_main(
        [
            "--root",
            str(root),
            "frontend-report",
            "--coverage-json",
            str(root / "frontend/coverage/coverage-final.json"),
            "--tool-version",
            version,
            "--output",
            str(root / "frontend/coverage/hierarchy.json"),
            "--text-output",
            str(root / "frontend/coverage/hierarchy.txt"),
        ]
    )


if __name__ == "__main__":
    raise SystemExit(run_frontend_hierarchy())
