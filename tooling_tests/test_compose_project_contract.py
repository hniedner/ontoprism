from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from scripts.validation.run_agent_replay import _PODMAN_PROJECT


@pytest.mark.unit
def test_compose_source_declares_the_governed_podman_project() -> None:
    root = Path(__file__).resolve().parents[1]
    inspected_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            root / "docker-compose.yml",
            root / "scripts/validation/run_agent_replay.py",
        )
    )

    assert _PODMAN_PROJECT == "ontoprism"
    assert f"name: {_PODMAN_PROJECT}" in inspected_text
    assert "ontoprism_pg_data:" in inspected_text
    assert "down -v" not in inspected_text
    assert "--volumes" not in inspected_text


@pytest.mark.unit
def test_postgres_volume_identity_is_pinned_independently_of_project_name() -> None:
    root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))

    assert compose["name"] == "ontoprism"
    assert compose["volumes"]["ontoprism_pg_data"]["name"] == (
        "ontoprism-podman-poc_ontoprism_pg_data"
    )
