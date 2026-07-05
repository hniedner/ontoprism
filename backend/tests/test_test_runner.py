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
# Register before exec so the module's @dataclass can resolve its string annotations
# via sys.modules[cls.__module__].
sys.modules[_spec.name] = test_runner
_spec.loader.exec_module(test_runner)


@pytest.mark.unit
def test_parse_pytest_counts_passed_failed_skipped() -> None:
    assert test_runner.parse_pytest("103 passed in 4.5s") == (103, 0, 0)
    assert test_runner.parse_pytest("1 failed, 60 passed, 2 skipped in 3.1s") == (
        60,
        1,
        2,
    )
    # deselected must not be miscounted as passed/failed/skipped.
    assert test_runner.parse_pytest("15 passed, 81 deselected in 58s") == (15, 0, 0)


@pytest.mark.unit
def test_parse_pytest_no_summary_is_zeros() -> None:
    assert test_runner.parse_pytest("....... [100%]") == (0, 0, 0)


@pytest.mark.unit
def test_parse_vitest_counts() -> None:
    assert test_runner.parse_vitest("Tests  18 passed (18)") == (18, 0, 0)
    assert test_runner.parse_vitest("Tests  2 failed | 16 passed (18)") == (16, 2, 0)
