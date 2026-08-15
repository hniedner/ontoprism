"""Repository-level contracts for the standalone service and tool supply chain."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from ontolib.core.data_build_tools import (
    JENA_RIOT_ARTIFACT,
    POSTGRES_IMAGE,
    QLEVER_IMAGE,
    QLEVER_TOOL,
    ROBOT_ARTIFACT,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]

_ROOT = Path(__file__).resolve().parents[2]
_DIGEST_PIN = re.compile(r"^[^:@/\s]+(?:/[^:@/\s]+)+@sha256:[0-9a-f]{64}$")
_PDM_VERSION = "2.28.0"
_SETUP_PDM_ACTION = "pdm-project/setup-pdm@973541a5febeafcfdadf8a51211435be6ecfd90f"
_ACTIONS_CACHE_ACTION = "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"


def _nested_image_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [
            item
            for key, child in value.items()
            for item in (
                [child]
                if key == "image" and isinstance(child, str)
                else _nested_image_values(child)
            )
        ]
    if isinstance(value, list):
        return [item for child in value for item in _nested_image_values(child)]
    return []


def test_compose_uses_only_digest_pinned_standalone_service_images() -> None:
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text())
    services = compose["services"]

    assert set(services) == {"qlever-ncit", "qlever-uberon", "postgres"}
    assert services["qlever-ncit"]["image"] == QLEVER_IMAGE
    assert services["qlever-uberon"]["image"] == QLEVER_IMAGE
    assert services["postgres"]["image"] == POSTGRES_IMAGE
    assert all(_DIGEST_PIN.fullmatch(service["image"]) for service in services.values())

    ncit_command = " ".join(services["qlever-ncit"]["command"])
    uberon_command = " ".join(services["qlever-uberon"]["command"])
    for command, basename in ((ncit_command, "ncit"), (uberon_command, "uberon")):
        assert f"qlever-server -i {basename}" in command
        assert "-j 2" in command
        assert "-s 30s" in command
        assert "--service-allowed-iri-prefixes -" in command
    assert "--persist-updates" in ncit_command
    assert "--persist-updates" not in uberon_command
    assert services["qlever-ncit"]["ports"] == ["127.0.0.1:7888:7001"]
    assert services["qlever-uberon"]["ports"] == ["127.0.0.1:7889:7001"]
    assert services["qlever-ncit"]["volumes"] == ["./data/qlever-ncit:/data"]
    assert services["qlever-uberon"]["volumes"] == ["./data/qlever-uberon:/data"]
    for name in ("qlever-ncit", "qlever-uberon"):
        assert "ASK" in " ".join(services[name]["healthcheck"]["test"])


def test_qlever_candidate_provenance_names_source_version_and_digest() -> None:
    assert QLEVER_TOOL.as_dict() == {
        "name": "qlever-index-server",
        "source": "docker.io/adfreiburg/qlever",
        "version": "65f84b4",
        "digest": (
            "sha256:abeb20ae245184cee2991a99c22a9bb0a62f6884bb1a03747bf7e56165cb0ca6"
        ),
    }


def test_qlever_build_converter_is_pinned() -> None:
    assert JENA_RIOT_ARTIFACT.identity.as_dict() == {
        "name": "apache-jena-riot",
        "source": (
            "https://archive.apache.org/dist/jena/binaries/apache-jena-6.1.0.tar.gz"
        ),
        "version": "6.1.0",
        "digest": (
            "sha256:653108a91fd9b309a89bc756258bae0bca01587cef475942d11852e3beba2ae3"
        ),
    }
    assert JENA_RIOT_ARTIFACT.filename == "apache-jena-6.1.0.tar.gz"


def test_workflow_images_and_robot_install_are_immutable() -> None:
    workflows = [_ROOT / ".github" / "workflows" / "ci.yml"]
    images = [
        image
        for workflow in workflows
        for image in _nested_image_values(yaml.safe_load(workflow.read_text()))
    ]
    assert all(_DIGEST_PIN.fullmatch(image) for image in images)

    ci = workflows[0].read_text()
    assert "scripts/install_robot.py" in ci
    assert "ONTOPRISM_ROBOT_DIR" in ci
    assert ROBOT_ARTIFACT.identity.digest.startswith("sha256:")
    assert "curl" not in "\n".join(
        line for line in ci.splitlines() if "robot" in line.lower()
    )


def _npm_script_bodies(
    command: str, scripts: dict[str, str], depth: int = 0
) -> list[str]:
    """Expand `npm run <script>` in *command* to the bodies it actually executes."""
    if depth > 3:  # guard against a self-referential script chain
        return []
    expanded: list[str] = []
    for name, body in scripts.items():
        if re.search(rf"npm (?:--prefix \S+ )?run {re.escape(name)}\b", command):
            expanded.append(body)
            expanded.extend(_npm_script_bodies(body, scripts, depth + 1))
    return expanded


def test_every_ci_job_installs_the_tools_its_steps_invoke() -> None:
    """A CI job that invokes `pdm` must also set PDM up — including via an npm script.

    `web tests + coverage` regressed exactly this way: `npm run test:coverage` grew a
    `pdm run coverage-gate` tail (the gate must use the pinned project interpreter)
    while the job installed only Node and Python, so CI failed with `pdm: not found`.
    No local run could reveal it, because pdm is on a developer's PATH. Only comparing
    the workflow against the scripts it invokes catches this class of drift.
    """
    workflow = yaml.safe_load((_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    scripts: dict[str, str] = json.loads(
        (_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )["scripts"]

    for job_name, job in workflow["jobs"].items():
        steps = job.get("steps", [])
        commands = [step["run"] for step in steps if step.get("run")]
        executed = [
            fragment
            for command in commands
            for fragment in (command, *_npm_script_bodies(command, scripts))
        ]
        invokes_pdm = any(re.search(r"(?<!\S)pdm\b", fragment) for fragment in executed)
        if not invokes_pdm:
            continue
        assert any(step.get("uses") == _SETUP_PDM_ACTION for step in steps), (
            f"CI job {job_name!r} invokes pdm but never installs it; "
            f"add {_SETUP_PDM_ACTION} to that job"
        )


def test_frontend_transitive_security_and_install_script_policy() -> None:
    """Patched transitive tools stay pinned and optional native scripts stay denied."""
    package = json.loads(
        (_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )
    lock = json.loads(
        (_ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8")
    )

    assert package["allowScripts"] == {"fsevents": False}
    assert package["overrides"]["brace-expansion"] == "^5.0.9"
    assert package["overrides"]["nanoid"] == "^3.3.18"
    assert lock["packages"]["node_modules/brace-expansion"]["version"] == "5.0.9"
    assert lock["packages"]["node_modules/nanoid"]["version"] == "3.3.18"


def test_ci_dependency_environments_are_pinned_clean_and_cached(
    tmp_path: Path,
) -> None:
    workflow = yaml.safe_load((_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    jobs = workflow["jobs"]
    expected_sync = {
        "quality": "pdm sync --clean-unselected --dev",
        "backend-tests": "pdm sync --clean-unselected --dev",
        "integration-tests": "pdm sync --clean-unselected --dev",
        "web-tests": "pdm sync --clean-unselected --dev",
        "embedding-model-contract": ("pdm sync --clean-unselected --dev -G data-build"),
    }

    for job_name, sync_command in expected_sync.items():
        steps = jobs[job_name]["steps"]
        setup = next(step for step in steps if step.get("uses") == _SETUP_PDM_ACTION)
        assert setup["with"] == {
            "python-version": "3.13",
            "version": _PDM_VERSION,
            "cache": True,
        }
        assert any(step.get("run") == sync_command for step in steps)

    for sync_command in set(expected_sync.values()):
        args = shlex.split(sync_command)
        # argv comes only from the fixed expected commands above, never workflow data.
        completed = subprocess.run(  # noqa: S603
            [*args[:2], "--dry-run", *args[2:]],
            cwd=_ROOT,
            capture_output=True,
            check=False,
            env={**os.environ, "PDM_LOG_DIR": str(tmp_path / "pdm-logs")},
            text=True,
        )
        assert completed.returncode == 0, completed.stderr

    quality_steps = jobs["quality"]["steps"]
    pre_commit_cache = next(
        step for step in quality_steps if step.get("uses") == _ACTIONS_CACHE_ACTION
    )
    assert pre_commit_cache["with"] == {
        "path": "~/.cache/pre-commit",
        "key": (
            "${{ runner.os }}-${{ runner.arch }}-pre-commit-"
            "${{ steps.setup-pdm.outputs.python-version }}-"
            "${{ hashFiles('.pre-commit-config.yaml') }}"
        ),
    }

    dockerfile = (_ROOT / "backend" / "Dockerfile").read_text()
    assert f"RUN pip install --no-cache-dir pdm=={_PDM_VERSION}" in dockerfile


def test_clean_machine_instructions_do_not_require_a_sibling_checkout() -> None:
    data_setup = (_ROOT / "docs" / "DATA_SETUP.md").read_text()

    assert "../fairdata" not in data_setup
    assert "scripts/install_robot.py" in data_setup
    assert "scripts/install_jena.py" in data_setup
    assert "docker compose up -d" in data_setup


def test_full_app_routes_icdo_entitlement_through_the_private_bff() -> None:
    compose = yaml.safe_load((_ROOT / "docker-compose.app.yml").read_text())
    services = compose["services"]

    assert services["api"]["environment"]["ICDO_ENTITLEMENT_KEY"] == (
        "${ICDO_ENTITLEMENT_KEY:-}"
    )
    assert services["api"]["environment"]["ENABLE_LICENSED_MAPPINGS"] == (
        "${ENABLE_LICENSED_MAPPINGS:-false}"
    )
    assert services["web"]["environment"]["ICDO_ENTITLEMENT_KEY"] == (
        "${ICDO_ENTITLEMENT_KEY:-}"
    )
    assert services["web"]["environment"]["ONTOPRISM_FASTAPI_ORIGIN"] == (
        "http://api:8011"
    )
    assert services["web"]["build"] == {
        "context": ".",
        "dockerfile": "frontend/Dockerfile",
    }

    caddy = (_ROOT / "Caddyfile").read_text()
    assert "handle /api/*" not in caddy
    assert "reverse_proxy web:3000" in caddy

    env_example = (_ROOT / ".env.example").read_text()
    assert "# ICDO_ENTITLEMENT_KEY=" in env_example
    assert "ENABLE_LICENSED_MAPPINGS=false" in env_example


def test_active_runtime_has_no_oxigraph_dependency() -> None:
    active_paths = (
        "docker-compose.yml",
        "docker-compose.app.yml",
        "pyproject.toml",
        ".env.example",
        "backend/src",
        "ontolib/src",
        "scripts",
        "test_support",
    )
    occurrences: list[str] = []
    for relative in active_paths:
        path = _ROOT / relative
        files = [path] if path.is_file() else sorted(path.rglob("*"))
        for file_path in files:
            if not file_path.is_file() or "__pycache__" in file_path.parts:
                continue
            try:
                text = file_path.read_text()
            except UnicodeDecodeError:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if "oxigraph" in line.lower():
                    occurrences.append(
                        f"{file_path.relative_to(_ROOT)}:{line_number}:{line.strip()}"
                    )
    assert occurrences == []
