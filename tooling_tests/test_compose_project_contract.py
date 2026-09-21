from __future__ import annotations

from pathlib import Path

import pytest
from scripts.validation.run_agent_replay import _PODMAN_PROJECT


@pytest.mark.unit
def test_compose_source_declares_the_governed_podman_project() -> None:
    root = Path(__file__).resolve().parents[1]
    inspected_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            root / "docker-compose.yml",
            root / "scripts/validation/run_agent_replay.py",
        )
    )

    assert f"name: {_PODMAN_PROJECT}" in inspected_text
    assert "ontoprism_pg_data:" in inspected_text
    assert "down -v" not in inspected_text
    assert "--volumes" not in inspected_text
