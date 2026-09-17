"""Safety properties of the OpenCode agent permission files.

OpenCode matches a bash command against wildcard patterns (``*`` any run, ``?`` one
character) and the last matching rule wins (https://opencode.ai/docs/permissions/).
These tests resolve representative commands under that rule. They assert what an agent
may do, not how the permission list is written, so the roster can change freely.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_AGENT_DIR = Path(__file__).parents[2] / ".opencode" / "agent"
_AGENTS = sorted(path.stem for path in _AGENT_DIR.glob("*.md"))
_PRIMARY = "ontoprism-team"

_NEVER_ALLOWED = (
    "rm -rf data",
    "git push origin main",
    "git push --force origin feat/x",
    "git reset --hard HEAD~1",
    "git clean -fdx",
    "git checkout -- .",
    "git commit -m x --no-verify",
    "gh pr merge 12",
    "gh pr merge 12 --squash --delete-branch --subject x --admin",
    "gh pr merge 12 --auto --squash --delete-branch --subject x",
    "pdm run agent-github issue-delete 12",
    "pdm run pytest backend/tests/test_x.py",
    "python3 -c pass",
    "curl https://example.org",
    "printenv",
    "git status --porcelain; rm -rf data",
    "git status --porcelain && rm -rf data",
    "git log --oneline -10 | sh",
    "ls $(rm -rf data)",
    "ls `rm -rf data`",
    "rg secret > out.txt",
)


def _frontmatter(agent: str) -> dict[str, object]:
    text = (_AGENT_DIR / f"{agent}.md").read_text(encoding="utf-8")
    return yaml.safe_load(text.split("\n---\n", 1)[0].removeprefix("---\n"))


def _resolve(agent: str, command: str) -> str:
    bash = _frontmatter(agent)["permission"]["bash"]  # type: ignore[index]
    action = "deny"
    for pattern, rule in bash.items():
        expression = "".join(
            ".*" if char == "*" else "." if char == "?" else re.escape(char)
            for char in pattern
        )
        if re.fullmatch(expression, command, flags=re.DOTALL):
            action = rule
    return action


@pytest.mark.parametrize("agent", _AGENTS)
@pytest.mark.parametrize("command", _NEVER_ALLOWED)
def test_no_agent_may_run_a_destructive_or_bypassing_command(
    agent: str, command: str
) -> None:
    assert _resolve(agent, command) == "deny"


@pytest.mark.parametrize("agent", [name for name in _AGENTS if name != _PRIMARY])
@pytest.mark.parametrize(
    "command",
    [
        "git add backend/src/backend/main.py",
        "pdm run agent-git commit-staged --message x",
        "pdm run agent-git push-origin feat/x",
        "pdm run agent-github pr-create --title x --head feat/x",
    ],
)
def test_only_the_primary_agent_can_stage_commit_or_publish(
    agent: str, command: str
) -> None:
    assert _resolve(agent, command) == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "pdm run agent-test backend/tests/test_x.py::test_y -v",
        "pdm run python tmp/scratch/inspect_run.py",
        "jq .status tmp/report.json",
        "git add backend/src/backend/main.py",
        "pdm run agent-git commit-staged --message x",
        "gh pr checks 336",
        "gh pr merge 12 --squash --delete-branch --subject x",
    ],
)
def test_the_primary_agent_can_work_without_dispatching_a_subagent(
    command: str,
) -> None:
    assert _resolve(_PRIMARY, command) == "allow"


def test_the_primary_agent_can_edit_and_only_dispatches_existing_subagents() -> None:
    permission = _frontmatter(_PRIMARY)["permission"]

    assert permission["edit"] == "allow"  # type: ignore[index]
    dispatchable = {
        name
        for name, rule in permission["task"].items()  # type: ignore[index]
        if name != "*" and rule == "allow"
    }
    assert dispatchable <= set(_AGENTS)
    assert permission["task"]["*"] == "deny"  # type: ignore[index]
