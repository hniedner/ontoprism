from __future__ import annotations

import json
import os
import shutil
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from scripts.validation.validate_opencode_config import (
    RESERVES,
    ROLES,
    SPECIALIST_ROLES,
    Validation,
    load_agent,
    safe_read_text,
    validate,
    validate_forbidden_content,
)

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parents[2]
FULL_STORE_TIMEOUT_PROMPTS = (
    ".opencode/agent/implementer.md",
    ".opencode/agent/ontoprism-team.md",
    ".opencode/command/review-pr.md",
)


@pytest.fixture
def config_root(tmp_path: Path) -> Path:
    for name in ("opencode.json", "AGENTS.md", ".gitignore", "pyproject.toml"):
        shutil.copy2(ROOT / name, tmp_path / name)
    (tmp_path / ".opencode").mkdir()
    for directory in ("agent", "command"):
        shutil.copytree(
            ROOT / ".opencode" / directory, tmp_path / ".opencode" / directory
        )
    scripts = tmp_path / "scripts" / "validation"
    scripts.mkdir(parents=True)
    shutil.copy2(
        ROOT / "scripts/validation/run_agent_github.py",
        scripts / "run_agent_github.py",
    )
    return tmp_path


def replace(root: Path, relative: str, old: str, new: str) -> None:
    path = root / relative
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new, 1))


def replace_full_store_timeout_contract(
    root: Path, relative: str, replacement: str
) -> None:
    path = root / relative
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if "podman-test-full-store" not in line
    ]
    path.write_text("\n".join([*lines, "", replacement, ""]), encoding="utf-8")


def update_root_config(root: Path, update: Callable[[dict[str, object]], None]) -> None:
    path = root / "opencode.json"
    config = json.loads(path.read_text())
    update(config)
    path.write_text(json.dumps(config))


def test_checked_out_opencode_configuration_is_valid(config_root: Path) -> None:
    assert validate(config_root) == []


@pytest.mark.parametrize("relative", FULL_STORE_TIMEOUT_PROMPTS)
def test_full_store_prompt_requires_outer_bash_timeout_on_first_attempt(
    config_root: Path, relative: str
) -> None:
    prompt = (config_root / relative).read_text(encoding="utf-8").lower()

    for semantic_token in (
        "pdm run agent-replay podman-test-full-store",
        "bash tool",
        "outer",
        "3600000",
        "first attempt",
        "internal timeout",
        "never start",
        "default",
        "shorter",
        "retry",
    ):
        assert semantic_token in prompt
    assert "podman-test-full-store --timeout" not in prompt


@pytest.mark.parametrize("relative", FULL_STORE_TIMEOUT_PROMPTS)
@pytest.mark.parametrize(
    "replacement",
    [
        (
            "The wrapper for `pdm run agent-replay podman-test-full-store` has an "
            "internal timeout of 3600 seconds."
        ),
        (
            "Invoke `pdm run agent-replay podman-test-full-store` through the Bash "
            "tool with its outer timeout set to a shorter value, then retry "
            "with 3600000 milliseconds."
        ),
    ],
)
def test_full_store_timeout_gate_rejects_internal_or_retry_only_guidance(
    config_root: Path, relative: str, replacement: str
) -> None:
    replace_full_store_timeout_contract(config_root, relative, replacement)

    assert any(
        error.startswith("FULL_STORE_TIMEOUT:") for error in validate(config_root)
    )


@pytest.mark.parametrize("relative", FULL_STORE_TIMEOUT_PROMPTS)
def test_full_store_timeout_gate_rejects_fake_shell_timeout_flag(
    config_root: Path, relative: str
) -> None:
    path = config_root / relative
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\n`pdm run agent-replay podman-test-full-store --timeout 3600000`\n",
        encoding="utf-8",
    )

    assert any(
        error.startswith("FULL_STORE_TIMEOUT:") and "shell flag" in error
        for error in validate(config_root)
    )


@pytest.mark.parametrize("relative", FULL_STORE_TIMEOUT_PROMPTS)
def test_full_store_timeout_gate_rejects_stale_wrong_timeout(
    config_root: Path, relative: str
) -> None:
    path = config_root / relative
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\nThe previous outer timeout was 1200000 milliseconds.\n",
        encoding="utf-8",
    )

    assert any(
        error.startswith("FULL_STORE_TIMEOUT:") and "stale" in error
        for error in validate(config_root)
    )


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ("agent-github", "python scripts/validation/run_agent_github.py"),
        (
            "agent-github-read",
            "python scripts/validation/run_agent_github.py --read-only",
        ),
    ],
)
def test_github_wrapper_scripts_are_exact(
    config_root: Path, script: str, expected: str
) -> None:
    pyproject = config_root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    pyproject.write_text(
        text.replace(f'{script} = "{expected}"', f'{script} = "python unsafe.py"'),
        encoding="utf-8",
    )

    assert f"GITHUB_WRAPPER: {script} script is not exact" in validate(config_root)


def test_github_wrapper_implementation_is_required(config_root: Path) -> None:
    (config_root / "scripts/validation/run_agent_github.py").unlink()

    assert (
        "GITHUB_WRAPPER: required file missing: scripts/validation/run_agent_github.py"
        in validate(config_root)
    )


@pytest.mark.parametrize("suffix", ["admin", "auto", "queue", "bypass"])
def test_orchestrator_requires_dangerous_merge_suffix_denies(
    config_root: Path, suffix: str
) -> None:
    relative = ".opencode/agent/ontoprism-team.md"
    rule = f'    "gh pr merge *--{suffix}*": deny\n'
    replace(config_root, relative, rule, "")

    assert any(f"gh pr merge *--{suffix}*" in error for error in validate(config_root))


def test_fallback_plugin_reintroduction_is_rejected(config_root: Path) -> None:
    update_root_config(
        config_root,
        lambda config: config.update(
            {"plugin": ["@razroo/opencode-model-fallback@0.3.2"]}
        ),
    )

    assert "ROOT_CONFIG: external plugins are forbidden" in validate(config_root)


@pytest.mark.parametrize(
    "relative",
    [
        ".opencode/agent/ontoprism-team.md",
        ".opencode/command/review-pr.md",
    ],
)
def test_task_reconciliation_guard_is_required(
    config_root: Path, relative: str
) -> None:
    replace(
        config_root,
        relative,
        "Never infer from silence",
        "treat silence as completion",
    )

    assert any(
        "missing required semantics: never infer from silence" in error
        for error in validate(config_root)
    )


def test_milestone_prompt_requires_agent_git_merge_wrapper(config_root: Path) -> None:
    assert (
        any(
            "missing required semantics: pdm run agent-git merge-no-ff <branch>"
            in error
            for error in validate(config_root)
        )
        is False
    )

    replace(
        config_root,
        ".opencode/agent/ontoprism-team.md",
        "pdm run agent-git merge-no-ff <branch>",
        "git merge --no-ff <branch>",
    )

    assert any(
        "missing required semantics: pdm run agent-git merge-no-ff <branch>" in error
        for error in validate(config_root)
    )


def test_repository_agent_guidance_names_only_the_merge_wrapper() -> None:
    guidance = Path("AGENTS.md").read_text(encoding="utf-8")

    assert "local `pdm run agent-git merge-no-ff <branch>` integration" in guidance
    assert "local `git merge --no-ff` integration" not in guidance


def test_dead_fallback_plugin_artifact_is_rejected(config_root: Path) -> None:
    relative = ".opencode/opencode-model-fallback.jsonc"
    (config_root / relative).write_text("{}")

    assert f"FILES: stale {relative} must be absent" in validate(config_root)


@pytest.mark.parametrize("role", sorted(set(ROLES) - {"ontoprism-team", "implementer"}))
@pytest.mark.parametrize(
    "pattern",
    ["git push origin main", "unknown future command"],
)
def test_every_project_agent_rejects_any_bash_ask_action(
    config_root: Path, role: str, pattern: str
) -> None:
    relative = f".opencode/agent/{role}.md"
    replace(
        config_root,
        relative,
        '    "*$*": deny\n',
        f'    "*$*": deny\n    "{pattern}": ask\n',
    )

    assert f"ROLE_PERMISSION: {role} has forbidden bash ask {pattern}" in validate(
        config_root
    )


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("opencode.json", "FILES: required file missing: opencode.json"),
        (
            ".opencode/agent/architect.md",
            "FILES: required file missing: .opencode/agent/architect.md",
        ),
    ],
)
def test_required_files_are_enforced(
    config_root: Path, relative: str, expected: str
) -> None:
    (config_root / relative).unlink()

    assert expected in validate(config_root)


@pytest.mark.parametrize(
    ("relative", "code"),
    [
        (".opencode/agent/architect.md", "ROLE_BODY"),
        (".opencode/command/review-pr.md", "REVIEW_COMMAND"),
        ("AGENTS.md", "AGENTS_PROCESS"),
    ],
)
def test_invalid_utf8_is_categorized_for_governance_inputs(
    config_root: Path, relative: str, code: str
) -> None:
    (config_root / relative).write_bytes(b"\xff")

    assert f"{code}: invalid UTF-8 in {relative}" in validate(config_root)


@pytest.mark.parametrize(
    ("relative", "code"),
    [
        (".opencode/agent/architect.md", "ROLE_BODY"),
        (".opencode/command/review-pr.md", "REVIEW_COMMAND"),
        ("AGENTS.md", "AGENTS_PROCESS"),
    ],
)
def test_read_races_are_categorized_without_traceback(
    config_root: Path,
    relative: str,
    code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = config_root / relative
    original = Path.read_text

    def fail_target(path: Path, *args: object, **kwargs: object) -> str:
        if path == target:
            raise OSError("injected read race")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_target)

    assert safe_read_text(target, Validation(config_root), code) is None
    assert f"{code}: cannot read {relative}" in validate(config_root)


def test_required_file_metadata_error_is_categorized(
    config_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = config_root / "opencode.json"
    original = Path.is_file

    def fail_target(path: Path) -> bool:
        if path == target:
            raise PermissionError("denied")
        return original(path)

    monkeypatch.setattr(Path, "is_file", fail_target)

    assert "FILES: cannot inspect opencode.json" in validate(config_root)


@pytest.mark.parametrize("operation", ["is_dir", "scandir"])
def test_agent_inventory_traversal_error_is_categorized(
    config_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    target = config_root / ".opencode/agent"
    if operation == "is_dir":
        original = Path.is_dir

        def fail_target(path: Path) -> object:
            if path == target:
                raise OSError("denied")
            return original(path)

        monkeypatch.setattr(Path, "is_dir", fail_target)
    else:
        original_scandir = os.scandir

        def fail_scandir(path: object) -> object:
            if Path(path) == target:
                raise OSError("denied")
            return original_scandir(path)

        monkeypatch.setattr(os, "scandir", fail_scandir)

    errors = validate(config_root)
    expected = "inspect" if operation == "is_dir" else "traverse"
    assert f"FILES: cannot {expected} .opencode/agent" in errors


def test_forbidden_content_traversal_error_is_categorized(
    config_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = config_root / ".opencode/command"
    original = os.scandir

    def fail_target(path: object) -> object:
        if Path(path) == target:
            raise OSError("denied")
        return original(path)

    monkeypatch.setattr(os, "scandir", fail_target)
    validation = Validation(config_root)

    validate_forbidden_content(validation)

    assert "FORBIDDEN_CONTENT: cannot traverse .opencode/command" in validation.errors


def test_nested_agent_scandir_error_is_categorized(
    config_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nested = config_root / ".opencode/agent/nested"
    nested.mkdir()
    original = os.scandir

    def fail_nested(path: object) -> object:
        if Path(path) == nested:
            raise PermissionError("denied")
        return original(path)

    monkeypatch.setattr(os, "scandir", fail_nested)

    assert "FILES: cannot traverse .opencode/agent/nested" in validate(config_root)


def test_plural_agent_inventory_nested_error_is_categorized(
    config_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plural = config_root / ".opencode/agents"
    plural.mkdir()
    original = os.scandir

    def fail_plural(path: object) -> object:
        if Path(path) == plural:
            raise PermissionError("denied")
        return original(path)

    monkeypatch.setattr(os, "scandir", fail_plural)

    assert "FILES: cannot traverse .opencode/agents" in validate(config_root)


def test_agent_inventory_does_not_follow_symlink_directories(
    config_root: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside-agents"
    outside.mkdir()
    shutil.copy2(config_root / ".opencode/agent/architect.md", outside / "writer.md")
    (config_root / ".opencode/agent/link").symlink_to(outside, target_is_directory=True)

    assert validate(config_root) == []


def test_agent_inventory_deletion_race_is_categorized(
    config_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = config_root / ".opencode/agent"
    vanished = target / "vanished.md"
    original = os.scandir

    class VanishedEntry:
        name = "vanished.md"
        path = str(vanished)

        @staticmethod
        def is_symlink() -> bool:
            return False

        @staticmethod
        def is_dir(*, follow_symlinks: bool) -> bool:
            return False

        @staticmethod
        def is_file(*, follow_symlinks: bool) -> bool:
            raise FileNotFoundError("deleted")

    class Entries:
        def __enter__(self) -> object:
            return iter([VanishedEntry()])

        def __exit__(self, *_args: object) -> None:
            return None

    def race(path: object) -> object:
        if Path(path) == target:
            return Entries()
        return original(path)

    monkeypatch.setattr(os, "scandir", race)

    assert "FILES: cannot traverse .opencode/agent/vanished.md" in validate(config_root)


def test_agent_inventory_post_scan_deletion_is_categorized(
    config_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = config_root / ".opencode/agent/architect.md"
    directory = target.parent
    original = os.scandir

    class VanishingEntry:
        name = target.name
        path = str(target)

        @staticmethod
        def is_symlink() -> bool:
            return False

        @staticmethod
        def is_dir(*, follow_symlinks: bool) -> bool:
            return False

        @staticmethod
        def is_file(*, follow_symlinks: bool) -> bool:
            target.unlink()
            return True

    class Entries:
        def __enter__(self) -> object:
            with original(directory) as entries:
                return iter(
                    [
                        VanishingEntry() if entry.name == target.name else entry
                        for entry in entries
                    ]
                )

        def __exit__(self, *_args: object) -> None:
            return None

    def vanish_after_scan(path: object) -> object:
        if Path(path) == directory:
            return Entries()
        return original(path)

    monkeypatch.setattr(os, "scandir", vanish_after_scan)

    assert "ROLE_BODY: cannot read .opencode/agent/architect.md" in validate(
        config_root
    )


def test_forbidden_content_post_scan_deletion_is_categorized(
    config_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = config_root / ".opencode/command/review-pr.md"
    directory = target.parent
    original = os.scandir

    class VanishingEntry:
        name = target.name
        path = str(target)

        @staticmethod
        def is_symlink() -> bool:
            return False

        @staticmethod
        def is_dir(*, follow_symlinks: bool) -> bool:
            return False

        @staticmethod
        def is_file(*, follow_symlinks: bool) -> bool:
            target.unlink()
            return True

    class Entries:
        def __enter__(self) -> object:
            with original(directory) as entries:
                return iter(
                    [
                        VanishingEntry() if entry.name == target.name else entry
                        for entry in entries
                    ]
                )

        def __exit__(self, *_args: object) -> None:
            return None

    def vanish_after_scan(path: object) -> object:
        if Path(path) == directory:
            return Entries()
        return original(path)

    monkeypatch.setattr(os, "scandir", vanish_after_scan)

    validation = Validation(config_root)
    validate_forbidden_content(validation)

    assert (
        "FORBIDDEN_CONTENT: cannot read .opencode/command/review-pr.md"
        in validation.errors
    )


@pytest.mark.parametrize(
    "pattern",
    [
        "git switch",
        "git branch",
        "git merge",
        "git switch --discard-changes *",
        "git branch --force *",
        "git merge -s ours *",
        "git -C . switch *",
        "git -c core.hooksPath=/tmp merge *",
        "git --no-pager switch *",
        "git -p switch *",
        "git --git-dir=.git branch --force *",
        "git --work-tree . merge --strategy ours *",
    ],
)
def test_unapproved_raw_git_branch_mutation_allows_are_rejected(
    config_root: Path, pattern: str
) -> None:
    replace(
        config_root,
        ".opencode/agent/architect.md",
        '    "git status --porcelain": allow\n',
        f'    "{pattern}": allow\n    "git status --porcelain": allow\n',
    )

    assert any(
        error.startswith("ROLE_PERMISSION: architect has unapproved bash allow")
        for error in validate(config_root)
    )


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
def test_each_role_class_rejects_every_extra_bash_allow(
    config_root: Path,
    role: str,
    existing_allow: str,
    extra_allow: str,
) -> None:
    replace(
        config_root,
        f".opencode/agent/{role}.md",
        f'    "{existing_allow}": allow\n',
        f'    "{extra_allow}": allow\n    "{existing_allow}": allow\n',
    )

    assert (
        f"ROLE_PERMISSION: {role} has unapproved bash allow {extra_allow}"
        in validate(config_root)
    )


@pytest.mark.parametrize(
    "pattern",
    [
        "git diff --no-ext-diff main...HEAD",
        "git diff --name-only main...HEAD",
    ],
)
def test_r3_requires_exact_committed_diff_scope_allows(
    config_root: Path, pattern: str
) -> None:
    replace(
        config_root,
        ".opencode/agent/pr-test-analyzer.md",
        f'    "{pattern}": allow\n',
        "",
    )

    assert f"R3_PERMISSION: R3 must allow {pattern}" in validate(config_root)


def test_stale_consolidated_reviewer_is_rejected(config_root: Path) -> None:
    shutil.copy2(
        config_root / ".opencode/agent/architect.md",
        config_root / ".opencode/agent/pr-reviewer.md",
    )

    assert "FILES: stale .opencode/agent/pr-reviewer.md must be absent" in validate(
        config_root
    )


def test_extra_project_agent_file_is_rejected(config_root: Path) -> None:
    shutil.copy2(
        config_root / ".opencode/agent/architect.md",
        config_root / ".opencode/agent/writer.md",
    )

    assert "FILES: unexpected project agent writer" in validate(config_root)


def test_duplicate_agent_name_across_supported_directories_is_rejected(
    config_root: Path,
) -> None:
    plural = config_root / ".opencode/agents"
    plural.mkdir()
    shutil.copy2(
        config_root / ".opencode/agent/architect.md",
        plural / "architect.md",
    )

    assert "FILES: duplicate project agent architect" in validate(config_root)


@pytest.mark.parametrize(
    ("relative", "old", "new", "expected"),
    [
        (
            "opencode.json",
            '"$schema": "https://opencode.ai/config.json"',
            '"$schema": "invalid"',
            "ROOT_CONFIG: root $schema",
        ),
        (
            "opencode.json",
            '"default_agent": "ontoprism-team"',
            '"default_agent": "implementer"',
            "DEFAULT_AGENT: default_agent must be ontoprism-team",
        ),
        (
            ".opencode/agent/ontoprism-team.md",
            "mode: primary",
            "mode: subagent",
            "DEFAULT_AGENT: configured default",
        ),
        (
            ".opencode/agent/ontoprism-team.md",
            "mode: primary",
            "mode: primary\nhidden: true",
            "DEFAULT_AGENT: ontoprism-team must be a visible primary",
        ),
        (
            ".opencode/agent/architect.md",
            "model: github-copilot/gpt-5.6-sol",
            "model: github-copilot/wrong",
            "ROLE_MODEL: architect model",
        ),
        (
            ".opencode/agent/architect.md",
            "mode: subagent",
            "mode: primary",
            "ROLE_MODE: architect mode",
        ),
        (
            ".opencode/agent/architect.md",
            "edit: deny",
            "edit: allow",
            "ROLE_PERMISSION: architect edit permission",
        ),
        (
            ".opencode/agent/implementer.md",
            "task: deny",
            "task: allow",
            "ROLE_PERMISSION: implementer task permission",
        ),
        (
            ".opencode/agent/architect.md",
            "description: Designs technical plans and acceptance contracts that "
            "preserve ONTOPRISM boundaries and end-to-end semantics.",
            "description: short",
            "ROLE_BODY: architect needs a nontrivial description",
        ),
        (
            ".opencode/agent/implementer.md",
            "model: openai/gpt-5.6-sol",
            "model: openai/gpt-5.6-sol\nfallback_models: []",
            "IMPLEMENTER_FALLBACK: fallback_models must be absent",
        ),
        (
            ".opencode/agent/ontoprism-team.md",
            "ordinary task",
            "standard task",
            "ORCHESTRATOR_PROCESS: ontoprism-team prompt missing required "
            "semantics: ordinary task",
        ),
        (
            ".opencode/agent/ontoprism-team.md",
            "milestone task",
            "release task",
            "ORCHESTRATOR_PROCESS: ontoprism-team prompt missing required "
            "semantics: milestone task",
        ),
        (
            ".opencode/agent/ontoprism-team.md",
            "explicitly authorizes that exact PR number",
            "A human handles completion.",
            "ORCHESTRATOR_PROCESS: ontoprism-team prompt missing required "
            "semantics: explicitly authorizes that exact PR number",
        ),
        (
            ".opencode/agent/bedrock-gpt-reserve.md",
            "explicit approval",
            "permission",
            "RESERVE_POLICY: bedrock-gpt-reserve prompt missing required "
            "semantics: approval",
        ),
        (
            ".opencode/agent/pr-test-analyzer.md",
            "outside the worktree",
            "elsewhere",
            "R3_ISOLATION: pr-test-analyzer prompt missing required semantics: "
            "outside the worktree",
        ),
        (
            ".opencode/command/review-pr.md",
            "agent: ontoprism-team",
            "agent: implementer",
            "REVIEW_COMMAND: review-pr command must use ontoprism-team",
        ),
        (
            ".opencode/command/review-pr.md",
            "R3 `pr-test-analyzer`",
            "R3 test reviewer",
            "REVIEW_COMMAND: review-pr command missing required semantics: "
            "pr-test-analyzer",
        ),
        (
            ".opencode/command/review-pr.md",
            "Do not claim merge readiness.",
            "Report ready to merge.",
            "REVIEW_COMMAND: review-pr must not make a merge-readiness claim",
        ),
    ],
)
def test_role_and_process_contract_mutations_are_rejected(
    config_root: Path,
    relative: str,
    old: str,
    new: str,
    expected: str,
) -> None:
    replace(config_root, relative, old, new)

    assert any(error.startswith(expected) for error in validate(config_root))


def test_duplicate_role_prompt_is_rejected(config_root: Path) -> None:
    architect = (config_root / ".opencode/agent/architect.md").read_text()
    architect_body = architect.split("\n---\n", 1)[1]
    ontology = config_root / ".opencode/agent/ontology-engineer.md"
    ontology_text = ontology.read_text()
    ontology.write_text(
        ontology_text.split("\n---\n", 1)[0] + "\n---\n" + architect_body
    )

    assert "ROLE_BODY: ontology-engineer duplicates architect prompt body" in validate(
        config_root
    )


def test_reserve_cannot_be_auto_dispatched(config_root: Path) -> None:
    replace(
        config_root,
        ".opencode/agent/ontoprism-team.md",
        "Reserve agents are manual tools and are never automatic routes.",
        "Automatically dispatch bedrock-gpt-reserve.",
    )

    assert (
        "RESERVE_POLICY: bedrock-gpt-reserve must not be named by automatic task routes"
        in validate(config_root)
    )


@pytest.mark.parametrize(
    ("relative", "old", "new", "expected"),
    [
        (
            ".opencode/agent/pr-test-analyzer.md",
            '    "git commit *": deny\n',
            "",
            "R3_PERMISSION: R3 must deny git commit *",
        ),
        (
            ".opencode/agent/pr-test-analyzer.md",
            '    "git status --porcelain": allow\n',
            '    "git commit *": deny\n    "git status --porcelain": allow\n',
            "R3_PERMISSION: pr-test-analyzer deny git commit * must follow all "
            "Git allow rules",
        ),
        (
            ".opencode/agent/pr-test-analyzer.md",
            '  bash:\n    "*": deny\n',
            "  bash:\n",
            "R3_PERMISSION: R3 bash permissions must place broad rule first",
        ),
        (
            ".opencode/agent/architect.md",
            '    "git show --stat --oneline HEAD": allow\n',
            '    "git reset *": deny\n    "git show --stat --oneline HEAD": allow\n',
            "ROLE_PERMISSION: architect deny git reset * must follow all Git "
            "allow rules",
        ),
    ],
)
def test_permission_denies_have_effective_last_match_order(
    config_root: Path,
    relative: str,
    old: str,
    new: str,
    expected: str,
) -> None:
    replace(config_root, relative, old, new)

    assert expected in validate(config_root)


def test_portable_local_config_ignore_is_required(config_root: Path) -> None:
    replace(
        config_root,
        ".gitignore",
        "/.opencode/opencode.json\n",
        "",
    )

    assert (
        "GITIGNORE: .gitignore must contain exact line /.opencode/opencode.json"
        in validate(config_root)
    )


@pytest.mark.parametrize(
    ("relative", "text", "expected"),
    [
        (
            "opencode.json",
            "\n// " + "/" + "Users/example/config\n",
            "FORBIDDEN_CONTENT: opencode.json contains local absolute user path",
        ),
        (
            "opencode.json",
            "\n// " + "postgresql" + "://user:pw@host/db\n",
            "FORBIDDEN_CONTENT: opencode.json contains database URL",
        ),
        (
            ".opencode/agent/architect.md",
            "\napi_" + "key=example\n",
            "FORBIDDEN_CONTENT: .opencode/agent/architect.md contains "
            "credential-shaped value",
        ),
        (
            ".opencode/agent/architect.md",
            "\n" + "tmp" + "/plans/note.md\n",
            "FORBIDDEN_CONTENT: .opencode/agent/architect.md contains temporary "
            "repository path",
        ),
    ],
)
def test_sensitive_local_content_is_rejected(
    config_root: Path, relative: str, text: str, expected: str
) -> None:
    path = config_root / relative
    path.write_text(path.read_text() + text)

    assert expected in validate(config_root)


def test_agents_governance_rules_are_required(config_root: Path) -> None:
    replace(
        config_root,
        "AGENTS.md",
        "- **Ephemeral planning/handover docs live in `tmp/plans/` "
        "(gitignored), never tracked.**",
        "- **Planning notes are not tracked.**",
    )

    assert any(
        error.startswith("AGENTS_GOVERNANCE:") for error in validate(config_root)
    )


@pytest.mark.parametrize(
    "role",
    sorted(SPECIALIST_ROLES - {"pr-test-analyzer"}),
)
def test_read_only_agents_require_deny_by_default(config_root: Path, role: str) -> None:
    replace(
        config_root,
        f".opencode/agent/{role}.md",
        '  bash:\n    "*": deny\n',
        '  bash:\n    "*": ask\n',
    )

    assert f"ROLE_PERMISSION: {role} bash catch-all must be deny" in validate(
        config_root
    )


def test_r3_requires_deny_by_default(config_root: Path) -> None:
    replace(
        config_root,
        ".opencode/agent/pr-test-analyzer.md",
        '  bash:\n    "*": deny\n',
        '  bash:\n    "*": ask\n',
    )

    assert "R3_PERMISSION: R3 bash catch-all must be deny" in validate(config_root)


@pytest.mark.parametrize(
    "pattern",
    [
        "git add",
        "git commit",
        "git merge",
        "git rebase",
        "git restore",
        "git checkout",
        "git reset",
        "git clean",
        "git stash",
        "git push",
        "gh pr",
    ],
)
def test_r3_requires_bare_mutation_denies(config_root: Path, pattern: str) -> None:
    replace(
        config_root,
        ".opencode/agent/pr-test-analyzer.md",
        f'    "{pattern}": deny\n',
        "",
    )

    assert f"R3_PERMISSION: R3 must deny {pattern}" in validate(config_root)


@pytest.mark.parametrize(
    "pattern",
    [
        "git reset --hard",
        "git clean",
        "git push -f*",
        "git push --force*",
        "gh pr",
        "gh pr merge",
        "npm publish",
        "pdm publish",
    ],
)
def test_implementer_requires_bare_dangerous_command_denies(
    config_root: Path, pattern: str
) -> None:
    replace(
        config_root,
        ".opencode/agent/implementer.md",
        f'    "{pattern}": deny\n',
        "",
    )

    assert f"ROLE_PERMISSION: implementer bash rule {pattern} must be deny" in validate(
        config_root
    )


def write_local_config(config_root: Path, config: dict[str, object]) -> None:
    (config_root / ".opencode/opencode.json").write_text(json.dumps(config))


def test_machine_local_lsp_and_mcp_are_allowed(config_root: Path) -> None:
    write_local_config(
        config_root,
        {
            "$schema": "https://opencode.ai/config.json",
            "lsp": {"local": {"disabled": True}},
            "mcp": {"local": {"enabled": False}},
        },
    )

    assert not any(error.startswith("LOCAL_CONFIG:") for error in validate(config_root))


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("default_agent", "build", "forbidden top-level key default_agent"),
        ("plugin", [], "forbidden top-level key plugin"),
        ("permission", {}, "forbidden top-level key permission"),
        ("provider", {}, "forbidden top-level key provider"),
        ("command", {}, "forbidden top-level key command"),
        ("instructions", [], "forbidden top-level key instructions"),
        (
            "agent",
            {"local-reviewer": {"model": "github-copilot/gpt-5.6-sol"}},
            "forbidden top-level key agent",
        ),
    ],
)
def test_machine_local_governance_overrides_are_rejected(
    config_root: Path,
    key: str,
    value: object,
    expected: str,
) -> None:
    write_local_config(config_root, {key: value})

    assert any(
        error.startswith(f"LOCAL_CONFIG: {expected}") for error in validate(config_root)
    )


@pytest.mark.parametrize("role", sorted(ROLES))
def test_all_project_agents_deny_unknown_tools_by_default(
    config_root: Path, role: str
) -> None:
    replace(
        config_root,
        f".opencode/agent/{role}.md",
        'permission:\n  "*": deny\n',
        "permission:\n",
    )

    assert f"ROLE_PERMISSION: {role} tool wildcard must be declared first" in validate(
        config_root
    )


@pytest.mark.parametrize("role", ["implementer", "ontoprism-team"])
def test_writer_agents_require_ask_by_default(config_root: Path, role: str) -> None:
    replace(
        config_root,
        f".opencode/agent/{role}.md",
        '  bash:\n    "*": ask\n',
        '  bash:\n    "*": deny\n',
    )

    assert f"ROLE_PERMISSION: {role} bash catch-all must be ask" in validate(
        config_root
    )


def test_review_command_requires_static_runtime_and_delegated_verify(
    config_root: Path,
) -> None:
    command = (config_root / ".opencode/command/review-pr.md").read_text()

    assert "pdm run validate-opencode-config" in command
    assert "pdm run validate-opencode-runtime" in command
    assert "dispatch `implementer` to run exact `pdm run verify`" in command


def test_authoritative_verify_starts_with_static_opencode_validation() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    verify = pyproject["tool"]["pdm"]["scripts"]["verify"]

    assert verify == "python -m scripts.validation.run_verify"


def test_agent_test_pdm_script_uses_repository_wrapper() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert pyproject["tool"]["pdm"]["scripts"]["agent-test"] == (
        "python scripts/validation/run_agent_test.py"
    )
    assert pyproject["tool"]["pdm"]["scripts"]["agent-git"] == (
        "python scripts/validation/run_agent_git.py"
    )
    assert pyproject["tool"]["pdm"]["scripts"]["agent-replay"] == (
        "python -m scripts.validation.run_agent_replay"
    )


def test_focused_test_guidance_requires_safe_full_store_wrapper() -> None:
    guidance = (ROOT / "AGENTS.md").read_text()
    implementer = (ROOT / ".opencode/agent/implementer.md").read_text()
    orchestrator = (ROOT / ".opencode/agent/ontoprism-team.md").read_text()

    assert "pdm run agent-test --full-store <node> -v" in guidance
    assert "full aggregate remains `pdm run test-integration-full-store`" in guidance
    for prompt in (implementer, orchestrator):
        assert "Never invoke raw `pdm run pytest`" in prompt
        assert "`pdm run agent-test --full-store <node> -v`" in prompt


@pytest.mark.parametrize(
    ("relative", "required", "code"),
    [
        (
            "AGENTS.md",
            "pdm run agent-test --full-store <node> -v",
            "AGENTS_PROCESS",
        ),
        (
            ".opencode/agent/implementer.md",
            "Never invoke raw `pdm run pytest`",
            "IMPLEMENTER_PROCESS",
        ),
        (
            ".opencode/agent/ontoprism-team.md",
            "Never invoke raw `pdm run pytest`",
            "ORCHESTRATOR_PROCESS",
        ),
    ],
)
def test_validator_enforces_safe_focused_test_guidance(
    config_root: Path, relative: str, required: str, code: str
) -> None:
    path = config_root / relative
    path.write_text(f"{path.read_text()}\n{required}\n")
    path.write_text(path.read_text().replace(required, "unsafe focused test guidance"))

    assert any(error.startswith(f"{code}:") for error in validate(config_root))


def test_machine_local_json_and_jsonc_are_both_ignored_and_mutually_exclusive(
    config_root: Path,
) -> None:
    ignores = (config_root / ".gitignore").read_text().splitlines()
    assert "/.opencode/opencode.json" in ignores
    assert "/.opencode/opencode.jsonc" in ignores

    write_local_config(config_root, {"mcp": {}})
    (config_root / ".opencode/opencode.jsonc").write_text('{"lsp": {}}')

    assert "LOCAL_CONFIG: machine-local JSON and JSONC cannot coexist" in validate(
        config_root
    )


def test_implementer_has_no_broad_package_manager_wrapper_allows(
    config_root: Path,
) -> None:
    implementer = (config_root / ".opencode/agent/implementer.md").read_text()

    for forbidden in (
        '"pdm install": allow',
        '"pdm install *": allow',
        '"pdm build *": allow',
        '"pdm *": allow',
        '"pdm run *": allow',
        '"npm *": allow',
        '"npx *": allow',
        '"npm test *": allow',
        '"npx vitest *": allow',
        '"git switch *": allow',
        '"git branch *": allow',
        '"git merge --no-ff *": allow',
        '"git commit": allow',
        '"git commit *": allow',
        '"pdm run pytest *": allow',
        '"pdm run test-integration-full-store *": allow',
    ):
        assert forbidden not in implementer
    for required in (
        '"pdm run verify": allow',
        '"pdm run agent-test *": allow',
        '"pdm run agent-git *": allow',
        '"pdm run agent-replay *": allow',
        '"pdm run pre-commit run --all-files": allow',
        '"npm --prefix frontend run test:coverage": allow',
        '"npm --prefix frontend run test:unit -- --run": allow',
        '"*&*": deny',
    ):
        assert required in implementer


@pytest.mark.parametrize("pattern", ["git commit", "git commit *"])
def test_implementer_rejects_raw_commit_allows(config_root: Path, pattern: str) -> None:
    replace(
        config_root,
        ".opencode/agent/implementer.md",
        f'    "{pattern}": deny\n',
        f'    "{pattern}": allow\n',
    )

    assert (
        f"ROLE_PERMISSION: implementer has unapproved bash allow {pattern}"
        in validate(config_root)
    )


@pytest.mark.parametrize("role", sorted(ROLES))
def test_agent_git_read_commands_are_fixed_and_argument_free(
    config_root: Path, role: str
) -> None:
    metadata, _ = load_agent(
        config_root / f".opencode/agent/{role}.md",
        Validation(config_root),
    )
    bash = metadata["permission"]["bash"]

    for forbidden in (
        "git status*",
        "git diff*",
        "git log*",
        "git show*",
        "git rev-parse*",
    ):
        assert forbidden not in bash
    for required in (
        "git status --porcelain",
        "git status --short --branch",
        "git rev-parse HEAD",
    ):
        assert bash[required] == "allow"
    if role != "pr-test-analyzer":
        assert bash["git diff --no-ext-diff main...HEAD"] == "allow"


def test_orchestrator_validator_commands_accept_no_arguments(config_root: Path) -> None:
    metadata, _ = load_agent(
        config_root / ".opencode/agent/ontoprism-team.md",
        Validation(config_root),
    )
    bash = metadata["permission"]["bash"]

    assert bash["pdm run validate-opencode-config"] == "allow"
    assert bash["pdm run validate-opencode-runtime"] == "allow"
    assert "pdm run validate-opencode-config*" not in bash
    assert "pdm run validate-opencode-runtime*" not in bash


@pytest.mark.parametrize(
    ("role", "expected_commands"),
    [
        (
            "ontoprism-team",
            (
                "pdm run agent-test *",
                "pdm run lint",
                "git diff --no-ext-diff",
                "git diff --check",
                "git diff --no-index /dev/null *",
            ),
        ),
        (
            "implementer",
            (
                "git diff --no-ext-diff",
                "git diff --check",
                "git diff --no-index /dev/null *",
            ),
        ),
    ],
)
def test_authorized_roles_have_only_the_new_safe_inspection_allows(
    config_root: Path, role: str, expected_commands: tuple[str, ...]
) -> None:
    metadata, _ = load_agent(
        config_root / f".opencode/agent/{role}.md",
        Validation(config_root),
    )
    permission = metadata["permission"]
    bash = permission["bash"]

    assert all(bash[command] == "allow" for command in expected_commands)
    assert permission["*"] == "deny"
    assert "external_directory" not in permission
    assert bash["pdm run pytest *"] == "deny"
    assert list(bash).index("git reset") > max(
        list(bash).index(command)
        for command in expected_commands
        if command.startswith("git ")
    )
    assert list(bash).index("*&*") > max(
        list(bash).index(command) for command in expected_commands
    )


@pytest.mark.parametrize("role", ["ontoprism-team", "implementer"])
def test_editing_agents_have_exact_safe_worktree_diff_permissions(
    config_root: Path, role: str
) -> None:
    metadata, _ = load_agent(
        config_root / f".opencode/agent/{role}.md",
        Validation(config_root),
    )
    bash = metadata["permission"]["bash"]

    for command in (
        "git diff --no-ext-diff",
        "git diff --check",
        "git diff --no-index /dev/null *",
    ):
        assert bash[command] == "allow"


def test_orchestrator_has_exact_safe_policy_gate_permissions(config_root: Path) -> None:
    metadata, _ = load_agent(
        config_root / ".opencode/agent/ontoprism-team.md",
        Validation(config_root),
    )
    bash = metadata["permission"]["bash"]

    assert bash["pdm run agent-test *"] == "allow"
    assert bash["pdm run lint"] == "allow"
    assert bash["pdm run pytest *"] == "deny"


def test_orchestrator_task_delegation_is_exact_and_excludes_reserves(
    config_root: Path,
) -> None:
    metadata, _ = load_agent(
        config_root / ".opencode/agent/ontoprism-team.md",
        Validation(config_root),
    )
    permission = metadata["permission"]
    expected = {
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

    task = permission["task"]
    assert list(task) == ["*", *sorted(expected)]
    assert task["*"] == "deny"
    assert all(task[name] == "allow" for name in expected)
    assert not expected & RESERVES


@pytest.mark.parametrize("role", sorted(ROLES))
def test_every_agent_denies_shell_metacharacter_chaining(
    config_root: Path, role: str
) -> None:
    agent = (config_root / f".opencode/agent/{role}.md").read_text()

    for pattern in ("*&*", "*;*", "*|*", "*>*", "*<*", "*`*", "*$*"):
        assert f'    "{pattern}": deny' in agent


@pytest.mark.parametrize("role", ["implementer", "pr-test-analyzer"])
def test_wildcard_bash_agents_end_with_literal_lf_and_cr_denies(
    config_root: Path, role: str
) -> None:
    metadata, _ = load_agent(
        config_root / f".opencode/agent/{role}.md",
        Validation(config_root),
    )
    bash = metadata["permission"]["bash"]

    assert list(bash)[-2:] == ["*\n*", "*\r*"]
    assert bash["*\n*"] == bash["*\r*"] == "deny"


def test_process_prose_limits_remote_mutations_to_user_or_authorized_merge(
    config_root: Path,
) -> None:
    agents = (config_root / "AGENTS.md").read_text()
    orchestrator = (config_root / ".opencode/agent/ontoprism-team.md").read_text()
    implementer = (config_root / ".opencode/agent/implementer.md").read_text()
    command = (config_root / ".opencode/command/review-pr.md").read_text()

    lasting_edits = (
        "Only the implementer makes lasting repository code, test, documentation, "
        "fix, or commit edits"
    )
    assert lasting_edits in agents
    assert "Pushes and PR creation or updates" in agents
    assert "remain manual user actions" in agents
    assert "Pushes and PR creation or updates are manual user actions" in orchestrator
    assert "explicitly authorizes that exact PR number" in orchestrator
    assert "report the ready state to the user" in implementer
    assert "launches fresh CLI processes" in command
    assert "quit and restart opencode" in command.lower()
