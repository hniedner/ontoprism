"""Runtime fail-closed contracts for the safe disposable integration lane."""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest
from test_support.integration_resources import ResourceOwnershipError


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
