#!/usr/bin/env python3
"""Validate the installed OpenCode CLI against the repository runtime contract."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validation.validate_opencode_config import (  # noqa: E402
    ASK_ACTION,
    AUTO_SUBAGENTS,
    PLUGIN,
    ROLES,
    Validation,
    bash_allow_contract_errors,
    load_agent,
    load_json,
    strict_scandir_files,
    validate_local_configs,
)

MODEL_IDS = {contract[0] for contract in ROLES.values()}
READ_ONLY_ROLES = set(ROLES) - {"implementer", "pr-test-analyzer"}
GOVERNANCE_ENV_PREFIX = "OPENCODE_CONFIG"
GOVERNANCE_ENV_EXACT = {"OPENCODE_PERMISSION"}
BUILTIN_AGENT_NAMES = {
    "build",
    "compaction",
    "explore",
    "general",
    "plan",
    "summary",
    "title",
}


class RuntimeContractError(RuntimeError):
    """A sanitized OpenCode runtime-contract failure."""


def parse_json_object(output: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeContractError(f"{label} output is not valid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeContractError(f"{label} output is not a JSON object")
    return value


def effective_action(rules: list[dict[str, Any]], command: str) -> str | None:
    action = None
    for rule in rules:
        pattern = rule.get("pattern")
        if (
            rule.get("permission") == "bash"
            and isinstance(pattern, str)
            and fnmatch.fnmatchcase(command, pattern)
        ):
            candidate = rule.get("action")
            action = candidate if isinstance(candidate, str) else action
    return action


def governance_environment(
    base: Mapping[str, str], *, controlled: dict[str, str]
) -> dict[str, str]:
    unexpected = sorted(
        name
        for name, value in base.items()
        if (name.startswith(GOVERNANCE_ENV_PREFIX) or name in GOVERNANCE_ENV_EXACT)
        and value != controlled.get(name)
    )
    if unexpected:
        raise RuntimeContractError("unexpected governance environment override")
    env = {
        key: value
        for key, value in base.items()
        if not key.startswith(GOVERNANCE_ENV_PREFIX) and key not in GOVERNANCE_ENV_EXACT
    }
    env |= controlled
    return env


def validate_mcp_status(output: str, expected: set[str]) -> list[str]:
    lowered = re.sub(r"\x1b\[[0-9;]*m", "", output).lower()
    lines = lowered.splitlines()
    errors: list[str] = []
    for name in sorted(expected):
        matching = [
            line for line in lines if re.search(rf"\b{re.escape(name.lower())}\b", line)
        ]
        if not any(
            re.search(r"\bconnected\b", line) and "not connected" not in line
            for line in matching
        ):
            errors.append(f"MCP {name} is not connected")
    return errors


def is_generated_tool_output_rule(value: object, expected_pattern: str) -> bool:
    if not isinstance(value, dict) or set(value) != {"permission", "pattern", "action"}:
        return False
    pattern = value.get("pattern")
    return (
        value.get("permission") == "external_directory"
        and value.get("action") == "allow"
        and pattern == expected_pattern
    )


def validate_permission_contract(
    resolved: list[object],
    expected: list[dict[str, Any]],
    *,
    generated_tool_output_pattern: str | None = None,
) -> list[str]:
    project_suffix = resolved[-len(expected) :]
    if any(
        isinstance(rule, dict)
        and rule.get("permission") == "bash"
        and rule.get("action") == ASK_ACTION
        for rule in project_suffix
    ):
        return [
            f"resolved project permission suffix contains a bash {ASK_ACTION} action"
        ]
    if project_suffix == expected:
        return []
    if (
        len(resolved) > len(expected)
        and resolved[-len(expected) - 1 : -1] == expected
        and generated_tool_output_pattern is not None
        and is_generated_tool_output_rule(resolved[-1], generated_tool_output_pattern)
    ):
        return []
    return ["resolved permission contract is not the exact effective project suffix"]


def expected_permission_contract(root: Path, name: str) -> list[dict[str, Any]]:
    validation = Validation(root)
    metadata, _ = load_agent(root / ".opencode" / "agent" / f"{name}.md", validation)
    if validation.errors:
        raise RuntimeContractError(f"cannot read repository permissions for {name}")
    permission = metadata.get("permission")
    if not isinstance(permission, dict):
        raise RuntimeContractError(f"repository permissions for {name} are invalid")
    allow_errors = bash_allow_contract_errors(name, metadata)
    if allow_errors:
        raise RuntimeContractError(allow_errors[0])
    rules: list[dict[str, Any]] = []
    for tool, value in permission.items():
        if isinstance(value, str):
            rules.append({"permission": tool, "pattern": "*", "action": value})
        elif isinstance(value, dict):
            rules.extend(
                {"permission": tool, "pattern": pattern, "action": action}
                for pattern, action in value.items()
            )
    return rules


def configured_model(agent: dict[str, Any]) -> str | None:
    model = agent.get("model")
    if isinstance(model, str):
        return model
    if isinstance(model, dict):
        provider = model.get("providerID")
        model_id = model.get("modelID")
        if isinstance(provider, str) and isinstance(model_id, str):
            return f"{provider}/{model_id}"
    return None


def expected_agent_commands(name: str) -> tuple[tuple[str, str], ...]:
    if name == "ontoprism-team":
        return (
            ("git status --porcelain", "allow"),
            (
                "gh pr merge 123 --squash --delete-branch --subject fix:example",
                "allow",
            ),
            ("gh pr merge 123", "deny"),
            ("gh pr merge 123 --admin", "deny"),
            ("touch forbidden", "deny"),
        )
    if name in READ_ONLY_ROLES:
        return (("touch forbidden", "deny"), ("git status --porcelain", "allow"))
    if name == "pr-test-analyzer":
        return (
            ("cp source target", "allow"),
            ("git status --porcelain", "allow"),
            (
                "pdm run agent-test backend/tests/test_opencode_config_validation.py",
                "allow",
            ),
            ("git commit", "deny"),
            ("git push --force", "deny"),
            ("gh pr merge", "deny"),
            ("touch forbidden", "deny"),
            ("cp source target\ngh pr merge", "deny"),
            ("pdm run agent-test backend/tests/test_x.py\r\ngit push", "deny"),
        )
    return (
        ("git commit change", "deny"),
        ("git commit -m change", "deny"),
        ("pdm run verify", "allow"),
        (
            "pdm run agent-test backend/tests/test_opencode_config_validation.py -q",
            "allow",
        ),
        ("pdm run lint", "allow"),
        ("npm --prefix frontend run test:coverage", "allow"),
        ("git push --force", "deny"),
        ("gh pr merge", "deny"),
        ("npm publish", "deny"),
        ("pdm run gh pr merge", "deny"),
        ("pdm run git push --force", "deny"),
        ("pdm run publish", "deny"),
        ("npm exec gh pr merge", "deny"),
        ("npm run publish", "deny"),
        ("npx gh pr merge", "deny"),
        ("pdm run verify && gh pr merge", "deny"),
        ("pdm run agent-test backend/tests/test_x.py\ngh pr merge", "deny"),
        ("pdm run agent-git switch-new feat/x", "allow"),
        ("git switch --discard-changes main", "deny"),
        ("git branch --force feat/x", "deny"),
        ("git merge --no-ff feat/x", "deny"),
    )


def validate_agent_permissions(
    name: str, bash_rules: list[dict[str, Any]]
) -> list[str]:
    errors: list[str] = []
    catch_alls = [
        rule
        for rule in bash_rules
        if rule.get("permission") == "bash" and rule.get("pattern") == "*"
    ]
    expected_catch_all = "deny"
    if not catch_alls or catch_alls[-1].get("action") != expected_catch_all:
        errors.append(f"{name} resolved bash catch-all must be {expected_catch_all}")
    for command, expected in expected_agent_commands(name):
        if effective_action(bash_rules, command) != expected:
            errors.append(f"{name} effective action for {command} must be {expected}")
    return errors


def validate_resolved_agent(name: str, agent: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_model, expected_mode, _, _ = ROLES[name]
    if configured_model(agent) != expected_model:
        errors.append(f"{name} resolved model is not repository-required")
    if agent.get("mode") != expected_mode:
        errors.append(f"{name} resolved mode is not repository-required")
    rules = agent.get("permission")
    if not isinstance(rules, list):
        return [*errors, f"{name} resolved permissions are not an array"]
    errors.extend(
        validate_agent_permissions(
            name, [rule for rule in rules if isinstance(rule, dict)]
        )
    )
    task_rules = [
        rule
        for rule in rules
        if isinstance(rule, dict) and rule.get("permission") == "task"
    ]
    if name == "ontoprism-team":
        # `opencode debug agent` exposes task rule patterns as agent names. This
        # validates that resolved map without dispatching an agent or model call.
        expected_task = [
            {"permission": "task", "pattern": "*", "action": "deny"},
            *(
                {"permission": "task", "pattern": agent_name, "action": "allow"}
                for agent_name in AUTO_SUBAGENTS
            ),
        ]
        if task_rules[-len(expected_task) :] != expected_task:
            errors.append("ontoprism-team resolved task delegation is not exact")
    return errors


def validate_implementer_config(agents: object) -> list[str]:
    if not isinstance(agents, dict) or not isinstance(agents.get("implementer"), dict):
        return ["resolved implementer config is missing"]
    implementer = agents["implementer"]
    errors: list[str] = []
    if implementer.get("model") != "openai/gpt-5.6-sol":
        errors.append("resolved implementer model is incorrect")
    fallback = implementer.get("fallback_models")
    options = implementer.get("options")
    if fallback is None and isinstance(options, dict):
        fallback = options.get("fallback_models")
    if fallback != ["github-copilot/gpt-5.6-sol"]:
        errors.append("resolved implementer fallback is incorrect")
    return errors


def validate_resolved_config(
    config: dict[str, Any], *, require_local_markers: bool
) -> list[str]:
    errors: list[str] = []
    if config.get("default_agent") != "ontoprism-team":
        errors.append("resolved default agent is not ontoprism-team")
    if config.get("plugin") != [PLUGIN]:
        errors.append("resolved plugin list is not the pinned repository plugin")
    command = config.get("command")
    review = command.get("review-pr") if isinstance(command, dict) else None
    if not isinstance(review, dict) or review.get("agent") != "ontoprism-team":
        errors.append("resolved review-pr command does not use ontoprism-team")
    errors.extend(validate_implementer_config(config.get("agent")))
    if require_local_markers:
        for marker in ("lsp", "mcp"):
            if not isinstance(config.get(marker), dict) or not config[marker]:
                errors.append(f"resolved local {marker} marker is missing")
    return errors


def run_command(
    arguments: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    operation: str,
    display_command: str,
    timeout: float = 120,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(  # noqa: S603 - fixed opencode commands only
            arguments,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeContractError(
            f"{operation}: {display_command} executable is unavailable"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeContractError(f"{operation}: {display_command} timed out") from exc
    except UnicodeDecodeError as exc:
        raise RuntimeContractError(
            f"{operation}: {display_command} produced undecodable output"
        ) from exc
    except OSError as exc:
        raise RuntimeContractError(
            f"{operation}: {display_command} could not start"
        ) from exc
    if result.returncode != 0:
        raise RuntimeContractError(
            f"{operation}: {display_command} exited {result.returncode}"
        )
    return result


def isolated_environment(directory: Path, base: dict[str, str]) -> dict[str, str]:
    env = base.copy()
    for variable, name in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_STATE_HOME", "state"),
    ):
        target = directory / name
        target.mkdir()
        env[variable] = str(target)
    env["OPENCODE_DISABLE_EXTERNAL_SKILLS"] = "1"
    env["OPENCODE_DISABLE_CLAUDE_CODE_SKILLS"] = "1"
    return env


def local_config_exists(project: Path) -> bool:
    return any(
        (project / relative).is_file()
        for relative in (
            ".opencode/opencode.json",
            ".opencode/opencode.jsonc",
        )
    )


def collect_local_mcp(config: dict[str, Any]) -> tuple[set[str], list[str]]:
    names: set[str] = set()
    errors: list[str] = []
    mcp = config.get("mcp", {})
    if not isinstance(mcp, dict):
        return names, ["machine-local MCP configuration is invalid"]
    for name, entry in mcp.items():
        if not isinstance(entry, dict):
            errors.append(f"MCP {name} is invalid")
        elif entry.get("enabled", True) is not False:
            names.add(name)
    return names, errors


def local_runtime_contract(project: Path) -> tuple[set[str], list[str]]:
    mcp_names: set[str] = set()
    errors: list[str] = []
    local_paths = [
        project / relative
        for relative in (".opencode/opencode.json", ".opencode/opencode.jsonc")
        if (project / relative).is_file()
    ]
    if len(local_paths) > 1:
        return set(), ["machine-local JSON and JSONC cannot coexist"]
    for path in local_paths:
        validation = Validation(project)
        config = load_json(path, validation, "LOCAL_CONFIG")
        if validation.errors or config is None:
            errors.append("machine-local OpenCode config cannot be parsed")
            continue
        local_mcp, mcp_errors = collect_local_mcp(config)
        mcp_names |= local_mcp
        errors.extend(mcp_errors)
    return mcp_names, errors


def read_plugin_evidence(isolated: Path) -> str:
    """Read plugin evidence strictly without exposing local bytes or paths."""
    try:
        paths = strict_scandir_files(isolated, suffix=".log")
        plugin_source = (
            isolated
            / "cache/opencode/packages/@razroo/opencode-model-fallback@0.3.2"
            / "node_modules/@razroo/opencode-model-fallback/dist/index.js"
        )
        if plugin_source.is_file():
            paths.append(plugin_source)
    except OSError as exc:
        raise RuntimeContractError(
            "Plugin sentinel validation: evidence could not be read"
        ) from exc
    evidence = ""
    for path in paths:
        try:
            evidence += path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeContractError(
                "Plugin sentinel validation: evidence produced undecodable output"
            ) from exc
        except OSError as exc:
            raise RuntimeContractError(
                "Plugin sentinel validation: evidence could not be read"
            ) from exc
    return evidence


def validate_plugin_sentinel(
    root: Path, env: dict[str, str], isolated: Path
) -> str | None:
    sentinel_env = governance_environment(
        env,
        controlled={
            "OPENCODE_CONFIG_CONTENT": json.dumps(
                {"agent": {"implementer": {"fallback_models": ["invalid-sentinel"]}}}
            )
        },
    )
    sentinel = run_command(
        ["opencode", "debug", "config", "--print-logs", "--log-level", "DEBUG"],
        cwd=root,
        env=sentinel_env,
        operation="Plugin sentinel validation",
        display_command="opencode debug config",
    )
    evidence = sentinel.stdout + sentinel.stderr
    evidence += read_plugin_evidence(isolated)
    markers = ("Invalid fallback_models entry", "implementer", "invalid-sentinel")
    if all(marker in evidence for marker in markers):
        return None
    return "fallback plugin did not report the isolated implementer sentinel"


def validate_layered_project(
    root: Path, project: Path, env: dict[str, str], expected_mcp: set[str]
) -> list[str]:
    layered_env = governance_environment(
        env,
        controlled={
            "OPENCODE_CONFIG": str(root / "opencode.json"),
            "OPENCODE_CONFIG_DIR": str(root / ".opencode"),
        },
    )
    layered = parse_json_object(
        run_command(
            ["opencode", "debug", "config"],
            cwd=project,
            env=layered_env,
            operation="Layered resolved config",
            display_command="opencode debug config",
        ).stdout,
        "layered debug config",
    )
    errors = validate_resolved_config(
        layered, require_local_markers=local_config_exists(project)
    )
    layered_agents = layered.get("agent")
    if not isinstance(layered_agents, dict) or not set(ROLES) <= set(layered_agents):
        errors.append("layered project agents are incomplete")
    generated_pattern = (
        Path(layered_env["XDG_DATA_HOME"]) / "opencode" / "tool-output" / "*"
    ).as_posix()
    for name in ROLES:
        agent = parse_json_object(
            run_command(
                ["opencode", "debug", "agent", name],
                cwd=project,
                env=layered_env,
                operation=f"Layered resolved agent {name}",
                display_command=f"opencode debug agent {name}",
            ).stdout,
            f"layered debug agent {name}",
        )
        errors.extend(validate_resolved_agent(name, agent))
        resolved_rules = agent.get("permission")
        if isinstance(resolved_rules, list):
            errors.extend(
                validate_permission_contract(
                    list(resolved_rules),
                    expected_permission_contract(root, name),
                    generated_tool_output_pattern=generated_pattern,
                )
            )
    if expected_mcp:
        status = run_command(
            ["opencode", "mcp", "list"],
            cwd=project,
            env=layered_env,
            operation="MCP connection check",
            display_command="opencode mcp list",
        )
        errors.extend(validate_mcp_status(status.stdout + status.stderr, expected_mcp))
    return errors


def validate_native_agents(root: Path, env: dict[str, str]) -> list[str]:
    errors: list[str] = []
    listed = run_command(
        ["opencode", "agent", "list"],
        cwd=root,
        env=env,
        operation="Agent inventory",
        display_command="opencode agent list",
    ).stdout
    listed_names = {
        line.split(" (", 1)[0] for line in listed.splitlines() if " (" in line
    }
    if listed_names != set(ROLES) | BUILTIN_AGENT_NAMES:
        errors.append("runtime agent inventory is not the exact allowed set")
    generated_pattern = (
        Path(env["XDG_DATA_HOME"]) / "opencode" / "tool-output" / "*"
    ).as_posix()
    for name in ROLES:
        agent = parse_json_object(
            run_command(
                ["opencode", "debug", "agent", name],
                cwd=root,
                env=env,
                operation=f"Resolved agent {name}",
                display_command=f"opencode debug agent {name}",
            ).stdout,
            f"debug agent {name}",
        )
        errors.extend(validate_resolved_agent(name, agent))
        resolved_rules = agent.get("permission")
        if isinstance(resolved_rules, list):
            errors.extend(
                validate_permission_contract(
                    list(resolved_rules),
                    expected_permission_contract(root, name),
                    generated_tool_output_pattern=generated_pattern,
                )
            )
    return errors


def validate_model_catalog(root: Path, env: dict[str, str]) -> list[str]:
    models = set(
        run_command(
            ["opencode", "models"],
            cwd=root,
            env=env,
            operation="Model catalog validation",
            display_command="opencode models",
        ).stdout.splitlines()
    )
    errors: list[str] = []
    if not models >= MODEL_IDS:
        errors.append("configured model IDs are absent from the local catalog")
    return errors


def validate_runtime(root: Path, project: Path) -> None:
    if shutil.which("opencode") is None:
        raise RuntimeContractError("opencode CLI is not installed")
    local_validation = Validation(project)
    validate_local_configs(local_validation)
    if local_validation.errors:
        raise RuntimeContractError("machine-local OpenCode governance is invalid")
    expected_mcp, local_errors = local_runtime_contract(project)
    if local_errors:
        raise RuntimeContractError("; ".join(local_errors))
    base_env = governance_environment(os.environ, controlled={})

    with tempfile.TemporaryDirectory(prefix="opencode-runtime-") as temporary:
        isolated = Path(temporary)
        env = isolated_environment(isolated, base_env)
        config = parse_json_object(
            run_command(
                ["opencode", "debug", "config"],
                cwd=root,
                env=env,
                operation="Resolved config validation",
                display_command="opencode debug config",
            ).stdout,
            "debug config",
        )
        errors = validate_resolved_config(config, require_local_markers=False)
        errors.extend(validate_native_agents(root, env))
        errors.extend(validate_model_catalog(root, base_env))
        run_command(
            ["opencode", "debug", "startup"],
            cwd=root,
            env=env,
            operation="Startup validation",
            display_command="opencode debug startup",
        )
        sentinel_error = validate_plugin_sentinel(root, env, isolated)
        if sentinel_error:
            errors.append(sentinel_error)
        errors.extend(validate_layered_project(root, project, env, expected_mcp))

    if errors:
        raise RuntimeContractError("; ".join(dict.fromkeys(errors)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--project", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    project = (args.project or root).resolve()
    try:
        validate_runtime(root, project)
    except RuntimeContractError as exc:
        print(f"OpenCode runtime validation failed: {exc}")
        return 1
    print("OpenCode runtime validation passed (no model calls).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
