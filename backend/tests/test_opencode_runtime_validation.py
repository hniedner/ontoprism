from __future__ import annotations

import json

import pytest
from scripts.validation.validate_opencode_runtime import (
    RuntimeContractError,
    parse_json_object,
    validate_resolved_agent,
    validate_resolved_config,
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
