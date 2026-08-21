from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from scripts.validation.run_agent_test import (
    AgentTestInputError,
    build_pytest_invocation,
    run_agent_test,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


@pytest.fixture
def owned_test_root(tmp_path: Path) -> Path:
    for relative in ("backend/tests/test_safe.py", "ontolib/tests/test_safe.py"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_safe():\n    assert True\n")
    return tmp_path


@pytest.mark.parametrize(
    "arguments",
    [
        ["backend/tests/test_safe.py", "-c=other.ini"],
        ["backend/tests/test_safe.py", "-cmalicious.ini"],
        ["backend/tests/test_safe.py", "-p=malicious"],
        ["backend/tests/test_safe.py", "-pmalicious"],
        ["backend/tests/test_safe.py", "--rootdir=other"],
        ["backend/tests/test_safe.py", "--override-ini=addopts=-pbad"],
        ["backend/tests/test_safe.py", "--import-mode=append"],
        ["backend/tests/test_safe.py", "--unknown"],
        ["backend/tests/test_safe.py", "&&", "gh", "pr", "merge"],
        ["backend/tests/../outside.py"],
    ],
)
def test_agent_test_rejects_unsafe_arguments(
    owned_test_root: Path, arguments: list[str]
) -> None:
    with pytest.raises(AgentTestInputError):
        build_pytest_invocation(arguments, owned_test_root)


def test_agent_test_rejects_absolute_outside_path(owned_test_root: Path) -> None:
    outside = owned_test_root.parent / "outside.py"
    outside.write_text("def test_outside(): pass\n")

    with pytest.raises(AgentTestInputError):
        build_pytest_invocation([str(outside)], owned_test_root)


def test_agent_test_rejects_symlink_escape(owned_test_root: Path) -> None:
    outside = owned_test_root.parent / "outside.py"
    outside.write_text("def test_outside(): pass\n")
    (owned_test_root / "backend/tests/test_link.py").symlink_to(outside)

    with pytest.raises(AgentTestInputError):
        build_pytest_invocation(["backend/tests/test_link.py"], owned_test_root)


def test_agent_test_accepts_owned_nodes_and_safe_flags(owned_test_root: Path) -> None:
    invocation = build_pytest_invocation(
        [
            "backend/tests/test_safe.py::test_safe",
            "ontolib/tests/test_safe.py",
            "-q",
            "-x",
            "--maxfail=2",
            "-k",
            "safe and not slow",
        ],
        owned_test_root,
    )

    assert invocation.arguments == (
        "pytest",
        "backend/tests/test_safe.py::test_safe",
        "ontolib/tests/test_safe.py",
        "-q",
        "-x",
        "--maxfail=2",
        "-k",
        "safe and not slow",
    )
    assert invocation.cwd == owned_test_root.resolve()


def test_agent_test_invokes_fixed_command_without_shell(
    owned_test_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    class Result:
        returncode = 0

    def fake_run(arguments: object, **kwargs: object) -> Result:
        observed["arguments"] = arguments
        observed.update(kwargs)
        return Result()

    monkeypatch.setenv("PYTEST_ADDOPTS", "-p malicious")
    monkeypatch.setenv("PYTEST_PLUGINS", "malicious")

    assert (
        run_agent_test(
            ["backend/tests/test_safe.py", "-v"], owned_test_root, runner=fake_run
        )
        == 0
    )
    assert observed["arguments"] == ("pytest", "backend/tests/test_safe.py", "-v")
    assert observed["cwd"] == owned_test_root.resolve()
    assert observed["shell"] is False
    environment = observed["env"]
    assert isinstance(environment, dict)
    assert "PYTEST_ADDOPTS" not in environment
    assert "PYTEST_PLUGINS" not in environment
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert os.environ["PYTEST_ADDOPTS"] == "-p malicious"
