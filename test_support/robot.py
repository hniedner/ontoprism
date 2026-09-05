"""Fail-closed ROBOT availability guard for integration contracts."""

from __future__ import annotations

import os
import shutil

import pytest


def require_robot() -> None:
    """Require ROBOT in managed lanes; permit manual unit-style skips elsewhere."""
    if shutil.which("robot") is not None:
        return
    if (
        os.environ.get("ONTOPRISM_SAFE_INTEGRATION") == "1"
        or os.environ.get("ONTOPRISM_TEST_PARTITION_LANE") is not None
    ):
        pytest.fail("ROBOT is required in managed integration execution")
    pytest.skip("robot not on PATH")
