"""Repository-level contracts for the standalone service and tool supply chain."""

from __future__ import annotations

import re
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


def test_clean_machine_instructions_do_not_require_a_sibling_checkout() -> None:
    data_setup = (_ROOT / "docs" / "DATA_SETUP.md").read_text()

    assert "../fairdata" not in data_setup
    assert "scripts/install_robot.py" in data_setup
    assert "scripts/install_jena.py" in data_setup
    assert "docker compose up -d" in data_setup


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
