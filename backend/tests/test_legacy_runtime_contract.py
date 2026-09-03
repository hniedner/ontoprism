"""Reject retired runtimes in tracked authored text and authored CHANGELOG text."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Final

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.security]

_ROOT = Path(__file__).resolve().parents[2]
_GENERATED_CHANGELOG: Final[str] = "CHANGELOG.md"
_CHANGELOG_HISTORY_MARKER: Final[bytes] = b"<!-- version list -->"
_RETIRED_ENGINE = bytes.fromhex("636f6c696d61").decode()
_RETIRED_VM = bytes.fromhex("6c696d61").decode()
_FORBIDDEN_RUNTIME_PATTERNS = (
    re.compile(_RETIRED_ENGINE, re.IGNORECASE),
    re.compile(_RETIRED_VM + "ctl", re.IGNORECASE),
    re.compile(_RETIRED_VM.upper() + "_"),
    re.compile(rf"\b{_RETIRED_VM}\b", re.IGNORECASE),
)


def _retired_runtime_violations(relative: str, source: bytes) -> list[str]:
    if relative == _GENERATED_CHANGELOG:
        if source.count(_CHANGELOG_HISTORY_MARKER) != 1:
            raise ValueError(
                f"{_GENERATED_CHANGELOG} must contain its generated-history marker "
                "exactly once"
            )
        # Generated history preserves immutable commit subjects, and AGENTS.md forbids
        # hand-editing it; only the authored prefix is governed by this contract.
        source = source.partition(_CHANGELOG_HISTORY_MARKER)[0]

    if b"\0" in source:
        return []

    violations: list[str] = []
    text = source.decode("utf-8")
    for line_number, line in enumerate(text.splitlines(), start=1):
        if any(pattern.search(line) for pattern in _FORBIDDEN_RUNTIME_PATTERNS):
            violations.append(f"{relative}:{line_number}: {line.strip()}")
    return violations


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
        path = _ROOT / relative
        source = path.read_bytes()
        violations.extend(_retired_runtime_violations(relative, source))

    assert violations == []


def test_changelog_authored_reference_is_reported() -> None:
    source = b"# Changelog\n" + _RETIRED_ENGINE.encode() + b"\n<!-- version list -->\n"

    assert _retired_runtime_violations("CHANGELOG.md", source) == [
        f"CHANGELOG.md:2: {_RETIRED_ENGINE}"
    ]


def test_changelog_generated_history_reference_is_tolerated() -> None:
    source = b"# Changelog\n<!-- version list -->\n" + _RETIRED_ENGINE.encode() + b"\n"

    assert _retired_runtime_violations("CHANGELOG.md", source) == []


@pytest.mark.parametrize(
    "source",
    [
        b"# Changelog\n",
        b"<!-- version list -->\n<!-- version list -->\n",
    ],
    ids=["missing", "duplicate"],
)
def test_changelog_requires_exactly_one_generated_history_marker(
    source: bytes,
) -> None:
    with pytest.raises(ValueError, match="exactly once"):
        _retired_runtime_violations("CHANGELOG.md", source)


def test_other_tracked_file_reference_is_reported() -> None:
    source = b"setup " + _RETIRED_ENGINE.encode() + b" runtime\n"

    assert _retired_runtime_violations("docs/runtime.md", source) == [
        f"docs/runtime.md:1: setup {_RETIRED_ENGINE} runtime"
    ]


def test_tracked_runtime_contract_fails_when_an_index_entry_vanishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Real Git is intentional: index/worktree deletion semantics are the contract.
    git = shutil.which("git")
    assert git is not None
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
    subprocess.run(  # noqa: S603 -- resolved Git executable, fixed test command
        [git, "init", "--quiet"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env=environment,
    )
    vanished = tmp_path / "docs/runtime.md"
    vanished.parent.mkdir()
    vanished.write_text("current runtime\n", encoding="utf-8")
    subprocess.run(  # noqa: S603 -- resolved Git executable, fixed test command
        [git, "add", "--", "docs/runtime.md"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env=environment,
    )
    vanished.unlink()
    monkeypatch.setitem(
        test_tracked_text_has_no_retired_local_runtime_references.__globals__,
        "_ROOT",
        tmp_path,
    )

    with pytest.raises(FileNotFoundError):
        test_tracked_text_has_no_retired_local_runtime_references()
