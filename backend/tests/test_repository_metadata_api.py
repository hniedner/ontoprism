"""Readiness/refresh API contracts for discriminated repository metadata."""

from datetime import UTC, datetime
from typing import Any, Literal

import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.dependencies import get_repository_metadata
from backend.main import create_app
from backend.repository_metadata import (
    CadsrRepositoryReady,
    CadsrSourceMetadata,
    IcdoRepositoryReady,
    NcitRepositoryReady,
    RepositoryUnhealthy,
    UberonClassCounts,
    UberonRepositoryReady,
)
from ontolib.terminologies.ncit.sibling_store import CandidateObservation
from ontolib.terminologies.uberon.store import (
    CertifiedUberonIndexObservation,
    UberonServingFingerprint,
)


def _ncit_ready() -> NcitRepositoryReady:
    return NcitRepositoryReady(
        source_identity="a" * 64,
        manifest_identity="b" * 64,
        release="26.07d",
        activated_at=datetime(2026, 8, 10, 18, 30, tzinfo=UTC),
        observation=CandidateObservation(
            default_triples=12_980_813,
            stated_triples=10_855_010,
            named_graphs=(),
            default_version="26.07d",
            stated_version="26.07d",
            restriction_count=150_000,
            has_required_restriction=True,
            default_has_stated_only_sentinel=False,
            stated_has_stated_only_sentinel=True,
        ),
    )


def _cadsr_ready() -> CadsrRepositoryReady:
    return CadsrRepositoryReady(
        source_identity="c" * 64,
        manifest_identity="d" * 64,
        item_count=79_835,
        source=CadsrSourceMetadata(
            url="https://example.test/released.zip",
            downloaded_at="2026-08-10T18:00:00+00:00",
            etag=None,
            last_modified=None,
            archive_size=123,
            archive_sha256="c" * 64,
            member_count=14,
            member_names_sha256="e" * 64,
            first_member_timestamp="2026-08-09T00:00:00",
            last_member_timestamp="2026-08-09T01:00:00",
        ),
    )


def _uberon_ready() -> UberonRepositoryReady:
    return UberonRepositoryReady(
        source_identity="f" * 64,
        manifest_identity="1" * 64,
        source_sha256="2" * 64,
        version_iri="http://example.test/uberon/2026-06-19",
        observation=CertifiedUberonIndexObservation(
            version_iri="http://example.test/uberon/2026-06-19",
            triples=900_000,
            has_uberon_lung=True,
            has_cell_class=True,
            has_ncit_xref=True,
            serving=UberonServingFingerprint(
                rows=100,
                sha256="f" * 64,
                uberon_classes=16_362,
                cl_classes=1_484,
                uberon_searchable_classes=16_071,
                cl_searchable_classes=1_484,
            ),
        ),
        activated_at=datetime(2026, 8, 12, tzinfo=UTC),
        class_counts=UberonClassCounts(
            uberon=16_362,
            cl=1_484,
            uberon_searchable=16_071,
            cl_searchable=1_484,
        ),
    )


class _Metadata:
    def __init__(
        self,
        ncit: NcitRepositoryReady | RepositoryUnhealthy,
        uberon: UberonRepositoryReady | RepositoryUnhealthy | None = None,
        cadsr: CadsrRepositoryReady | RepositoryUnhealthy | None = None,
        icdo: IcdoRepositoryReady | RepositoryUnhealthy | None = None,
    ) -> None:
        self._ncit = ncit
        self._uberon = uberon or _uberon_ready()
        self._cadsr = cadsr or _cadsr_ready()
        self._icdo = icdo
        self.icdo_calls = 0

    async def ncit(self) -> NcitRepositoryReady | RepositoryUnhealthy:
        return self._ncit

    def cadsr(self) -> CadsrRepositoryReady | RepositoryUnhealthy:
        return self._cadsr

    async def uberon(
        self, *, force: bool = False
    ) -> UberonRepositoryReady | RepositoryUnhealthy:
        del force
        return self._uberon

    async def icdo(
        self,
        edition: Literal["3.2", "4.0"],
        axis: Literal["morphology", "topography"],
    ) -> IcdoRepositoryReady | RepositoryUnhealthy:
        self.icdo_calls += 1
        return self._icdo or IcdoRepositoryReady(
            edition=edition,
            axis=axis,
            source_identity="3" * 64,
            serving_identity="4" * 64,
            activation_identity="5" * 64,
            row_count=1,
            activated_at=datetime(2026, 8, 12, tzinfo=UTC),
        )


@pytest.mark.api
def test_ready_reports_manifest_bound_active_ncit_identity() -> None:
    app = create_app()
    app.dependency_overrides[get_repository_metadata] = lambda: _Metadata(_ncit_ready())

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "ready": True,
        "repository": _ncit_ready().model_dump(mode="json"),
        "repositories": [
            _ncit_ready().model_dump(mode="json"),
            _cadsr_ready().model_dump(mode="json"),
            _uberon_ready().model_dump(mode="json"),
        ],
    }


@pytest.mark.api
def test_ready_returns_typed_503_without_claiming_an_active_identity() -> None:
    unhealthy = RepositoryUnhealthy(
        repository="ncit",
        reason="release-mismatch",
        message="default and stated releases differ",
    )
    app = create_app()
    app.dependency_overrides[get_repository_metadata] = lambda: _Metadata(unhealthy)

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    detail: dict[str, Any] = response.json()["detail"]
    assert detail == unhealthy.model_dump(mode="json")
    assert "source_identity" not in detail
    assert "manifest_identity" not in detail


@pytest.mark.api
def test_ready_refuses_when_uberon_release_is_unhealthy() -> None:
    unhealthy = RepositoryUnhealthy(
        repository="uberon",
        reason="release-mismatch",
        message="live and indexed Uberon releases differ",
    )
    app = create_app()
    app.dependency_overrides[get_repository_metadata] = lambda: _Metadata(
        _ncit_ready(), unhealthy
    )

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == unhealthy.model_dump(mode="json")


@pytest.mark.api
def test_ready_refuses_when_manifest_declared_cadsr_is_unhealthy() -> None:
    unhealthy = RepositoryUnhealthy(
        repository="cadsr",
        reason="repository-unreachable",
        message="caDSR unavailable",
    )
    app = create_app()
    app.dependency_overrides[get_repository_metadata] = lambda: _Metadata(
        _ncit_ready(), cadsr=unhealthy
    )
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["detail"] == unhealthy.model_dump(mode="json")


@pytest.mark.api
def test_ready_does_not_read_protected_icdo_metadata() -> None:
    unhealthy = RepositoryUnhealthy(
        repository="icdo",
        reason="observation-mismatch",
        message="ICD-O serving fingerprint drift",
    )
    app = create_app()
    metadata = _Metadata(_ncit_ready(), icdo=unhealthy)
    app.dependency_overrides[get_repository_metadata] = lambda: metadata
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    assert all(
        repository["repository"] != "icdo"
        for repository in response.json()["repositories"]
    )
    assert metadata.icdo_calls == 0


@pytest.mark.api
@pytest.mark.parametrize("entitlement", [None, "stale"])
def test_refresh_refuses_before_protected_metadata_read(
    monkeypatch: pytest.MonkeyPatch, entitlement: str | None
) -> None:
    monkeypatch.setattr(get_settings(), "icdo_entitlement_key", "licensed")
    metadata = _Metadata(_ncit_ready())
    app = create_app()
    app.dependency_overrides[get_repository_metadata] = lambda: metadata
    headers = {"X-ICDO-Entitlement": entitlement} if entitlement is not None else {}

    with TestClient(app) as client:
        response = client.post("/api/v1/refresh", headers=headers)

    assert response.status_code == 403
    assert metadata.icdo_calls == 0


@pytest.mark.api
def test_refresh_returns_discriminated_local_repository_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "icdo_entitlement_key", "licensed")
    app = create_app()
    app.dependency_overrides[get_repository_metadata] = lambda: _Metadata(_ncit_ready())

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/refresh", headers={"X-ICDO-Entitlement": "licensed"}
        )

    assert response.status_code == 200
    repositories = response.json()["repositories"]
    assert [(item["repository"], item["state"]) for item in repositories] == [
        ("ncit", "ready"),
        ("cadsr", "ready"),
        ("uberon", "ready"),
        ("icdo", "ready"),
        ("icdo", "ready"),
        ("icdo", "ready"),
    ]
    assert repositories[0]["source_identity"] == "a" * 64
    assert repositories[1]["manifest_identity"] == "d" * 64
    assert repositories[2]["source_sha256"] == "2" * 64
    assert [(row["edition"], row["axis"]) for row in repositories[3:]] == [
        ("3.2", "morphology"),
        ("4.0", "morphology"),
        ("4.0", "topography"),
    ]
