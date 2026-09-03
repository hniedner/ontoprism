"""Behavioral contracts for the governed Python compatibility runner."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from scripts.validation.run_python_compatibility import owned_environment

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def test_owned_compatibility_environment_cleans_only_its_disposable_directory(
    tmp_path: Path,
) -> None:
    primary = tmp_path / ".venv"
    primary.mkdir()
    sentinel = primary / "sentinel"
    sentinel.write_text("python 3.13")
    owned_path: Path | None = None

    def exercise_cleanup() -> None:
        nonlocal owned_path
        with owned_environment(tmp_path) as environment:
            owned_path = environment
            assert environment.parent == tmp_path / "tmp"
            assert environment.name.startswith("python-314-compatibility-")
            (environment / "created-by-runner").write_text("owned")
            raise RuntimeError("exercise cleanup")

    with pytest.raises(RuntimeError, match="exercise cleanup"):
        exercise_cleanup()

    assert owned_path is not None
    assert not owned_path.exists()
    assert sentinel.read_text() == "python 3.13"
