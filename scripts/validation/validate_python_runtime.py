#!/usr/bin/env python3
"""Reject unsupported interpreters for operational repository workflows."""

from __future__ import annotations

import sys

_REQUIRED_RUNTIME = (3, 14, 7)


def main(version_info: tuple[int, int, int] | None = None) -> int:
    """Return success only for the repository's exact operational Python runtime."""
    selected = version_info or sys.version_info[:3]
    actual = (int(selected[0]), int(selected[1]), int(selected[2]))
    if actual == _REQUIRED_RUNTIME:
        return 0
    required_text = ".".join(map(str, _REQUIRED_RUNTIME))
    actual_text = ".".join(map(str, actual))
    print(
        f"OntoPrism operational workflows require Python {required_text}; "
        f"executing interpreter is {actual_text}.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
