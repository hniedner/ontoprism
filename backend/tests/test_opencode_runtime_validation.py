from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from scripts.validation.validate_opencode_config import RESERVES
from scripts.validation.validate_opencode_runtime import (
    RuntimeContractError,
    effective_action,
    expected_permission_contract,
    governance_environment,
    local_runtime_contract,
    parse_json_object,
    run_command,
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
        "plugin": ["@razroo/opencode-model-fallback@0.3.2"],
        "command": {"review-pr": {"agent": "ontoprism-team"}},
        "agent": {
            "implementer": {
                "model": "openai/gpt-5.6-sol",
                "options": {"fallback_models": ["github-copilot/gpt-5.6-sol"]},
            }
        },
        "lsp": {"local": {}},
        "mcp": {"local": {}},
    }

    assert validate_resolved_config(config, require_local_markers=True) == []

    config["plugin"] = []
    assert (
        "resolved plugin list is not the pinned repository plugin"
        in validate_resolved_config(config, require_local_markers=True)
    )


def test_resolved_read_only_agent_requires_effective_deny_catch_all() -> None:
    agent = {
        "model": "github-copilot/claude-opus-5",
        "mode": "subagent",
        "permission": [
            {"permission": "bash", "pattern": "*", "action": "deny"},
            {"permission": "bash", "pattern": "git status*", "action": "allow"},
        ],
    }

    assert validate_resolved_agent("pr-code-reviewer", agent) == []

    agent["permission"][0]["action"] = "ask"
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
        ("git commit change", "allow"),
        ("git merge --no-ff feature", "allow"),
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
        ("pdm run pytest backend/tests/test_opencode_config_validation.py -q", "deny"),
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
    ],
)
def test_actual_implementer_contract_allows_only_canonical_mutations(
    command: str, expected: str
) -> None:
    rules = expected_permission_contract(Path(__file__).parents[2], "implementer")

    assert effective_action(rules, command) == expected


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
