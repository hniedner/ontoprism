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

import json
import os
import re
import subprocess
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
    "git diff --no-ext-diff --output=tmp/x main...HEAD",
    "git diff --no-ext-diff --no-index /dev/null /etc/passwd --src-prefix=...HEAD",
    "wc -l tmp/../../../../Users/hannes/.ssh/id_ed25519",
    "pdm run python tmp/scratch/../../evil.py",
    "ls -la /etc",
    "wc -l /etc/passwd",
    "head ~/.aws/credentials",
    "tail -n 5 ../../.ssh/id_ed25519",
    "jq . ~/.config/gh/hosts.yml",
    "ls -la ~/.ssh",
    "ls -la /Users/hannes",
    # wrappers and option prefixes around a denied command
    "sudo rm -rf data",
    "xargs rm",
    "nohup curl https://example.org",
    "git --no-pager push origin main",
    "git --git-dir=.git push origin main",
    "git -C .. push origin main",
    # chaining, substitution, redirection and line breaks after an allowed prefix
    "git status --porcelain; rm -rf data",
    "git status --porcelain && rm -rf data",
    "git log --oneline -10 | sh",
    "ls -la $(rm -rf data)",
    "ls -la `rm -rf data`",
    "git status --porcelain > out.txt",
    "git status --porcelain\nrm -rf data",
)


class _StrictLoader(yaml.SafeLoader):
    """Reject duplicate mapping keys: PyYAML keeps the last value silently, while
    OpenCode drops the whole rule set for such a file."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> Any:
        seen: set[object] = set()
        for key_node, _value in node.value:
            key = self.construct_object(key_node, deep=deep)
            assert key not in seen, f"duplicate permission key {key!r}"
            seen.add(key)
        return super().construct_mapping(node, deep)


def _frontmatter(agent: str) -> dict[str, Any]:
    text = (_AGENT_DIR / f"{agent}.md").read_text(encoding="utf-8")
    frontmatter = text.split("\n---\n", 1)[0].removeprefix("---\n")
    return yaml.load(frontmatter, Loader=_StrictLoader)  # noqa: S506 -- SafeLoader


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
        "npm --prefix frontend run test:coverage",
        "npm --prefix frontend run check",
        "pdm run pre-commit run --all-files",
        "git status --porcelain",
        "git merge-base main HEAD",
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


@pytest.mark.parametrize(
    "command",
    [
        "cat README.md",
        "pdm run decompose --branch neoplasm",
        "pdm run agent-git merge-no-ff feat/x",
        "git fetch origin feat/m1-6-1-provisional-publication",
    ],
)
def test_the_primary_agent_asks_for_anything_not_listed(command: str) -> None:
    assert _resolve(_PRIMARY, command) == "ask"


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


_LOCAL_CONFIGS = (
    "opencode.json",
    "opencode.jsonc",
    ".opencode/opencode.json",
    ".opencode/opencode.jsonc",
)


def _load_jsonc(text: str) -> dict[str, Any]:
    """Strip block comments and whole-line ``//`` comments, then parse as JSON.

    OpenCode's parser also accepts end-of-line comments and trailing commas; such a
    file fails here loudly rather than being skipped."""
    without_block_comments = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    without_line_comments = re.sub(r"^\s*//.*$", "", without_block_comments, flags=re.M)
    return json.loads(without_line_comments)


def test_the_tracked_opencode_config_is_present() -> None:
    assert (_ROOT / "opencode.json").is_file()


@pytest.mark.parametrize("path", _LOCAL_CONFIGS)
def test_no_local_opencode_config_overrides_agents_or_permissions(path: str) -> None:
    """OpenCode reads all four of these over the agent files; only the agent files may
    grant permissions. Only the root ``opencode.json`` is tracked; the others are
    machine-local and absent on most checkouts, hence the skip."""
    config_path = _ROOT / path
    if not config_path.exists():
        pytest.skip(f"{path} is not present on this machine")
    config = _load_jsonc(config_path.read_text(encoding="utf-8"))

    assert not {"agent", "permission", "plugin"} & set(config), sorted(config)


def _opencode_binary() -> Path | None:
    """The OpenCode CLI to contract against: ``ONTOPRISM_OPENCODE_BIN``, else the
    darwin-arm64 build the owner's untracked harness config installs under
    ``~/.local/share/omnigent-harnesses``. Elsewhere set the variable."""
    configured = os.environ.get("ONTOPRISM_OPENCODE_BIN")
    candidates = [Path(configured)] if configured else []
    candidates.extend(
        sorted(
            Path.home().glob(
                ".local/share/omnigent-harnesses/opencode-*/node_modules/opencode-darwin-arm64/bin/opencode"
            )
        )
    )
    return next((path for path in candidates if path.is_file()), None)


@pytest.mark.parametrize("agent", _AGENTS)
def test_opencode_resolves_the_same_rules_as_the_agent_file(
    agent: str, tmp_path: Path
) -> None:
    """Contract with the real tool: the ordered bash, edit and task rules OpenCode
    resolves for an agent equal the file's, so the emulation above operates on the
    right input. The run is isolated: an empty OpenCode config under a private
    XDG_CONFIG_HOME, its own XDG data dir so parallel cases never share one SQLite
    database, its own state and cache dirs so nothing is written into the owner's,
    no ``OPENCODE_*`` override from the shell, no models catalogue fetch, and
    ``--pure``. It pins the repository's contribution only; a global OpenCode config
    or plugin on the owner's machine can still widen an agent and no test in this
    suite checks it.
    Skipped, not passed, where the binary is absent (always in CI); run it locally
    after editing any agent file."""
    binary = _opencode_binary()
    if binary is None:
        pytest.skip(
            "OpenCode binary not found; set ONTOPRISM_OPENCODE_BIN (not verified)"
        )
    (tmp_path / "opencode").mkdir()
    (tmp_path / "opencode" / "opencode.json").write_text("{}", encoding="utf-8")
    inherited = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("OPENCODE_")
    }
    result = subprocess.run(  # noqa: S603 -- argv list, no shell
        [str(binary), "debug", "agent", agent, "--pure"],
        cwd=_ROOT,
        env={
            **inherited,
            "XDG_CONFIG_HOME": str(tmp_path),
            "XDG_DATA_HOME": str(tmp_path / "data"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "OPENCODE_DISABLE_MODELS_FETCH": "1",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert list((tmp_path / "data" / "opencode").glob("opencode*.db")), (
        "the binary must keep its database under the test's own XDG data dir; "
        "sharing the owner's makes parallel cases fail with 'database is locked'"
    )
    start = result.stdout.find("{")
    assert start >= 0, result.stdout
    resolved, _ = json.JSONDecoder().raw_decode(result.stdout[start:])

    def resolved_rules(kind: str) -> list[tuple[str, str]]:
        return [
            (rule["pattern"], rule["action"])
            for rule in resolved["permission"]
            if rule["permission"] == kind
        ]

    permission = _frontmatter(agent)["permission"]
    assert resolved_rules("bash") == list(_bash_rules(agent).items())
    assert resolved_rules("edit") == [("*", permission["edit"])]
    task = permission["task"]
    expected_task = list(task.items()) if isinstance(task, dict) else [("*", task)]
    assert resolved_rules("task") == expected_task
