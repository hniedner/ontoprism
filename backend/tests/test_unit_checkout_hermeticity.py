"""Clean-checkout contracts for unit-test inputs."""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

import pytest
from scripts.validation.unit_checkout_hermeticity import (
    fixed_ignored_path_violations,
    mixed_test_marker_surface_violations,
    mixed_test_marker_violations,
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
@pytest.mark.parametrize(
    ("source", "expected_test"),
    [
        (
            "import pytest\n"
            "pytestmark = pytest.mark.unit\n"
            "@pytest.mark.integration\n"
            "def test_real_store() -> None:\n    pass\n",
            "test_real_store",
        ),
        (
            "import pytest\n"
            "pytestmark = [pytest.mark.unit]\n"
            "@pytest.mark.full_store\n"
            "class TestCorpus:\n"
            "    def test_real_store(self) -> None:\n        pass\n",
            "TestCorpus::test_real_store",
        ),
        (
            "import pytest\n"
            "@pytest.mark.integration\n"
            "class TestService:\n"
            "    @pytest.mark.unit\n"
            "    def test_endpoint(self) -> None:\n        pass\n",
            "TestService::test_endpoint",
        ),
    ],
)
def test_mixed_marker_contract_rejects_effective_marker_combinations(
    source: str,
    expected_test: str,
) -> None:
    violations = mixed_test_marker_violations(source, filename="test_subject.py")

    assert len(violations) == 1
    assert expected_test in violations[0].message
    assert "unit" in violations[0].message


@pytest.mark.unit
def test_repository_has_no_mixed_unit_and_real_boundary_markers() -> None:
    violations = mixed_test_marker_surface_violations(_ROOT)

    assert violations == (), "\n".join(
        f"{item.path}:{item.line}: {item.message}" for item in violations
    )


@pytest.mark.unit
def test_surface_discovery_excludes_full_store_modules_and_fails_closed(
    tmp_path: Path,
) -> None:
    tests = tmp_path / "ontolib/tests"
    tests.mkdir(parents=True)
    (tests / "test_full_store.py").write_text(
        "import pytest\n"
        "from pathlib import Path\n"
        "pytestmark = pytest.mark.full_store\n"
        '_MANIFEST = Path("data/qlever-ncit/manifest.json")\n'
        "def test_manifest() -> None:\n"
        "    _MANIFEST.read_bytes()\n",
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
def test_unit_surface_does_not_exclude_full_store_decorated_ranges(
    tmp_path: Path,
) -> None:
    tests = tmp_path / "ontolib/tests"
    tests.mkdir(parents=True)
    (tests / "test_mixed.py").write_text(
        "import pytest\n"
        "from pathlib import Path\n"
        "pytestmark = pytest.mark.unit\n"
        "@pytest.mark.full_store\n"
        "def test_real_store() -> None:\n"
        '    Path("data/qlever-ncit/manifest.json").read_bytes()\n',
        encoding="utf-8",
    )

    violations = unit_test_surface_violations(tmp_path)

    assert len(violations) == 1
    assert violations[0].path == "ontolib/tests/test_mixed.py"
    assert "data/qlever-ncit/manifest.json" in violations[0].message


@pytest.mark.unit
@pytest.mark.parametrize(
    ("assignment", "expected_path"),
    [
        (
            '_MANIFEST = Path("data/qlever-ncit/manifest.json")',
            "data/qlever-ncit/manifest.json",
        ),
        (
            "_MANIFEST: pathlib.Path = "
            'pathlib.Path(__file__).parent / "tmp" / "manifest.json"',
            "tmp/manifest.json",
        ),
    ],
)
def test_unit_surface_rejects_fixed_module_path_constants_used_by_unit_tests(
    tmp_path: Path,
    assignment: str,
    expected_path: str,
) -> None:
    tests = tmp_path / "backend/tests"
    tests.mkdir(parents=True)
    (tests / "test_manifest.py").write_text(
        "import pathlib\n"
        "import pytest\n"
        "from pathlib import Path\n"
        f"{assignment}\n"
        "_MANIFEST_ALIAS = _MANIFEST\n"
        "@pytest.mark.unit\n"
        "def test_manifest() -> None:\n"
        "    _MANIFEST_ALIAS.read_bytes()\n",
        encoding="utf-8",
    )

    violations = unit_test_surface_violations(tmp_path)

    assert len(violations) == 1
    assert violations[0].path == "backend/tests/test_manifest.py"
    assert expected_path in violations[0].message


@pytest.mark.unit
def test_unit_surface_allows_module_documentation_and_pytest_owned_paths(
    tmp_path: Path,
) -> None:
    tests = tmp_path / "backend/tests"
    tests.mkdir(parents=True)
    (tests / "test_safe.py").write_text(
        "from pathlib import Path\n"
        "import pytest\n"
        'DOCUMENTATION = "Read data/qlever-ncit/manifest.json when configured."\n'
        "@pytest.mark.unit\n"
        "def test_safe(tmp_path: Path) -> None:\n"
        '    manifest = tmp_path / "data" / "manifest.json"\n'
        "    manifest.write_bytes(DOCUMENTATION.encode())\n",
        encoding="utf-8",
    )

    assert unit_test_surface_violations(tmp_path) == ()


@pytest.mark.unit
def test_non_full_store_unit_tests_do_not_read_fixed_gitignored_inputs() -> None:
    violations = unit_test_surface_violations(_ROOT)
    assert violations == (), "\n".join(
        f"{item.path}:{item.line}: {item.message}" for item in violations
    )
