"""Local-only production CLI contract against the exact certified release pair."""

import json

import pytest
import typer
from scripts.release_delta import DeltaSettings, main
from typer.testing import CliRunner


def configure(monkeypatch):
    monkeypatch.setenv(
        "RELEASE_DELTA_SOURCES",
        json.dumps(
            {
                "26.07d": {
                    "manifest": "data/qlever-ncit/.ontoprism-ncit-candidate.json"
                },
                "26.09d": {
                    "manifest": (
                        "data/.qlever-ncit.candidate-49102609de184a69b7c8271de9010001/"
                        ".ontoprism-ncit-candidate.json"
                    )
                },
            }
        ),
    )
    monkeypatch.setenv("RELEASE_DELTA_ACTIVE_ENDPOINT", "http://127.0.0.1:7888")
    monkeypatch.setenv("RELEASE_DELTA_HISTORY_RELEASES", '["26.08e", "26.09d"]')
    monkeypatch.setenv("RELEASE_DELTA_ROBOT_HEAP_GIB", "24")
    monkeypatch.setenv("RELEASE_DELTA_ROBOT_MAX_ELEMENT_DEPTH", "0")
    monkeypatch.setenv("RELEASE_DELTA_ROBOT_TIMEOUT_SECONDS", "1200")


def test_environment_profile_loads_paths_and_jvm_settings(monkeypatch):
    configure(monkeypatch)
    settings = DeltaSettings(_env_file=None)
    assert settings.robot_heap_gib == 24
    assert settings.robot_max_element_depth == 0
    assert settings.sources["26.09d"].manifest.name == ".ontoprism-ncit-candidate.json"


@pytest.mark.integration
@pytest.mark.full_store
def test_full_store_command_matches_measured_release_delta(monkeypatch):
    configure(monkeypatch)
    app = typer.Typer()
    app.command()(main)
    outcome = CliRunner().invoke(app, ["--from", "26.07d", "--to", "26.09d"])
    assert outcome.exit_code == 0, outcome.output + repr(outcome.exception)
    report = json.loads(outcome.stdout)
    assert report["axiom_counts"] == {"removed": 3544, "added": 21615}
    scope = report["neoplasm"]
    assert (scope["scope_before"], scope["scope_after"]) == (15633, 15808)
    assert len(scope["added"]) == 180
    assert scope["retired"] == ["C173176", "C173400", "C202625", "C6642", "C67453"]
    assert report["history_reconciliation"]["create"]["history_only"] == ["C3050"]
    print(
        "DEMO",
        json.dumps(
            {
                "axioms": report["axiom_counts"],
                "scope_before": scope["scope_before"],
                "scope_after": scope["scope_after"],
                "new": len(scope["added"]),
                "retired": scope["retired"],
                "reconciliation": report["history_reconciliation"],
                "history_sources": report["history_sources"],
            }
        ),
    )
