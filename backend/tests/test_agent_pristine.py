from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from scripts.validation.run_agent_pristine import (
    AgentPristineInputError,
    run_agent_pristine,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _worktree(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "module.py").write_bytes(b"value = 1\n")
    return root, tmp_path / "scratch"


def test_a_saved_file_is_restored_byte_for_byte_after_a_mutation(
    tmp_path: Path,
) -> None:
    root, scratch = _worktree(tmp_path)
    target = root / "src" / "module.py"

    assert run_agent_pristine(["save", "src/module.py"], root, scratch) == 0
    target.write_bytes(b"value = 2  # mutation\n")
    assert run_agent_pristine(["restore", "src/module.py"], root, scratch) == 0

    assert target.read_bytes() == b"value = 1\n"
    assert (scratch / "src" / "module.py").read_bytes() == b"value = 1\n"


def test_a_file_the_mutation_deleted_is_restored(tmp_path: Path) -> None:
    root, scratch = _worktree(tmp_path)
    run_agent_pristine(["save", "src/module.py"], root, scratch)
    (root / "src" / "module.py").unlink()

    assert run_agent_pristine(["restore", "src/module.py"], root, scratch) == 0
    assert (root / "src" / "module.py").read_bytes() == b"value = 1\n"


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        "src/../../outside.txt",
        "/etc/hosts",
        "~/.ssh/id_ed25519",
        ".git/config",
        "src",
        "src/missing.py",
    ],
)
def test_save_copies_only_a_file_inside_the_worktree(tmp_path: Path, path: str) -> None:
    root, scratch = _worktree(tmp_path)
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n")
    (tmp_path / "outside.txt").write_text("secret\n")

    with pytest.raises(AgentPristineInputError):
        run_agent_pristine(["save", path], root, scratch)

    assert not scratch.exists()


def test_save_refuses_a_symlink_to_a_file_outside_the_worktree(tmp_path: Path) -> None:
    root, scratch = _worktree(tmp_path)
    (tmp_path / "outside.txt").write_text("secret\n")
    (root / "src" / "link.py").symlink_to(tmp_path / "outside.txt")

    with pytest.raises(AgentPristineInputError):
        run_agent_pristine(["save", "src/link.py"], root, scratch)

    assert not scratch.exists()


def test_restore_refuses_a_file_that_was_never_saved(tmp_path: Path) -> None:
    root, scratch = _worktree(tmp_path)

    with pytest.raises(AgentPristineInputError, match="no saved copy"):
        run_agent_pristine(["restore", "src/module.py"], root, scratch)

    assert (root / "src" / "module.py").read_bytes() == b"value = 1\n"


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["save"],
        ["save", "src/module.py", "elsewhere/x"],
        ["copy", "src/module.py"],
        ["restore", "src/module.py", "--to", "elsewhere"],
    ],
)
def test_only_save_or_restore_of_one_path_is_accepted(
    tmp_path: Path, arguments: list[str]
) -> None:
    root, scratch = _worktree(tmp_path)

    with pytest.raises(AgentPristineInputError):
        run_agent_pristine(arguments, root, scratch)


def test_the_scratch_directory_must_lie_outside_the_worktree(tmp_path: Path) -> None:
    root, _scratch = _worktree(tmp_path)

    with pytest.raises(AgentPristineInputError, match="outside the worktree"):
        run_agent_pristine(["save", "src/module.py"], root, root / "tmp" / "pristine")
