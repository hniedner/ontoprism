"""Safety properties of the OpenCode agent permission files.

OpenCode matches a bash command against wildcard patterns (``*`` any run, ``?`` one
character) and the last matching rule wins (https://opencode.ai/docs/permissions/).
``_resolve`` emulates that documented rule; it is not OpenCode itself, and whether the
runtime matches a pipeline as one string or per command is not documented, so the
metacharacter cases below assert what the files say, not an observed runtime verdict.

These tests describe what the bash permission layer refuses, not everything an agent can
do: the primary's scratch-script lane (``pdm run python tmp/scratch/*``) is a deliberate
escape hatch bounded by rule, not by the map.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).parents[2]
_AGENT_DIR = _ROOT / ".opencode" / "agent"
_AGENTS = sorted(path.stem for path in _AGENT_DIR.glob("*.md"))
_PRIMARY = "ontoprism-team"
_ACTIONS = {"allow", "ask", "deny"}

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
    # command execution or file access smuggled through an inspection tool
    "rg --pre rm pattern",
    'sqlite3 -readonly data/x.db ".shell rm -rf data"',
    "git log -p --output=/Users/hannes/.zshrc",
    "git diff --stat --output=/Users/hannes/x",
    "git show HEAD --output=/Users/hannes/x",
    "head ~/.aws/credentials",
    "tail -n 5 ../../.ssh/id_ed25519",
    "jq . ~/.config/gh/hosts.yml",
    "ls -la ~/.ssh",
    "ls -la /Users/hannes",
    # chaining, substitution, redirection and line breaks after an allowed prefix
    "git status --porcelain; rm -rf data",
    "git status --porcelain && rm -rf data",
    "git log --oneline -10 | sh",
    "ls -la $(rm -rf data)",
    "ls -la `rm -rf data`",
    "git status --porcelain > out.txt",
    "git status --porcelain\nrm -rf data",
)


def _frontmatter(agent: str) -> dict[str, Any]:
    text = (_AGENT_DIR / f"{agent}.md").read_text(encoding="utf-8")
    return yaml.safe_load(text.split("\n---\n", 1)[0].removeprefix("---\n"))


def _bash_rules(agent: str) -> dict[str, str]:
    bash = _frontmatter(agent)["permission"]["bash"]
    assert "*" in bash, f"{agent}: bash permissions must start from a catch-all"
    for pattern, rule in bash.items():
        assert rule in _ACTIONS, (agent, pattern, rule)
    return bash


def _resolve(agent: str, command: str) -> str:
    bash = _bash_rules(agent)
    action = bash["*"]
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
        "pdm run test-unit",
        "pdm run verify",
        "pdm run python tmp/scratch/inspect_run.py",
        "pdm run agent-replay ensure-podman-stack",
        "git status --porcelain",
        "git diff --no-ext-diff feat/m1-6-1-provisional-publication...HEAD",
        "git add backend/src/backend/main.py",
        "pdm run agent-git switch-existing feat/m1-6-1-provisional-publication",
        "pdm run agent-git switch-new feat/x-1",
        "pdm run agent-git commit-staged --message x",
        "pdm run agent-git push-origin feat/x-1",
        "pdm run agent-github pr-create --title x --body-file tmp/plans/pr.md "
        "--head feat/x-1 --base feat/m1-6-1-provisional-publication",
        "gh pr checks 336",
        "gh run watch 1 --exit-status",
        "gh pr merge 12 --squash --delete-branch --subject x",
    ],
)
def test_the_primary_agent_can_work_without_dispatching_a_subagent(
    command: str,
) -> None:
    assert _resolve(_PRIMARY, command) == "allow"


def test_the_primary_agent_asks_for_anything_not_listed() -> None:
    assert _resolve(_PRIMARY, "cat README.md") == "ask"
    assert _resolve(_PRIMARY, "pdm run decompose --branch neoplasm") == "ask"


@pytest.mark.parametrize("agent", [name for name in _AGENTS if name != _PRIMARY])
@pytest.mark.parametrize(
    "command",
    [
        "git diff --no-ext-diff feat/m1-6-1-provisional-publication...HEAD",
        "git diff --no-ext-diff main...HEAD",
    ],
)
def test_reviewers_can_diff_against_a_milestone_base(agent: str, command: str) -> None:
    if "reserve" in agent:
        pytest.skip("manual reserve agents are not part of the review flow")
    assert _resolve(agent, command) == "allow"


def test_the_primary_agent_can_edit_and_only_dispatches_existing_subagents() -> None:
    permission = _frontmatter(_PRIMARY)["permission"]

    assert permission["edit"] == "allow"
    dispatchable = {
        name
        for name, rule in permission["task"].items()
        if name != "*" and rule == "allow"
    }
    assert dispatchable <= set(_AGENTS)
    assert permission["task"]["*"] == "deny"


def test_the_wrappers_the_read_only_agents_rely_on_are_still_read_only() -> None:
    """Subagents may run these aliases; their safety lives in pyproject, not the map."""
    scripts = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["pdm"]["scripts"]

    assert scripts["agent-github-read"].endswith("--read-only")
    assert scripts["agent-test"] == "python scripts/validation/run_agent_test.py"
