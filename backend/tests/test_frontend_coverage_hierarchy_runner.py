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
def test_frontend_hierarchy_runner_returns_injected_callable_status(
    tmp_path: Path,
) -> None:
    package = tmp_path / "frontend/node_modules/vitest/package.json"
    package.parent.mkdir(parents=True)
    package.write_text(json.dumps({"version": "3.2.4"}), encoding="utf-8")

    status = run_frontend_hierarchy(root=tmp_path, hierarchy_main=lambda _arguments: 7)
    assert status == 7


@pytest.mark.unit
def test_frontend_hierarchy_runner_propagates_malformed_coverage_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.validation.coverage_hierarchy._git",
        lambda *_arguments, **_keywords: "a" * 40,
    )
    package = tmp_path / "frontend/node_modules/vitest/package.json"
    package.parent.mkdir(parents=True)
    package.write_text(json.dumps({"version": "3.2.4"}), encoding="utf-8")
    source = tmp_path / "frontend/src/lib/example.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export const value = 1;\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        "[tool.coverage.report]\nexclude_also = []\npartial_also = []\n",
        encoding="utf-8",
    )
    (tmp_path / "coverage-surfaces.toml").write_text(
        """
schema_version = 1
report_only = true
limitations = ["Fixture."]
required_production_paths = []

[[group]]
name = "frontend"
classification = "production"
language = "frontend"
measurement = "vitest"
tree = "frontend"
kind = "application"
executable = true

[[inventory]]
root = "frontend/src/lib"
extensions = [".ts"]
default_group = "frontend"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    coverage = tmp_path / "frontend/coverage/coverage-final.json"
    coverage.parent.mkdir(parents=True)
    coverage.write_text(
        json.dumps(
            {
                str(source): {
                    "statementMap": {"0": {"start": {"line": 1}}},
                    "s": {"0": -1},
                    "branchMap": {},
                    "b": {},
                    "fnMap": {},
                    "f": {},
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError, match=r"statement hits\.0 must be non-negative"
    ) as error:
        run_frontend_hierarchy(root=tmp_path)

    assert str(error.value) == "statement hits.0 must be non-negative"
