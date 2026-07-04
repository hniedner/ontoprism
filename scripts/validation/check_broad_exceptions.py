#!/usr/bin/env python3
"""Pre-commit hook to check for broad exception SWALLOWING.

This script identifies problematic exception handling patterns:
- Bare except Exception without re-raising
- Broad exception catches that swallow errors
- Missing error propagation in internal methods

Legitimate uses (allowed):
- API boundary error handlers with logging
- External service calls with specific recovery
- Exception catch-and-re-raise with added context

Scope is deliberately narrow — **swallow detection only** (does a broad handler
re-raise or log?). NOT redundant with the sibling exception linters (#839, pinned
by ``backend/tests/unit/scripts/test_exception_linters_distinct.py``):
``validate_exception_handlers.py`` checks handler ORDERING (interrupts before
broad Exception); ``lint/no_exception_interpolation_in_http_error.py`` (FDW001)
checks raw exception text in response bodies. Neither is a superset of another.
"""

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple


class Violation(NamedTuple):
    """Exception handling violation."""

    file: Path
    line_num: int
    line_text: str
    category: str


# Patterns for different exception types
BROAD_EXCEPTION_PATTERN = re.compile(r"^\s*except Exception(\s+as\s+\w+)?:\s*$")
BARE_EXCEPT_PATTERN = re.compile(r"^\s*except:\s*$")
RAISE_PATTERN = re.compile(
    r"^\s*raise(\s|$)", re.MULTILINE
)  # Match bare raise or raise with args
NORETURN_METHOD_PATTERN = re.compile(r"^\s*\w+\._handle_\w+_error\(", re.MULTILINE)
LOGGER_PATTERN = re.compile(r"logger\.(error|warning|critical|exception)")
RETURN_PATTERN = re.compile(r"^\s*return\s+", re.MULTILINE)


def _extract_handler_lines(file_lines: list[str], exception_line_idx: int) -> list[str]:
    """Extract lines of an exception handler block (until dedent or 10 lines)."""
    handler_lines = []
    base_indent = len(file_lines[exception_line_idx]) - len(
        file_lines[exception_line_idx].lstrip()
    )
    for i in range(
        exception_line_idx + 1, min(exception_line_idx + 11, len(file_lines))
    ):
        line = file_lines[i]
        if line.strip() and not line.startswith(" " * (base_indent + 1)):
            break
        handler_lines.append(line)
    return handler_lines


def _has_fallback_comment(handler_lines: list[str]) -> bool:
    """Return True if any handler line mentions a fallback."""
    return any(
        "fallback" in line.lower() or "fall back" in line.lower()
        for line in handler_lines
    )


_EXPLANATION_KEYWORDS = (
    "intentional",
    "catch-all",
    "boundary",
    "unexpected",
    "system-level",
)


def _has_explanation_comment(handler_lines: list[str]) -> bool:
    """Return True if the first 3 handler lines contain an explanatory comment."""
    for line in handler_lines[:3]:
        if "#" in line and any(kw in line.lower() for kw in _EXPLANATION_KEYWORDS):
            return True
    return False


def is_allowed_broad_exception(
    file_lines: list[str], exception_line_idx: int, file_path: Path | None = None
) -> tuple[bool, str]:
    """Check if a broad exception pattern is allowed.

    Args:
        file_lines: All lines in the file
        exception_line_idx: Index of the line with 'except Exception:'
        file_path: Optional path to the file being checked (for debugging)

    Returns:
        Tuple of (is_allowed, reason)
    """
    handler_lines = _extract_handler_lines(file_lines, exception_line_idx)
    handler_text = "\n".join(handler_lines)

    if RAISE_PATTERN.search(handler_text):
        return True, "re-raises exception"
    if NORETURN_METHOD_PATTERN.search(handler_text):
        return True, "calls error handler method"
    if LOGGER_PATTERN.search(handler_text):
        return True, "logs error (API boundary)"
    if _has_fallback_comment(handler_lines):
        return True, "documented fallback"
    if _has_explanation_comment(handler_lines):
        return True, "documented exception"
    return False, "swallows exception silently"


def check_file(file_path: Path) -> list[Violation]:
    """Check a single Python file for broad exception violations.

    Args:
        file_path: Path to the Python file

    Returns:
        List of violations found
    """
    try:
        content = file_path.read_text(encoding="utf-8")
        lines = content.splitlines()
    except Exception as e:
        print(f"Warning: Could not read {file_path}: {e}", file=sys.stderr)
        return []

    violations = []

    for i, line in enumerate(lines):
        line_num = i + 1

        # Check for bare except (always bad)
        if BARE_EXCEPT_PATTERN.match(line):
            violations.append(
                Violation(
                    file=file_path,
                    line_num=line_num,
                    line_text=line.strip(),
                    category="bare_except",
                )
            )
            continue

        # Check for broad except Exception
        if BROAD_EXCEPTION_PATTERN.match(line):
            is_allowed, reason = is_allowed_broad_exception(lines, i, file_path)
            if not is_allowed:
                violations.append(
                    Violation(
                        file=file_path,
                        line_num=line_num,
                        line_text=line.strip(),
                        category=f"broad_exception ({reason})",
                    )
                )

    return violations


def main() -> int:
    """Main entry point for the check script.

    Returns:
        Exit code (0 for success, 1 for violations found)
    """
    parser = argparse.ArgumentParser(
        description="Check for problematic broad exception patterns"
    )
    parser.add_argument("files", nargs="*", help="Files to check")
    parser.add_argument(
        "--all", action="store_true", help="Check all Python files in project"
    )
    args = parser.parse_args()

    if args.all:
        # Check all Python files in src directories
        root = Path(__file__).parent.parent.parent
        files = list(root.glob("backend/src/**/*.py")) + list(
            root.glob("ontolib/src/**/*.py")
        )
    else:
        files = [Path(f) for f in args.files]

    all_violations = []
    for file_path in files:
        if file_path.suffix != ".py":
            continue
        violations = check_file(file_path)
        all_violations.extend(violations)

    if all_violations:
        print("\n❌ Found broad exception violations:\n")
        for v in all_violations:
            print(f"{v.file}:{v.line_num}: {v.category}")
            print(f"  {v.line_text}\n")
        print(f"Total: {len(all_violations)} violations")
        return 1

    # No violations - exit silently (pre-commit will show "Passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
