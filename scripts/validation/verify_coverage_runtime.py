#!/usr/bin/env python3
"""Verify the standalone coverage-report runtime against the project lock."""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts.validation.coverage_hierarchy import verify_runtime_dependencies


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    args = parser.parse_args()
    installed = verify_runtime_dependencies(args.lock)
    for name, version in sorted(installed.items()):
        print(f"{name}=={version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
