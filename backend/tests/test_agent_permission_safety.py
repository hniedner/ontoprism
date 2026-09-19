r"""Safety properties of the OpenCode agent permission files.

OpenCode matches a bash command against wildcard patterns (``*`` any run, ``?`` one
character) and the last matching rule wins (https://opencode.ai/docs/permissions/).
``_resolve`` emulates that rule plus two the documentation leaves out, both read from
the OpenCode binary rather than observed as a verdict: a pattern that ends in a space
and ``*`` also matches without that last argument (``"git diff * *"`` matches
``git diff main...HEAD``), and ``\`` becomes ``/`` in both the pattern and the
command. ``_resolve`` is not OpenCode itself, and whether the runtime matches a
pipeline as one string or per command is not documented, so the
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
    "head ~/.aws/credentials",
    "tail -n 5 ../../.ssh/id_ed25519",
    "jq . ~/.config/gh/hosts.yml",
    "ls -la ~/.ssh",
    "ls -la /Users/hannes",
    "ls -la ..",
    "wc -l ..",
    "git ls-files --exclude-from=/tmp/x",
    "git ls-files --exclude-from=/var/x",
    "git ls-files --exclude-from=..",
    # each older path deny, caught by that pattern alone after a wildcard git allow
    "git log --format=%H /Users/x",
    "git log --format=%H --exclude-from=/Users/x",
    "git log --format=%H /var/x",
    "git log --format=%H /tmp/x",
    "git log --format=%H ../x",
    "git log --format=%H --x=../x",
    "git log --format=%H\t../x",
    # two paths make git diff an implicit --no-index diff that reads outside files
    "git diff --no-ext-diff /private/tmp/outside.txt x...HEAD",
    "git diff --check /private/tmp/outside.txt x...HEAD",
    "git diff --name-only /private/tmp/outside.txt x...HEAD",
    "git diff --no-ext-diff /private/tmp/outside.txt\tx...HEAD",
    # git echoes the first line of the pathspec file in its error
    "git add --pathspec-from-file=/private/tmp/x",
    "git add --pathspec-fr=/private/tmp/x",
    "git add --pathspec-from /private/tmp/x",
    # the shell splits a brace list into several words after the pattern matched one
    # (a sequence such as x{1..2}...HEAD has no comma and is not refused: every word
    # it expands to keeps the ...HEAD suffix, so no plain path reaches git diff)
    "git diff --no-ext-diff {/private/tmp/o,x}...HEAD",
    "git log --format=%H --x=~/y",
    # OpenCode turns a backslash into /, and bash turns ..\\/x into ../x
    "git log --format=%H ..\\/x",
    "git log --format=%H --no-index a b",
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
    # the same after a wildcard allow such as `pdm run agent-test *`
    "pdm run agent-test backend/tests/test_x.py | sh",
    "pdm run agent-test backend/tests/test_x.py ; rm -rf data",
    "pdm run agent-test backend/tests/test_x.py && curl https://example.org",
    "pdm run agent-test backend/tests/test_x.py\nrm -rf data",
    "pdm run agent-test backend/tests/test_x.py\rrm -rf data",
    "pdm run agent-test backend/tests/test_x.py < .env",
    "pdm run agent-github-read issue-list > out.txt",
    "pdm run agent-github-read issue-view $(cat .env)",
    "pdm run agent-github-read issue-view `cat .env`",
    # a dropped or cleared entry leaves the stash list; recovery needs fsck and is not
    # routine. `git reflog delete|expire` and `git update-ref -d refs/stash` destroy
    # the same entries.
    "git stash drop",
    "git stash drop stash@{0}",
    "git stash clear",
    "git reflog delete refs/stash@{0}",
    "git reflog expire --expire=now refs/stash",
    "git update-ref -d refs/stash",
    # stash inspection keeps the refusals every other inspection form has
    "git stash show -p --output=tmp/x",
    "git stash show -p --ext-diff",
    "git stash list | sh",
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
    command = command.replace("\\", "/")
    for raw_pattern, rule in bash.items():
        pattern = raw_pattern.replace("\\", "/")
        optional_tail = pattern.endswith(" *")
        expression = "".join(
            ".*" if char == "*" else "." if char == "?" else re.escape(char)
            for char in (pattern[:-2] if optional_tail else pattern)
        )
        if optional_tail:
            expression += "( .*)?"
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
        "pdm run agent-test --safe-integration backend/tests/test_x.py::test_y",
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
        "git stash list",
        "git stash show --stat",
        "git stash show -p stash@{0}",
        "git diff --no-ext-diff feat/m1-6-1-provisional-publication...HEAD",
        "git diff --check main...HEAD",
        "git diff --name-only main...HEAD",
        "git log --oneline",
        "git rev-parse --abbrev-ref HEAD",
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
        # path-taking inspection forms prompt: a pattern list cannot confine their paths
        "ls -la src",
        "ls -la .. README.md",
        "wc -l README.md",
        "git ls-files --exclude-from=.. x",
        "ls -la /etc",
        "wc -l /etc/passwd",
        "git rev-parse --resolve-git-dir /private/tmp/x/.git",
        # a stash write moves work out of or into the worktree: the owner sees it
        "git stash",
        "git stash push -m wip",
        "git stash pop",
        "git stash apply stash@{0}",
        "git stash branch rescue stash@{0}",
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


_STEWARD = "issue-steward"


def test_the_primary_agent_can_dispatch_the_issue_steward() -> None:
    assert _frontmatter(_PRIMARY)["permission"]["task"][_STEWARD] == "allow"


def test_the_issue_steward_cannot_edit_or_delegate() -> None:
    permission = _frontmatter(_STEWARD)["permission"]

    assert permission["*"] == "deny"
    assert permission["edit"] == "deny"
    assert permission["task"] == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "pdm run agent-github-read issue-list --state open",
        "pdm run agent-github-read issue-view 398",
        "pdm run agent-github-read milestone-list --state open",
        "pdm run agent-test backend/tests/test_x.py::test_y -v",
    ],
)
def test_the_issue_steward_can_read_the_tracker_and_reproduce_a_finding(
    command: str,
) -> None:
    assert _resolve(_STEWARD, command) == "allow"


@pytest.mark.parametrize(
    "command",
    [
        "pdm run agent-github issue-create --title x --body-file tmp/plans/x.md",
        "pdm run agent-github issue-edit 398 --milestone 12",
        "pdm run agent-github issue-close 398",
        "pdm run agent-github milestone-edit 16 --title x",
        "gh issue create --title x",
        "gh issue edit 398 --milestone x",
        "gh api -X PATCH repos/hniedner/ontoprism/milestones/16",
    ],
)
def test_the_issue_steward_never_writes_the_tracker(command: str) -> None:
    assert _resolve(_STEWARD, command) == "deny"


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
    grant permissions, and only the tracked root ``opencode.json`` may set the agent
    shell (a machine-local ``shell`` would override it). The others are machine-local
    and absent on most checkouts, hence the skip."""
    config_path = _ROOT / path
    if not config_path.exists():
        pytest.skip(f"{path} is not present on this machine")
    config = _load_jsonc(config_path.read_text(encoding="utf-8"))

    assert not _overrides(path, config), sorted(config)


def _overrides(path: str, config: dict[str, Any]) -> set[str]:
    """The keys in ``config`` that this config file must not set."""
    forbidden = {"agent", "permission", "plugin"}
    if path != "opencode.json":
        forbidden.add("shell")
    return forbidden & set(config)


def test_only_the_tracked_config_may_set_the_agent_shell() -> None:
    assert _overrides(".opencode/opencode.json", {"shell": "/bin/zsh"}) == {"shell"}
    assert _overrides("opencode.json", {"shell": "/bin/bash"}) == set()


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
    ``--pure``. It pins the repository's contribution only; a global or managed
    OpenCode config or plugin on the owner's machine can still widen an agent and no
    test in this suite checks it.
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
    assert list((tmp_path / "data" / "opencode").glob("*.db")), (
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


_GLOB_QUALIFIER = "echo seed(e:'touch executed':)"
# OpenCode's bash tool runs ``<shell> -c <command>``; its ``!`` user-shell path wraps
# the command in ``eval``.
_QUALIFIER_FORMS = {
    "bash-tool": _GLOB_QUALIFIER,
    "user-shell": f'eval "{_GLOB_QUALIFIER}"',
}


def _run_under(
    shell: str, command: str, tmp_path: Path
) -> tuple[bool, subprocess.CompletedProcess[str]]:
    """Run one form of the qualifier; report whether the embedded command ran."""
    (tmp_path / "seed").touch()
    result = subprocess.run(  # noqa: S603 -- argv list, fixed command
        [shell, "-c", command],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return (tmp_path / "executed").exists(), result


@pytest.mark.parametrize("form", _QUALIFIER_FORMS)
def test_zsh_runs_a_command_hidden_in_a_glob_qualifier(
    form: str, tmp_path: Path
) -> None:
    """Contract with zsh: a word such as ``seed(e:'cmd':)`` runs ``cmd`` when zsh
    expands it and a file named ``seed`` exists. The string holds none of the
    characters the maps deny, so every wildcard allow would admit it; the maps cannot
    stop this, only the shell can."""
    if not Path("/bin/zsh").is_file():
        pytest.skip("zsh is not installed here (not verified)")
    executed, _ = _run_under("/bin/zsh", _QUALIFIER_FORMS[form], tmp_path)

    assert executed


@pytest.mark.parametrize("form", _QUALIFIER_FORMS)
def test_the_configured_agent_shell_does_not_run_glob_qualifiers(
    form: str, tmp_path: Path
) -> None:
    config = json.loads((_ROOT / "opencode.json").read_text(encoding="utf-8"))
    shell = config.get("shell")

    assert shell == "/bin/bash", "OpenCode would fall back to $SHELL, often zsh"
    executed, result = _run_under(shell, _QUALIFIER_FORMS[form], tmp_path)

    assert not executed
    assert result.returncode != 0
    assert "syntax error" in result.stderr


def test_opencode_resolves_the_repository_shell(tmp_path: Path) -> None:
    """Contract with the real tool: OpenCode reads ``shell`` from the tracked
    ``opencode.json``, so its bash tool does not fall back to ``$SHELL``. It pins the
    repository's contribution only: ``OPENCODE_CONFIG_CONTENT`` (stripped here) or
    ``OPENCODE_DISABLE_PROJECT_CONFIG`` can still change the shell at run time.
    Skipped, not passed, where the binary is absent (always in CI)."""
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
        [str(binary), "debug", "config", "--pure"],
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
    start = result.stdout.find("{")
    assert start >= 0, result.stdout
    resolved, _ = json.JSONDecoder().raw_decode(result.stdout[start:])

    assert resolved.get("shell") == "/bin/bash"


@pytest.mark.parametrize(
    "command",
    # known limits of a pattern list: letter case and aliases of /tmp slip past the
    # path denies, so these may prompt or be refused, but never run unprompted
    ["ls -la tmp /users/hannes", "ls -la tmp /private/tmp/x"],
)
def test_the_primary_agent_never_runs_a_known_path_gap_unprompted(command: str) -> None:
    assert _resolve(_PRIMARY, command) != "allow"
