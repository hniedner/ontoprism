#!/usr/bin/env python3
"""Run the disposable integration lane with application endpoints fail-closed."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_support.integration_resources import (
    build_safe_integration_environment,
)


def main() -> int:
    """Execute pytest after separating provisioning from application configuration."""
    pytest = shutil.which("pytest")
    if pytest is None:
        raise RuntimeError("pytest console script is required")
    with tempfile.TemporaryDirectory(prefix="ontoprism-integration-data-") as directory:
        environment = build_safe_integration_environment(
            os.environ,
            data_root=Path(directory),
        )
        return subprocess.run(  # noqa: S603
            [pytest, *sys.argv[1:]],
            check=False,
            env=environment,
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
