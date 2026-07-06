"""Hermetic tests for the decomposition read endpoint (fake client + store)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.dependencies import get_ncit_client, get_ncit_store
from backend.main import create_app
from ontolib.decomposition import vocab
from ontolib.terminologies.namespaces import NCIT_NS


def _row(**kw: str) -> dict[str, str | None]:
    base = dict.fromkeys(
        ("status", "decomposedOn", "axis", "filler", "axisSource", "mostSpecific"), None
    )
    return base | kw


class _FakeClient:
    """Returns canned decomposition rows regardless of the query."""

    def __init__(self, rows: list[dict[str, str | None]]) -> None:
        self._rows = rows

    async def select(self, _query: str) -> list[dict[str, str | None]]:
        return self._rows


class _FakeStore:
    async def labels_for(self, codes: list[str]) -> dict[str, str]:
        known = {"C27970": "Stage III", "C12400": "Thyroid Gland"}
        return {c: known[c] for c in codes if c in known}


def _client(rows: list[dict[str, str | None]]) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_ncit_client] = lambda: _FakeClient(rows)
    app.dependency_overrides[get_ncit_store] = _FakeStore
    with TestClient(app) as client:
        yield client


_DECOMPOSED_ROWS = [
    _row(
        status=vocab.LEGACY_PRECOORDINATED,
        decomposedOn="2026-07-06",
        axis=f"{NCIT_NS}R88",
        filler=f"{NCIT_NS}C27970",
        axisSource="role",
        mostSpecific="false",
    ),
    _row(
        status=vocab.LEGACY_PRECOORDINATED,
        decomposedOn="2026-07-06",
        axis=f"{NCIT_NS}R101",
        filler=f"{NCIT_NS}C12400",
        axisSource="role",
        mostSpecific="true",
    ),
]


@pytest.mark.api
def test_decomposition_returns_flagged_constituents_with_labels() -> None:
    client = next(_client(_DECOMPOSED_ROWS))
    resp = client.get("/api/v1/ncit/concepts/C6135/decomposition")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == "C6135"
    assert body["is_legacy_precoordinated"] is True
    assert body["decomposed_on"] == "2026-07-06"
    by_axis = {c["axis"]: c for c in body["constituents"]}
    assert set(by_axis) == {"R88", "R101"}
    # Filler labels are resolved for display; most-specific flag round-trips.
    assert by_axis["R101"]["filler"] == "C12400"
    assert by_axis["R101"]["filler_label"] == "Thyroid Gland"
    assert by_axis["R101"]["most_specific"] is True
    assert by_axis["R88"]["most_specific"] is False


@pytest.mark.api
def test_undecomposed_concept_resolves_without_a_flag() -> None:
    # A concept absent from the decomposed graph returns 200 with no constituents, so
    # the UI shows "not decomposed" instead of a 404.
    client = next(_client([]))
    resp = client.get("/api/v1/ncit/concepts/C3262/decomposition")
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_legacy_precoordinated"] is False
    assert body["constituents"] == []


@pytest.mark.api
def test_decomposition_rejects_malformed_code() -> None:
    client = next(_client([]))
    resp = client.get("/api/v1/ncit/concepts/bad code/decomposition")
    assert resp.status_code == 404


@pytest.mark.api
def test_op_axis_filler_without_label_is_null_not_dropped() -> None:
    rows = [
        _row(
            status=vocab.LEGACY_PRECOORDINATED,
            axis=f"{vocab.ONTOPRISM_NS}Morphology",
            filler=f"{NCIT_NS}C40384",
            axisSource="parent",
        )
    ]
    client = next(_client(rows))
    body = client.get("/api/v1/ncit/concepts/C6135/decomposition").json()
    (c,) = body["constituents"]
    assert c["axis"] == "op:Morphology"
    assert c["axis_source"] == "parent"
    assert c["filler_label"] is None  # unknown to the fake store → null, still present
