#!/usr/bin/env python3
"""Save a worktree file to a fixed scratch directory outside the worktree, and restore
its exact bytes once. The test-validity reviewer mutates production code temporarily;
this is the only copy command its bash map allows, and it copies a worktree file only
into that scratch directory and back.
"""

from __future__ import annotations

import sys
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
    # casefold: on a case-insensitive filesystem .GIT is the repository directory
    in_git_dir = bool(relative.parts) and relative.parts[0].casefold() == ".git"
    if in_git_dir or path.is_symlink():
        raise AgentPristineInputError("path is not a worktree file")
    if (must_exist or path.exists()) and not path.is_file():
        raise AgentPristineInputError("path is not a worktree file")
    return relative


def run_agent_pristine(arguments: list[str], root: Path, scratch: Path) -> int:
    """``save <path>`` copies a worktree file into ``scratch`` and refuses while a copy
    exists, so a mutation is never saved over the original; ``restore <path>`` writes
    the saved bytes back and removes the copy; ``discard <path>`` removes a copy only
    while the file still matches it, so it never drops the only way back."""
    root = root.resolve()
    scratch = scratch.resolve()
    if scratch == root or scratch.is_relative_to(root):
        raise AgentPristineInputError("scratch directory must be outside the worktree")
    match arguments:
        case ["save" | "restore" | "discard" as operation, value]:
            pass
        case _:
            raise AgentPristineInputError(
                "usage: save <path> | restore <path> | discard <path>"
            )
    relative = _worktree_file(root, value, must_exist=operation == "save")
    saved = scratch / relative
    if operation == "save":
        # read first: a copy created before a failed read would restore as empty
        data = (root / relative).read_bytes()
        saved.parent.mkdir(parents=True, exist_ok=True)
        try:
            copy = saved.open("xb")
        except FileExistsError as exc:
            raise AgentPristineInputError(
                f"{relative} is already saved; run discard, which drops the copy only "
                "while the file still matches it"
            ) from exc
        try:
            with copy:
                copy.write(data)
        except BaseException:
            saved.unlink(missing_ok=True)
            raise
        print(f"saved {relative}")
        return 0
    if not saved.is_file():
        raise AgentPristineInputError(f"no saved copy of {relative}")
    target = root / relative
    if operation == "restore":
        target.write_bytes(saved.read_bytes())
    elif not target.is_file() or target.read_bytes() != saved.read_bytes():
        raise AgentPristineInputError(
            f"{relative} differs from the saved copy; ask the owner"
        )
    saved.unlink()
    print(f"{'restored' if operation == 'restore' else 'discarded'} {relative}")
    return 0


def scratch_directory(root: Path, home: Path) -> Path:
    """Per user, and per checkout: the directory mirrors the checkout's full path."""
    root = root.resolve()
    return home / ".cache" / "ontoprism-pristine" / root.relative_to(root.anchor)


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    scratch = scratch_directory(root, Path.home())
    try:
        return run_agent_pristine(sys.argv[1:], root, scratch)
    except (AgentPristineInputError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
