"""Run the locked Python 3.14 compatibility lane without touching the primary venv."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

_ROOT = Path(__file__).resolve().parents[2]
_PYTHON = "python3.14"


@contextmanager
def owned_environment(root: Path = _ROOT) -> Iterator[Path]:
    """Yield one disposable directory under ``tmp`` and remove only that directory."""
    parent = root / "tmp"
    parent.mkdir(parents=True, exist_ok=True)
    environment = Path(tempfile.mkdtemp(prefix="python-314-compatibility-", dir=parent))
    try:
        yield environment
    finally:
        shutil.rmtree(environment)


def _primary_identity(root: Path) -> tuple[str, bytes | None]:
    interpreter = (root / ".venv" / "bin" / "python").resolve(strict=True)
    selection = root / ".pdm-python"
    return str(interpreter), selection.read_bytes() if selection.exists() else None


def _run(command: list[str], *, env: dict[str, str]) -> None:
    subprocess.run(command, cwd=_ROOT, env=env, check=True)  # noqa: S603


def main() -> int:
    """Create, certify, and remove an isolated Python 3.14 environment."""
    before = _primary_identity(_ROOT)
    pdm = shutil.which("pdm")
    python = shutil.which(_PYTHON)
    if pdm is None or python is None:
        missing = "pdm" if pdm is None else _PYTHON
        raise RuntimeError(f"required executable is unavailable: {missing}")

    try:
        with owned_environment() as environment:
            command_env = os.environ.copy()
            command_env.update(
                {
                    "HOME": str(environment / "home"),
                    "PDM_VENV_IN_PROJECT": "false",
                    "PDM_VENV_LOCATION": str(environment / "venvs"),
                }
            )
            _run(
                [pdm, "venv", "create", "--name", "compatibility", python],
                env=command_env,
            )
            _run(
                [
                    pdm,
                    "sync",
                    "--venv",
                    "compatibility",
                    "--clean-unselected",
                    "--dev",
                    "--group",
                    "python-compatibility",
                ],
                env=command_env,
            )
            _run(
                [
                    pdm,
                    "run",
                    "--venv",
                    "compatibility",
                    "python",
                    "scripts/validation/python_compatibility_smoke.py",
                ],
                env=command_env,
            )
            _run(
                [
                    pdm,
                    "sync",
                    "--venv",
                    "compatibility",
                    "--clean-unselected",
                    "--dev",
                ],
                env=command_env,
            )
            _run(
                [pdm, "run", "--venv", "compatibility", "test-ci-no-gate"],
                env=command_env,
            )
    finally:
        after = _primary_identity(_ROOT)
        if after != before:
            raise RuntimeError("primary .venv or .pdm-python identity changed")

    return 0


if __name__ == "__main__":
    sys.exit(main())
