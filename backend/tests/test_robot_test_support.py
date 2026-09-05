from __future__ import annotations

import pytest
from test_support import robot as robot_support


def test_missing_robot_fails_in_safe_or_partition_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(robot_support.shutil, "which", lambda _name: None)
    for name, value in (
        ("ONTOPRISM_SAFE_INTEGRATION", "1"),
        ("ONTOPRISM_TEST_PARTITION_LANE", "integration"),
    ):
        monkeypatch.delenv("ONTOPRISM_SAFE_INTEGRATION", raising=False)
        monkeypatch.delenv("ONTOPRISM_TEST_PARTITION_LANE", raising=False)
        monkeypatch.setenv(name, value)
        with pytest.raises(pytest.fail.Exception, match="ROBOT is required"):
            robot_support.require_robot()
