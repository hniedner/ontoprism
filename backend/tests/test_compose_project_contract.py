from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from scripts.validation.run_agent_replay import _PODMAN_PROJECT


@pytest.mark.unit
def test_plain_compose_config_preserves_active_podman_project_and_volume() -> None:
    root = Path(__file__).resolve().parents[2]
    docker = shutil.which("docker")
    assert docker is not None
    result = subprocess.run(  # noqa: S603
        [docker, "compose", "config", "--format", "json"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    config = json.loads(result.stdout)

    assert config["name"] == "ontoprism-podman-poc"
    assert config["name"] == _PODMAN_PROJECT
    assert config["volumes"]["ontoprism_pg_data"]["name"] == (
        "ontoprism-podman-poc_ontoprism_pg_data"
    )
    inspected_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            root / "docker-compose.yml",
            root / "scripts/validation/run_agent_replay.py",
        )
    )
    assert "down -v" not in inspected_text
    assert "--volumes" not in inspected_text
