from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from scripts.validation.run_agent_pristine import (
    AgentPristineInputError,
    run_agent_pristine,
    scratch_directory,
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
    assert not (scratch / "src" / "module.py").exists()


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
        ".GIT/config",
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


def test_a_copy_is_restored_once(tmp_path: Path) -> None:
    root, scratch = _worktree(tmp_path)
    run_agent_pristine(["save", "src/module.py"], root, scratch)
    run_agent_pristine(["restore", "src/module.py"], root, scratch)

    with pytest.raises(AgentPristineInputError, match="no saved copy"):
        run_agent_pristine(["restore", "src/module.py"], root, scratch)


def test_a_second_save_cannot_overwrite_the_pristine_copy(tmp_path: Path) -> None:
    """Saving again after a mutation would make the mutation the copy to restore."""
    root, scratch = _worktree(tmp_path)
    run_agent_pristine(["save", "src/module.py"], root, scratch)
    (root / "src" / "module.py").write_bytes(b"value = 2  # mutation\n")

    with pytest.raises(AgentPristineInputError, match="already saved"):
        run_agent_pristine(["save", "src/module.py"], root, scratch)

    run_agent_pristine(["restore", "src/module.py"], root, scratch)
    assert (root / "src" / "module.py").read_bytes() == b"value = 1\n"


def test_discard_drops_a_copy_that_matches_the_file(tmp_path: Path) -> None:
    root, scratch = _worktree(tmp_path)
    run_agent_pristine(["save", "src/module.py"], root, scratch)

    assert run_agent_pristine(["discard", "src/module.py"], root, scratch) == 0

    assert run_agent_pristine(["save", "src/module.py"], root, scratch) == 0


def test_discard_keeps_the_only_way_back_to_a_mutated_file(tmp_path: Path) -> None:
    root, scratch = _worktree(tmp_path)
    run_agent_pristine(["save", "src/module.py"], root, scratch)
    (root / "src" / "module.py").write_bytes(b"value = 3\n")

    with pytest.raises(AgentPristineInputError, match="differs from the saved copy"):
        run_agent_pristine(["discard", "src/module.py"], root, scratch)

    run_agent_pristine(["restore", "src/module.py"], root, scratch)
    assert (root / "src" / "module.py").read_bytes() == b"value = 1\n"


def test_a_save_that_cannot_read_the_file_leaves_no_copy(tmp_path: Path) -> None:
    """An empty copy left behind would later restore as an empty file."""
    root, scratch = _worktree(tmp_path)
    target = root / "src" / "module.py"
    target.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            run_agent_pristine(["save", "src/module.py"], root, scratch)
    finally:
        target.chmod(0o644)

    assert not (scratch / "src" / "module.py").exists()
    assert run_agent_pristine(["save", "src/module.py"], root, scratch) == 0


def test_each_checkout_gets_its_own_scratch_directory(tmp_path: Path) -> None:
    home = tmp_path / "home"

    assert scratch_directory(tmp_path / "a" / "b_c", home) != scratch_directory(
        tmp_path / "a_b" / "c", home
    )
    assert scratch_directory(tmp_path / "a" / "repo", home) != scratch_directory(
        tmp_path / "b" / "repo", home
    )


def test_an_absolute_path_is_refused_even_inside_the_worktree(tmp_path: Path) -> None:
    root, scratch = _worktree(tmp_path)

    with pytest.raises(AgentPristineInputError, match="relative to the worktree"):
        run_agent_pristine(["save", str(root / "src" / "module.py")], root, scratch)


@pytest.mark.parametrize("operation", ["restore", "discard"])
@pytest.mark.parametrize("path", ["../outside.txt", ".git/config", ".GIT/config"])
def test_restore_and_discard_stay_inside_the_worktree(
    tmp_path: Path, operation: str, path: str
) -> None:
    """A planted copy must not let restore write, or discard judge, a file outside
    the worktree or inside .git."""
    root, _ = _worktree(tmp_path)
    scratch = tmp_path / "cache" / "scratch"
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n")
    (tmp_path / "outside.txt").write_text("secret\n")
    # where a copy would sit if the path were not checked: scratch/<path as given>
    for planted in (scratch.parent / "outside.txt", scratch / ".git" / "config"):
        planted.parent.mkdir(parents=True, exist_ok=True)
        planted.write_text("planted\n")

    with pytest.raises(AgentPristineInputError):
        run_agent_pristine([operation, path], root, scratch)

    assert (tmp_path / "outside.txt").read_text() == "secret\n"
    assert (root / ".git" / "config").read_text() == "[core]\n"
