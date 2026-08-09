"""Repository-level contracts for the standalone service and tool supply chain."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from ontolib.core.data_build_tools import (
    OXIGRAPH_TOOL,
    POSTGRES_IMAGE,
    ROBOT_ARTIFACT,
)
from ontolib.terminologies.ncit.sibling_store import OXIGRAPH_IMAGE

pytestmark = [pytest.mark.unit, pytest.mark.security]

_ROOT = Path(__file__).resolve().parents[2]
_DIGEST_PIN = re.compile(r"^[^:@\s]+(?:/[^:@\s]+)+@sha256:[0-9a-f]{64}$")


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

    assert services["oxigraph-ncit"]["image"] == OXIGRAPH_IMAGE
    assert services["oxigraph-uberon"]["image"] == OXIGRAPH_IMAGE
    assert services["postgres"]["image"] == POSTGRES_IMAGE
    assert all(_DIGEST_PIN.fullmatch(service["image"]) for service in services.values())

    expected_healthcheck = [
        "CMD",
        "/usr/local/bin/oxigraph",
        "query",
        "--location",
        "/data",
        "--query",
        "ASK {}",
        "--results-format",
        "json",
    ]
    assert services["oxigraph-ncit"]["healthcheck"]["test"] == expected_healthcheck
    assert services["oxigraph-uberon"]["healthcheck"]["test"] == expected_healthcheck


def test_oxigraph_candidate_provenance_names_source_version_and_digest() -> None:
    assert OXIGRAPH_TOOL.as_dict() == {
        "name": "oxigraph-cli",
        "source": "ghcr.io/oxigraph/oxigraph",
        "version": "0.5.3",
        "digest": (
            "sha256:cc943499d4724fbb348c75c623335c69a047de71c59852413b0d0467d3caebe3"
        ),
    }


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


def test_clean_machine_instructions_do_not_require_a_sibling_checkout() -> None:
    data_setup = (_ROOT / "docs" / "DATA_SETUP.md").read_text()

    assert "../fairdata" not in data_setup
    assert "scripts/install_robot.py" in data_setup
    assert "docker compose up -d" in data_setup
