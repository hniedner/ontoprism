import base64
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.dependencies import (
    get_icdo_repository,
    get_repository_metadata,
    get_uberon_store,
)
from backend.main import create_app
from backend.repository_metadata import RepositoryUnhealthy
from ontolib.repositories.icdo.models import CanonicalDataset, IcdoRecord, SourceShape
from ontolib.repositories.icdo.store import IcdoCertificationError


class _Store:
    def __init__(self) -> None:
        self.calls = 0

    async def metadata(self, edition: str, axis: str) -> object:
        self.calls += 1
        return SimpleNamespace(
            model_dump=lambda **_: {"edition": edition, "axis": axis, "row_count": 1}
        )

    async def certified_metadata(
        self, edition: str, axis: str, expected: object
    ) -> object:
        del expected
        return await self.metadata(edition, axis)

    async def search(
        self, edition: str, axis: str, **kwargs: object
    ) -> dict[str, object]:
        self.calls += 1
        return {
            "edition": edition,
            "axis": axis,
            "query": kwargs.get("query", ""),
            "total": 1,
            "limit": kwargs["limit"],
            "offset": kwargs["offset"],
            "hits": [
                {
                    "code": "8503/0",
                    "level": "morphology",
                    "preferred": "Intraductal papilloma",
                    "behaviour": "0",
                }
            ],
        }

    async def detail(
        self, edition: str, axis: str, code: str
    ) -> dict[str, object] | None:
        self.calls += 1
        if code != "8503/0":
            return None
        return {
            "code": code,
            "level": "morphology",
            "preferred": "Intraductal papilloma",
            "synonyms": ["Papilloma"],
            "related": [],
            "notes": [],
        }

    async def dataset(self, edition: str, axis: str) -> object | None:
        self.calls += 1
        return None


def _client(store: _Store, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    class _Metadata:
        async def icdo(self, edition: str, axis: str) -> object:
            try:
                result = await store.certified_metadata(edition, axis, object())
            except IcdoCertificationError as exc:
                return RepositoryUnhealthy(
                    repository="icdo",
                    reason="observation-mismatch",
                    message=str(exc),
                )
            if result is None:
                return RepositoryUnhealthy(
                    repository="icdo",
                    reason="repository-unreachable",
                    message="ICD-O dataset is unavailable.",
                )
            return result

    monkeypatch.setattr(get_settings(), "icdo_entitlement_key", "licensed")
    app = create_app()
    app.dependency_overrides[get_icdo_repository] = lambda: store
    metadata = _Metadata()
    app.dependency_overrides[get_repository_metadata] = lambda: metadata
    with TestClient(app) as client:
        yield client


@pytest.mark.api
def test_entitlement_refuses_before_repository_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _Store()
    response = next(_client(store, monkeypatch)).get("/api/v1/icdo/3.2/morphology/list")
    assert response.status_code == 403
    assert store.calls == 0
    assert "Intraductal" not in response.text


@pytest.mark.api
def test_list_search_metadata_and_safe_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store()
    client = next(_client(store, monkeypatch))
    headers = {"X-ICDO-Entitlement": "licensed"}
    listed = client.get("/api/v1/icdo/3.2/morphology/list", headers=headers)
    searched = client.get(
        "/api/v1/icdo/3.2/morphology/search",
        params={"q": "papilloma", "behaviour": "0"},
        headers=headers,
    )
    metadata = client.get("/api/v1/icdo/3.2/morphology/metadata", headers=headers)
    detail = client.get(
        "/api/v1/icdo/3.2/morphology/concepts/ODUwMy8w", headers=headers
    )
    assert [
        listed.status_code,
        searched.status_code,
        metadata.status_code,
        detail.status_code,
    ] == [200] * 4
    assert detail.json()["code"] == "8503/0"
    assert metadata.json() == {"edition": "3.2", "axis": "morphology", "row_count": 1}


@pytest.mark.api
def test_invalid_dataset_combination_and_code_are_input_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = next(_client(_Store(), monkeypatch))
    headers = {"X-ICDO-Entitlement": "licensed"}
    assert (
        client.get("/api/v1/icdo/3.2/topography/list", headers=headers).status_code
        == 404
    )
    assert (
        client.get(
            "/api/v1/icdo/4.0/morphology/concepts/not-base64", headers=headers
        ).status_code
        == 404
    )


@pytest.mark.api
@pytest.mark.parametrize(
    ("edition", "axis", "code"),
    [("4.0", "morphology", "85032/0"), ("4.0", "topography", "C34.9")],
)
def test_icdo4_code_variants_round_trip_from_safe_segments(
    monkeypatch: pytest.MonkeyPatch, edition: str, axis: str, code: str
) -> None:
    class _CodeStore(_Store):
        async def detail(self, edition: str, axis: str, code: str) -> dict[str, object]:
            self.calls += 1
            assert (edition, axis, code) == (
                expected_edition,
                expected_axis,
                expected_code,
            )
            return {"code": code, "level": axis, "preferred": "Publisher term"}

    expected_edition, expected_axis, expected_code = edition, axis, code
    segment = base64.urlsafe_b64encode(expected_code.encode()).decode().rstrip("=")
    response = next(_client(_CodeStore(), monkeypatch)).get(
        f"/api/v1/icdo/{edition}/{axis}/concepts/{segment}",
        headers={"X-ICDO-Entitlement": "licensed"},
    )
    assert response.status_code == 200
    assert response.json()["code"] == code


@pytest.mark.api
def test_well_formed_absent_code_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    segment = base64.urlsafe_b64encode(b"9999/9").decode().rstrip("=")
    response = next(_client(_Store(), monkeypatch)).get(
        f"/api/v1/icdo/3.2/morphology/concepts/{segment}",
        headers={"X-ICDO-Entitlement": "licensed"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "ICD-O code not found."


@pytest.mark.api
def test_unpublished_metadata_is_typed_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Unavailable(_Store):
        async def metadata(self, edition: str, axis: str) -> None:
            self.calls += 1

    client = next(_client(_Unavailable(), monkeypatch))
    response = client.get(
        "/api/v1/icdo/4.0/topography/metadata",
        headers={"X-ICDO-Entitlement": "licensed"},
    )
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"]["message"]


@pytest.mark.api
def test_congruence_entitlement_refuses_before_icdo_or_uberon_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Uberon:
        calls = 0

        async def congruence_records(self, **kwargs: object) -> list[dict[str, str]]:
            self.calls += 1
            return []

    store = _Store()
    uberon = _Uberon()
    app = create_app()
    monkeypatch.setattr(get_settings(), "icdo_entitlement_key", "licensed")
    app.dependency_overrides[get_icdo_repository] = lambda: store
    app.dependency_overrides[get_uberon_store] = lambda: uberon
    with TestClient(app) as client:
        response = client.get("/api/v1/icdo/4.0/topography/congruence")
    assert response.status_code == 403
    assert store.calls == uberon.calls == 0


@pytest.mark.api
def test_congruence_classifies_active_sources_and_pages_uberon_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = CanonicalDataset(
        edition="4.0",
        axis="topography",
        records=(
            IcdoRecord(
                code="C34.9", level="leaf", parent_code="C34", preferred="Lung, NOS"
            ),
        ),
        source_shape=SourceShape(
            sheet_names=(), headers=(), merged_ranges=(), trailing_blank_rows=0
        ),
        source_sha256="a" * 64,
    )

    class _ReportStore(_Store):
        async def dataset(self, edition: str, axis: str) -> CanonicalDataset:
            self.calls += 1
            return dataset

    class _Uberon:
        async def congruence_records(
            self, *, limit: int, offset: int
        ) -> list[dict[str, str]]:
            assert limit == 5000
            return (
                [
                    {
                        "code": "UBERON:0002048",
                        "label": "lung",
                        "synonyms": "",
                        "parents": "",
                    }
                ]
                if offset == 0
                else []
            )

    class _Metadata:
        async def icdo(self, edition: str, axis: str) -> object:
            assert (edition, axis) == ("4.0", "topography")
            return SimpleNamespace(serving_identity="b" * 64)

        async def uberon(self, *, force: bool = False) -> object:
            del force
            return SimpleNamespace(
                observation=SimpleNamespace(serving=SimpleNamespace(sha256="c" * 64))
            )

    app = create_app()
    monkeypatch.setattr(get_settings(), "icdo_entitlement_key", "licensed")
    report_store = _ReportStore()
    uberon = _Uberon()
    metadata = _Metadata()
    app.dependency_overrides[get_icdo_repository] = lambda: report_store
    app.dependency_overrides[get_uberon_store] = lambda: uberon
    app.dependency_overrides[get_repository_metadata] = lambda: metadata
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/icdo/4.0/topography/congruence",
            headers={"X-ICDO-Entitlement": "licensed"},
        )
    assert response.status_code == 200
    assert response.json()["rows"][0]["classification"] == "one-supported-candidate"


@pytest.mark.api
def test_congruence_refuses_unhealthy_uberon_without_inventory_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ReportStore(_Store):
        async def dataset(self, edition: str, axis: str) -> object:
            return object()

        async def metadata(self, edition: str, axis: str) -> object:
            return object()

    class _Metadata:
        async def icdo(self, edition: str, axis: str) -> object:
            del edition, axis
            return SimpleNamespace(serving_identity="b" * 64)

        async def uberon(self, *, force: bool = False) -> RepositoryUnhealthy:
            del force
            return RepositoryUnhealthy(
                repository="uberon",
                reason="observation-mismatch",
                message="drift",
            )

    app = create_app()
    monkeypatch.setattr(get_settings(), "icdo_entitlement_key", "licensed")
    report_store = _ReportStore()
    metadata = _Metadata()
    app.dependency_overrides[get_icdo_repository] = lambda: report_store
    app.dependency_overrides[get_repository_metadata] = lambda: metadata
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/icdo/4.0/topography/congruence",
            headers={"X-ICDO-Entitlement": "licensed"},
        )
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"]


@pytest.mark.api
def test_congruence_refuses_uncertified_icdo_before_protected_rows_are_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ReportStore(_Store):
        async def dataset(self, edition: str, axis: str) -> object:
            self.calls += 1
            return object()

    class _Metadata:
        async def icdo(self, edition: str, axis: str) -> RepositoryUnhealthy:
            del edition, axis
            return RepositoryUnhealthy(
                repository="icdo",
                reason="observation-mismatch",
                message="drift",
            )

        async def uberon(self, *, force: bool = False) -> object:
            del force
            return SimpleNamespace(
                observation=SimpleNamespace(serving=SimpleNamespace(sha256="c" * 64))
            )

    app = create_app()
    monkeypatch.setattr(get_settings(), "icdo_entitlement_key", "licensed")
    report_store = _ReportStore()
    app.dependency_overrides[get_icdo_repository] = lambda: report_store
    metadata = _Metadata()
    app.dependency_overrides[get_repository_metadata] = lambda: metadata
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/icdo/4.0/topography/congruence",
            headers={"X-ICDO-Entitlement": "licensed"},
        )
    assert response.status_code == 503
    assert report_store.calls == 0


@pytest.mark.api
def test_congruence_refuses_missing_active_topography_after_certification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Metadata:
        async def icdo(self, edition: str, axis: str) -> object:
            del edition, axis
            return SimpleNamespace(serving_identity="b" * 64)

        async def uberon(self, *, force: bool = False) -> object:
            del force
            return SimpleNamespace(
                observation=SimpleNamespace(serving=SimpleNamespace(sha256="c" * 64))
            )

    app = create_app()
    monkeypatch.setattr(get_settings(), "icdo_entitlement_key", "licensed")
    store = _Store()
    metadata = _Metadata()
    app.dependency_overrides[get_icdo_repository] = lambda: store
    app.dependency_overrides[get_repository_metadata] = lambda: metadata
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/icdo/4.0/topography/congruence",
            headers={"X-ICDO-Entitlement": "licensed"},
        )
    assert response.status_code == 503


@pytest.mark.api
@pytest.mark.parametrize(
    ("edition", "axis"),
    [("4.0", "morphology"), ("4.0", "topography")],
)
def test_metadata_selects_each_certified_dataset_expectation(
    monkeypatch: pytest.MonkeyPatch, edition: str, axis: str
) -> None:
    store = _Store()
    response = next(_client(store, monkeypatch)).get(
        f"/api/v1/icdo/{edition}/{axis}/metadata",
        headers={"X-ICDO-Entitlement": "licensed"},
    )
    assert response.status_code == 200


@pytest.mark.api
def test_metadata_drift_returns_typed_unhealthy_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Drift(_Store):
        async def certified_metadata(
            self, edition: str, axis: str, expected: object
        ) -> object:
            raise IcdoCertificationError("serving_sha256 drift")

    response = next(_client(_Drift(), monkeypatch)).get(
        "/api/v1/icdo/4.0/topography/metadata",
        headers={"X-ICDO-Entitlement": "licensed"},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "observation-mismatch"
