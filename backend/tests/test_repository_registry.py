"""Language-neutral repository registry contracts."""

import json
from pathlib import Path
from typing import get_args

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.api.v1.grid import GridService
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


@pytest.mark.parametrize(
    "change", ["columns", "empty-sort", "text-domain", "remote-metadata"]
)
def test_rejects_invalid_grid_capabilities(tmp_path: Path, change: str) -> None:
    payload = json.loads(_MANIFEST.read_text())
    capabilities = {
        "sorts": {"list": ["source"], "search": ["relevance"]},
        "filters": {
            "code": {"kind": "text", "text_parameter": "code_text", "values": {}}
        },
        "pagination": "offset",
        "query_before_results": False,
        "metadata": "certified",
        "graph": "ontology",
        "links": "mapping",
    }
    if change == "columns":
        capabilities["columns"] = []
    elif change == "empty-sort":
        capabilities["sorts"]["list"] = []
    elif change == "text-domain":
        capabilities["filters"]["code"]["values"] = {"C1": "Concept"}
    else:
        capabilities["metadata"] = "remote"
    payload[0]["capabilities"] = capabilities
    invalid = tmp_path / "repositories.json"
    invalid.write_text(json.dumps(payload))
    with pytest.raises(ValidationError):
        load_repository_registry(invalid)


def test_declared_ncit_controls_available_to_grid_consumers() -> None:
    ncit = load_repository_registry(_MANIFEST)[0]
    capabilities = ncit.capabilities
    assert capabilities is not None
    assert capabilities.sorts["list"][0] == "source"
    assert capabilities.filters["representation_status"].values == {
        "legacy-precoordinated": "Legacy pre-coordinated"
    }
    semantic_type = capabilities.filters["semantic_type"]
    assert semantic_type.multiple is True
    assert semantic_type.source_domain == "semantic-types"


@pytest.mark.parametrize(
    ("sort", "text", "selected"),
    [
        ("unknown", {}, {}),
        ("source", {"unknown": "x"}, {}),
        ("source", {}, {"code": "C1"}),
        ("source", {}, {"unknown": "x"}),
        ("source", {}, {"representation_status": "unknown"}),
    ],
)
def test_grid_refuses_controls_outside_declaration(sort, text, selected) -> None:
    async def ready():
        return "ready"

    capabilities = load_repository_registry(_MANIFEST)[0].capabilities
    assert capabilities is not None
    grid = GridService("NCIt", capabilities, ready)
    with pytest.raises(HTTPException) as error:
        grid.validate("list", sort, text, selected)
    assert error.value.status_code == 422
