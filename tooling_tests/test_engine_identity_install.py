"""A non-editable wheel can construct identity without a repository source tree."""
# ruff: noqa: S603 — fixed build/install commands in disposable directories

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = [pytest.mark.integration, pytest.mark.mutating_integration]


def test_wheel_constructs_engine_identity_outside_checkout(tmp_path: Path) -> None:
    project = tmp_path / "package"
    shutil.copytree(ROOT / "ontolib/src", project / "src")
    shutil.copyfile(ROOT / "ontolib/pyproject.toml", project / "pyproject.toml")
    pdm = shutil.which("pdm")
    assert pdm is not None
    subprocess.run(
        [pdm, "build", "--no-sdist"],
        cwd=project,
        env={**os.environ, "PDM_BUILD_SCM_VERSION": "0.0.0"},
        check=True,
        capture_output=True,
        text=True,
    )
    (wheel,) = (project / "dist").glob("*.whl")
    environment = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--system-site-packages", str(environment)],
        check=True,
        capture_output=True,
    )
    python = environment / "bin/python"
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)],
        check=True,
        capture_output=True,
    )
    shutil.rmtree(project)
    probe = (
        "import sys; from pathlib import Path; "
        "from ontolib.decomposition import semantic_identity as identity; "
        "assert Path(identity.__file__).is_relative_to(sys.prefix); "
        "print(identity.routing_implementation_identity())"
    )
    result = subprocess.run(
        [str(python), "-I", "-c", probe],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert len(result.stdout.strip()) == 64
