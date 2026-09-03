"""Behavioral contracts for the governed Python compatibility runner."""

from __future__ import annotations

import hashlib
import inspect
import subprocess
import warnings
from pathlib import Path

import pytest
from scripts.validation import run_python_compatibility_tests
from scripts.validation.python_warnings import STARLETTE_ANYIO_ALIAS_WARNING
from scripts.validation.run_python_compatibility import (
    _INVENTORY_PROGRAM,
    PrimaryEnvironmentIdentity,
    assert_coverage_unchanged,
    capture_coverage_identity,
    capture_primary_environment_identity,
    compatibility_commands,
    compatibility_environment,
    owned_environment,
    preserve_primary_environment,
)

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
    assert not (tmp_path / "tmp").exists()
    assert sentinel.read_text() == "python 3.13"


def test_owned_compatibility_environment_preserves_preexisting_tmp_parent(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "tmp"
    parent.mkdir()
    sentinel = parent / "not-owned"
    sentinel.write_text("preserve")

    with owned_environment(tmp_path):
        pass

    assert parent.is_dir()
    assert sentinel.read_text() == "preserve"


def test_primary_identity_covers_interpreter_version_selection_venv_and_inventory(
    tmp_path: Path,
) -> None:
    venv = tmp_path / ".venv"
    interpreter = venv / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(Path(__file__).resolve().parents[2] / ".venv/bin/python")
    configuration = b"home = /controlled/python\n"
    (venv / "pyvenv.cfg").write_bytes(configuration)
    selection = b"/controlled/python\n"
    (tmp_path / ".pdm-python").write_bytes(selection)

    identity = capture_primary_environment_identity(tmp_path)

    inventory = subprocess.run(  # noqa: S603
        [interpreter, "-c", _INVENTORY_PROGRAM],
        check=True,
        capture_output=True,
    ).stdout
    assert identity == PrimaryEnvironmentIdentity(
        interpreter=interpreter.resolve(strict=True),
        observed_version=(3, 13),
        pdm_python=selection,
        pyvenv_configuration=configuration,
        distribution_inventory_sha256=hashlib.sha256(inventory).hexdigest(),
    )
    assert inspect.isclass(PrimaryEnvironmentIdentity)


def test_primary_identity_rejects_missing_primary_environment(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="primary virtual environment"):
        capture_primary_environment_identity(tmp_path)


def test_compatibility_environment_scrubs_selectors_and_isolates_coverage(
    tmp_path: Path,
) -> None:
    disposable = tmp_path / "disposable"
    inherited = {
        "SAFE": "retained",
        "VIRTUAL_ENV": "/wrong/venv",
        "PDM_PYTHON": "/wrong/python",
        "PDM_IGNORE_ACTIVE_VENV": "1",
        "PDM_VENV_IN_PROJECT": "true",
        "PDM_VENV_LOCATION": "/wrong/location",
        "PDM_HOME": "/wrong/home",
        "PDM_CONFIG_FILE": "/wrong/config.toml",
        "COVERAGE_FILE": "/wrong/.coverage",
        "PYTEST_ADDOPTS": "-p malicious",
        "PYTEST_PLUGINS": "malicious_plugin",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }

    result = compatibility_environment(disposable, inherited)

    assert result == {
        "SAFE": "retained",
        "HOME": str(disposable / "home"),
        "PDM_HOME": str(disposable / "pdm-home"),
        "PDM_VENV_IN_PROJECT": "false",
        "PDM_VENV_LOCATION": str(disposable / "venvs"),
        "COVERAGE_FILE": str(disposable / ".coverage-compatibility"),
    }


def test_coverage_identity_detects_any_primary_coverage_mutation(
    tmp_path: Path,
) -> None:
    (tmp_path / ".coverage").write_bytes(b"primary")
    (tmp_path / ".coverage.worker").write_bytes(b"worker")
    before = capture_coverage_identity(tmp_path)
    (tmp_path / "coverage.xml").write_text("new")

    with pytest.raises(RuntimeError, match="coverage artifacts changed"):
        assert_coverage_unchanged(tmp_path, before)


def test_compatibility_commands_are_fixed_clean_smoke_and_non_coverage_suite() -> None:
    assert compatibility_commands("/tools/pdm", "/tools/python3.14") == (
        (
            "/tools/pdm",
            "venv",
            "create",
            "--name",
            "compatibility",
            "/tools/python3.14",
        ),
        (
            "/tools/pdm",
            "sync",
            "--venv",
            "compatibility",
            "--clean-unselected",
            "--dev",
        ),
        (
            "/tools/pdm",
            "run",
            "--venv",
            "compatibility",
            "test-python-compatibility-smoke",
        ),
        (
            "/tools/pdm",
            "run",
            "--venv",
            "compatibility",
            "test-python-compatibility",
        ),
    )


def test_primary_failure_is_not_masked_when_identity_also_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    identities = iter(
        [
            PrimaryEnvironmentIdentity(Path("/python"), (3, 13), b"a", b"b", "one"),
            PrimaryEnvironmentIdentity(Path("/python"), (3, 13), b"a", b"b", "two"),
        ]
    )
    monkeypatch.setattr(
        "scripts.validation.run_python_compatibility.capture_primary_environment_identity",
        lambda _root: next(identities),
    )

    with (
        pytest.raises(BaseExceptionGroup) as caught,
        preserve_primary_environment(tmp_path),
    ):
        raise subprocess.CalledProcessError(9, ["compatibility-test"])

    assert isinstance(caught.value.exceptions[0], subprocess.CalledProcessError)
    assert isinstance(caught.value.exceptions[1], RuntimeError)


def test_primary_and_coverage_failures_are_aggregated_with_body_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    identities = iter(
        [
            PrimaryEnvironmentIdentity(Path("/python"), (3, 13), b"a", b"b", "one"),
            PrimaryEnvironmentIdentity(Path("/python"), (3, 13), b"a", b"b", "two"),
        ]
    )
    monkeypatch.setattr(
        "scripts.validation.run_python_compatibility.capture_primary_environment_identity",
        lambda _root: next(identities),
    )

    def fail_coverage_assertion(
        _root: Path, _before: tuple[tuple[str, str], ...]
    ) -> None:
        raise OSError("coverage recapture failed")

    monkeypatch.setattr(
        "scripts.validation.run_python_compatibility.assert_coverage_unchanged",
        fail_coverage_assertion,
    )

    with (
        pytest.raises(BaseExceptionGroup) as caught,
        preserve_primary_environment(tmp_path),
    ):
        raise ValueError("compatibility body failed")

    assert len(caught.value.exceptions) == 3
    assert isinstance(caught.value.exceptions[0], ValueError)
    assert isinstance(caught.value.exceptions[1], RuntimeError)
    assert isinstance(caught.value.exceptions[2], OSError)


def test_primary_and_coverage_failures_are_aggregated_without_body_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    identities = iter(
        [
            PrimaryEnvironmentIdentity(Path("/python"), (3, 13), b"a", b"b", "one"),
            PrimaryEnvironmentIdentity(Path("/python"), (3, 13), b"a", b"b", "two"),
        ]
    )
    monkeypatch.setattr(
        "scripts.validation.run_python_compatibility.capture_primary_environment_identity",
        lambda _root: next(identities),
    )

    def fail_coverage_assertion(
        _root: Path, _before: tuple[tuple[str, str], ...]
    ) -> None:
        raise OSError("coverage recapture failed")

    monkeypatch.setattr(
        "scripts.validation.run_python_compatibility.assert_coverage_unchanged",
        fail_coverage_assertion,
    )

    with (
        pytest.raises(BaseExceptionGroup) as caught,
        preserve_primary_environment(tmp_path),
    ):
        pass

    assert len(caught.value.exceptions) == 2
    assert isinstance(caught.value.exceptions[0], RuntimeError)
    assert isinstance(caught.value.exceptions[1], OSError)


def _compatibility_test_source(message: str, module: str) -> str:
    return (
        "import warnings\n"
        "def test_warning_policy() -> None:\n"
        f"    warnings.warn_explicit({message!r}, DeprecationWarning, "
        f"'subject.py', 1, module={module!r})\n"
    )


def _run_compatibility_test_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    message: str,
    module: str,
) -> None:
    subject = tmp_path / f"test_warning_subject_{tmp_path.name}.py"
    subject.write_text(_compatibility_test_source(message, module), encoding="utf-8")
    monkeypatch.setattr(
        run_python_compatibility_tests,
        "_COMPATIBILITY_TEST_PATHS",
        (str(subject),),
    )
    monkeypatch.setattr(
        run_python_compatibility_tests,
        "_PYTEST_PARALLEL_ARGUMENTS",
        (),
    )
    with warnings.catch_warnings():
        run_python_compatibility_tests.main()


@pytest.mark.parametrize(
    ("message", "module"),
    [
        ("project-owned deprecated API", "ontolib.example"),
        ("generic deprecated API", "dependency.example"),
        (STARLETTE_ANYIO_ALIAS_WARNING + " near match", "starlette.testclient"),
        (STARLETTE_ANYIO_ALIAS_WARNING, "starlette.testclient.extra"),
    ],
)
def test_pytest_compatibility_warning_policy_reject_branch_is_live(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    message: str,
    module: str,
) -> None:
    with pytest.raises(SystemExit) as caught:
        _run_compatibility_test_main(
            monkeypatch, tmp_path, message=message, module=module
        )

    assert caught.value.code != 0
    output = capsys.readouterr()
    assert message in output.out + output.err
    assert "DeprecationWarning" in output.out + output.err


def test_pytest_compatibility_warning_policy_allows_exact_starlette_alias(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        _run_compatibility_test_main(
            monkeypatch,
            tmp_path,
            message=STARLETTE_ANYIO_ALIAS_WARNING,
            module="starlette.testclient",
        )
        is None
    )
    output = capsys.readouterr()
    assert STARLETTE_ANYIO_ALIAS_WARNING not in output.out + output.err
