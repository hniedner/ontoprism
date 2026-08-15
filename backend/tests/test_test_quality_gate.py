"""Behavioral contracts for the test-quality pre-commit gate."""

from pathlib import Path

import pytest
from scripts.validation.check_test_quality import check_file

pytestmark = pytest.mark.unit


def _check(tmp_path: Path, source: str) -> list[str]:
    path = tmp_path / "test_example.py"
    path.write_text(source, encoding="utf-8")
    failures, _warnings = check_file(path)
    return failures


def test_quality_gate_rejects_test_with_no_observable_assertion(tmp_path: Path) -> None:
    failures = _check(
        tmp_path,
        "def test_main():\n    value = 1 + 1\n    print(value)\n",
    )

    assert any("no observable assertion" in failure for failure in failures)


def test_quality_gate_rejects_mock_execution_without_behavior(tmp_path: Path) -> None:
    failures = _check(
        tmp_path,
        "from unittest.mock import Mock\n"
        "def test_main():\n"
        "    collaborator = Mock(return_value=3)\n"
        "    collaborator()\n",
    )

    assert any("no observable assertion" in failure for failure in failures)
