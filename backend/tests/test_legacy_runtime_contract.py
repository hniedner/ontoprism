"""Repository contract for the single supported local container runtime."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.security]

_ROOT = Path(__file__).resolve().parents[2]
_RETIRED_ENGINE = bytes.fromhex("636f6c696d61").decode()
_RETIRED_VM = bytes.fromhex("6c696d61").decode()
_FORBIDDEN_RUNTIME_PATTERNS = (
    re.compile(_RETIRED_ENGINE, re.IGNORECASE),
    re.compile(_RETIRED_VM + "ctl", re.IGNORECASE),
    re.compile(_RETIRED_VM.upper() + "_"),
    re.compile(rf"\b{_RETIRED_VM}\b", re.IGNORECASE),
)


def test_tracked_text_has_no_retired_local_runtime_references() -> None:
    git = shutil.which("git")
    assert git is not None
    tracked = subprocess.run(  # noqa: S603 -- fixed repository inventory command
        [git, "ls-files", "-z"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    violations: list[str] = []
    for encoded_relative in tracked:
        if not encoded_relative:
            continue
        relative = encoded_relative.decode()
        source = (_ROOT / relative).read_bytes()
        if b"\0" in source:
            continue
        text = source.decode("utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if any(pattern.search(line) for pattern in _FORBIDDEN_RUNTIME_PATTERNS):
                violations.append(f"{relative}:{line_number}: {line.strip()}")

    assert violations == []
