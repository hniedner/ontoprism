from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING

import pytest
from scripts.validation.validate_opencode_runtime import (
    RuntimeContractError,
    effective_action,
    governance_environment,
    parse_json_object,
    redact_diagnostic,
    run_command,
    validate_mcp_status,
    validate_permission_contract,
    validate_resolved_agent,
    validate_resolved_config,
)

if TYPE_CHECKING:
    from pathlib import Path

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
    assert "resolved permission contract omits or reorders project rules" in (
        validate_permission_contract(list(reversed(expected)), expected)
    )


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


def test_governance_environment_rejects_uncontrolled_overrides() -> None:
    with pytest.raises(RuntimeContractError, match="unexpected governance environment"):
        governance_environment(
            {"OPENCODE_CONFIG_CONTENT": "injected"},
            controlled={},
        )


def test_diagnostics_are_bounded_and_redacted() -> None:
    raw = (
        "failure under /" + "Users/example/project with "
        "postgresql"
        + "://user:password@host/db and api_"
        + "key=secret-value "
        + "x" * 500
    )

    diagnostic = redact_diagnostic(raw)

    assert "/" + "Users/" not in diagnostic
    assert "postgresql" + "://" not in diagnostic
    assert "secret-value" not in diagnostic
    assert len(diagnostic) <= 240


def test_mcp_status_requires_each_expected_server_connected() -> None:
    output = "postgres \x1b[90mconnected\nsqlite \x1b[90mconnected\n"

    assert validate_mcp_status(output, {"postgres", "sqlite"}) == []
    assert "MCP sqlite is not connected" in validate_mcp_status(
        "postgres connected\nsqlite failed\n", {"postgres", "sqlite"}
    )


def test_command_errors_distinguish_missing_nonzero_and_timeout(tmp_path: Path) -> None:
    with pytest.raises(RuntimeContractError, match="executable is unavailable"):
        run_command(
            ["definitely-absent-opencode-test"], cwd=tmp_path, env=os.environ.copy()
        )

    secret = "api_" + "key=do-not-print"
    with pytest.raises(RuntimeContractError) as nonzero:
        run_command(
            [
                sys.executable,
                "-c",
                f"import sys;sys.stderr.write({secret!r});sys.exit(2)",
            ],
            cwd=tmp_path,
            env=os.environ.copy(),
        )
    assert "do-not-print" not in str(nonzero.value)
    assert "exited 2" in str(nonzero.value)

    with pytest.raises(RuntimeContractError, match="command timed out"):
        run_command(
            [sys.executable, "-c", "import time;time.sleep(1)"],
            cwd=tmp_path,
            env=os.environ.copy(),
            timeout=0.01,
        )
