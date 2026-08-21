#!/usr/bin/env python3
"""Run a narrowly scoped repository pytest node for an OpenCode agent."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

SHELL_METACHARACTERS = frozenset("&;|><`$\n\r")
SAFE_FLAGS = frozenset({"-q", "-v", "-x"})
SAFE_K_EXPRESSION = re.compile(r"[A-Za-z0-9_ .()\-]+")
MAXFAIL = re.compile(r"--maxfail=([1-9][0-9]*)")
OWNED_TEST_ROOTS = (PurePosixPath("backend/tests"), PurePosixPath("ontolib/tests"))


class AgentTestInputError(ValueError):
    """The requested focused test cannot be safely executed."""


@dataclass(frozen=True)
class AgentTestInvocation:
    """A validated fixed-cwd pytest invocation."""

    arguments: tuple[str, ...]
    cwd: Path


class CommandResult(Protocol):
    returncode: int


class CommandRunner(Protocol):
    def __call__(
        self, arguments: tuple[str, ...], **kwargs: object
    ) -> CommandResult: ...


def _reject_shell_syntax(argument: str) -> None:
    if not argument or any(character in argument for character in SHELL_METACHARACTERS):
        raise AgentTestInputError("unsafe agent test argument")


def _validate_node(node: str, root: Path) -> None:
    path_text = node.split("::", 1)[0]
    candidate = PurePosixPath(path_text)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise AgentTestInputError("test node must be a repository-relative path")
    owned_root = next(
        (owned for owned in OWNED_TEST_ROOTS if candidate.is_relative_to(owned)),
        None,
    )
    if owned_root is None:
        raise AgentTestInputError("test node is outside an owned test root")
    resolved_root = (root / owned_root).resolve()
    resolved_path = (root / candidate).resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise AgentTestInputError("test node escapes its owned test root") from exc
    if not resolved_path.exists():
        raise AgentTestInputError("test node path does not exist")


def build_pytest_invocation(arguments: list[str], root: Path) -> AgentTestInvocation:
    """Validate agent arguments and return the sole permitted subprocess shape."""
    resolved_root = root.resolve()
    validated: list[str] = []
    node_count = 0
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        _reject_shell_syntax(argument)
        if argument in SAFE_FLAGS or MAXFAIL.fullmatch(argument):
            validated.append(argument)
        elif argument == "-k":
            if index + 1 >= len(arguments):
                raise AgentTestInputError("-k requires an expression")
            expression = arguments[index + 1]
            _reject_shell_syntax(expression)
            if SAFE_K_EXPRESSION.fullmatch(expression) is None:
                raise AgentTestInputError("unsafe -k expression")
            validated.extend((argument, expression))
            index += 1
        elif argument.startswith("-"):
            raise AgentTestInputError("unsupported pytest option")
        else:
            _validate_node(argument, resolved_root)
            validated.append(argument)
            node_count += 1
        index += 1
    if node_count == 0:
        raise AgentTestInputError("at least one test node is required")
    return AgentTestInvocation(("pytest", *validated), resolved_root)


def _controlled_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONPATH"):
        environment.pop(name, None)
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def run_agent_test(
    arguments: list[str],
    root: Path,
    *,
    runner: CommandRunner = subprocess.run,
) -> int:
    """Execute a validated pytest command directly, never through a shell."""
    invocation = build_pytest_invocation(arguments, root)
    result = runner(
        invocation.arguments,
        cwd=invocation.cwd,
        env=_controlled_environment(),
        shell=False,
        check=False,
    )
    return result.returncode


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    try:
        return run_agent_test(sys.argv[1:], root)
    except AgentTestInputError:
        print("Agent test request rejected.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
