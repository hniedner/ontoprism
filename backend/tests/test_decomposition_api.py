"""Hermetic tests for the decomposition read endpoint (fake client + store)."""

from collections.abc import Collection, Iterator

import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.decomposition_reader import DecompositionReader
from backend.dependencies import (
    get_decomposition_reader,
    get_ncit_store,
    get_repository_metadata,
    get_xref_store,
)
from backend.main import create_app
from ontolib.core.exceptions import StorageError
from ontolib.decomposition import vocab
from ontolib.repositories.xref.models import EndpointIdentity, MappingResult
from ontolib.repositories.xref.vocab import (
    BROAD_MATCH,
    CLOSE_MATCH,
    NARROW_MATCH,
    MappingPredicate,
)
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.sparql_http_client import SparqlHttpClient


def _row(**kw: str) -> dict[str, str | None]:
    base = dict.fromkeys(
        (
            "status",
            "decomposedOn",
            "axis",
            "filler",
            "axisSource",
            "sourceRole",
            "mostSpecific",
        ),
        None,
    )
    return base | kw


class _FakeClient:
    """Returns canned decomposition rows regardless of the query."""

    def __init__(self, rows: list[dict[str, str | None]]) -> None:
        self._rows = rows

    async def select(
        self, query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str]]:
        _ = query, required_variables
        return [
            {key: value for key, value in row.items() if value is not None}
            for row in self._rows
        ]


class _MissingProjectionClient(SparqlHttpClient):
    async def select_raw(self, query: str) -> dict[str, object]:
        _ = query
        return {"head": {"vars": []}, "results": {"bindings": []}}


class _FakeStore:
    async def labels_for(self, codes: list[str]) -> dict[str, str]:
        known = {"C27970": "Stage III", "C12400": "Thyroid Gland"}
        return {c: known[c] for c in codes if c in known}


class _FakeXrefStore:
    def __init__(self, rows: list[MappingResult] | None = None) -> None:
        self.rows = rows or []

    async def mappings_for_identifiers(
        self, codes: set[str], **_kwargs: object
    ) -> dict[str, list[MappingResult]]:
        return {
            code: [
                row
                for row in self.rows
                if code in (row.subject.identifier, row.object.identifier)
            ]
            for code in codes
        }


class _FakeMetadata:
    async def ncit(self) -> object:
        return type("NcitReady", (), {"source_identity": "a" * 64})()

    async def uberon(self) -> object:
        serving = type("Serving", (), {"sha256": "b" * 64})()
        observation = type("Observation", (), {"serving": serving})()
        return type(
            "UberonReady",
            (),
            {"source_identity": "c" * 64, "observation": observation},
        )()

    async def icdo(self, edition: str, axis: str) -> object:
        del edition, axis
        return type(
            "IcdoReady",
            (),
            {"activation_identity": "d" * 64, "serving_identity": "e" * 64},
        )()


def _client(
    rows: list[dict[str, str | None]], xrefs: _FakeXrefStore | None = None
) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_decomposition_reader] = lambda: DecompositionReader(
        _FakeClient(rows)
    )
    app.dependency_overrides[get_ncit_store] = _FakeStore
    app.dependency_overrides[get_xref_store] = lambda: xrefs or _FakeXrefStore()
    app.dependency_overrides[get_repository_metadata] = _FakeMetadata
    with TestClient(app) as client:
        yield client


_DECOMPOSED_ROWS = [
    _row(
        status=vocab.LEGACY_PRECOORDINATED,
        decomposedOn="2026-07-06",
        axis=f"{vocab.ONTOPRISM_NS}StageValue",
        filler=f"{NCIT_NS}C27970",
        axisSource="role",
        sourceRole=f"{NCIT_NS}R88",
        mostSpecific="false",
    ),
    _row(
        status=vocab.LEGACY_PRECOORDINATED,
        decomposedOn="2026-07-06",
        axis=f"{vocab.ONTOPRISM_NS}PrimarySite",
        filler=f"{NCIT_NS}C12400",
        axisSource="role",
        sourceRole=f"{NCIT_NS}R101",
        mostSpecific="true",
    ),
]


@pytest.mark.unit
async def test_reader_rejects_a_response_missing_its_projected_variables() -> None:
    reader = DecompositionReader(_MissingProjectionClient("http://unused.test"))

    with pytest.raises(StorageError, match="missing required projected variable"):
        await reader.rows_for("C6135")


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
    assert set(by_axis) == {"op:StageValue", "op:PrimarySite"}
    # Filler labels are resolved for display; most-specific flag round-trips.
    assert by_axis["op:PrimarySite"]["filler"] == "C12400"
    assert by_axis["op:PrimarySite"]["filler_label"] == "Thyroid Gland"
    assert by_axis["op:PrimarySite"]["source_role"] == "R101"
    assert by_axis["op:PrimarySite"]["most_specific"] is True
    assert by_axis["op:StageValue"]["source_role"] == "R88"
    assert by_axis["op:StageValue"]["most_specific"] is False


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


@pytest.mark.api
def test_decomposition_hides_icdo_upstream_without_entitlement() -> None:
    xrefs = _FakeXrefStore(
        [
            MappingResult(
                subject=EndpointIdentity("ncit", "26.07d", "C12400"),
                predicate=CLOSE_MATCH,
                object=EndpointIdentity("icdo", "3.2", "8503/0"),
                lifecycle="proposed",
                confidence=0.9,
            )
        ]
    )
    response = next(_client(_DECOMPOSED_ROWS, xrefs)).get(
        "/api/v1/ncit/concepts/C6135/decomposition"
    )

    assert response.status_code == 200
    assert "8503/0" not in response.text


@pytest.mark.api
def test_decomposition_entitlement_cannot_override_disabled_server_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "enable_licensed_mappings", False)
    monkeypatch.setattr(get_settings(), "icdo_entitlement_key", "licensed")
    xrefs = _FakeXrefStore(
        [
            MappingResult(
                subject=EndpointIdentity("ncit", "26.07d", "C12400"),
                predicate=CLOSE_MATCH,
                object=EndpointIdentity("icdo", "3.2", "8503/0"),
                lifecycle="proposed",
                confidence=0.9,
            )
        ]
    )

    response = next(_client(_DECOMPOSED_ROWS, xrefs)).get(
        "/api/v1/ncit/concepts/C6135/decomposition",
        headers={"X-ICDO-Entitlement": "licensed"},
    )

    assert response.status_code == 200
    assert "8503/0" not in response.text


@pytest.mark.api
def test_decomposition_serves_icdo_when_capability_and_entitlement_allow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "enable_licensed_mappings", True)
    monkeypatch.setattr(get_settings(), "icdo_entitlement_key", "licensed")
    xrefs = _FakeXrefStore(
        [
            MappingResult(
                subject=EndpointIdentity("ncit", "26.07d", "C12400"),
                predicate=CLOSE_MATCH,
                object=EndpointIdentity("icdo", "3.2", "8503/0"),
                lifecycle="proposed",
                confidence=0.9,
            )
        ]
    )

    response = next(_client(_DECOMPOSED_ROWS, xrefs)).get(
        "/api/v1/ncit/concepts/C6135/decomposition",
        headers={"X-ICDO-Entitlement": "licensed"},
    )

    assert response.status_code == 200
    assert "8503/0" in response.text


@pytest.mark.api
@pytest.mark.parametrize("entitlement", [None, "wrong"])
def test_decomposition_hides_icdo_without_valid_entitlement_when_capable(
    monkeypatch: pytest.MonkeyPatch, entitlement: str | None
) -> None:
    monkeypatch.setattr(get_settings(), "enable_licensed_mappings", True)
    monkeypatch.setattr(get_settings(), "icdo_entitlement_key", "licensed")
    xrefs = _FakeXrefStore(
        [
            MappingResult(
                subject=EndpointIdentity("ncit", "26.07d", "C12400"),
                predicate=CLOSE_MATCH,
                object=EndpointIdentity("icdo", "3.2", "8503/0"),
                lifecycle="proposed",
                confidence=0.9,
            )
        ]
    )
    headers = {"X-ICDO-Entitlement": entitlement} if entitlement is not None else {}

    response = next(_client(_DECOMPOSED_ROWS, xrefs)).get(
        "/api/v1/ncit/concepts/C6135/decomposition", headers=headers
    )

    assert response.status_code == 200
    assert "8503/0" not in response.text


@pytest.mark.api
@pytest.mark.parametrize(
    ("stored", "exposed"),
    [(BROAD_MATCH, NARROW_MATCH), (NARROW_MATCH, BROAD_MATCH)],
)
def test_decomposition_orients_directional_rows_to_requested_filler(
    stored: MappingPredicate, exposed: MappingPredicate
) -> None:
    xrefs = _FakeXrefStore(
        [
            MappingResult(
                subject=EndpointIdentity("uberon", "2026-06-19", "UBERON:0002046"),
                predicate=stored,
                object=EndpointIdentity("ncit", "26.07d", "C12400"),
                lifecycle="proposed",
                confidence=0.9,
            )
        ]
    )

    response = next(_client(_DECOMPOSED_ROWS, xrefs)).get(
        "/api/v1/ncit/concepts/C6135/decomposition"
    )

    assert response.status_code == 200
    primary_site = next(
        row for row in response.json()["constituents"] if row["filler"] == "C12400"
    )
    assert primary_site["upstream"][0]["object_id"] == "UBERON:0002046"
    assert primary_site["upstream"][0]["predicate"] == exposed
