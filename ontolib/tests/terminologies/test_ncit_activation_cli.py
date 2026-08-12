from __future__ import annotations

import re
from pathlib import Path

from scripts.data_build import app
from typer.testing import CliRunner

# Rich renders `--help` with ANSI styling and wraps at the reported terminal width.
# Both are environment-dependent: CI enables colour, which injects escape sequences
# *inside* an option name, and a terminal narrower than 80 columns wraps the name.
# Either splits the token, so a raw substring check fails while the CLI contract is
# intact. Render wide and strip styling so these assertions pin the contract, not
# Rich's rendering decisions.
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
_WIDE_TERMINAL = {"COLUMNS": "200"}


def _plain(output: str) -> str:
    return _ANSI_ESCAPE.sub("", output)


def test_ncit_activate_help_requires_exact_candidate_manifest_path() -> None:
    runner = CliRunner()

    help_result = runner.invoke(app, ["ncit-activate", "--help"], env=_WIDE_TERMINAL)
    missing_result = runner.invoke(app, ["ncit-activate"], env=_WIDE_TERMINAL)

    assert help_result.exit_code == 0
    assert "--candidate-manifest" in _plain(help_result.stdout)
    assert "PATH" in _plain(help_result.stdout)
    assert missing_result.exit_code == 2
    assert "--candidate-manifest" in _plain(missing_result.output)


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
