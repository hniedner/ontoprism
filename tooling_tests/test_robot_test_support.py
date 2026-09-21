from __future__ import annotations

import pytest
from test_support import robot as robot_support


class ManualSkipSentinelError(Exception):
    pass


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ONTOPRISM_SAFE_INTEGRATION", "1"),
        ("ONTOPRISM_TEST_PARTITION_LANE", "integration"),
    ],
)
def test_missing_robot_fails_in_managed_execution_instead_of_skipping(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setattr(robot_support.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        robot_support.pytest,
        "skip",
        lambda _message: (_ for _ in ()).throw(ManualSkipSentinelError()),
    )
    monkeypatch.delenv("ONTOPRISM_SAFE_INTEGRATION", raising=False)
    monkeypatch.delenv("ONTOPRISM_TEST_PARTITION_LANE", raising=False)
    monkeypatch.setenv(name, value)

    with pytest.raises(pytest.fail.Exception, match="ROBOT is required"):
        robot_support.require_robot()


def test_missing_robot_manual_execution_requests_a_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(robot_support.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        robot_support.pytest,
        "skip",
        lambda _message: (_ for _ in ()).throw(ManualSkipSentinelError()),
    )
    monkeypatch.delenv("ONTOPRISM_SAFE_INTEGRATION", raising=False)
    monkeypatch.delenv("ONTOPRISM_TEST_PARTITION_LANE", raising=False)

    with pytest.raises(ManualSkipSentinelError):
        robot_support.require_robot()
