"""Run the locked Python 3.14 compatibility lane without touching the primary venv."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from scripts.validation.python_versions import PYTHON_COMPATIBILITY_INTERPRETER

if TYPE_CHECKING:
    from collections.abc import Iterator

_ROOT = Path(__file__).resolve().parents[2]
_INVENTORY_PROGRAM = (
    "import importlib.metadata as m,json; "
    "print(json.dumps(sorted((d.metadata['Name'],d.version) "
    "for d in m.distributions())))"
)


@dataclass(frozen=True, slots=True)
class PrimaryEnvironmentIdentity:
    """Content identity of the selected primary project environment."""

    interpreter: Path
    observed_version: tuple[int, int]
    pdm_python: bytes | None
    pyvenv_configuration: bytes
    distribution_inventory_sha256: str


@contextmanager
def owned_environment(root: Path = _ROOT) -> Iterator[Path]:
    """Remove the owned directory and a newly-created ``tmp`` parent only if empty."""
    parent = root / "tmp"
    parent_preexisted = parent.exists()
    parent.mkdir(parents=True, exist_ok=True)
    environment = Path(tempfile.mkdtemp(prefix="python-314-compatibility-", dir=parent))
    try:
        yield environment
    finally:
        shutil.rmtree(environment)
        if not parent_preexisted and not any(parent.iterdir()):
            parent.rmdir()


def capture_primary_environment_identity(root: Path) -> PrimaryEnvironmentIdentity:
    """Read interpreter selection, version, venv config, and installed inventory."""
    interpreter_path = root / ".venv" / "bin" / "python"
    configuration_path = root / ".venv" / "pyvenv.cfg"
    if not interpreter_path.exists() or not configuration_path.is_file():
        raise RuntimeError(f"primary virtual environment is unavailable under {root}")
    interpreter = interpreter_path.resolve(strict=True)
    selection = root / ".pdm-python"
    version_output = subprocess.run(  # noqa: S603
        [
            interpreter_path,
            "-c",
            "import json,sys; print(json.dumps(sys.version_info[:2]))",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    inventory = subprocess.run(  # noqa: S603
        [interpreter_path, "-c", _INVENTORY_PROGRAM],
        check=True,
        capture_output=True,
    ).stdout
    version_values = json.loads(version_output)
    return PrimaryEnvironmentIdentity(
        interpreter=interpreter,
        observed_version=(int(version_values[0]), int(version_values[1])),
        pdm_python=selection.read_bytes() if selection.exists() else None,
        pyvenv_configuration=configuration_path.read_bytes(),
        distribution_inventory_sha256=hashlib.sha256(inventory).hexdigest(),
    )


def capture_coverage_identity(root: Path) -> tuple[tuple[str, str], ...]:
    """Hash every primary coverage artifact that currently exists."""
    paths = {root / ".coverage", root / "coverage.xml", *root.glob(".coverage.*")}
    return tuple(
        sorted(
            (path.name, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in paths
            if path.is_file()
        )
    )


def assert_coverage_unchanged(root: Path, before: tuple[tuple[str, str], ...]) -> None:
    """Fail if compatibility execution added, removed, or changed coverage files."""
    if capture_coverage_identity(root) != before:
        raise RuntimeError("primary coverage artifacts changed")


@contextmanager
def preserve_primary_environment(root: Path = _ROOT) -> Iterator[None]:
    """Fail if primary environment content or coverage changes, even on failure."""
    environment_before = capture_primary_environment_identity(root)
    coverage_before = capture_coverage_identity(root)
    body_failure: BaseException | None = None
    try:
        yield
    except BaseException as error:
        body_failure = error

    identity_failures: list[BaseException] = []
    try:
        if capture_primary_environment_identity(root) != environment_before:
            identity_failures.append(
                RuntimeError("primary environment identity changed")
            )
    except BaseException as error:
        identity_failures.append(error)
    try:
        assert_coverage_unchanged(root, coverage_before)
    except BaseException as error:
        identity_failures.append(error)

    if body_failure is not None and identity_failures:
        raise BaseExceptionGroup(
            "compatibility execution and isolation checks failed",
            [body_failure, *identity_failures],
        )
    if body_failure is not None:
        raise body_failure
    if identity_failures:
        raise identity_failures[0]


def compatibility_environment(
    environment: Path, inherited: dict[str, str]
) -> dict[str, str]:
    """Return a scrubbed environment with controlled PDM, home, and coverage paths."""
    result = {
        key: value
        for key, value in inherited.items()
        if not key.startswith("PDM_")
        and key
        not in {
            "VIRTUAL_ENV",
            "PYTHONHOME",
            "PYTHONPATH",
            "COVERAGE_FILE",
            "PYTEST_ADDOPTS",
            "PYTEST_PLUGINS",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
        }
    }
    result.update(
        {
            "HOME": str(environment / "home"),
            "PDM_HOME": str(environment / "pdm-home"),
            "PDM_VENV_IN_PROJECT": "false",
            "PDM_VENV_LOCATION": str(environment / "venvs"),
            "COVERAGE_FILE": str(environment / ".coverage-compatibility"),
        }
    )
    return result


def compatibility_commands(pdm: str, python: str) -> tuple[tuple[str, ...], ...]:
    """Return the clean sync, import smoke, and non-coverage compatibility commands."""
    return (
        (pdm, "venv", "create", "--name", "compatibility", python),
        (
            pdm,
            "sync",
            "--venv",
            "compatibility",
            "--clean-unselected",
            "--dev",
        ),
        (
            pdm,
            "run",
            "--venv",
            "compatibility",
            "test-python-compatibility-smoke",
        ),
        (
            pdm,
            "run",
            "--venv",
            "compatibility",
            "test-python-compatibility",
        ),
    )


def _run(command: list[str], *, env: dict[str, str]) -> None:
    subprocess.run(command, cwd=_ROOT, env=env, check=True)  # noqa: S603


def main() -> None:
    """Certify Python 3.14 and fail if primary venv or coverage content changes."""
    pdm = shutil.which("pdm")
    python = shutil.which(PYTHON_COMPATIBILITY_INTERPRETER)
    if pdm is None or python is None:
        missing = "pdm" if pdm is None else PYTHON_COMPATIBILITY_INTERPRETER
        raise RuntimeError(f"required executable is unavailable: {missing}")

    with preserve_primary_environment(), owned_environment() as environment:
        command_env = compatibility_environment(environment, dict(os.environ))
        for command in compatibility_commands(pdm, python):
            _run(list(command), env=command_env)


if __name__ == "__main__":
    main()
