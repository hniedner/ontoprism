#!/usr/bin/env python3
"""Reject unsupported interpreters for operational repository workflows."""

from __future__ import annotations

import sys
from pathlib import Path

_RUNTIME_SELECTOR = Path(__file__).resolve().parents[2] / ".python-version"
_VERSION_PART_COUNT = 3


def _required_runtime(selector: Path) -> tuple[int, int, int] | None:
    try:
        selector_text = selector.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        print(
            "Cannot validate OntoPrism's operational Python runtime: required "
            f"runtime selector {selector} is missing.",
            file=sys.stderr,
        )
        return None
    except OSError as error:
        print(
            "Cannot validate OntoPrism's operational Python runtime: required "
            f"runtime selector {selector} cannot be read: {error}.",
            file=sys.stderr,
        )
        return None

    parts = selector_text.split(".")
    if len(parts) != _VERSION_PART_COUNT or any(not part.isdecimal() for part in parts):
        print(
            "Cannot validate OntoPrism's operational Python runtime: required "
            f"runtime selector {selector} must contain exactly three dot-separated "
            f"integers; got {selector_text!r}.",
            file=sys.stderr,
        )
        return None
    return int(parts[0]), int(parts[1]), int(parts[2])


def main(
    version_info: tuple[int, ...] | None = None,
    selector: Path = _RUNTIME_SELECTOR,
) -> int:
    """Return success only for the repository's exact operational Python runtime."""
    selected = (
        sys.version_info[:_VERSION_PART_COUNT] if version_info is None else version_info
    )
    if len(selected) != _VERSION_PART_COUNT:
        print(
            "Cannot validate OntoPrism's operational Python runtime: executing "
            "interpreter version must contain exactly three integers; "
            f"got {selected!r}.",
            file=sys.stderr,
        )
        return 1

    required_runtime = _required_runtime(selector)
    if required_runtime is None:
        return 1
    if selected == required_runtime:
        return 0
    required_text = ".".join(map(str, required_runtime))
    actual_text = ".".join(map(str, selected))
    print(
        f"OntoPrism operational workflows require Python {required_text}; "
        f"executing interpreter is {actual_text}.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
