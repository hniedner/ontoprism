#!/usr/bin/env python3
"""Save a worktree file to a fixed scratch directory outside the worktree, and restore
its exact bytes. The test-validity reviewer mutates production code temporarily; this
is its only copy operation, so it cannot copy anything else anywhere else.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path


class AgentPristineInputError(ValueError):
    """The requested copy is outside the save-and-restore contract."""


def _worktree_file(root: Path, value: str, *, must_exist: bool) -> Path:
    candidate = Path(value)
    if candidate.is_absolute() or value.startswith("~"):
        raise AgentPristineInputError("path must be relative to the worktree")
    path = root / candidate
    try:
        relative = path.resolve().relative_to(root)
    except ValueError as exc:
        raise AgentPristineInputError("path must stay inside the worktree") from exc
    if relative.parts[:1] == (".git",) or path.is_symlink():
        raise AgentPristineInputError("path is not a worktree file")
    if (must_exist or path.exists()) and not path.is_file():
        raise AgentPristineInputError("path is not a worktree file")
    return relative


def run_agent_pristine(arguments: list[str], root: Path, scratch: Path) -> int:
    """``save <path>`` copies a worktree file into ``scratch``; ``restore <path>``
    writes the saved bytes back."""
    root = root.resolve()
    scratch = scratch.resolve()
    if scratch == root or scratch.is_relative_to(root):
        raise AgentPristineInputError("scratch directory must be outside the worktree")
    match arguments:
        case ["save" | "restore" as operation, value]:
            pass
        case _:
            raise AgentPristineInputError("usage: save <path> | restore <path>")
    relative = _worktree_file(root, value, must_exist=operation == "save")
    saved = scratch / relative
    if operation == "save":
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes((root / relative).read_bytes())
        print(f"saved {relative}")
        return 0
    if not saved.is_file():
        raise AgentPristineInputError(f"no saved copy of {relative}")
    (root / relative).write_bytes(saved.read_bytes())
    print(f"restored {relative}")
    return 0


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    scratch = Path(tempfile.gettempdir()) / f"ontoprism-pristine-{root.name}"
    try:
        return run_agent_pristine(sys.argv[1:], root, scratch)
    except (AgentPristineInputError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
