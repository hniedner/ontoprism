from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from scripts.validation.frontend_coverage_hierarchy import run_frontend_hierarchy

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


@pytest.mark.unit
def test_frontend_hierarchy_runner_uses_installed_vitest_version_and_fixed_paths(
    tmp_path: Path,
) -> None:
    package = tmp_path / "frontend/node_modules/vitest/package.json"
    package.parent.mkdir(parents=True)
    package.write_text(json.dumps({"version": "3.2.4"}), encoding="utf-8")
    observed: list[str] = []

    def hierarchy_main(arguments: Sequence[str] | None) -> int:
        assert arguments is not None
        observed.extend(arguments)
        return 0

    assert run_frontend_hierarchy(root=tmp_path, hierarchy_main=hierarchy_main) == 0
    assert observed == [
        "--root",
        str(tmp_path),
        "frontend-report",
        "--coverage-json",
        str(tmp_path / "frontend/coverage/coverage-final.json"),
        "--tool-version",
        "3.2.4",
        "--output",
        str(tmp_path / "frontend/coverage/hierarchy.json"),
        "--text-output",
        str(tmp_path / "frontend/coverage/hierarchy.txt"),
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    ["{not-json", "[]", json.dumps({}), json.dumps({"version": 3})],
)
def test_frontend_hierarchy_runner_fails_closed_for_invalid_vitest_metadata(
    tmp_path: Path, payload: str
) -> None:
    package = tmp_path / "frontend/node_modules/vitest/package.json"
    package.parent.mkdir(parents=True)
    package.write_text(payload, encoding="utf-8")

    with pytest.raises((ValueError, json.JSONDecodeError)):
        run_frontend_hierarchy(root=tmp_path, hierarchy_main=lambda _arguments: 0)


@pytest.mark.unit
def test_frontend_hierarchy_runner_propagates_report_parser_failures(
    tmp_path: Path,
) -> None:
    package = tmp_path / "frontend/node_modules/vitest/package.json"
    package.parent.mkdir(parents=True)
    package.write_text(json.dumps({"version": "3.2.4"}), encoding="utf-8")

    assert (
        run_frontend_hierarchy(root=tmp_path, hierarchy_main=lambda _arguments: 7) == 7
    )
