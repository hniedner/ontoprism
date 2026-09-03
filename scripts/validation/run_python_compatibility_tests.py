"""Run the fixed Python compatibility suite without coverage collection."""

from __future__ import annotations

from pathlib import Path

from scripts.validation.python_warnings import (
    PYTEST_WARNING_ARGUMENTS,
    configure_compatibility_warnings,
)

_ROOT = Path(__file__).resolve().parents[2]
_COMPATIBILITY_TEST_PATHS = ("ontolib/tests", "backend/tests")
_PYTEST_PARALLEL_ARGUMENTS = ("-n", "auto")
_PYTEST_CONFIGURATION_ARGUMENTS = (
    "--rootdir",
    str(_ROOT),
    "-c",
    str(_ROOT / "pyproject.toml"),
)


def main() -> None:
    """Run every non-integration Python test with compatibility warning policy."""
    configure_compatibility_warnings()
    import pytest  # noqa: PLC0415

    arguments = [
        *_COMPATIBILITY_TEST_PATHS,
        *_PYTEST_CONFIGURATION_ARGUMENTS,
        "-m",
        "not integration",
        *_PYTEST_PARALLEL_ARGUMENTS,
        *PYTEST_WARNING_ARGUMENTS,
    ]
    result = pytest.main(arguments)
    if result != pytest.ExitCode.OK:
        raise SystemExit(result)


if __name__ == "__main__":
    main()
