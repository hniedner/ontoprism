"""Hermetic tests for the mappings + $translate endpoints (issue #82)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.dependencies import get_ncit_client, get_ncit_store, get_xref_store
from backend.main import create_app
from ontolib.repositories.xref.vocab import CLOSE_MATCH, EXACT_MATCH


class _FakeStore:
    async def labels_for(self, codes: list[str]) -> dict[str, str]:
        return {}


class _FakeClient:
    async def select(self, _query: str) -> list[dict[str, str | None]]:
        return []


class _FakeXrefStore:
    def __init__(self) -> None:
        self.mappings: dict[str, list[tuple[str, str, str, float]]] = {
            "C12400": [
                ("UBERON:0002046", EXACT_MATCH, "validated", 0.95),
                ("UBERON:0002048", CLOSE_MATCH, "proposed", 0.7),
            ],
            "C3262": [
                ("UBERON:0002107", EXACT_MATCH, "active", 1.0),
            ],
            "C12345": [
                ("ICD-O-3:1234", EXACT_MATCH, "validated", 0.9),
            ],
        }
        self.reverse: dict[str, list[tuple[str, str, str, float]]] = {
            "UBERON:0002046": [
                ("C12400", EXACT_MATCH, "validated", 0.95),
                ("C3262", CLOSE_MATCH, "proposed", 0.6),
            ],
        }

    async def mappings_by_subjects(
        self, codes: set[str]
    ) -> dict[str, list[tuple[str, str, str, float]]]:
        return {c: self.mappings.get(c, []) for c in codes if c in self.mappings}

    async def mappings_by_objects(
        self, curies: set[str]
    ) -> dict[str, list[tuple[str, str, str, float]]]:
        return {c: self.reverse.get(c, []) for c in curies if c in self.reverse}


def _client() -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_ncit_client] = _FakeClient
    app.dependency_overrides[get_ncit_store] = _FakeStore
    app.dependency_overrides[get_xref_store] = _FakeXrefStore
    with TestClient(app) as client:
        yield client


@pytest.mark.api
def test_concept_mappings_returns_forward_mappings() -> None:
    client = next(_client())
    resp = client.get("/api/v1/ncit/concepts/C12400/mappings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == "C12400"
    assert len(body["mappings"]) == 2
    m0 = body["mappings"][0]
    assert m0["object_id"] == "UBERON:0002046"
    assert m0["predicate"] == EXACT_MATCH
    assert m0["lifecycle"] == "validated"
    assert m0["confidence"] == 0.95
    assert m0["is_identity"] is True


@pytest.mark.api
def test_concept_mappings_no_mappings_returns_empty() -> None:
    client = next(_client())
    resp = client.get("/api/v1/ncit/concepts/C99999/mappings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == "C99999"
    assert body["mappings"] == []


@pytest.mark.api
def test_concept_mappings_rejects_malformed_code() -> None:
    client = next(_client())
    resp = client.get("/api/v1/ncit/concepts/bad code/mappings")
    assert resp.status_code == 404


# --- $translate ---


@pytest.mark.api
def test_translate_ncit_to_upstream() -> None:
    client = next(_client())
    resp = client.post(
        "/api/v1/mappings/$translate",
        json={"code": "C12400"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["result"]) >= 1
    entry = body["result"][0]
    assert "equivalence" in entry
    assert "concept" in entry
    assert entry["concept"]["code"] == "UBERON:0002046"
    assert entry["equivalence"] == "equivalent"
    assert entry["confidence"] == 0.95


@pytest.mark.api
def test_translate_filters_proposed_and_quarantined() -> None:
    """$translate must never serve proposed or quarantined lifecycles."""
    client = next(_client())
    resp = client.post(
        "/api/v1/mappings/$translate",
        json={"code": "C12400"},
    )
    assert resp.status_code == 200
    results = resp.json()["result"]
    # UBERON:0002048 is proposed — must be filtered
    assert not any(e["concept"]["code"] == "UBERON:0002048" for e in results)
    # UBERON:0002046 is validated — survives
    assert any(e["concept"]["code"] == "UBERON:0002046" for e in results)


@pytest.mark.api
def test_translate_ncit_with_no_mappings_returns_unmatched() -> None:
    client = next(_client())
    resp = client.post(
        "/api/v1/mappings/$translate",
        json={"code": "C99999"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["result"]) == 1
    assert body["result"][0]["equivalence"] == "unmatched"
    assert body["result"][0]["concept"]["code"] == "C99999"


@pytest.mark.api
def test_translate_filters_licensed_sources() -> None:
    """$translate must filter ICD-O-3 when enable_licensed_mappings is False."""
    client = next(_client())
    resp = client.post(
        "/api/v1/mappings/$translate",
        json={"code": "C12345"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # All mappings for C12345 are ICD-O-3 — gate removes them, fallback to unmatched
    assert len(body["result"]) == 1
    assert body["result"][0]["equivalence"] == "unmatched"
