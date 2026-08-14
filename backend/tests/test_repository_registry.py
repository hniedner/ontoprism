"""Language-neutral repository registry contracts."""

import json
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from backend.repository_metadata import RepositoryName
from backend.repository_registry import load_repository_registry

pytestmark = pytest.mark.unit

_MANIFEST = Path(__file__).parents[2] / "repository-manifest.json"


def test_manifest_declares_exact_served_local_and_remote_repositories() -> None:
    registry = load_repository_registry(_MANIFEST)

    local = {entry.id for entry in registry if entry.kind == "local-certified-proxy"}
    assert local == set(get_args(RepositoryName))
    assert {entry.id for entry in registry if entry.kind == "remote-live-service"} == {
        "clinicaltrials",
        "pubmed",
    }


def test_manifest_rejects_identity_fields_on_remote_descriptors(tmp_path: Path) -> None:
    payload = json.loads(_MANIFEST.read_text())
    remote = next(entry for entry in payload if entry["id"] == "pubmed")
    remote["release"] = "not-applicable"
    invalid = tmp_path / "repositories.json"
    invalid.write_text(json.dumps(payload))

    with pytest.raises(ValidationError, match="release"):
        load_repository_registry(invalid)
