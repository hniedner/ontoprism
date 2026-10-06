"""A non-editable wheel can construct identity without a repository source tree."""
# ruff: noqa: S603 — fixed build/install commands in disposable directories

import os
import shutil
import subprocess
import sys
import sysconfig
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
        [sys.executable, "-I", "-m", "venv", str(environment)],
        check=True,
        capture_output=True,
    )
    python = environment / "bin/python"
    # Guard against inherited dependencies, even on a base Python with global deps.
    subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            "import importlib.util; "
            "assert importlib.util.find_spec('pydantic') is None",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)],
        check=True,
        capture_output=True,
    )
    site_result = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    wheel_site = Path(site_result.stdout.strip())
    dependency_site = Path(sysconfig.get_path("purelib")).resolve()
    assert dependency_site.is_relative_to(Path(sys.prefix).resolve()), (
        f"Dependencies must belong to the project interpreter: {dependency_site}"
    )
    # Append resolved project dependencies without processing its editable .pth files.
    # The wheel's own site-packages stays first; no base/user site-packages are enabled.
    (wheel_site / "project-dependencies.pth").write_text(
        str(dependency_site) + "\n", encoding="utf-8"
    )
    shutil.rmtree(project)
    probe = (
        "import sys; from pathlib import Path; "
        "from ontolib.decomposition import semantic_identity as identity; "
        "import pydantic; "
        "assert Path(pydantic.__file__).resolve().is_relative_to("
        f"Path({str(dependency_site)!r})), 'pydantic not from project dependencies'; "
        "assert all(Path(module.__file__).resolve().is_relative_to("
        "Path(sys.prefix).resolve()) "
        "for name, module in sys.modules.items() "
        "if name == 'ontolib' or name.startswith('ontolib.')), "
        "'ontolib not from wheel'; "
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
    assert set(result.stdout.strip()) <= set("0123456789abcdef")
