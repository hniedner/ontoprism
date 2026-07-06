"""Unit tests for the test-summary runner's output parsers (scripts/test_runner.py)."""

import importlib.util
import sys
from pathlib import Path

import pytest

_RUNNER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "test_runner.py"
_spec = importlib.util.spec_from_file_location("summary_runner", _RUNNER_PATH)
assert _spec is not None
assert _spec.loader is not None
test_runner = importlib.util.module_from_spec(_spec)
# Register in sys.modules before exec — @dataclass looks up sys.modules[cls.__module__]
# while processing the class, which is None for an unregistered dynamic module.
sys.modules[_spec.name] = test_runner
_spec.loader.exec_module(test_runner)


@pytest.mark.unit
def test_parse_pytest_counts_passed_failed_errors_skipped() -> None:
    assert test_runner.parse_pytest("103 passed in 4.5s") == (103, 0, 0, 0)
    assert test_runner.parse_pytest("1 failed, 60 passed, 2 skipped in 3.1s") == (
        60,
        1,
        0,
        2,
    )
    # A collection/fixture error is counted in its own tier.
    assert test_runner.parse_pytest("2 passed, 1 error in 1.2s") == (2, 0, 1, 0)
    # deselected must not be miscounted as passed/failed/skipped.
    assert test_runner.parse_pytest("15 passed, 81 deselected in 58s") == (15, 0, 0, 0)


@pytest.mark.unit
def test_parse_pytest_no_summary_is_zeros() -> None:
    assert test_runner.parse_pytest("....... [100%]") == (0, 0, 0, 0)


@pytest.mark.unit
def test_parse_vitest_counts() -> None:
    assert test_runner.parse_vitest("Tests  18 passed (18)") == (18, 0, 0, 0)
    assert test_runner.parse_vitest("Tests  2 failed | 16 passed (18)") == (16, 2, 0, 0)
    # failed | passed | skipped, in vitest's order.
    assert test_runner.parse_vitest("Tests  1 failed | 15 passed | 2 skipped (18)") == (
        15,
        1,
        0,
        2,
    )


@pytest.mark.unit
def test_parse_playwright_counts() -> None:
    assert test_runner.parse_playwright("  3 passed (3.9s)") == (3, 0, 0, 0)
    assert test_runner.parse_playwright("  1 failed\n  2 passed (4s)") == (2, 1, 0, 0)


@pytest.mark.unit
def test_run_suite_verdict_flips_on_raw_failure_line() -> None:
    # Even if a merged summary read clean, a raw FAILED line must fail the verdict.
    text = "FAILED backend/tests/test_x.py::test_y\n60 passed in 1s"
    passed, failed, errors, _ = test_runner.parse_pytest(text)
    # The summary parse sees only "60 passed", but the raw guard catches the failure.
    assert (passed, failed, errors) == (60, 0, 0)
    assert test_runner._RAW_FAILURE.search(text) is not None
