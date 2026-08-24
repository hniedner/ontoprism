"""Policy tests for the narrowly scoped current-replay agent wrapper."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest
from scripts.validation.run_agent_replay import (
    AgentReplayInputError,
    run_agent_replay,
)

if TYPE_CHECKING:
    from pathlib import Path


class _Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(
        self, arguments: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((arguments, kwargs))
        return subprocess.CompletedProcess(arguments, 0)


@pytest.mark.unit
def test_current_replay_uses_only_the_documented_fixed_inputs(tmp_path: Path) -> None:
    for relative in (
        "scripts/decompose.py",
        "data/qlever-ncit/.ontoprism-ncit-candidate.json",
        "samples/ncit-26.07d-m1-current-replay.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    runner = _Runner()

    assert run_agent_replay(["decompose-current"], tmp_path, runner=runner) == 0

    command, options = runner.calls[0]
    assert command[1:] == [
        str(tmp_path / "scripts/decompose.py"),
        "--source-manifest",
        str(tmp_path / "data/qlever-ncit/.ontoprism-ncit-candidate.json"),
        "--branch",
        "neoplasm",
        "--sample-manifest",
        str(tmp_path / "samples/ncit-26.07d-m1-current-replay.json"),
        "--out",
        str(tmp_path / "tmp/m1-6-current-replay.ttl"),
    ]
    assert options["shell"] is False


@pytest.mark.unit
def test_evidence_generation_requires_a_real_neoplasm_run_id(tmp_path: Path) -> None:
    with pytest.raises(AgentReplayInputError, match="run ID"):
        run_agent_replay(["generate-current-evidence", "guessed-run"], tmp_path)


@pytest.mark.unit
def test_axis_diagnostics_reject_unsafe_or_unbounded_fillers(tmp_path: Path) -> None:
    with pytest.raises(AgentReplayInputError, match="filler"):
        run_agent_replay(
            ["generate-axis-diagnostics", "C35501", "../../unsafe"], tmp_path
        )
    with pytest.raises(AgentReplayInputError, match="at most 8"):
        run_agent_replay(
            ["generate-axis-diagnostics", *(f"C{index}" for index in range(9))],
            tmp_path,
        )


@pytest.mark.unit
def test_wrapper_rejects_unlisted_operations(tmp_path: Path) -> None:
    with pytest.raises(AgentReplayInputError, match="unsupported"):
        run_agent_replay(["import-workbook"], tmp_path)


@pytest.mark.unit
def test_inventory_refresh_uses_only_the_repository_generator(tmp_path: Path) -> None:
    script = tmp_path / "scripts/validation/write_sparql_inventory.py"
    script.parent.mkdir(parents=True)
    script.touch()
    runner = _Runner()

    assert run_agent_replay(["refresh-sparql-inventory"], tmp_path, runner=runner) == 0

    command, _options = runner.calls[0]
    assert command[1:] == [
        str(script),
        "--root",
        str(tmp_path),
        "--output",
        str(tmp_path / "scripts/validation/sparql-inventory.json"),
    ]
