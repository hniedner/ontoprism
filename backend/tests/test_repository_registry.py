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
from ontolib.repositories.clinicaltrials.models import CTFilterPhase, CTStatus

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
    "change",
    [
        "mismatched-path",
        "empty-datasets",
        "missing-capabilities",
        "empty-dataset-key",
    ],
)
def test_manifest_rejects_invalid_descriptor_identity_or_dataset_domain(
    tmp_path: Path, change: str
) -> None:
    payload = json.loads(_MANIFEST.read_text())
    if change == "mismatched-path":
        payload[0]["path"] = "/repositories/not-ncit"
    elif change == "empty-datasets":
        icdo = next(entry for entry in payload if entry["id"] == "icdo")
        icdo["capabilities_by_dataset"] = {}
    elif change == "missing-capabilities":
        payload[0].pop("capabilities")
    else:
        icdo = next(entry for entry in payload if entry["id"] == "icdo")
        first = next(iter(icdo["capabilities_by_dataset"].values()))
        icdo["capabilities_by_dataset"] = {"": first}
    invalid = tmp_path / "repositories.json"
    invalid.write_text(json.dumps(payload))

    with pytest.raises(ValidationError):
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


def test_declared_uberon_controls_include_ontology_and_two_source_filter() -> None:
    uberon = next(
        entry for entry in load_repository_registry(_MANIFEST) if entry.id == "uberon"
    )
    capabilities = uberon.capabilities

    assert capabilities is not None
    assert capabilities.graph == "ontology"
    assert capabilities.links == "mapping"
    assert capabilities.filters["code"].kind == "text"
    assert capabilities.filters["label"].kind == "text"
    source = capabilities.filters["source"]
    assert source.multiple is True
    assert source.values == {"uberon": "Uberon", "cl": "Cell Ontology"}


def test_declared_remote_controls_match_upstream_search_contracts() -> None:
    registry = load_repository_registry(_MANIFEST)
    pubmed = next(entry for entry in registry if entry.id == "pubmed").capabilities
    trials = next(
        entry for entry in registry if entry.id == "clinicaltrials"
    ).capabilities

    assert pubmed is not None
    assert pubmed.query_before_results is True
    assert pubmed.pagination == "offset"
    assert pubmed.sorts["search"] == ["relevance", "pub_date"]
    assert trials is not None
    assert trials.query_before_results is True
    assert trials.pagination == "cursor"
    assert set(trials.filters) == {"status", "phase"}
    assert trials.filters["status"].multiple is True
    assert trials.filters["status"].text_parameter is None
    assert set(trials.filters["status"].values) == set(get_args(CTStatus))
    assert set(trials.filters["phase"].values) == set(get_args(CTFilterPhase))


def test_declared_icdo_controls_are_bounded_to_each_served_dataset() -> None:
    icdo = next(
        entry for entry in load_repository_registry(_MANIFEST) if entry.id == "icdo"
    )

    assert icdo.capabilities is None
    assert icdo.capabilities_by_dataset is not None
    assert set(icdo.capabilities_by_dataset) == {
        "3.2/morphology",
        "4.0/morphology",
        "4.0/topography",
    }
    morphology = icdo.capabilities_by_dataset["3.2/morphology"]
    topography = icdo.capabilities_by_dataset["4.0/topography"]
    assert set(morphology.filters) == {"code", "preferred", "behaviour"}
    assert morphology.filters["behaviour"].multiple is True
    assert morphology.graph == "none"
    assert morphology.links == "mapping"
    assert set(topography.filters) == {"code", "preferred", "level"}
    assert topography.filters["level"].values == {
        "category": "category",
        "leaf": "leaf",
    }
    assert topography.graph == "none"
    assert topography.links == "none"


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
async def test_grid_refuses_controls_outside_declaration(sort, text, selected) -> None:
    async def ready():
        return "ready"

    capabilities = load_repository_registry(_MANIFEST)[0].capabilities
    assert capabilities is not None
    grid = GridService("NCIt", capabilities, ready)
    with pytest.raises(HTTPException) as error:
        await grid.validate("list", sort, text, selected)
    assert error.value.status_code == 422


async def test_grid_refuses_text_for_a_categorical_only_filter() -> None:
    async def ready():
        return "ready"

    clinical_trials = next(
        item
        for item in load_repository_registry(_MANIFEST)
        if item.id == "clinicaltrials"
    )
    assert clinical_trials.capabilities is not None
    grid = GridService("ClinicalTrials.gov", clinical_trials.capabilities, ready)

    with pytest.raises(HTTPException) as error:
        await grid.validate("search", "relevance", {"status": "RECRUITING"}, {})

    assert error.value.status_code == 422
