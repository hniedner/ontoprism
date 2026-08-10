from __future__ import annotations

from pathlib import Path

from scripts.data_build import app
from typer.testing import CliRunner


def test_ncit_activate_help_requires_exact_candidate_manifest_path() -> None:
    runner = CliRunner()

    help_result = runner.invoke(app, ["ncit-activate", "--help"])
    missing_result = runner.invoke(app, ["ncit-activate"])

    assert help_result.exit_code == 0
    assert "--candidate-manifest" in help_result.stdout
    assert "PATH" in help_result.stdout
    assert missing_result.exit_code == 2
    assert "--candidate-manifest" in missing_result.output


def test_data_setup_documents_activation_refusal_rollback_and_recovery() -> None:
    documentation = Path("docs/DATA_SETUP.md").read_text()

    assert "data-build ncit-activate --candidate-manifest" in documentation
    assert ".qlever-ncit.activation.json" in documentation
    assert "65f84b4" in documentation
    assert (
        "docker.io/adfreiburg/qlever@sha256:"
        "abeb20ae245184cee2991a99c22a9bb0a62f6884bb1a03747bf7e56165cb0ca6"
        in documentation
    )
    assert "Activation refusal" in documentation
    assert "Automatic rollback" in documentation
    assert "Interrupted recovery" in documentation
