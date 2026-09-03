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


def test_quality_gate_fails_closed_when_test_file_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "test_unreadable.py"
    path.write_text("def test_contract():\n    assert True\n", encoding="utf-8")
    original_read_text = Path.read_text

    def raise_for_target(
        self: Path, encoding: str | None = None, errors: str | None = None
    ) -> str:
        if self == path:
            raise OSError("permission denied")
        return original_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", raise_for_target)

    with pytest.raises(OSError, match="permission denied"):
        check_file(path)


def test_quality_gate_fails_closed_on_non_utf8_test_file(tmp_path: Path) -> None:
    path = tmp_path / "test_non_utf8.py"
    path.write_bytes(b"\xff")

    with pytest.raises(UnicodeDecodeError):
        check_file(path)
