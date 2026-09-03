"""Run the fixed Python compatibility suite without coverage collection."""

from __future__ import annotations

import pytest
from scripts.validation.python_warnings import PYTEST_WARNING_ARGUMENTS


def main() -> None:
    """Run every non-integration Python test with compatibility warning policy."""
    arguments = [
        "ontolib/tests",
        "backend/tests",
        "-m",
        "not integration",
        "-n",
        "auto",
        *PYTEST_WARNING_ARGUMENTS,
    ]
    result = pytest.main(arguments)
    if result != pytest.ExitCode.OK:
        raise SystemExit(result)


if __name__ == "__main__":
    main()
