"""Runtime contracts for the safe integration lane and its external tools."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest
from scripts.validation.run_agent_replay import _PODMAN_PROJECT
from test_support.integration_resources import ResourceOwnershipError


@pytest.mark.integration
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

    assert config["name"] == _PODMAN_PROJECT
    assert config["volumes"]["ontoprism_pg_data"]["name"] == (
        "ontoprism-podman-poc_ontoprism_pg_data"
    )


@pytest.mark.integration
def test_safe_lane_rejects_an_unregistered_tcp_target_before_connection() -> None:
    assert os.environ.get("ONTOPRISM_SAFE_INTEGRATION") == "1"

    with pytest.raises(ResourceOwnershipError, match="not owned"):
        socket.create_connection(("127.0.0.1", 7888), timeout=0.1)


@pytest.mark.integration
def test_safe_lane_uses_only_run_owned_application_paths() -> None:
    root = Path(os.environ["NCIT_OWL_DIR"]).parent

    assert root.is_absolute()
    assert root.name.startswith("ontoprism-integration-data-")
    assert Path(os.environ["CADSR_DB_PATH"]).is_relative_to(root)
    assert Path(os.environ["CADSR_DATA_DIR"]).is_relative_to(root)
    assert Path(os.environ["NCIT_OWL_DIR"]).is_relative_to(root)
    assert Path(os.environ["NCIT_STORE_DIR"]).is_relative_to(root)
