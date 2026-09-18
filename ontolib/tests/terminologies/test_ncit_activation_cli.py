from __future__ import annotations

import re

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
