"""Hermetic tests for the mappings + $translate endpoints (issue #82)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.dependencies import (
    get_ncit_client,
    get_ncit_store,
    get_repository_metadata,
    get_xref_store,
)
from backend.main import create_app
from backend.repository_metadata import RepositoryUnhealthy
from ontolib.repositories.xref.models import (
    EndpointIdentity,
    MappingResult,
    UnavailableXrefGenerationError,
)
from ontolib.repositories.xref.vocab import (
    BROAD_MATCH,
    CLOSE_MATCH,
    EXACT_MATCH,
    NARROW_MATCH,
    RELATED_MATCH,
    MappingLifecycle,
    MappingPredicate,
)


class _FakeStore:
    async def labels_for(self, codes: list[str]) -> dict[str, str]:
        return {}


class _FakeClient:
    async def select(self, _query: str) -> list[dict[str, str | None]]:
        return []


class _FakeMetadata:
    icdo_calls = 0

    async def ncit(self) -> object:
        return type(
            "NcitReady",
            (),
            {"source_identity": "a" * 64, "manifest_identity": "d" * 64},
        )()

    async def uberon(self) -> object:
        serving = type("Serving", (), {"sha256": "b" * 64})()
        observation = type("Observation", (), {"serving": serving})()
        return type(
            "UberonReady",
            (),
            {"source_identity": "c" * 64, "observation": observation},
        )()

    async def icdo(self, edition: str, axis: str) -> object:
        type(self).icdo_calls += 1
        del edition, axis
        return type(
            "IcdoReady",
            (),
            {
                "activation_identity": "e" * 64,
                "serving_identity": "f" * 64,
            },
        )()


class _FakeXrefStore:
    def __init__(self) -> None:
        def mapping(
            subject: str,
            obj: str,
            predicate: MappingPredicate,
            lifecycle: MappingLifecycle,
            confidence: float,
        ) -> MappingResult:
            return MappingResult(
                subject=EndpointIdentity("ncit", "26.07d", subject),
                predicate=predicate,
                object=EndpointIdentity(
                    "icdo" if obj.startswith("ICD-O-3:") else "uberon",
                    "3.2" if obj.startswith("ICD-O-3:") else "2026-06-19",
                    obj,
                ),
                lifecycle=lifecycle,
                confidence=confidence,
            )

        self.mappings: dict[str, list[MappingResult]] = {
            "C12400": [
                mapping("C12400", "UBERON:0002046", EXACT_MATCH, "validated", 0.95),
                mapping("C12400", "UBERON:0002048", CLOSE_MATCH, "proposed", 0.7),
            ],
            "C3262": [
                mapping("C3262", "UBERON:0002107", EXACT_MATCH, "active", 1.0),
            ],
            "C12345": [
                mapping("C12345", "ICD-O-3:1234", EXACT_MATCH, "validated", 0.9),
            ],
            "C12346": [
                mapping("C12346", "UBERON:0002046", EXACT_MATCH, "validated", 0.95),
                mapping("C12346", "ICD-O-3:1234", EXACT_MATCH, "validated", 0.9),
            ],
            "C188218": [
                MappingResult(
                    subject=EndpointIdentity("ncit", "26.07d", "C188218"),
                    predicate=EXACT_MATCH,
                    object=EndpointIdentity("icdo", "3.2", "8240/3"),
                    lifecycle="validated",
                    confidence=0.9,
                )
            ],
            "C50000": [
                mapping("C50000", "UBERON:0002107", EXACT_MATCH, "quarantined", 0.5),
            ],
            "C60000": [
                mapping("C60000", "UBERON:0009999", CLOSE_MATCH, "validated", 0.9),
            ],
        }
        self.reverse: dict[str, list[MappingResult]] = {
            "UBERON:0002046": [
                mapping("C12400", "UBERON:0002046", EXACT_MATCH, "validated", 0.95),
                mapping("C3262", "UBERON:0002046", CLOSE_MATCH, "proposed", 0.6),
            ],
            "UBERON:0009998": [
                mapping("C70000", "UBERON:0009998", BROAD_MATCH, "validated", 0.8)
            ],
            "UBERON:0009997": [
                mapping("C70001", "UBERON:0009997", NARROW_MATCH, "validated", 0.75)
            ],
        }
        self.lookup_calls = 0

    async def mappings_by_subjects(
        self, codes: set[str], **_kwargs: object
    ) -> dict[str, list[MappingResult]]:
        return {c: self.mappings.get(c, []) for c in codes if c in self.mappings}

    async def mappings_by_objects(
        self, curies: set[str], **_kwargs: object
    ) -> dict[str, list[MappingResult]]:
        return {c: self.reverse.get(c, []) for c in curies if c in self.reverse}

    async def mappings_for_identifiers(
        self, identifiers: set[str], **_kwargs: object
    ) -> dict[str, list[MappingResult]]:
        self.lookup_calls += 1
        return {
            code: [*self.mappings.get(code, []), *self.reverse.get(code, [])]
            for code in identifiers
            if code in self.mappings or code in self.reverse
        }


def _client() -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_ncit_client] = _FakeClient
    app.dependency_overrides[get_ncit_store] = _FakeStore
    app.dependency_overrides[get_xref_store] = _FakeXrefStore
    app.dependency_overrides[get_repository_metadata] = _FakeMetadata
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
    assert m0["system"] == "uberon"
    assert m0["version"] == "2026-06-19"
    assert m0["predicate"] == EXACT_MATCH
    assert m0["lifecycle"] == "validated"
    assert m0["confidence"] == 0.95
    assert m0["is_identity"] is True


@pytest.mark.api
def test_public_concept_mappings_omit_icdo_but_entitled_call_returns_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "enable_licensed_mappings", True)
    monkeypatch.setattr(get_settings(), "icdo_entitlement_key", "licensed")
    client = next(_client())

    public = client.get("/api/v1/ncit/concepts/C188218/mappings")
    entitled = client.get(
        "/api/v1/ncit/concepts/C188218/mappings",
        headers={"X-ICDO-Entitlement": "licensed"},
    )

    assert public.status_code == entitled.status_code == 200
    assert public.json()["mappings"] == []
    assert entitled.json()["mappings"][0]["system"] == "icdo"


@pytest.mark.api
def test_entitlement_cannot_expose_icdo_when_server_capability_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "enable_licensed_mappings", False)
    monkeypatch.setattr(get_settings(), "icdo_entitlement_key", "licensed")

    response = next(_client()).get(
        "/api/v1/ncit/concepts/C188218/mappings",
        headers={"X-ICDO-Entitlement": "licensed"},
    )

    assert response.status_code == 200
    assert response.json()["mappings"] == []


@pytest.mark.api
def test_concept_mappings_preserves_reverse_many_to_one_in_one_indexed_query() -> None:
    store = _FakeXrefStore()
    store.reverse["C12468"] = [
        MappingResult(
            subject=EndpointIdentity("uberon-cl", "2026-06-19", code),
            predicate=CLOSE_MATCH,
            object=EndpointIdentity("ncit", "26.07d", "C12468"),
            lifecycle="proposed",
            confidence=0.9,
        )
        for code in ("UBERON:0000171", "UBERON:0002048")
    ]
    app = create_app()
    app.dependency_overrides[get_ncit_store] = _FakeStore
    app.dependency_overrides[get_ncit_client] = _FakeClient
    app.dependency_overrides[get_xref_store] = lambda: store
    app.dependency_overrides[get_repository_metadata] = _FakeMetadata

    with TestClient(app) as client:
        response = client.get("/api/v1/ncit/concepts/C12468/mappings")

    assert response.status_code == 200
    assert [row["object_id"] for row in response.json()["mappings"]] == [
        "UBERON:0000171",
        "UBERON:0002048",
    ]
    assert store.lookup_calls == 1


@pytest.mark.api
@pytest.mark.parametrize(
    ("stored", "exposed"),
    [
        (BROAD_MATCH, NARROW_MATCH),
        (NARROW_MATCH, BROAD_MATCH),
        (EXACT_MATCH, EXACT_MATCH),
        (CLOSE_MATCH, CLOSE_MATCH),
        (RELATED_MATCH, RELATED_MATCH),
    ],
)
def test_concept_mappings_orients_directional_reverse_rows_to_requested_ncit(
    stored: MappingPredicate, exposed: MappingPredicate
) -> None:
    store = _FakeXrefStore()
    store.reverse["C12468"] = [
        MappingResult(
            subject=EndpointIdentity("uberon-cl", "2026-06-19", "UBERON:0002048"),
            predicate=stored,
            object=EndpointIdentity("ncit", "26.07d", "C12468"),
            lifecycle="proposed",
            confidence=0.9,
        )
    ]
    app = create_app()
    app.dependency_overrides[get_ncit_store] = _FakeStore
    app.dependency_overrides[get_ncit_client] = _FakeClient
    app.dependency_overrides[get_xref_store] = lambda: store
    app.dependency_overrides[get_repository_metadata] = _FakeMetadata

    with TestClient(app) as client:
        response = client.get("/api/v1/ncit/concepts/C12468/mappings")

    assert response.status_code == 200
    assert response.json()["mappings"][0]["predicate"] == exposed


@pytest.mark.api
@pytest.mark.parametrize("predicate", [BROAD_MATCH, NARROW_MATCH])
def test_concept_mappings_preserve_direction_for_requested_subject(
    predicate: MappingPredicate,
) -> None:
    store = _FakeXrefStore()
    store.mappings["C12468"] = [
        MappingResult(
            subject=EndpointIdentity("ncit", "26.07d", "C12468"),
            predicate=predicate,
            object=EndpointIdentity("uberon-cl", "2026-06-19", "UBERON:0002048"),
            lifecycle="proposed",
            confidence=0.9,
        )
    ]
    app = create_app()
    app.dependency_overrides[get_ncit_store] = _FakeStore
    app.dependency_overrides[get_ncit_client] = _FakeClient
    app.dependency_overrides[get_xref_store] = lambda: store
    app.dependency_overrides[get_repository_metadata] = _FakeMetadata

    with TestClient(app) as client:
        response = client.get("/api/v1/ncit/concepts/C12468/mappings")

    assert response.status_code == 200
    assert response.json()["mappings"][0]["predicate"] == predicate


@pytest.mark.api
def test_concept_mappings_exact_match_with_nonactive_lifecycle_is_not_identity() -> (
    None
):
    client = next(_client())
    resp = client.get("/api/v1/ncit/concepts/C50000/mappings")
    assert resp.status_code == 200
    entry = resp.json()["mappings"][0]
    assert entry["predicate"] == EXACT_MATCH
    assert entry["lifecycle"] == "quarantined"
    # exactMatch alone is not identity; the lifecycle must be validated/active.
    assert entry["is_identity"] is False


@pytest.mark.api
def test_nonexact_match_with_active_lifecycle_is_not_identity() -> None:
    client = next(_client())
    resp = client.get("/api/v1/ncit/concepts/C60000/mappings")
    assert resp.status_code == 200
    entry = resp.json()["mappings"][0]
    assert entry["predicate"] == CLOSE_MATCH
    assert entry["lifecycle"] == "validated"
    # A validated/active lifecycle is not enough; the predicate must be exactMatch.
    assert entry["is_identity"] is False


@pytest.mark.api
def test_concept_mappings_no_mappings_returns_empty() -> None:
    client = next(_client())
    resp = client.get("/api/v1/ncit/concepts/C99999/mappings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == "C99999"
    assert body["mappings"] == []


@pytest.mark.api
def test_concept_mappings_maps_missing_requested_family_to_503() -> None:
    class _Unavailable(_FakeXrefStore):
        async def mappings_for_identifiers(
            self, identifiers: set[str], **_kwargs: object
        ) -> dict[str, list[MappingResult]]:
            del identifiers
            raise UnavailableXrefGenerationError(
                "no active certified Uberon alignment generation"
            )

    app = create_app()
    app.dependency_overrides[get_ncit_store] = _FakeStore
    app.dependency_overrides[get_ncit_client] = _FakeClient
    app.dependency_overrides[get_xref_store] = _Unavailable
    app.dependency_overrides[get_repository_metadata] = _FakeMetadata

    with TestClient(app) as client:
        response = client.get("/api/v1/ncit/concepts/C99999/mappings")

    assert response.status_code == 503


@pytest.mark.api
def test_concept_mappings_rejects_malformed_code() -> None:
    client = next(_client())
    resp = client.get("/api/v1/ncit/concepts/bad code/mappings")
    assert resp.status_code == 404


@pytest.mark.api
@pytest.mark.parametrize("repository", ["ncit", "uberon"])
def test_concept_mappings_refuses_each_uncertified_source(repository: str) -> None:
    class _Unhealthy(_FakeMetadata):
        async def ncit(self) -> object:
            if repository == "ncit":
                return RepositoryUnhealthy(
                    repository="ncit", reason="observation-mismatch", message="drift"
                )
            return await super().ncit()

        async def uberon(self) -> object:
            if repository == "uberon":
                return RepositoryUnhealthy(
                    repository="uberon", reason="observation-mismatch", message="drift"
                )
            return await super().uberon()

        async def icdo(self, edition: str, axis: str) -> object:
            if repository == "icdo":
                return RepositoryUnhealthy(
                    repository="icdo", reason="observation-mismatch", message="drift"
                )
            return await super().icdo(edition, axis)

    store = _FakeXrefStore()
    app = create_app()
    app.dependency_overrides[get_ncit_client] = _FakeClient
    app.dependency_overrides[get_ncit_store] = _FakeStore
    app.dependency_overrides[get_xref_store] = lambda: store
    app.dependency_overrides[get_repository_metadata] = _Unhealthy

    with TestClient(app) as client:
        response = client.get("/api/v1/ncit/concepts/C12400/mappings")

    assert response.status_code == 503
    assert store.lookup_calls == 0


@pytest.mark.api
def test_public_concept_mappings_does_not_request_licensed_family() -> None:
    class _UnhealthyIcdo(_FakeMetadata):
        async def icdo(self, edition: str, axis: str) -> object:
            del edition, axis
            raise AssertionError("public mapping read requested ICD-O metadata")

    app = create_app()
    app.dependency_overrides[get_ncit_client] = _FakeClient
    app.dependency_overrides[get_ncit_store] = _FakeStore
    app.dependency_overrides[get_xref_store] = _FakeXrefStore
    app.dependency_overrides[get_repository_metadata] = _UnhealthyIcdo

    with TestClient(app) as client:
        response = client.get("/api/v1/ncit/concepts/C12400/mappings")

    assert response.status_code == 200


@pytest.mark.api
def test_entitled_concept_mappings_refuses_uncertified_licensed_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "enable_licensed_mappings", True)
    monkeypatch.setattr(get_settings(), "icdo_entitlement_key", "licensed")

    class _UnhealthyIcdo(_FakeMetadata):
        async def icdo(self, edition: str, axis: str) -> object:
            del edition, axis
            return RepositoryUnhealthy(
                repository="icdo", reason="observation-mismatch", message="drift"
            )

    app = create_app()
    app.dependency_overrides[get_ncit_client] = _FakeClient
    app.dependency_overrides[get_ncit_store] = _FakeStore
    app.dependency_overrides[get_xref_store] = _FakeXrefStore
    app.dependency_overrides[get_repository_metadata] = _UnhealthyIcdo

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/ncit/concepts/C12400/mappings",
            headers={"X-ICDO-Entitlement": "licensed"},
        )

    assert response.status_code == 503


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
    assert entry["concept"]["system"] == "uberon"
    assert entry["concept"]["version"] == "2026-06-19"
    assert entry["equivalence"] == "equivalent"
    assert entry["confidence"] == 0.95


@pytest.mark.api
def test_translate_upstream_to_ncit_selects_subject_and_inverts_direction() -> None:
    response = next(_client()).post(
        "/api/v1/mappings/$translate", json={"code": "UBERON:0009998"}
    )

    assert response.status_code == 200
    assert response.json()["result"] == [
        {
            "equivalence": "narrow",
            "concept": {
                "code": "C70000",
                "system": "ncit",
                "version": "26.07d",
            },
            "confidence": 0.8,
        }
    ]


@pytest.mark.api
def test_translate_reverse_narrow_match_becomes_broad() -> None:
    response = next(_client()).post(
        "/api/v1/mappings/$translate", json={"code": "UBERON:0009997"}
    )

    assert response.status_code == 200
    assert response.json()["result"] == [
        {
            "equivalence": "broad",
            "concept": {
                "code": "C70001",
                "system": "ncit",
                "version": "26.07d",
            },
            "confidence": 0.75,
        }
    ]


@pytest.mark.api
@pytest.mark.parametrize("repository", ["ncit", "uberon", "icdo"])
def test_translate_refuses_each_requested_uncertified_family(
    repository: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers: dict[str, str] = {}
    if repository == "icdo":
        monkeypatch.setattr(get_settings(), "enable_licensed_mappings", True)
        monkeypatch.setattr(get_settings(), "icdo_entitlement_key", "licensed")
        headers["X-ICDO-Entitlement"] = "licensed"

    class _Unhealthy(_FakeMetadata):
        async def ncit(self) -> object:
            if repository == "ncit":
                return RepositoryUnhealthy(
                    repository="ncit", reason="observation-mismatch", message="drift"
                )
            return await super().ncit()

        async def uberon(self) -> object:
            if repository == "uberon":
                return RepositoryUnhealthy(
                    repository="uberon", reason="observation-mismatch", message="drift"
                )
            return await super().uberon()

        async def icdo(self, edition: str, axis: str) -> object:
            if repository == "icdo":
                return RepositoryUnhealthy(
                    repository="icdo", reason="observation-mismatch", message="drift"
                )
            return await super().icdo(edition, axis)

    app = create_app()
    app.dependency_overrides[get_xref_store] = _FakeXrefStore
    app.dependency_overrides[get_repository_metadata] = _Unhealthy

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/mappings/$translate",
            json={"code": "C12400"},
            headers=headers,
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "Mapping sources are unavailable."


@pytest.mark.api
def test_translate_maps_missing_requested_family_to_503() -> None:
    class _Unavailable(_FakeXrefStore):
        async def mappings_by_subjects(
            self, codes: set[str], **_kwargs: object
        ) -> dict[str, list[MappingResult]]:
            del codes
            raise UnavailableXrefGenerationError(
                "no active certified Uberon alignment generation"
            )

    app = create_app()
    app.dependency_overrides[get_xref_store] = _Unavailable
    app.dependency_overrides[get_repository_metadata] = _FakeMetadata

    with TestClient(app) as client:
        response = client.post("/api/v1/mappings/$translate", json={"code": "C12400"})

    assert response.status_code == 503


@pytest.mark.api
def test_translate_preserves_same_identifier_across_systems_and_versions() -> None:
    store = _FakeXrefStore()
    store.mappings["C12400"] = [
        MappingResult(
            subject=EndpointIdentity("ncit", "26.07d", "C12400"),
            predicate=EXACT_MATCH,
            object=EndpointIdentity(system, version, "SHARED:1"),
            lifecycle="active",
            confidence=1.0,
        )
        for system, version in (("uberon", "v1"), ("other", "v1"), ("uberon", "v2"))
    ]
    app = create_app()
    app.dependency_overrides[get_ncit_client] = _FakeClient
    app.dependency_overrides[get_ncit_store] = _FakeStore
    app.dependency_overrides[get_xref_store] = lambda: store

    with TestClient(app) as client:
        response = client.post("/api/v1/mappings/$translate", json={"code": "C12400"})

    assert response.status_code == 200
    assert {
        (row["concept"]["system"], row["concept"]["version"])
        for row in response.json()["result"]
    } == {("uberon", "v1"), ("other", "v1"), ("uberon", "v2")}


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


@pytest.mark.api
def test_translate_filters_typed_p334_icdo_endpoint_without_prefix() -> None:
    client = next(_client())
    response = client.post("/api/v1/mappings/$translate", json={"code": "C188218"})
    assert response.status_code == 200
    assert response.json()["result"] == [
        {
            "equivalence": "unmatched",
            "concept": {"code": "C188218", "system": None, "version": None},
            "confidence": 0.0,
        }
    ]


@pytest.mark.api
def test_translate_filters_quarantined_lifecycle() -> None:
    """$translate must filter quarantined mappings (only quarantined → unmatched)."""
    client = next(_client())
    resp = client.post(
        "/api/v1/mappings/$translate",
        json={"code": "C50000"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["result"]) == 1
    assert body["result"][0]["equivalence"] == "unmatched"


@pytest.mark.api
@pytest.mark.parametrize("entitlement", [None, "invalid"])
def test_translate_filters_icdo_without_valid_consumer_entitlement(
    monkeypatch: pytest.MonkeyPatch,
    entitlement: str | None,
) -> None:
    """Server capability alone must not expose or resolve ICD-O mappings."""
    monkeypatch.setattr(get_settings(), "enable_licensed_mappings", True)
    monkeypatch.setattr(get_settings(), "icdo_entitlement_key", "licensed")
    _FakeMetadata.icdo_calls = 0
    client = next(_client())
    headers = {"X-ICDO-Entitlement": entitlement} if entitlement is not None else {}
    resp = client.post(
        "/api/v1/mappings/$translate",
        json={"code": "C12346"},
        headers=headers,
    )
    assert resp.status_code == 200
    codes = [entry["concept"]["code"] for entry in resp.json()["result"]]
    assert codes == ["UBERON:0002046"]
    assert _FakeMetadata.icdo_calls == 0


@pytest.mark.api
def test_translate_serves_icdo_with_capability_and_consumer_entitlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "enable_licensed_mappings", True)
    monkeypatch.setattr(get_settings(), "icdo_entitlement_key", "licensed")
    _FakeMetadata.icdo_calls = 0
    response = next(_client()).post(
        "/api/v1/mappings/$translate",
        json={"code": "C12345"},
        headers={"X-ICDO-Entitlement": "licensed"},
    )
    assert response.status_code == 200
    assert [entry["concept"]["code"] for entry in response.json()["result"]] == [
        "ICD-O-3:1234"
    ]
    assert _FakeMetadata.icdo_calls == 1
