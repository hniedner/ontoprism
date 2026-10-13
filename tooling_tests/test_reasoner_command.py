"""The CLI preserves computed findings when the separate ledger preview fails."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import typer
from scripts import reasoner_check as command
from typer.testing import CliRunner

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("preview_succeeds", [False, True])
def test_preview_outcome_preserves_reasoner_findings_and_sets_exit_status(
    tmp_path, monkeypatch, preview_succeeds
):
    settings = SimpleNamespace(
        database_url="unused", ncit_sparql_url="unused", ncit_owl_dir=tmp_path
    )
    monkeypatch.setattr(command, "get_settings", lambda: settings)
    monkeypatch.setattr(
        command, "RobotSettings", lambda: SimpleNamespace(robot_config=lambda: None)
    )
    monkeypatch.setattr(command, "make_engine", lambda _: None)
    monkeypatch.setattr(command, "make_sessionmaker", lambda _: None)
    monkeypatch.setattr(command, "dispose_engine", AsyncMock())
    monkeypatch.setattr(
        command,
        "_inputs",
        AsyncMock(
            return_value=({"ncit_version": "test", "source_identity": "a" * 64}, [])
        ),
    )

    @asynccontextmanager
    async def client(_):
        yield SimpleNamespace(version=AsyncMock(return_value="test"))

    monkeypatch.setattr(command, "ncit_sparql_client", client)
    monkeypatch.setattr(
        command,
        "read_axis_diagnostic_source",
        AsyncMock(return_value=SimpleNamespace(snapshot=None)),
    )
    monkeypatch.setattr(command, "disjoint_fillers", lambda *_: [])
    monkeypatch.setattr(
        command,
        "check_projection",
        lambda *_: {"new_unsatisfiable": ["C1"], "phase_seconds": {}},
    )
    monkeypatch.setattr(
        command,
        "preview_reasoner_evidence",
        AsyncMock(return_value={"dry_run": True, "affected_assertions": 1})
        if preview_succeeds
        else AsyncMock(side_effect=ValueError("ledger unavailable")),
    )
    app = typer.Typer()
    app.command()(command.main)
    result = CliRunner().invoke(
        app, ["--run", "test", "--workspace", str(tmp_path / "run")]
    )
    assert result.exit_code == (0 if preview_succeeds else 1)
    report = json.loads(result.stdout)
    assert report["new_unsatisfiable"] == ["C1"]
    if preview_succeeds:
        assert report["ledger_preview"] == {"dry_run": True, "affected_assertions": 1}
        assert "ledger_preview_error" not in report
    else:
        assert report["ledger_preview_error"] == "ValueError: ledger unavailable"
        assert "ledger_preview" not in report
