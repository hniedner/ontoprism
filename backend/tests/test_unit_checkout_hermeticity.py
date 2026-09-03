"""Contracts for unit markers, inventories, and checkout-owned unit inputs."""

from __future__ import annotations

import ast
import dataclasses
import fcntl
import os
import pathlib
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from scripts.validation.unit_checkout_hermeticity import (
    TestInventory,
    TrackedInventory,
    _call_candidates,
    _ResolvedPath,
    _run_git,
    _unit_nodes,
    fixed_untracked_input_violations,
    mixed_test_marker_surface_violations,
    mixed_test_marker_violations,
    unit_test_surface_violations,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence


_ROOT = pathlib.Path(__file__).resolve().parents[2]
_TREE_SCAN_LOCK = Path(tempfile.gettempdir()) / "ontoprism-tree-scan.lock"


def _inventory(*files: str) -> TrackedInventory:
    return TrackedInventory.from_files(frozenset(files))


@contextmanager
def _exclusive_tree_scan() -> Iterator[None]:
    """Serialize the real-tree gate against the collection-hook probe test."""
    with _TREE_SCAN_LOCK.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _git(root: Path, *arguments: str) -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "GIT_CEILING_DIRECTORIES",
            "GIT_DIR",
            "GIT_INDEX_FILE",
            "GIT_WORK_TREE",
        }
    }
    subprocess.run(  # noqa: S603 - fixed Git test-repository command
        ("git", *arguments),  # noqa: S607 - intentional Git PATH lookup
        cwd=root,
        check=True,
        capture_output=True,
        env=environment,
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
        source, filename="test_subject.py", inventory=_inventory()
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
            source, filename="test_subject.py", inventory=_inventory()
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
        source, filename="test_subject.py", inventory=_inventory()
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
        source, filename="test_subject.py", inventory=_inventory()
    )

    assert len(violations) == 1
    assert "private-corpus/manifest.json" in violations[0].message


@pytest.mark.unit
def test_assignment_resolution_is_lexical_between_test_functions() -> None:
    source = """
import pytest
from pathlib import Path
@pytest.mark.unit
def test_checkout_manifest() -> None:
    manifest = Path("private-corpus/manifest.json")
    manifest.read_text()
@pytest.mark.unit
def test_temporary_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.read_text()
"""

    violations = fixed_untracked_input_violations(
        source, filename="test_subject.py", inventory=_inventory()
    )

    assert [(item.kind, item.line) for item in violations] == [("input", 7)]


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
        inventory=_inventory(),
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
        inventory=_inventory(*tracked_paths),
    )

    assert (violations == ()) is expected_clean


@pytest.mark.unit
@pytest.mark.parametrize(
    "statement",
    ['Path("goldens").read_text()', 'open("goldens")'],
)
def test_file_reader_rejects_a_tracked_directory_as_a_file_input(
    statement: str,
) -> None:
    source = f"def test_read() -> None:\n    {statement}\n"

    violations = fixed_untracked_input_violations(
        source,
        filename="test_subject.py",
        inventory=_inventory("goldens/result.json"),
    )

    assert [item.kind for item in violations] == ["input"]


@pytest.mark.unit
def test_directory_loader_accepts_a_tracked_directory_input() -> None:
    source = (
        'def test_read() -> None:\n    load_directory(Path("goldens"))\n'
        '    consume(source_dir=Path("goldens"))\n'
    )

    assert (
        fixed_untracked_input_violations(
            source,
            filename="test_subject.py",
            inventory=_inventory("goldens/result.json"),
        )
        == ()
    )


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["w", "a", "x"])
def test_path_open_write_modes_are_not_checkout_inputs(mode: str) -> None:
    source = (
        f'def test_write() -> None:\n    Path("generated/out.txt").open("{mode}")\n'
    )

    assert (
        fixed_untracked_input_violations(
            source, filename="test_subject.py", inventory=_inventory()
        )
        == ()
    )


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["r", "r+", "w+"])
def test_path_open_read_modes_remain_checkout_inputs(mode: str) -> None:
    source = f'def test_read() -> None:\n    Path("private/in.txt").open("{mode}")\n'

    violations = fixed_untracked_input_violations(
        source, filename="test_subject.py", inventory=_inventory()
    )

    assert [item.kind for item in violations] == ["input"]


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
        (
            "import pytest\n@pytest.mark.mutating_integration\n"
            "@pytest.mark.unit\ndef test_mutating() -> None:\n    pass\n",
            "test_mutating",
        ),
        (
            "import pytest\n@pytest.mark.full_build\n"
            "@pytest.mark.unit\ndef test_build() -> None:\n    pass\n",
            "test_build",
        ),
        (
            "import pytest\n@pytest.mark.e2e\n"
            "@pytest.mark.unit\ndef test_browser() -> None:\n    pass\n",
            "test_browser",
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
def test_unit_unmarked_boundary_module_is_skipped_while_mixed_module_is_scanned(
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
def test_method_level_unit_in_an_unmarked_class_is_scanned(git_root: Path) -> None:
    _tracked_test(
        git_root,
        "backend/tests/test_method.py",
        "import pytest\nfrom pathlib import Path\nclass TestManifest:\n"
        "    @pytest.mark.unit\n"
        "    def test_manifest(self) -> None:\n"
        '        Path("private-corpus/method.json").read_text()\n',
    )

    violations = unit_test_surface_violations(git_root)

    assert [(item.kind, item.path) for item in violations] == [
        ("input", "backend/tests/test_method.py")
    ]


@pytest.mark.unit
def test_unit_surface_has_a_typed_shape_for_module_statements() -> None:
    tree = ast.parse(
        "import pytest\nVALUE = 1\n"
        "@pytest.mark.unit\ndef test_value() -> None:\n    assert VALUE\n"
    )

    surface = _unit_nodes(tree)

    assert dataclasses.is_dataclass(surface)
    assert surface.module_scope is False
    assert len(surface.nodes) == 1
    assert len(surface.module_statements) == 2


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
    assert absent[0].kind == "input"


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
def test_untracked_test_file_is_rejected_then_scanned_when_tracked(
    git_root: Path,
) -> None:
    _tracked_test(
        git_root,
        "backend/tests/test_existing.py",
        "def test_existing() -> None:\n    assert True\n",
    )
    probe = git_root / "backend/tests/test_new.py"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(
        "import pytest\nfrom pathlib import Path\npytestmark = pytest.mark.unit\n"
        "def test_new() -> None:\n"
        '    Path("private-corpus/new.json").read_bytes()\n',
        encoding="utf-8",
    )

    before = unit_test_surface_violations(git_root)
    assert [(item.kind, item.path) for item in before] == [
        ("untracked_test", "backend/tests/test_new.py")
    ]
    _git(git_root, "add", "-f", "--", "backend/tests/test_new.py")

    violations = unit_test_surface_violations(git_root)
    assert [(item.kind, item.path) for item in violations] == [
        ("input", "backend/tests/test_new.py")
    ]


@pytest.mark.unit
def test_ignored_untracked_test_file_is_inventoried_and_rejected(
    git_root: Path,
) -> None:
    _tracked_test(
        git_root,
        "backend/tests/test_existing.py",
        "def test_existing() -> None:\n    assert True\n",
    )
    (git_root / ".gitignore").write_text(
        "backend/tests/test_ignored.py\n", encoding="utf-8"
    )
    probe = git_root / "backend/tests/test_ignored.py"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(
        "import pytest\npytestmark = pytest.mark.unit\n"
        "def test_ignored() -> None:\n    assert True\n",
        encoding="utf-8",
    )

    violations = unit_test_surface_violations(git_root)

    assert [(item.kind, item.path) for item in violations] == [
        ("untracked_test", "backend/tests/test_ignored.py")
    ]


@pytest.mark.unit
def test_surface_batches_deterministic_git_inventory_commands(tmp_path: Path) -> None:
    calls: list[tuple[tuple[str, ...], Path, float]] = []
    probe = tmp_path / "backend/tests/test_unit_checkout_hermeticity.py"
    probe.parent.mkdir(parents=True)
    probe.write_text("def test_probe() -> None:\n    assert True\n", encoding="utf-8")

    def recording_runner(
        arguments: Sequence[str],
        *,
        cwd: Path,
        timeout: float,
        env: dict[str, str],
    ) -> bytes:
        assert (
            not {
                "GIT_CEILING_DIRECTORIES",
                "GIT_DIR",
                "GIT_INDEX_FILE",
                "GIT_WORK_TREE",
            }
            & env.keys()
        )
        calls.append((tuple(arguments), cwd, timeout))
        if "--others" in arguments:
            return b""
        return b"backend/tests/test_unit_checkout_hermeticity.py\0"

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
        (
            (
                "git",
                "ls-files",
                "-z",
                "--others",
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
    def failing_runner(
        arguments: Sequence[str],
        *,
        cwd: Path,
        timeout: float,
        env: dict[str, str],
    ) -> bytes:
        del arguments, cwd, timeout, env
        raise failure

    violations = unit_test_surface_violations(tmp_path, runner=failing_runner)

    assert len(violations) == 1
    assert violations[0].kind == "inventory_error"
    assert violations[0].path == "<git inventory>"
    assert violations[0].line is None
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
def test_read_failure_diagnostic_is_bounded_and_sanitized(
    monkeypatch: pytest.MonkeyPatch, git_root: Path
) -> None:
    probe = _tracked_test(
        git_root,
        "backend/tests/test_unreadable.py",
        "import pytest\npytestmark = pytest.mark.unit\n",
    )
    original_read_text = pathlib.Path.read_text

    def read_with_secret_failure(
        path: pathlib.Path,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> str:
        if path == probe:
            raise PermissionError(
                f"cannot read {probe} via /outside/private.txt token=super-secret "
                + "x" * 500
            )
        return original_read_text(
            path, encoding=encoding, errors=errors, newline=newline
        )

    monkeypatch.setattr(pathlib.Path, "read_text", read_with_secret_failure)

    violation = unit_test_surface_violations(git_root)[0]

    assert violation.kind == "read_error"
    assert "PermissionError" in violation.message
    assert "cannot read" in violation.message
    assert str(git_root) not in violation.message
    assert "/outside/private.txt" not in violation.message
    assert "super-secret" not in violation.message
    assert len(violation.message) <= 320


@pytest.mark.unit
def test_git_failure_diagnostic_is_bounded_and_sanitized(tmp_path: Path) -> None:
    secret_path = f"{tmp_path}/private/token.txt"
    failure = subprocess.CalledProcessError(
        7,
        ("git", "ls-files", "--others"),
        stderr=(
            f"fatal: {secret_path} /outside/index Authorization=Bearer-secret "
            + "x" * 500
        ).encode(),
    )

    def failing_runner(
        arguments: Sequence[str],
        *,
        cwd: Path,
        timeout: float,
        env: dict[str, str],
    ) -> bytes:
        del arguments, cwd, timeout, env
        raise failure

    violation = unit_test_surface_violations(tmp_path, runner=failing_runner)[0]

    assert violation.kind == "inventory_error"
    assert "returncode=7" in violation.message
    assert "--others" in violation.message
    assert "stderr=" in violation.message
    assert str(tmp_path) not in violation.message
    assert "/outside/index" not in violation.message
    assert "Bearer-secret" not in violation.message
    assert len(violation.message) <= 320


@pytest.mark.unit
def test_production_git_runner_scrubs_injected_git_environment(
    monkeypatch: pytest.MonkeyPatch, git_root: Path
) -> None:
    # This intentionally executes real Git: the contract protects the checkout index,
    # which an in-process fake cannot certify.
    malicious_index = git_root / "outside.index"
    monkeypatch.setenv("GIT_DIR", str(git_root / "missing-git-dir"))
    monkeypatch.setenv("GIT_WORK_TREE", str(git_root / "missing-work-tree"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(malicious_index))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(git_root))

    payload = _run_git(
        ("git", "rev-parse", "--git-dir"), cwd=git_root, timeout=10.0, env=None
    )

    assert payload.strip() == b".git"
    assert not malicious_index.exists()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("source", "expected_kind", "expected_message"),
    [
        (
            "def test_path() -> None:\n"
            '    Path("public/../../private.json").read_text()\n',
            "invalid_path",
            "invalid statically resolvable checkout input path: ../private.json",
        ),
        (
            "def test_path() -> None:\n"
            "    Path(__file__).resolve().parents[50].read_text()\n",
            "invalid_path",
            "invalid statically resolvable checkout input path: "
            "parent traversal above checkout root",
        ),
    ],
)
def test_parent_escape_is_an_exact_invalid_path_violation(
    source: str, expected_kind: str, expected_message: str
) -> None:
    violations = fixed_untracked_input_violations(
        source,
        filename="backend/tests/test_subject.py",
        inventory=_inventory(),
    )

    assert len(violations) == 1
    assert violations[0].kind == expected_kind
    assert violations[0].message == expected_message


@pytest.mark.unit
@pytest.mark.parametrize(
    "arguments",
    [
        {"state": "unresolved", "value": "manifest.json"},
        {"state": "pytest_owned", "invalid_reason": "impossible"},
        {"state": "checkout", "value": ""},
        {"state": "invalid", "invalid_reason": ""},
    ],
)
def test_resolved_path_discriminator_rejects_invalid_combinations(
    arguments: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="path"):
        _ResolvedPath(**arguments)  # type: ignore[arg-type]


@pytest.mark.unit
def test_candidate_is_a_named_frozen_dataclass() -> None:
    statement = ast.parse('open("manifest.json")').body[0]
    assert isinstance(statement, ast.Expr)
    call = statement.value
    assert isinstance(call, ast.Call)

    candidate = _call_candidates(call)[0]

    assert dataclasses.is_dataclass(candidate)
    assert candidate.expression is call.args[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        candidate.line = 99  # type: ignore[misc]


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        'def test_read() -> None:\n    load_cached_manifest("private/a.json")\n',
        'def test_read() -> None:\n    read_target_manifest("private/b.json")\n',
        'def test_read() -> None:\n    parse_directory_manifest("private/c.json")\n',
        'def test_read() -> None:\n    consume(cache_source_path="private/d.json")\n',
        'def test_read() -> None:\n    open("golden files/result.json")\n',
    ],
)
def test_input_semantics_override_output_words_and_allow_spaced_paths(
    source: str,
) -> None:
    violations = fixed_untracked_input_violations(
        source, filename="test_subject.py", inventory=_inventory()
    )

    assert [item.kind for item in violations] == ["input"]


@pytest.mark.unit
def test_repository_unit_surface_uses_only_tracked_checkout_inputs() -> None:
    with _exclusive_tree_scan():
        violations = unit_test_surface_violations(_ROOT)
    assert violations == (), "\n".join(
        f"{item.path}:{item.line}: {item.message}" for item in violations
    )


@pytest.mark.unit
def test_repository_gate_is_unit_marked_and_nonintegration_collection_selects_it(
    request: pytest.FixtureRequest,
) -> None:
    assert request.node.get_closest_marker("unit") is not None
    configuration = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "test-ci =" in configuration
    assert "-m 'not integration'" in configuration


@pytest.mark.unit
def test_empty_tracked_test_inventory_fails_closed(tmp_path: Path) -> None:
    calls = 0

    def empty_runner(
        arguments: tuple[str, ...],
        *,
        cwd: Path,
        timeout: float,
        env: dict[str, str],
    ) -> bytes:
        nonlocal calls
        del arguments, cwd, timeout, env
        calls += 1
        return b""

    violations = unit_test_surface_violations(tmp_path, runner=empty_runner)

    assert calls == 2
    assert [(item.kind, item.path) for item in violations] == [
        ("inventory_error", "<git inventory>")
    ]


@pytest.mark.unit
def test_inventory_types_preserve_file_and_directory_distinctions() -> None:
    tests = TestInventory(
        tracked=frozenset({"backend/tests/test_gate.py"}),
        untracked=frozenset({"backend/tests/test_new.py"}),
    )
    tracked = _inventory("goldens/result.json")

    assert tests.count == 1
    assert tracked.has_file("goldens/result.json")
    assert tracked.has_directory("goldens")
    assert not tracked.has_file("goldens")
