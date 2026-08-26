from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from scripts.validation.validate_opencode_config import RESERVES, ROLES
from scripts.validation.validate_opencode_runtime import (
    RuntimeContractError,
    effective_action,
    expected_permission_contract,
    governance_environment,
    local_runtime_contract,
    parse_json_object,
    run_command,
    validate_layered_project,
    validate_mcp_status,
    validate_permission_contract,
    validate_resolved_agent,
    validate_resolved_config,
    validate_runtime,
)

pytestmark = pytest.mark.unit


def test_runtime_json_parser_accepts_an_object_without_exposing_raw_output() -> None:
    assert parse_json_object(
        json.dumps({"default_agent": "ontoprism-team"}), "config"
    ) == {"default_agent": "ontoprism-team"}


def test_runtime_json_parser_rejects_non_object_output() -> None:
    with pytest.raises(
        RuntimeContractError, match="config output is not a JSON object"
    ):
        parse_json_object("[]", "config")


def test_resolved_config_requires_shared_and_local_markers() -> None:
    config = {
        "default_agent": "ontoprism-team",
        "plugin": [],
        "command": {"review-pr": {"agent": "ontoprism-team"}},
        "agent": {
            "implementer": {
                "model": "openai/gpt-5.6-sol",
            }
        },
        "lsp": {"local": {}},
        "mcp": {"local": {}},
    }

    assert validate_resolved_config(config, require_local_markers=True) == []

    config["plugin"] = ["@razroo/opencode-model-fallback@0.3.2"]
    assert "resolved external plugin list must be empty" in validate_resolved_config(
        config, require_local_markers=True
    )


@pytest.mark.parametrize("name", sorted(ROLES))
def test_resolved_specialist_models_remain_exact(name: str) -> None:
    model, mode, _, _ = ROLES[name]
    agent = {
        "model": model,
        "mode": mode,
        "permission": expected_permission_contract(Path(__file__).parents[2], name),
    }

    assert validate_resolved_agent(name, agent) == []


def test_resolved_read_only_agent_requires_effective_deny_catch_all() -> None:
    permissions = expected_permission_contract(
        Path(__file__).parents[2], "pr-code-reviewer"
    )
    agent = {
        "model": "github-copilot/claude-opus-5",
        "mode": "subagent",
        "permission": permissions,
    }

    assert validate_resolved_agent("pr-code-reviewer", agent) == []

    catch_all = next(
        rule
        for rule in permissions
        if rule["permission"] == "bash" and rule["pattern"] == "*"
    )
    catch_all["action"] = "ask"
    assert (
        "pr-code-reviewer resolved bash catch-all must be deny"
        in validate_resolved_agent("pr-code-reviewer", agent)
    )


def test_permission_contract_rejects_missing_or_reordered_project_rules() -> None:
    expected = [
        {"permission": "*", "pattern": "*", "action": "deny"},
        {"permission": "read", "pattern": "*", "action": "allow"},
    ]

    assert validate_permission_contract(expected, expected) == []
    assert validate_permission_contract(list(reversed(expected)), expected)
    injected = [
        expected[0],
        {"permission": "execute", "pattern": "*", "action": "allow"},
        expected[1],
    ]
    errors = validate_permission_contract(injected, expected)

    assert errors


@pytest.mark.parametrize(
    "trailing",
    [
        {"permission": "*", "pattern": "*", "action": "allow"},
        {"permission": "bash", "pattern": "rm *", "action": "allow"},
        {"permission": "edit", "pattern": "*", "action": "allow"},
        {"permission": "task", "pattern": "*", "action": "allow"},
        {"permission": "external_directory", "pattern": "*", "action": "allow"},
        "malformed",
    ],
)
def test_permission_contract_rejects_every_unapproved_trailing_rule(
    trailing: object,
) -> None:
    expected = [{"permission": "*", "pattern": "*", "action": "deny"}]

    assert validate_permission_contract([*expected, trailing], expected)


def test_permission_contract_allows_only_the_generated_tool_output_suffix() -> None:
    expected = [{"permission": "*", "pattern": "*", "action": "deny"}]
    generated = {
        "permission": "external_directory",
        "pattern": "/isolated/opencode/tool-output/*",
        "action": "allow",
    }

    assert (
        validate_permission_contract(
            [*expected, generated],
            expected,
            generated_tool_output_pattern="/isolated/opencode/tool-output/*",
        )
        == []
    )


@pytest.mark.parametrize("pattern", ["git push origin main", "unknown future command"])
def test_permission_contract_accepts_only_asks_in_the_exact_expected_suffix(
    pattern: str,
) -> None:
    global_ask = {"permission": "bash", "pattern": "git push *", "action": "ask"}
    expected = [
        {"permission": "bash", "pattern": "*", "action": "deny"},
        {"permission": "bash", "pattern": "git status --porcelain", "action": "allow"},
    ]

    assert validate_permission_contract([global_ask, *expected], expected) == []
    project_ask = {
        "permission": "bash",
        "pattern": pattern,
        "action": "ask",
    }
    assert (
        validate_permission_contract(
            [global_ask, *expected, project_ask], [*expected, project_ask]
        )
        == []
    )
    assert validate_permission_contract([global_ask, *expected, project_ask], expected)


@pytest.mark.parametrize("role", sorted(ROLES))
def test_checked_in_project_permission_suffix_has_only_governed_actions(
    role: str,
) -> None:
    expected = expected_permission_contract(Path(__file__).parents[2], role)
    inherited_ask = {
        "permission": "bash",
        "pattern": "git push *",
        "action": "ask",
    }

    bash_actions = {rule["action"] for rule in expected if rule["permission"] == "bash"}
    expected_actions = (
        {"allow", "deny", "ask"}
        if role in {"ontoprism-team", "implementer"}
        else {"allow", "deny"}
    )
    assert bash_actions <= expected_actions
    assert validate_permission_contract([inherited_ask, *expected], expected) == []


@pytest.mark.parametrize(
    "ask_pattern", ["git push origin main", "unknown future command"]
)
def test_layered_project_validates_each_actual_resolved_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ask_pattern: str
) -> None:
    root = Path(__file__).parents[2]
    calls: list[list[str]] = []
    data_home = tmp_path / "data"

    def resolved_command(arguments: list[str], **_kwargs: object) -> object:
        calls.append(arguments)
        if arguments == ["opencode", "debug", "config"]:
            return type(
                "Completed",
                (),
                {
                    "stdout": json.dumps(
                        {
                            "default_agent": "ontoprism-team",
                            "plugin": [],
                            "command": {"review-pr": {"agent": "ontoprism-team"}},
                            "agent": {
                                "implementer": {
                                    "model": "openai/gpt-5.6-sol",
                                },
                                **{name: {} for name in RESERVES | {"ontoprism-team"}},
                                **{
                                    name: {}
                                    for name in (
                                        "architect",
                                        "ontology-engineer",
                                        "oncology-evidence-analyst",
                                        "plan-adversary",
                                        "ontology-validator",
                                        "pr-code-reviewer",
                                        "pr-silent-failure-hunter",
                                        "pr-test-analyzer",
                                        "pr-comment-analyzer",
                                        "pr-type-design-analyzer",
                                    )
                                },
                            },
                        }
                    ),
                    "stderr": "",
                },
            )()
        name = arguments[-1]
        expected = expected_permission_contract(root, name)
        if name == "implementer":
            expected = [
                *expected,
                {"permission": "bash", "pattern": ask_pattern, "action": "ask"},
            ]
        expected.append(
            {
                "permission": "external_directory",
                "pattern": (data_home / "opencode" / "tool-output" / "*").as_posix(),
                "action": "allow",
            }
        )
        model, mode, _, _ = ROLES[name]
        return type(
            "Completed",
            (),
            {
                "stdout": json.dumps(
                    {"model": model, "mode": mode, "permission": expected}
                ),
                "stderr": "",
            },
        )()

    monkeypatch.setattr(
        "scripts.validation.validate_opencode_runtime.run_command", resolved_command
    )

    errors = validate_layered_project(
        root, tmp_path, {"XDG_DATA_HOME": str(data_home)}, set()
    )

    assert errors == [
        "resolved permission contract is not the exact effective project suffix"
    ]
    assert ["opencode", "debug", "agent", "implementer"] in calls
    assert sum(call[:3] == ["opencode", "debug", "agent"] for call in calls) == 14


def test_expected_orchestrator_task_rules_are_exact_agent_name_patterns() -> None:
    rules = expected_permission_contract(Path(__file__).parents[2], "ontoprism-team")
    task_rules = [rule for rule in rules if rule["permission"] == "task"]
    allowed = {rule["pattern"] for rule in task_rules if rule["action"] == "allow"}

    assert task_rules[0] == {
        "permission": "task",
        "pattern": "*",
        "action": "deny",
    }
    assert allowed == {
        "implementer",
        "architect",
        "ontology-engineer",
        "oncology-evidence-analyst",
        "plan-adversary",
        "ontology-validator",
        "pr-code-reviewer",
        "pr-silent-failure-hunter",
        "pr-test-analyzer",
        "pr-comment-analyzer",
        "pr-type-design-analyzer",
    }
    assert not allowed & RESERVES


@pytest.mark.parametrize("dangerous", ["--admin", "--auto", "--queue", "--bypass"])
@pytest.mark.parametrize(
    "command",
    [
        "gh pr merge 123 {dangerous} --squash --delete-branch --subject fix:example",
        "gh pr merge 123 --squash {dangerous} --delete-branch --subject fix:example",
        "gh pr merge 123 --squash --delete-branch --subject fix:{dangerous}-example",
    ],
)
def test_orchestrator_denies_dangerous_merge_options_anywhere(
    dangerous: str, command: str
) -> None:
    rules = expected_permission_contract(Path(__file__).parents[2], "ontoprism-team")

    assert effective_action(rules, command.format(dangerous=dangerous)) == "deny"


@pytest.mark.parametrize(
    ("role", "existing_allow"),
    [
        ("implementer", "pdm run verify"),
        ("ontoprism-team", "git status --porcelain"),
        ("architect", "git status --porcelain"),
        ("pr-test-analyzer", "cp *"),
    ],
)
@pytest.mark.parametrize(
    "extra_allow",
    [
        "git -c alias.x=branch x *",
        "git frobnicate *",
    ],
)
def test_runtime_permission_contract_rejects_extra_project_bash_allows(
    tmp_path: Path,
    role: str,
    existing_allow: str,
    extra_allow: str,
) -> None:
    source = Path(__file__).parents[2] / ".opencode" / "agent" / f"{role}.md"
    target = tmp_path / ".opencode" / "agent" / f"{role}.md"
    target.parent.mkdir(parents=True)
    text = source.read_text()
    needle = f'    "{existing_allow}": allow\n'
    assert needle in text
    target.write_text(text.replace(needle, f'    "{extra_allow}": allow\n{needle}', 1))

    with pytest.raises(RuntimeContractError, match="unapproved bash allow"):
        expected_permission_contract(tmp_path, role)


@pytest.mark.parametrize(
    "command",
    [
        "git -C . reset --hard",
        "git reset",
        "git stash",
        "git push",
        "git push -f",
        "git push --force",
        "gco --hard",
    ],
)
def test_default_deny_blocks_global_option_and_alias_bypasses(command: str) -> None:
    rules = [
        {"permission": "bash", "pattern": "*", "action": "deny"},
        {"permission": "bash", "pattern": "git status*", "action": "allow"},
    ]

    assert effective_action(rules, command) == "deny"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git status --porcelain", "allow"),
        ("git commit change", "deny"),
        ("git commit -m change", "deny"),
        ("git merge --no-ff feature", "deny"),
        ("git merge feature", "deny"),
        ("git push", "deny"),
        ("git -C . push", "deny"),
        ("gh pr create", "deny"),
        ("gh --repo owner/repository pr create", "deny"),
        ("gco feature", "deny"),
        ("pdm run verify", "allow"),
        (
            "pdm run agent-test backend/tests/test_opencode_config_validation.py -q",
            "allow",
        ),
        (
            "pdm run agent-test ontolib/tests/terminologies/"
            "test_sparql_inventory.py -q",
            "allow",
        ),
        (
            "pdm run agent-test --full-store ontolib/tests/decomposition/"
            "test_axis_diagnostics_full_store.py::test_name -v",
            "allow",
        ),
        ("pdm run pytest backend/tests/test_opencode_config_validation.py -q", "deny"),
        ("pdm run test-integration-full-store anything", "deny"),
        ("pdm run lint", "allow"),
        ("npm --prefix frontend run test:coverage", "allow"),
        ("npm --prefix frontend run test:unit -- --run", "allow"),
        ("pdm run pre-commit run --all-files", "allow"),
        ("pdm install", "deny"),
        ("pdm install --project other", "deny"),
        ("pdm --project other run verify", "deny"),
        ("pdm run pytest --rootdir other backend/tests/test_x.py", "deny"),
        ("pdm run pytest -c other.ini backend/tests/test_x.py", "deny"),
        ("pdm run pytest --override-ini addopts=x backend/tests/test_x.py", "deny"),
        ("pdm run pytest -p malicious backend/tests/test_x.py", "deny"),
        ("npm --prefix other run test:coverage", "deny"),
        ("npm --config other --prefix frontend run test:coverage", "deny"),
        ("npm exec -- gh pr merge", "deny"),
        ("npx vitest run frontend/src/lib/api.test.ts", "deny"),
        ("pdm run gh pr merge", "deny"),
        ("pdm run git push --force", "deny"),
        ("pdm run publish", "deny"),
        ("npm exec gh pr merge", "deny"),
        ("npm run publish", "deny"),
        ("npx gh pr merge", "deny"),
        ("pdm run verify && gh pr merge", "deny"),
        ("npm --prefix frontend run test:coverage; git push", "deny"),
        ("git diff --output=/tmp/leak main...HEAD", "deny"),
        ("git diff --ext-diff main...HEAD", "deny"),
        ("git diff --no-ext-diff HEAD~1...HEAD", "deny"),
        ("git diff --no-ext-diff", "allow"),
        ("git diff --check", "allow"),
        ("git diff --no-index /dev/null candidate.md", "allow"),
        ("git diff --no-index candidate.md /dev/null", "deny"),
        ("git switch --discard-changes main", "deny"),
        ("git branch --force feat/x", "deny"),
        ("git branch -D feat/x", "deny"),
        ("git merge --no-ff feat/x", "deny"),
        ("pdm run agent-git switch-new feat/x", "allow"),
        ("pdm run agent-replay decompose-current", "allow"),
        (
            "pdm run agent-git commit-staged --message test:change",
            "allow",
        ),
        ("pdm run agent-test backend/tests/test_x.py\ngh pr merge", "deny"),
        ("pdm run agent-test backend/tests/test_x.py\rgit push", "deny"),
        ("pdm run agent-test backend/tests/test_x.py\r\ngh pr merge", "deny"),
    ],
)
def test_actual_implementer_contract_allows_only_canonical_mutations(
    command: str, expected: str
) -> None:
    rules = expected_permission_contract(Path(__file__).parents[2], "implementer")

    assert effective_action(rules, command) == expected


@pytest.mark.parametrize(
    ("role", "command", "expected"),
    [
        ("ontoprism-team", "pdm run agent-test backend/tests/test_x.py", "allow"),
        (
            "ontoprism-team",
            "pdm run agent-test --full-store ontolib/tests/test_x.py::test_name -v",
            "allow",
        ),
        (
            "implementer",
            "pdm run agent-test --full-store ontolib/tests/test_x.py::test_name -v",
            "allow",
        ),
        ("ontoprism-team", "pdm run lint", "allow"),
        ("ontoprism-team", "git diff --no-ext-diff", "allow"),
        ("ontoprism-team", "git diff --check", "allow"),
        ("ontoprism-team", "git diff --no-index /dev/null new-file", "allow"),
        ("implementer", "git diff --no-ext-diff", "allow"),
        ("implementer", "git diff --check", "allow"),
        ("implementer", "git diff --no-index /dev/null new-file", "allow"),
        ("architect", "pdm run agent-test backend/tests/test_x.py", "allow"),
        (
            "pr-code-reviewer",
            "pdm run agent-test --full-store ontolib/tests/test_x.py::test_name -v",
            "allow",
        ),
        (
            "pr-silent-failure-hunter",
            "pdm run agent-test --full-store ontolib/tests/test_x.py::test_name -v",
            "allow",
        ),
        (
            "pr-comment-analyzer",
            "pdm run agent-test --full-store ontolib/tests/test_x.py::test_name -v",
            "allow",
        ),
        (
            "pr-type-design-analyzer",
            "pdm run agent-test --full-store ontolib/tests/test_x.py::test_name -v",
            "allow",
        ),
        ("architect", "pdm run lint", "deny"),
        ("architect", "git diff --no-ext-diff", "deny"),
        ("architect", "git diff --check", "deny"),
        ("architect", "git diff --no-index /dev/null new-file", "deny"),
        ("pr-test-analyzer", "git diff --no-ext-diff", "deny"),
        ("ontoprism-team", "pdm run pytest backend/tests/test_x.py", "deny"),
        ("ontoprism-team", "pdm run test-integration-full-store anything", "deny"),
        ("ontoprism-team", "git diff --ext-diff", "deny"),
        ("ontoprism-team", "git diff --no-index other new-file", "ask"),
        ("ontoprism-team", "git diff --no-ext-diff; git reset", "deny"),
        ("implementer", "git diff --check && git clean -fd", "deny"),
        (
            "implementer",
            "git diff --no-index /dev/null new-file\ngit push",
            "deny",
        ),
    ],
)
def test_new_safe_inspection_commands_resolve_only_for_intended_roles(
    role: str, command: str, expected: str
) -> None:
    rules = expected_permission_contract(Path(__file__).parents[2], role)

    assert effective_action(rules, command) == expected


@pytest.mark.parametrize(
    "command",
    [
        "pdm run agent-test backend/tests/test_x.py\ngh pr merge",
        "pdm run agent-test backend/tests/test_x.py\rgit push",
        "pdm run agent-test backend/tests/test_x.py\r\ngh pr merge",
        "cp source target\ngh pr merge",
    ],
)
def test_r3_literal_line_break_denies_override_wildcard_allows(command: str) -> None:
    rules = expected_permission_contract(Path(__file__).parents[2], "pr-test-analyzer")

    assert effective_action(rules, command) == "deny"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git status --porcelain", "allow"),
        ("git status --short --branch", "allow"),
        ("git rev-parse HEAD", "allow"),
        ("git diff --no-ext-diff main...HEAD", "allow"),
        ("git diff --output=/tmp/leak main...HEAD", "deny"),
        ("git diff --ext-diff main...HEAD", "deny"),
        ("git diff --no-ext-diff HEAD~1...HEAD", "deny"),
        ("git show --output=/tmp/leak HEAD", "deny"),
        ("pdm run validate-opencode-config", "allow"),
        ("pdm run validate-opencode-config --root other", "deny"),
        ("pdm run validate-opencode-runtime", "allow"),
        ("pdm run validate-opencode-runtime --project other", "deny"),
    ],
)
def test_orchestrator_contract_allows_only_fixed_inspection_commands(
    command: str, expected: str
) -> None:
    rules = expected_permission_contract(Path(__file__).parents[2], "ontoprism-team")

    assert effective_action(rules, command) == expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (
            "gh pr view 123 --json title,baseRefName,headRefName,headRefOid,"
            "mergeStateStatus,statusCheckRollup",
            "allow",
        ),
        (
            "gh run list --workflow ci.yml --branch main --event push --json "
            "databaseId,headSha,status,conclusion,createdAt",
            "allow",
        ),
        (
            "gh run list --workflow pr-title.yml --branch feat/example --event "
            "pull_request --json displayTitle,headSha,status,conclusion,createdAt",
            "allow",
        ),
        ("gh run watch 456 --exit-status", "allow"),
        ("gh pr view 123 --json body", "ask"),
        ("gh run watch 456", "ask"),
    ],
)
def test_orchestrator_can_execute_only_required_read_only_gh_checks(
    command: str, expected: str
) -> None:
    rules = expected_permission_contract(Path(__file__).parents[2], "ontoprism-team")

    assert effective_action(rules, command) == expected


@pytest.mark.parametrize("suffix", ["--admin", "--auto", "--queue", "--bypass"])
def test_orchestrator_merge_contract_rejects_dangerous_suffixes(suffix: str) -> None:
    rules = expected_permission_contract(Path(__file__).parents[2], "ontoprism-team")
    command = "gh pr merge 123 --squash --delete-branch --subject fix:example " + suffix

    assert effective_action(rules, command) == "deny"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (
            "pdm run agent-test backend/tests/test_opencode_config_validation.py -q",
            "allow",
        ),
        ("pdm run lint", "allow"),
        ("pdm run pytest backend/tests/test_opencode_config_validation.py -q", "deny"),
        ("git diff --no-ext-diff", "allow"),
        ("git diff --check", "allow"),
        ("git diff --no-index /dev/null candidate.md", "allow"),
        ("git diff --no-index candidate.md /dev/null", "deny"),
    ],
)
def test_orchestrator_contract_allows_safe_policy_commands_only(
    command: str, expected: str
) -> None:
    rules = expected_permission_contract(Path(__file__).parents[2], "ontoprism-team")

    assert effective_action(rules, command) == expected


def test_governance_environment_rejects_uncontrolled_overrides() -> None:
    with pytest.raises(RuntimeContractError, match="unexpected governance environment"):
        governance_environment(
            {"OPENCODE_CONFIG_CONTENT": "injected"},
            controlled={},
        )

    with pytest.raises(RuntimeContractError, match="unexpected governance environment"):
        governance_environment(
            {"OPENCODE_CONFIG_FUTURE_OVERRIDE": "injected"},
            controlled={},
        )
    with pytest.raises(RuntimeContractError, match="unexpected governance environment"):
        governance_environment({"OPENCODE_PERMISSION": "allow"}, controlled={})


@pytest.mark.parametrize(
    ("role", "command", "expected"),
    [
        ("ontoprism-team", "uname -a", "ask"),
        ("ontoprism-team", "pdm run agent-github issue-create --title x", "allow"),
        ("ontoprism-team", "pdm run agent-github milestone-close 4", "allow"),
        ("ontoprism-team", "pdm run agent-github issue-delete 4", "deny"),
        ("ontoprism-team", "pdm run agent-github milestone-delete 4", "deny"),
        ("ontoprism-team", "gh issue create --repo hniedner/ontoprism", "deny"),
        ("ontoprism-team", "gh pr edit 4 --title changed", "deny"),
        ("ontoprism-team", "pdm install", "deny"),
        ("implementer", "uname -a", "ask"),
        ("implementer", "pdm run agent-github issue-close 4", "deny"),
        ("implementer", "pdm run agent-github-read issue-view 4", "deny"),
        ("implementer", "pdm install", "deny"),
    ],
)
def test_governed_writer_shell_contract(role: str, command: str, expected: str) -> None:
    rules = expected_permission_contract(Path(__file__).parents[2], role)

    assert effective_action(rules, command) == expected


SPECIALISTS = sorted(set(ROLES) - {"ontoprism-team", "implementer", *RESERVES})


@pytest.mark.parametrize("role", SPECIALISTS)
@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("pdm run agent-github-read issue-view 4", "allow"),
        ("pdm run agent-github-read milestone-list --state open", "allow"),
        ("pdm run agent-github-read pr-view 4", "allow"),
        ("pdm run agent-github-read run-list --branch main", "allow"),
        ("pdm run agent-test backend/tests/test_x.py -v", "allow"),
        (
            "pdm run agent-test --full-store ontolib/tests/test_x.py::test_name -v",
            "allow",
        ),
        (
            "pdm run agent-test --safe-integration backend/tests/test_x.py -v",
            "deny",
        ),
        ("pdm run agent-github issue-close 4", "deny"),
        ("pdm run pytest backend/tests/test_x.py", "deny"),
        ("touch forbidden", "deny"),
    ],
)
def test_read_only_specialists_get_only_governed_reads_and_safe_tests(
    role: str, command: str, expected: str
) -> None:
    rules = expected_permission_contract(Path(__file__).parents[2], role)

    assert effective_action(rules, command) == expected


def test_runtime_entry_rejects_an_actual_injected_override_before_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.validation.validate_opencode_runtime.shutil.which",
        lambda _name: "/installed/opencode",
    )
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", '{"permission":"allow"}')

    with pytest.raises(RuntimeContractError, match="unexpected governance environment"):
        validate_runtime(Path(__file__).parents[2], Path(__file__).parents[2])


def test_runtime_entry_rejects_injected_permission_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.validation.validate_opencode_runtime.shutil.which",
        lambda _name: "/installed/opencode",
    )
    monkeypatch.setenv("OPENCODE_PERMISSION", "allow")

    with pytest.raises(RuntimeContractError, match="unexpected governance environment"):
        validate_runtime(Path(__file__).parents[2], Path(__file__).parents[2])


def test_mcp_status_requires_each_expected_server_connected() -> None:
    output = "postgres \x1b[90mconnected\nsqlite \x1b[90mconnected\n"

    assert validate_mcp_status(output, {"postgres", "sqlite"}) == []
    assert "MCP sqlite is not connected" in validate_mcp_status(
        "postgres connected\nsqlite failed\n", {"postgres", "sqlite"}
    )
    assert "MCP sqlite is not connected" in validate_mcp_status(
        "sqlite not connected\n", {"sqlite"}
    )


def test_local_runtime_contract_collects_enabled_mcps(tmp_path: Path) -> None:
    config_dir = tmp_path / ".opencode"
    config_dir.mkdir()
    (config_dir / "opencode.json").write_text(
        json.dumps(
            {
                "mcp": {
                    "connected": {"type": "local", "enabled": True},
                    "disabled": {"type": "local", "enabled": False},
                },
            }
        )
    )

    mcps, errors = local_runtime_contract(tmp_path)

    assert mcps == {"connected"}
    assert errors == []


@pytest.mark.parametrize(
    "local_config",
    [
        {"mcp": {"broken": "not-an-object"}},
    ],
)
def test_local_runtime_contract_rejects_unverifiable_entries(
    tmp_path: Path, local_config: dict[str, object]
) -> None:
    config_dir = tmp_path / ".opencode"
    config_dir.mkdir()
    (config_dir / "opencode.json").write_text(json.dumps(local_config))

    assert local_runtime_contract(tmp_path)[1]


def test_command_errors_distinguish_missing_nonzero_and_timeout(tmp_path: Path) -> None:
    with pytest.raises(RuntimeContractError, match="executable is unavailable"):
        run_command(
            ["definitely-absent-opencode-test"],
            cwd=tmp_path,
            env=os.environ.copy(),
            operation="Agent inventory",
            display_command="opencode agent list",
        )

    secrets = [
        "Bearer bearer-secret",
        "GITHUB_TOKEN=github-secret",
        "AWS_SECRET_ACCESS_KEY=aws-secret",
        "https://user:url-secret@example.invalid/path",
        "https://example.invalid/path?token=query-secret",
    ]
    emit_secrets = (
        f"import sys;sys.stdout.write({str(secrets)!r});"
        f"sys.stderr.write({str(secrets)!r});sys.exit(2)"
    )
    with pytest.raises(RuntimeContractError) as nonzero:
        run_command(
            [
                sys.executable,
                "-c",
                emit_secrets,
            ],
            cwd=tmp_path,
            env=os.environ.copy(),
            operation="MCP connection check",
            display_command="opencode mcp list",
        )
    for secret in secrets:
        assert secret not in str(nonzero.value)
    assert "exited 2" in str(nonzero.value)
    assert str(nonzero.value) == "MCP connection check: opencode mcp list exited 2"

    with pytest.raises(
        RuntimeContractError,
        match="Startup validation: opencode debug startup timed out",
    ):
        run_command(
            [sys.executable, "-c", "import time;time.sleep(1)"],
            cwd=tmp_path,
            env=os.environ.copy(),
            timeout=0.01,
            operation="Startup validation",
            display_command="opencode debug startup",
        )


def test_command_error_categorizes_undecodable_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def undecodable(*_args: object, **_kwargs: object) -> object:
        raise UnicodeDecodeError("utf-8", b"\xffsecret", 0, 1, "invalid")

    monkeypatch.setattr(
        "scripts.validation.validate_opencode_runtime.subprocess.run", undecodable
    )

    with pytest.raises(RuntimeContractError) as failure:
        run_command(
            ["opencode", "models"],
            cwd=tmp_path,
            env=os.environ.copy(),
            operation="Model catalog validation",
            display_command="opencode models",
        )
    assert str(failure.value) == (
        "Model catalog validation: opencode models produced undecodable output"
    )
    assert "secret" not in str(failure.value)
