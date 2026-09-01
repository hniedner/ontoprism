"""API contract for the explicit enhanced-NCIt showcase representation."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.dependencies import get_decomposition_reader, get_ncit_store
from backend.main import create_app
from ontolib.decomposition import vocab
from ontolib.decomposition.enhanced_showcase import (
    load_packaged_showcase_decision_set,
)
from ontolib.terminologies.namespaces import NCIT_NS


class _CountingReader:
    def __init__(self, *, malformed: bool = False, missing: bool = False) -> None:
        self.selects = 0
        self.malformed = malformed
        self.missing = missing

    async def rows_for(self, concept_code: str) -> list[dict[str, str]]:
        self.selects += 1
        return [
            {
                "status": vocab.LEGACY_PRECOORDINATED,
                "axis": f"{vocab.ONTOPRISM_NS}NormalTissueOrigin",
                "filler": f"{NCIT_NS}C33782",
                "axisSource": "role",
                "sourceRole": f"{NCIT_NS}R103",
                "mostSpecific": "true",
            },
            {
                "status": vocab.LEGACY_PRECOORDINATED,
                "axis": f"{vocab.ONTOPRISM_NS}PrimarySite",
                "filler": f"{NCIT_NS}C12400",
                "axisSource": "role",
                "sourceRole": f"{NCIT_NS}R101",
                "mostSpecific": "true",
            },
        ]

    async def showcase_rows_for(self, concept_code: str) -> list[dict[str, str]]:
        self.selects += 1
        if self.malformed:
            return [{"disposition": "invented"}]
        if self.missing:
            return []
        return [
            {"payload": decision.model_dump_json()}
            for decision in load_packaged_showcase_decision_set()
            .concept(concept_code)
            .decisions
        ]


class _Store:
    async def labels_for(self, codes: list[str]) -> dict[str, str]:
        labels = {"C33782": "Thyroid Gland Follicle", "C12400": "Thyroid Gland"}
        return {code: labels[code] for code in codes if code in labels}


def _client(reader: _CountingReader) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_decomposition_reader] = lambda: reader
    app.dependency_overrides[get_ncit_store] = _Store
    with TestClient(app) as client:
        yield client


@pytest.mark.api
def test_explicit_showcase_endpoint_returns_bounded_active_overlay() -> None:
    reader = _CountingReader()
    response = next(_client(reader)).get(
        "/api/v1/ncit/concepts/C6135/enhanced-ncit-showcase"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["representation"] == "enhanced-ncit-showcase"
    assert {item["filler"] for item in body["base_constituents"]} == {
        "C33782",
        "C12400",
    }
    assert "C33782" not in {item["filler"] for item in body["effective_constituents"]}
    assert {decision["disposition"] for decision in body["decisions"]} >= {
        "include",
        "exclude",
        "unresolved-visible",
    }
    assert reader.selects == 2
    assert len(response.content) <= 131_072
    assert len(body["decisions"]) <= 128


@pytest.mark.api
def test_showcase_refuses_unknown_concepts_and_malformed_stored_rows() -> None:
    unknown = next(_client(_CountingReader())).get(
        "/api/v1/ncit/concepts/C9379/enhanced-ncit-showcase"
    )
    malformed = next(_client(_CountingReader(malformed=True))).get(
        "/api/v1/ncit/concepts/C6135/enhanced-ncit-showcase"
    )
    missing = next(_client(_CountingReader(missing=True))).get(
        "/api/v1/ncit/concepts/C6135/enhanced-ncit-showcase"
    )

    assert unknown.status_code == 404
    assert malformed.status_code == 503
    assert missing.status_code == 503
