"""Clean-checkout contracts for unit-test inputs."""

from __future__ import annotations

import pathlib
import subprocess
from typing import TYPE_CHECKING

import pytest
from scripts.validation.unit_checkout_hermeticity import (
    fixed_untracked_input_violations,
    mixed_test_marker_surface_violations,
    mixed_test_marker_violations,
    unit_test_surface_violations,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(  # noqa: S603 - fixed Git test-repository command
        ("git", *arguments),  # noqa: S607 - intentional Git PATH lookup
        cwd=root,
        check=True,
        capture_output=True,
    )


def _tracked_test(root: Path, relative: str, source: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    _git(root, "add", "-f", "--", relative)
    return path


@pytest.fixture
def git_root(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "--quiet")
    return tmp_path


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        'def test_read() -> None:\n    open("private-corpus/review.json")\n',
        (
            "def test_read() -> None:\n"
            '    Path("private-corpus/review.json").read_bytes()\n'
        ),
        (
            "def test_read() -> None:\n"
            '    Path("private-corpus").joinpath("manifest.json").read_text()\n'
        ),
        (
            "def test_read() -> None:\n"
            '    open(os.path.join("private-corpus", "manifest.json"))\n'
        ),
        'def test_read() -> None:\n    load(input_path="private-corpus/review.json")\n',
        'def test_read() -> None:\n    load("private-corpus/review.json")\n',
        'def test_read() -> None:\n    open("../private/review.json")\n',
        'def test_read() -> None:\n    open("public/../../private/review.json")\n',
        'def test_read() -> None:\n    open("/private/review.json")\n',
    ],
)
def test_fixed_untracked_input_detector_reject_branch_is_live(source: str) -> None:
    assert fixed_untracked_input_violations(
        source, filename="test_subject.py", tracked_paths=frozenset()
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        """
def test_safe(tmp_path: Path) -> None:
    output = tmp_path / "data" / "example.json"
    output.write_text("safe")
    output.read_text()
""",
        """
def test_safe(tmpdir: object) -> None:
    output = tmpdir.join("manifest.json")
    output.write("safe")
""",
        """
def test_safe() -> None:
    Path("generated/report.json").write_text("safe")
    Path("generated").mkdir()
    build(output_path="generated/report.json", cache_directory="cache")
""",
        '''
def test_safe() -> None:
    """Documentation may mention private-corpus/example.json."""
    expected = {"node_modules/pkg": {"version": "1.0"}}
    assert expected
''',
    ],
)
def test_detector_exempts_owned_temp_outputs_and_non_inputs(source: str) -> None:
    assert (
        fixed_untracked_input_violations(
            source, filename="test_subject.py", tracked_paths=frozenset()
        )
        == ()
    )


@pytest.mark.unit
def test_write_only_repo_path_does_not_exempt_later_read() -> None:
    source = """
def test_generated() -> None:
    output = Path("generated/report.json")
    output.write_text("safe")
    output.read_text()
"""

    violations = fixed_untracked_input_violations(
        source, filename="test_subject.py", tracked_paths=frozenset()
    )

    assert len(violations) == 1
    assert "generated/report.json" in violations[0].message


@pytest.mark.unit
def test_detector_resolves_module_constants_and_aliases() -> None:
    source = """
from pathlib import Path as RepoPath
CORPUS = RepoPath("private-corpus")
MANIFEST = CORPUS.joinpath("manifest.json")
ALIAS = MANIFEST
def test_manifest() -> None:
    ALIAS.read_bytes()
"""

    violations = fixed_untracked_input_violations(
        source, filename="test_subject.py", tracked_paths=frozenset()
    )

    assert len(violations) == 1
    assert "private-corpus/manifest.json" in violations[0].message


@pytest.mark.unit
def test_detector_resolves_segmented_repository_anchors() -> None:
    source = """
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
def test_manifest() -> None:
    (ROOT / "private-corpus" / "manifest.json").read_text()
"""

    violations = fixed_untracked_input_violations(
        source,
        filename="backend/tests/test_subject.py",
        tracked_paths=frozenset(),
    )

    assert len(violations) == 1
    assert "private-corpus/manifest.json" in violations[0].message


@pytest.mark.unit
@pytest.mark.parametrize(
    ("tracked_paths", "expected_path", "expected_clean"),
    [
        ({"goldens/result.json"}, "goldens/result.json", True),
        ({"snapshots/view.json"}, "snapshots/view.json", True),
        ({"opencode.json"}, "opencode.json", True),
        (set(), ".opencode/opencode.json", False),
    ],
)
def test_tracked_classification_replaces_ignore_rule_classification(
    tracked_paths: set[str], expected_path: str, expected_clean: bool
) -> None:
    source = f'def test_read() -> None:\n    Path("{expected_path}").read_text()\n'

    violations = fixed_untracked_input_violations(
        source,
        filename="test_subject.py",
        tracked_paths=frozenset(tracked_paths),
    )

    assert (violations == ()) is expected_clean


@pytest.mark.unit
@pytest.mark.parametrize(
    ("source", "expected_test"),
    [
        (
            "import pytest\npytestmark = pytest.mark.unit\n"
            "@pytest.mark.integration\ndef test_real_store() -> None:\n    pass\n",
            "test_real_store",
        ),
        (
            "import pytest\npytestmark = [pytest.mark.unit]\n"
            "@pytest.mark.full_store\nclass TestCorpus:\n"
            "    def test_real_store(self) -> None:\n        pass\n",
            "TestCorpus::test_real_store",
        ),
        (
            "import pytest\n@pytest.mark.integration\nclass TestService:\n"
            "    @pytest.mark.unit\n"
            "    def test_endpoint(self) -> None:\n        pass\n",
            "TestService::test_endpoint",
        ),
    ],
)
def test_mixed_marker_contract_rejects_effective_marker_combinations(
    source: str, expected_test: str
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
def test_full_store_only_is_excluded_but_mixed_unit_is_scanned(
    git_root: Path,
) -> None:
    _tracked_test(
        git_root,
        "ontolib/tests/test_full_store.py",
        "import pytest\nfrom pathlib import Path\n"
        "pytestmark = [pytest.mark.integration, pytest.mark.full_store]\n"
        "def test_manifest() -> None:\n"
        '    Path("private-corpus/full.json").read_bytes()\n',
    )
    _tracked_test(
        git_root,
        "ontolib/tests/test_mixed.py",
        "import pytest\nfrom pathlib import Path\n"
        "pytestmark = [pytest.mark.unit, pytest.mark.integration, "
        "pytest.mark.full_store]\n"
        "def test_manifest() -> None:\n"
        '    Path("private-corpus/mixed.json").read_bytes()\n',
    )

    violations = unit_test_surface_violations(git_root)

    assert len(violations) == 1
    assert violations[0].path == "ontolib/tests/test_mixed.py"
    assert "private-corpus/mixed.json" in violations[0].message


@pytest.mark.unit
def test_untracked_absent_and_present_inputs_have_same_verdict(
    git_root: Path,
) -> None:
    _tracked_test(
        git_root,
        "backend/tests/test_manifest.py",
        "import pytest\nfrom pathlib import Path\npytestmark = pytest.mark.unit\n"
        "def test_manifest() -> None:\n"
        '    Path("private-corpus/manifest.json").read_text()\n',
    )

    absent = unit_test_surface_violations(git_root)
    local = git_root / "private-corpus/manifest.json"
    local.parent.mkdir()
    local.write_text("local", encoding="utf-8")
    present = unit_test_surface_violations(git_root)

    assert absent == present
    assert len(absent) == 1
    assert "fixed untracked checkout input path" in absent[0].message


@pytest.mark.unit
def test_tracking_the_same_input_changes_the_verdict(git_root: Path) -> None:
    _tracked_test(
        git_root,
        "backend/tests/test_manifest.py",
        "import pytest\nfrom pathlib import Path\npytestmark = pytest.mark.unit\n"
        "def test_manifest() -> None:\n"
        '    Path("private-corpus/manifest.json").read_text()\n',
    )
    manifest = git_root / "private-corpus/manifest.json"
    manifest.parent.mkdir()
    manifest.write_text("tracked", encoding="utf-8")

    before = unit_test_surface_violations(git_root)
    _git(git_root, "add", "-f", "--", "private-corpus/manifest.json")
    after = unit_test_surface_violations(git_root)

    assert len(before) == 1
    assert after == ()


@pytest.mark.unit
def test_tracked_inventory_discovers_a_new_test_file(git_root: Path) -> None:
    probe = git_root / "backend/tests/test_new.py"
    probe.parent.mkdir(parents=True)
    probe.write_text(
        "import pytest\nfrom pathlib import Path\npytestmark = pytest.mark.unit\n"
        "def test_new() -> None:\n"
        '    Path("private-corpus/new.json").read_bytes()\n',
        encoding="utf-8",
    )

    assert unit_test_surface_violations(git_root) == ()
    _git(git_root, "add", "-f", "--", "backend/tests/test_new.py")

    violations = unit_test_surface_violations(git_root)
    assert len(violations) == 1
    assert violations[0].path == "backend/tests/test_new.py"


@pytest.mark.unit
def test_surface_batches_deterministic_git_inventory_commands(tmp_path: Path) -> None:
    calls: list[tuple[tuple[str, ...], Path, float]] = []

    def recording_runner(
        arguments: Sequence[str], *, cwd: Path, timeout: float
    ) -> bytes:
        calls.append((tuple(arguments), cwd, timeout))
        return b""

    assert unit_test_surface_violations(tmp_path, runner=recording_runner) == ()
    assert calls == [
        (
            (
                "git",
                "ls-files",
                "-z",
                "--",
                "ontolib/tests",
                "backend/tests",
            ),
            tmp_path,
            10.0,
        ),
        (("git", "ls-files", "-z"), tmp_path, 10.0),
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("git"),
        subprocess.TimeoutExpired("git", 10),
        subprocess.CalledProcessError(2, "git"),
    ],
)
def test_git_inventory_failures_fail_closed(
    tmp_path: Path, failure: BaseException
) -> None:
    def failing_runner(arguments: Sequence[str], *, cwd: Path, timeout: float) -> bytes:
        del arguments, cwd, timeout
        raise failure

    violations = unit_test_surface_violations(tmp_path, runner=failing_runner)

    assert len(violations) == 1
    assert violations[0].path == "<git inventory>"
    assert "unable to inventory tracked checkout" in violations[0].message


@pytest.mark.unit
@pytest.mark.parametrize("failure_kind", ["unreadable", "undecodable", "syntax"])
def test_tracked_test_read_and_parse_failures_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    git_root: Path,
    failure_kind: str,
) -> None:
    probe = _tracked_test(
        git_root,
        "backend/tests/test_broken.py",
        "import pytest\npytestmark = pytest.mark.unit\n",
    )
    if failure_kind == "syntax":
        probe.write_text("def test_broken(:\n", encoding="utf-8")
    elif failure_kind == "undecodable":
        probe.write_bytes(b"\xff")
    else:
        original_read_text = pathlib.Path.read_text

        def read_with_failure(
            path: pathlib.Path,
            encoding: str | None = None,
            errors: str | None = None,
            newline: str | None = None,
        ) -> str:
            if path == probe:
                raise PermissionError(probe)
            return original_read_text(
                path, encoding=encoding, errors=errors, newline=newline
            )

        monkeypatch.setattr(pathlib.Path, "read_text", read_with_failure)

    violations = unit_test_surface_violations(git_root)

    assert len(violations) == 1
    assert violations[0].path == "backend/tests/test_broken.py"
    assert "unable to" in violations[0].message


@pytest.mark.unit
def test_repository_unit_surface_uses_only_tracked_checkout_inputs() -> None:
    violations = unit_test_surface_violations(_ROOT)
    assert violations == (), "\n".join(
        f"{item.path}:{item.line}: {item.message}" for item in violations
    )


@pytest.mark.unit
def test_repository_gate_is_in_the_test_ci_collection(
    request: pytest.FixtureRequest,
) -> None:
    assert request.node.get_closest_marker("unit") is not None
    configuration = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "test-ci =" in configuration
    assert "-m 'not integration'" in configuration
