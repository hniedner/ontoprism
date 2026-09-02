"""Clean-checkout contracts for unit-test inputs."""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

import pytest
from scripts.validation.unit_checkout_hermeticity import (
    fixed_ignored_path_violations,
    unit_test_surface_violations,
)

if TYPE_CHECKING:
    from pathlib import Path


_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        'def test_read() -> None:\n    open("data/review.json")\n',
        'def test_read() -> None:\n    Path(__file__) / "tmp" / "review.json"\n',
        'def test_read() -> None:\n    pathlib.Path("tmp") / "review.json"\n',
        'def test_read() -> None:\n    load(input_path="data/review.json")\n',
        (
            "def test_read() -> None:\n"
            '    load(source_manifest=Path("data/review.json"))\n'
        ),
        'def test_read() -> None:\n    open(file="data/review.json")\n',
        'def test_read() -> None:\n    Path("data/review.json").read_bytes()\n',
    ],
)
def test_fixed_ignored_path_detector_reject_branch_is_live(source: str) -> None:
    assert fixed_ignored_path_violations(source, filename="test_subject.py")


@pytest.mark.unit
def test_detector_allows_owned_temp_paths_and_documentation() -> None:
    source = '''
def test_safe(tmp_path: Path) -> None:
    """Documentation may mention data/example.json without reading it."""
    output = tmp_path / "data" / "example.json"
    output.write_text("safe")
'''

    assert fixed_ignored_path_violations(source, filename="test_subject.py") == ()


@pytest.mark.unit
def test_surface_discovery_excludes_full_store_modules_and_fails_closed(
    tmp_path: Path,
) -> None:
    tests = tmp_path / "ontolib/tests"
    tests.mkdir(parents=True)
    (tests / "test_full_store.py").write_text(
        'import pytest\npytestmark = pytest.mark.full_store\nPath("data/live")\n',
        encoding="utf-8",
    )
    (tests / "test_broken.py").write_text(
        "import pytest\npytestmark = pytest.mark.unit\ndef test_broken(:\n",
        encoding="utf-8",
    )

    violations = unit_test_surface_violations(tmp_path)

    assert len(violations) == 1
    assert violations[0].path == "ontolib/tests/test_broken.py"
    assert violations[0].message.startswith("unable to parse unit-test candidate:")


@pytest.mark.unit
def test_real_mixed_marker_hierarchy_contract_is_excluded_from_unit_surface() -> None:
    relative = "ontolib/tests/decomposition/test_axis_diagnostic_report.py"
    source = (_ROOT / relative).read_text(encoding="utf-8")

    direct = fixed_ignored_path_violations(source, filename=relative)
    surface = unit_test_surface_violations(_ROOT)

    assert any(
        item.line == 328 and "data/qlever-ncit" in item.message for item in direct
    )
    assert not any(item.path == relative and item.line == 328 for item in surface)


@pytest.mark.unit
def test_non_full_store_unit_tests_do_not_read_fixed_gitignored_inputs() -> None:
    violations = unit_test_surface_violations(_ROOT)
    assert violations == (), "\n".join(
        f"{item.path}:{item.line}: {item.message}" for item in violations
    )
