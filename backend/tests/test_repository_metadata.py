"""Manifest-bound repository metadata contracts."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.icdo_datasets import ServedIcdoDataset
from backend.repository_metadata import (
    CadsrRepositoryReady,
    IcdoAccessCertification,
    IcdoRepositoryReady,
    NcitRepositoryReady,
    RepositoryMetadataError,
    RepositoryMetadataService,
    RepositoryUnhealthy,
    UberonClassCounts,
    UberonRepositoryReady,
    bind_cadsr_repository_metadata,
    bind_ncit_repository_metadata,
    bind_uberon_repository_metadata,
    observe_uberon_repository,
)
from ontolib.core.exceptions import StorageError
from ontolib.repositories.cadsr.archive import CadsrSource
from ontolib.repositories.icdo.store import (
    CertificationExpectation,
    IcdoCertificationError,
    IcdoManifest,
)
from ontolib.terminologies.ncit.activation import ActivationJournal
from ontolib.terminologies.ncit.sibling_store import (
    QLEVER_IMAGE,
    QLEVER_INDEX_VERSION,
    CandidateGraph,
    CandidateObservation,
    NcitSiblingStoreManifest,
)
from ontolib.terminologies.uberon.store import (
    UBERON_INDEX_MANIFEST_FILENAME,
    CertifiedUberonIndexObservation,
    UberonArtifactManifest,
    UberonIndexManifest,
    UberonIndexObservation,
    UberonServingFingerprint,
)

pytestmark = pytest.mark.unit


def _uberon_observation(**changes: object) -> UberonIndexObservation:
    values: dict[str, object] = {
        "version_iri": (
            "http://purl.obolibrary.org/obo/uberon/releases/2026-06-19/uberon.owl"
        ),
        "triples": 900_000,
        "has_uberon_lung": True,
        "has_cell_class": True,
        "has_ncit_xref": True,
        "serving": UberonServingFingerprint(
            rows=100,
            sha256="f" * 64,
            uberon_classes=16_362,
            cl_classes=1_484,
            uberon_searchable_classes=16_071,
            cl_searchable_classes=1_484,
        ),
    }
    values.update(changes)
    return UberonIndexObservation.model_validate(values)


@dataclass
class _Settings:
    ncit_store_dir: str
    ncit_sparql_url: str
    uberon_store_dir: str = "/missing/uberon"
    uberon_sparql_url: str = "http://example.test:7889"
    uberon_owl_url: str = "https://example.test/uberon.owl"
    uberon_expected_version_iri: str = (
        "http://purl.obolibrary.org/obo/uberon/releases/2026-06-19/uberon.owl"
    )
    uberon_expected_sha256: str = "d" * 64
    uberon_expected_serving_sha256: str = "f" * 64
    uberon_expected_serving_rows: int = 100
    uberon_expected_uberon_classes: int = 16_362
    uberon_expected_cl_classes: int = 1_484
    uberon_expected_uberon_searchable_classes: int = 16_071
    uberon_expected_cl_searchable_classes: int = 1_484
    icdo_32_morphology_source_sha256: str = "1" * 64
    icdo_32_morphology_serving_sha256: str = "2" * 64
    icdo_40_source_sha256: str = "3" * 64
    icdo_40_morphology_serving_sha256: str = "4" * 64
    icdo_40_topography_serving_sha256: str = "5" * 64


def _uberon_counts() -> UberonClassCounts:
    return UberonClassCounts(
        uberon=16_362,
        cl=1_484,
        uberon_searchable=16_071,
        cl_searchable=1_484,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dataset", "count"),
    [
        (ServedIcdoDataset.ICDO_32_MORPHOLOGY, 1143),
        (ServedIcdoDataset.ICDO_40_MORPHOLOGY, 2390),
        (ServedIcdoDataset.ICDO_40_TOPOGRAPHY, 406),
    ],
)
async def test_icdo_readiness_is_bound_to_certified_active_dataset(
    dataset: ServedIcdoDataset, count: int
) -> None:
    settings = _Settings(
        ncit_store_dir="/missing", ncit_sparql_url="http://example.test"
    )

    class _Icdo:
        async def certified_metadata(
            self,
            edition: str,
            axis: str,
            expected: CertificationExpectation,
        ) -> IcdoManifest:
            assert (edition, axis) == dataset.value
            source = (
                settings.icdo_32_morphology_source_sha256
                if dataset.edition == "3.2"
                else settings.icdo_40_source_sha256
            )
            serving = getattr(
                settings,
                "icdo_"
                f"{dataset.edition.replace('.', '')}_{dataset.axis}_serving_sha256",
            )
            assert expected == CertificationExpectation(
                source_sha256=source,
                edition=dataset.edition,
                axis=dataset.axis,
                row_count=count,
                serving_sha256=serving,
            )
            return IcdoManifest(
                generation_id="a" * 64,
                edition=dataset.edition,
                axis=dataset.axis,
                publisher_url="https://example.test",
                source_sha256=source,
                archive_sha256=None,
                annex_sha256=None,
                reader_identity="reader",
                serving_sha256=serving,
                row_count=count,
                term_counts={},
                published_at=datetime.now(UTC),
            )

    result = await RepositoryMetadataService(
        settings=settings, cadsr=_CertifiedCadsr(), icdo=_Icdo()
    ).icdo(dataset)
    assert isinstance(result, IcdoRepositoryReady)
    assert (result.edition, result.axis, result.row_count) == (*dataset.value, count)


@pytest.mark.asyncio
async def test_icdo_readiness_returns_typed_drift_and_unavailable_refusals() -> None:
    settings = _Settings(
        ncit_store_dir="/missing", ncit_sparql_url="http://example.test"
    )

    class _Drift:
        async def certified_metadata(
            self, edition: str, axis: str, expected: object
        ) -> object:
            raise IcdoCertificationError("source_sha256 drift")

    drift = await RepositoryMetadataService(
        settings=settings, cadsr=_CertifiedCadsr(), icdo=_Drift()
    ).icdo(ServedIcdoDataset.ICDO_40_TOPOGRAPHY)
    unavailable = await RepositoryMetadataService(
        settings=settings, cadsr=_CertifiedCadsr()
    ).icdo(ServedIcdoDataset.ICDO_40_TOPOGRAPHY)
    assert isinstance(drift, RepositoryUnhealthy)
    assert drift.reason == "observation-mismatch"
    assert isinstance(unavailable, RepositoryUnhealthy)
    assert unavailable.reason == "repository-unreachable"


@pytest.mark.asyncio
async def test_icdo_access_recertifies_each_request() -> None:
    settings = _Settings(
        ncit_store_dir="/missing", ncit_sparql_url="http://example.test"
    )

    class _Icdo:
        calls = 0

        async def certified_metadata(
            self,
            edition: str,
            axis: str,
            expected: CertificationExpectation,
        ) -> IcdoManifest:
            del expected
            self.calls += 1
            source = (
                settings.icdo_32_morphology_source_sha256
                if edition == "3.2"
                else settings.icdo_40_source_sha256
            )
            return IcdoManifest(
                generation_id="a" * 64,
                edition="3.2" if edition == "3.2" else "4.0",
                axis="topography" if axis == "topography" else "morphology",
                publisher_url="https://example.test",
                source_sha256=source,
                archive_sha256=None,
                annex_sha256=None,
                reader_identity="reader",
                serving_sha256=getattr(
                    settings,
                    f"icdo_{edition.replace('.', '')}_{axis}_serving_sha256",
                ),
                row_count={
                    ("3.2", "morphology"): 1143,
                    ("4.0", "morphology"): 2390,
                    ("4.0", "topography"): 406,
                }[(edition, axis)],
                term_counts={},
                published_at=datetime.now(UTC),
            )

    repository = _Icdo()
    service = RepositoryMetadataService(
        settings=settings, cadsr=_CertifiedCadsr(), icdo=repository
    )

    first = await service.icdo_access()
    second = await service.icdo_access()
    third = await service.icdo_access(force=True)

    assert first is not second
    assert all(isinstance(result, IcdoRepositoryReady) for result in third.values())
    assert repository.calls == 9


def test_icdo_access_certification_rejects_misassigned_ready_dataset() -> None:
    topography = IcdoRepositoryReady(
        edition="4.0",
        axis="topography",
        source_identity="a" * 64,
        serving_identity="b" * 64,
        activation_identity="c" * 64,
        row_count=406,
        activated_at=datetime.now(UTC),
    )

    with pytest.raises(ValueError, match="dataset mismatch"):
        IcdoAccessCertification(
            morphology_32=topography,
            morphology_40=topography,
            topography_40=topography,
        )


def test_icdo_ready_metadata_rejects_an_unserved_dataset() -> None:
    with pytest.raises(ValueError, match="served dataset"):
        IcdoRepositoryReady(
            edition="3.2",
            axis="topography",
            source_identity="a" * 64,
            serving_identity="b" * 64,
            activation_identity="c" * 64,
            row_count=1,
            activated_at=datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_icdo_access_retries_after_an_unhealthy_snapshot() -> None:
    settings = _Settings(
        ncit_store_dir="/missing", ncit_sparql_url="http://example.test"
    )

    class _Icdo:
        calls = 0

        async def certified_metadata(
            self, edition: str, axis: str, expected: CertificationExpectation
        ) -> IcdoManifest:
            del expected
            self.calls += 1
            if self.calls == 1:
                raise IcdoCertificationError("transient observation failure")
            return IcdoManifest(
                generation_id="a" * 64,
                edition="3.2" if edition == "3.2" else "4.0",
                axis="topography" if axis == "topography" else "morphology",
                publisher_url="https://example.test",
                source_sha256=(
                    settings.icdo_32_morphology_source_sha256
                    if edition == "3.2"
                    else settings.icdo_40_source_sha256
                ),
                archive_sha256=None,
                annex_sha256=None,
                reader_identity="reader",
                serving_sha256=getattr(
                    settings,
                    f"icdo_{edition.replace('.', '')}_{axis}_serving_sha256",
                ),
                row_count={
                    ("3.2", "morphology"): 1143,
                    ("4.0", "morphology"): 2390,
                    ("4.0", "topography"): 406,
                }[(edition, axis)],
                term_counts={},
                published_at=datetime.now(UTC),
            )

    repository = _Icdo()
    service = RepositoryMetadataService(
        settings=settings, cadsr=_CertifiedCadsr(), icdo=repository
    )

    first = await service.icdo_access()
    second = await service.icdo_access()

    assert isinstance(first.morphology_32, RepositoryUnhealthy)
    assert all(isinstance(result, IcdoRepositoryReady) for result in second.values())
    assert repository.calls == 6


def _certified_uberon_observation(
    **changes: object,
) -> CertifiedUberonIndexObservation:
    return CertifiedUberonIndexObservation.model_validate(
        _uberon_observation(**changes).model_dump()
    )


def _observation(**changes: object) -> CandidateObservation:
    values: dict[str, object] = {
        "default_triples": 12_980_813,
        "stated_triples": 10_855_010,
        "named_graphs": (
            CandidateGraph(
                graph_iri=("http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-stated.owl"),
                triples=10_855_010,
            ),
        ),
        "default_version": "26.07d",
        "stated_version": "26.07d",
        "restriction_count": 150_000,
        "has_required_restriction": True,
        "default_has_stated_only_sentinel": False,
        "stated_has_stated_only_sentinel": True,
    }
    values.update(changes)
    return CandidateObservation.model_validate(values)


def _manifest(observation: CandidateObservation) -> NcitSiblingStoreManifest:
    return NcitSiblingStoreManifest.model_construct(
        owner="a" * 32,
        candidate_path="/data/qlever-ncit",
        active_store_path="/data/qlever-ncit",
        pair_manifest_identity="b" * 64,
        ontology_version="26.07d",
        source_identity="c" * 64,
        observation=observation,
    )


def _journal(**changes: object) -> ActivationJournal:
    values: dict[str, object] = {
        "phase": "complete",
        "active_path": "/data/qlever-ncit",
        "candidate_path": "/data/.qlever-ncit.candidate-" + "a" * 32,
        "rollback_path": "/data/.qlever-ncit.rollback-" + "a" * 32,
        "candidate_manifest_path": (
            "/data/.qlever-ncit.candidate-" + "a" * 32 + "/manifest.json"
        ),
        "candidate_manifest_sha256": "d" * 64,
        "candidate_owner": "a" * 32,
        "active_owner": "e" * 32,
        "candidate_source_identity": "c" * 64,
        "active_source_identity": "f" * 64,
        "store_format_identity": "1" * 64,
        "qlever_image": QLEVER_IMAGE,
        "qlever_image_id": "sha256:" + QLEVER_IMAGE.rsplit("@sha256:", 1)[1],
        "qlever_index_version": QLEVER_INDEX_VERSION,
        "qlever_index_basename": "ncit",
        "activated_at": datetime(2026, 8, 10, 18, 30, tzinfo=UTC),
    }
    values.update(changes)
    return ActivationJournal.model_validate(values)


def test_ready_ncit_metadata_is_bound_to_manifest_journal_and_observation() -> None:
    observation = _observation()

    metadata = bind_ncit_repository_metadata(
        _manifest(observation),
        manifest_identity="2" * 64,
        journal=_journal(),
        observed=observation,
    )

    assert isinstance(metadata, NcitRepositoryReady)
    assert metadata.state == "ready"
    assert metadata.repository == "ncit"
    assert metadata.source_identity == "c" * 64
    assert metadata.manifest_identity == "2" * 64
    assert metadata.release == "26.07d"
    assert metadata.observation == observation
    assert metadata.activated_at == datetime(2026, 8, 10, 18, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("journal", "observed", "reason"),
    [
        (
            _journal(phase="preflight", activated_at=None),
            _observation(),
            "activation-incomplete",
        ),
        (
            _journal(candidate_source_identity="9" * 64),
            _observation(),
            "activation-mismatch",
        ),
        (_journal(), _observation(stated_version="26.08a"), "release-mismatch"),
        (_journal(), _observation(default_triples=12_980_812), "observation-mismatch"),
    ],
)
def test_ncit_metadata_rejects_unbound_or_release_skewed_state(
    journal: ActivationJournal,
    observed: CandidateObservation,
    reason: str,
) -> None:
    with pytest.raises(RepositoryMetadataError) as captured:
        bind_ncit_repository_metadata(
            _manifest(_observation()),
            manifest_identity="2" * 64,
            journal=journal,
            observed=observed,
        )

    assert captured.value.reason == reason


def test_cadsr_ready_and_unhealthy_variants_do_not_share_identity_shape() -> None:
    source = CadsrSource(
        url="https://example.test/released.zip",
        downloaded_at="2026-08-10T18:00:00+00:00",
        etag='"release"',
        last_modified="Sun, 09 Aug 2026 00:00:00 GMT",
        archive_size=123,
        archive_sha256="3" * 64,
        member_count=14,
        member_names_sha256="4" * 64,
        first_member_timestamp="2026-08-09T00:00:00",
        last_member_timestamp="2026-08-09T01:00:00",
    )

    ready = bind_cadsr_repository_metadata(
        source,
        item_count=79_835,
        source_fingerprint="5" * 64,
    )
    unhealthy = RepositoryUnhealthy(
        repository="cadsr",
        reason="repository-unreachable",
        message="database locked",
    )

    assert isinstance(ready, CadsrRepositoryReady)
    assert ready.source_identity == "3" * 64
    assert ready.manifest_identity == "5" * 64
    assert ready.source.member_count == 14
    assert "source_identity" not in unhealthy.model_dump()
    assert "manifest_identity" not in unhealthy.model_dump()


def test_uberon_ready_metadata_has_no_fabricated_activation_timestamp() -> None:
    manifest = UberonIndexManifest.model_construct(
        owner="a" * 32,
        target_path="/data/qlever-uberon",
        artifact_manifest_path="/data/uberon/uberon-artifact.json",
        artifact_identity="b" * 64,
        source_identity="c" * 64,
        loader={},
        installed_at=datetime(2026, 8, 12, tzinfo=UTC),
        observation=_certified_uberon_observation(),
    )

    ready = bind_uberon_repository_metadata(
        manifest,
        manifest_identity="d" * 64,
        source_sha256="e" * 64,
        class_counts=_uberon_counts(),
    )

    assert isinstance(ready, UberonRepositoryReady)
    assert ready.repository == "uberon"
    assert ready.source_identity != manifest.source_identity
    assert len(ready.source_identity) == 64
    assert ready.version_iri.endswith("/2026-06-19/uberon.owl")
    assert ready.class_counts == _uberon_counts()
    assert ready.activated_at == datetime(2026, 8, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"version_iri": "wrong-version"}, "versions must agree"),
        (
            {
                "class_counts": UberonClassCounts(
                    uberon=16_363,
                    cl=1_484,
                    uberon_searchable=16_071,
                    cl_searchable=1_484,
                )
            },
            "class counts must match",
        ),
    ],
)
def test_uberon_ready_metadata_rejects_internal_identity_mismatch(
    changes: dict[str, object], message: str
) -> None:
    observation = _certified_uberon_observation()
    values: dict[str, object] = {
        "source_identity": "a" * 64,
        "manifest_identity": "b" * 64,
        "source_sha256": "c" * 64,
        "version_iri": observation.version_iri,
        "activated_at": datetime(2026, 8, 12, tzinfo=UTC),
        "class_counts": _uberon_counts(),
        "observation": observation,
    }
    values.update(changes)

    with pytest.raises(ValueError, match=message):
        UberonRepositoryReady.model_validate(values)


def test_uberon_class_counts_require_both_positive_sources() -> None:
    with pytest.raises(ValueError, match="cl"):
        UberonClassCounts.model_validate({"uberon": 16_071})
    with pytest.raises(ValueError, match="greater than 0"):
        UberonClassCounts(
            uberon=16_362,
            cl=0,
            uberon_searchable=16_071,
            cl_searchable=1_484,
        )


def test_uberon_ready_metadata_requires_serving_content_proof() -> None:
    manifest = UberonIndexManifest.model_construct(
        source_identity="c" * 64,
        observation=_uberon_observation(serving=None),
    )

    with pytest.raises(RepositoryMetadataError) as captured:
        bind_uberon_repository_metadata(
            manifest,
            manifest_identity="d" * 64,
            source_sha256="e" * 64,
            class_counts=_uberon_counts(),
        )

    assert captured.value.reason == "observation-mismatch"


@pytest.mark.asyncio
async def test_live_uberon_observation_requires_serving_content_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _observe(_url: str) -> UberonIndexObservation:
        return _uberon_observation(serving=None)

    monkeypatch.setattr("backend.repository_metadata.observe_uberon_index", _observe)

    with pytest.raises(RepositoryMetadataError) as captured:
        await observe_uberon_repository("http://uberon.test")

    assert captured.value.reason == "observation-mismatch"


class _CertifiedCadsr:
    def certification(self) -> tuple[CadsrSource, int, str]:
        return (
            CadsrSource(
                url="https://example.test/released.zip",
                downloaded_at="2026-08-10T18:00:00+00:00",
                etag=None,
                last_modified=None,
                archive_size=123,
                archive_sha256="3" * 64,
                member_count=14,
                member_names_sha256="4" * 64,
                first_member_timestamp="2026-08-09T00:00:00",
                last_member_timestamp="2026-08-09T01:00:00",
            ),
            79_835,
            "5" * 64,
        )


@pytest.mark.asyncio
async def test_service_certifies_exact_active_ncit_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = tmp_path / "qlever-ncit"
    active.mkdir()
    manifest_path = active / ".ontoprism-ncit-candidate.json"
    manifest_path.write_bytes(b'{"exact":"manifest"}\n')
    observation = _observation()
    manifest = _manifest(observation).model_copy(
        update={"candidate_path": str(active), "active_store_path": str(active)}
    )
    journal = _journal().model_copy(update={"active_path": str(active)})
    monkeypatch.setattr(
        "backend.repository_metadata.validate_ncit_sibling_manifest",
        lambda path: manifest,
    )
    monkeypatch.setattr(
        "backend.repository_metadata.read_activation_journal", lambda path: journal
    )

    async def _observe(_url: str) -> CandidateObservation:
        return observation

    monkeypatch.setattr("backend.repository_metadata.observe_ncit_candidate", _observe)
    settings = _Settings(
        ncit_store_dir=str(active), ncit_sparql_url="http://example.test:7888"
    )
    service = RepositoryMetadataService(
        settings=settings,
        cadsr=_CertifiedCadsr(),
    )

    result = await service.ncit()

    assert isinstance(result, NcitRepositoryReady)
    assert (
        result.manifest_identity
        == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    )
    assert service.cadsr().state == "ready"


@pytest.mark.asyncio
async def test_service_refuses_identity_when_active_manifest_is_missing(
    tmp_path: Path,
) -> None:
    settings = _Settings(
        ncit_store_dir=str(tmp_path / "missing"),
        ncit_sparql_url="http://example.test:7888",
    )
    service = RepositoryMetadataService(
        settings=settings,
        cadsr=_CertifiedCadsr(),
    )

    result = await service.ncit()

    assert isinstance(result, RepositoryUnhealthy)
    assert result.reason == "manifest-missing"
    assert "source_identity" not in result.model_dump()


@pytest.mark.asyncio
async def test_service_binds_uberon_identity_to_manifest_artifact_and_live_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "qlever-uberon"
    store.mkdir()
    manifest_path = store / UBERON_INDEX_MANIFEST_FILENAME
    manifest_path.write_bytes(b'{"exact":"uberon-index"}\n')
    artifact_path = tmp_path / "uberon-artifact.json"
    artifact_path.write_text("{}")
    manifest = UberonIndexManifest.model_construct(
        owner="a" * 32,
        target_path=str(store),
        artifact_manifest_path=str(artifact_path),
        artifact_identity="b" * 64,
        source_identity="c" * 64,
        loader={},
        installed_at=datetime(2026, 8, 12, tzinfo=UTC),
        observation=_certified_uberon_observation(),
    )
    artifact = UberonArtifactManifest.model_construct(
        source_url="https://example.test/uberon.owl",
        version_iri=manifest.observation.version_iri,
        sha256="d" * 64,
    )
    proof_loads = 0

    def _validate_proof(_path: Path):
        nonlocal proof_loads
        proof_loads += 1
        return manifest, artifact

    monkeypatch.setattr(
        "backend.repository_metadata.validate_uberon_index_proof", _validate_proof
    )

    async def _observe(
        _url: str,
    ) -> tuple[UberonIndexObservation, UberonClassCounts]:
        return (
            UberonIndexObservation.model_validate(manifest.observation.model_dump()),
            _uberon_counts(),
        )

    monkeypatch.setattr(
        "backend.repository_metadata.observe_uberon_repository", _observe
    )
    settings = _Settings(
        ncit_store_dir=str(tmp_path / "ncit"), ncit_sparql_url="http://ncit.test"
    )
    settings.uberon_store_dir = str(store)
    settings.uberon_sparql_url = "http://uberon.test"
    service = RepositoryMetadataService(settings=settings, cadsr=_CertifiedCadsr())

    result = await service.uberon()
    repeated = await service.uberon()

    assert isinstance(result, UberonRepositoryReady)
    assert isinstance(repeated, UberonRepositoryReady)
    assert proof_loads == 1
    assert (
        result.manifest_identity
        == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    )
    assert result.source_sha256 == "d" * 64
    assert result.class_counts == _uberon_counts()

    forced = await service.uberon(force=True)
    assert isinstance(forced, UberonRepositoryReady)
    assert proof_loads == 2


@pytest.mark.asyncio
async def test_normal_uberon_read_revalidates_changed_live_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "qlever-uberon"
    store.mkdir()
    (store / UBERON_INDEX_MANIFEST_FILENAME).write_text("{}")
    settings = _Settings(
        ncit_store_dir=str(tmp_path / "ncit"),
        ncit_sparql_url="http://ncit.test",
        uberon_store_dir=str(store),
        uberon_sparql_url="http://uberon.test",
    )
    expected = _certified_uberon_observation(
        version_iri=settings.uberon_expected_version_iri
    )
    manifest = _uberon_manifest_for_test(
        tmp_path / "artifact.json", expected
    ).model_copy(update={"installed_at": datetime(2026, 8, 12, tzinfo=UTC)})
    monkeypatch.setattr(
        "backend.repository_metadata.validate_uberon_index_proof",
        lambda path: (manifest, _artifact_for_settings(settings)),
    )
    calls = 0

    async def _observe(_url: str) -> tuple[UberonIndexObservation, UberonClassCounts]:
        nonlocal calls
        calls += 1
        observed = (
            expected if calls == 1 else expected.model_copy(update={"triples": 1})
        )
        return observed, _uberon_counts()

    monkeypatch.setattr(
        "backend.repository_metadata.observe_uberon_repository", _observe
    )
    service = RepositoryMetadataService(settings=settings, cadsr=_CertifiedCadsr())
    assert isinstance(await service.uberon(), UberonRepositoryReady)
    changed = await service.uberon()
    assert isinstance(changed, RepositoryUnhealthy)
    assert changed.reason == "observation-mismatch"


@pytest.mark.asyncio
async def test_service_refuses_uberon_release_skew_without_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "qlever-uberon"
    store.mkdir()
    (store / UBERON_INDEX_MANIFEST_FILENAME).write_text("{}")
    manifest = UberonIndexManifest.model_construct(
        source_identity="c" * 64,
        observation=_certified_uberon_observation(version_iri="expected"),
        artifact_manifest_path=str(tmp_path / "artifact.json"),
    )
    monkeypatch.setattr(
        "backend.repository_metadata.validate_uberon_index_proof",
        lambda path: (
            manifest,
            UberonArtifactManifest.model_construct(
                source_url="https://example.test/uberon.owl",
                version_iri="expected",
                sha256="d" * 64,
            ),
        ),
    )

    async def _observe(
        _url: str,
    ) -> tuple[UberonIndexObservation, UberonClassCounts]:
        return (
            manifest.observation.model_copy(update={"version_iri": "different"}),
            _uberon_counts(),
        )

    monkeypatch.setattr(
        "backend.repository_metadata.observe_uberon_repository", _observe
    )
    settings = _Settings(
        ncit_store_dir=str(tmp_path / "ncit"), ncit_sparql_url="http://ncit.test"
    )
    settings.uberon_store_dir = str(store)
    settings.uberon_sparql_url = "http://uberon.test"
    settings.uberon_expected_version_iri = "expected"

    result = await RepositoryMetadataService(
        settings=settings, cadsr=_CertifiedCadsr()
    ).uberon()

    assert isinstance(result, RepositoryUnhealthy)
    assert result.reason == "release-mismatch"
    assert "source_identity" not in result.model_dump()


def _artifact_for_settings(settings: _Settings) -> UberonArtifactManifest:
    return UberonArtifactManifest.model_construct(
        source_url=settings.uberon_owl_url,
        version_iri=settings.uberon_expected_version_iri,
        sha256=settings.uberon_expected_sha256,
    )


def _uberon_manifest_for_test(
    artifact_path: Path, observation: UberonIndexObservation
) -> UberonIndexManifest:
    return UberonIndexManifest.model_construct(
        source_identity="c" * 64,
        artifact_manifest_path=str(artifact_path),
        observation=observation,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("source_url", "https://example.test/wrong.owl"),
        ("version_iri", "wrong-version"),
        ("sha256", "0" * 64),
    ],
)
async def test_service_refuses_artifact_outside_configured_pins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    wrong_value: str,
) -> None:
    store = tmp_path / "qlever-uberon"
    store.mkdir()
    (store / UBERON_INDEX_MANIFEST_FILENAME).write_text("{}")
    settings = _Settings(
        ncit_store_dir=str(tmp_path / "ncit"),
        ncit_sparql_url="http://ncit.test",
        uberon_store_dir=str(store),
        uberon_sparql_url="http://uberon.test",
    )
    observation = _certified_uberon_observation(
        version_iri=settings.uberon_expected_version_iri
    )
    artifact = _artifact_for_settings(settings).model_copy(update={field: wrong_value})
    manifest = _uberon_manifest_for_test(tmp_path / "artifact.json", observation)
    monkeypatch.setattr(
        "backend.repository_metadata.validate_uberon_index_proof",
        lambda path: (manifest, artifact),
    )

    result = await RepositoryMetadataService(
        settings=settings, cadsr=_CertifiedCadsr()
    ).uberon()

    assert isinstance(result, RepositoryUnhealthy)
    assert result.reason == "release-mismatch"


@pytest.mark.asyncio
async def test_service_refuses_same_version_live_observation_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "qlever-uberon"
    store.mkdir()
    (store / UBERON_INDEX_MANIFEST_FILENAME).write_text("{}")
    settings = _Settings(
        ncit_store_dir=str(tmp_path / "ncit"),
        ncit_sparql_url="http://ncit.test",
        uberon_store_dir=str(store),
        uberon_sparql_url="http://uberon.test",
    )
    expected = _certified_uberon_observation(
        version_iri=settings.uberon_expected_version_iri
    )
    manifest = _uberon_manifest_for_test(tmp_path / "artifact.json", expected)
    monkeypatch.setattr(
        "backend.repository_metadata.validate_uberon_index_proof",
        lambda path: (manifest, _artifact_for_settings(settings)),
    )

    async def _observe(
        _url: str,
    ) -> tuple[UberonIndexObservation, UberonClassCounts]:
        return (
            expected.model_copy(update={"triples": expected.triples - 1}),
            _uberon_counts(),
        )

    monkeypatch.setattr(
        "backend.repository_metadata.observe_uberon_repository", _observe
    )

    result = await RepositoryMetadataService(
        settings=settings, cadsr=_CertifiedCadsr()
    ).uberon()

    assert isinstance(result, RepositoryUnhealthy)
    assert result.reason == "observation-mismatch"


@pytest.mark.asyncio
async def test_service_reports_unreachable_uberon_without_ready_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "qlever-uberon"
    store.mkdir()
    (store / UBERON_INDEX_MANIFEST_FILENAME).write_text("{}")
    settings = _Settings(
        ncit_store_dir=str(tmp_path / "ncit"),
        ncit_sparql_url="http://ncit.test",
        uberon_store_dir=str(store),
        uberon_sparql_url="http://uberon.test",
    )
    observation = _certified_uberon_observation(
        version_iri=settings.uberon_expected_version_iri
    )
    manifest = _uberon_manifest_for_test(tmp_path / "artifact.json", observation)
    monkeypatch.setattr(
        "backend.repository_metadata.validate_uberon_index_proof",
        lambda path: (manifest, _artifact_for_settings(settings)),
    )

    async def _unreachable(
        _url: str,
    ) -> tuple[UberonIndexObservation, UberonClassCounts]:
        raise StorageError("Uberon endpoint unavailable")

    monkeypatch.setattr(
        "backend.repository_metadata.observe_uberon_repository", _unreachable
    )

    result = await RepositoryMetadataService(
        settings=settings, cadsr=_CertifiedCadsr()
    ).uberon()

    assert isinstance(result, RepositoryUnhealthy)
    assert result.reason == "repository-unreachable"
    assert "source_identity" not in result.model_dump()


@pytest.mark.asyncio
async def test_service_refuses_serving_content_outside_configured_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "qlever-uberon"
    store.mkdir()
    (store / UBERON_INDEX_MANIFEST_FILENAME).write_text("{}")
    settings = _Settings(
        ncit_store_dir=str(tmp_path / "ncit"),
        ncit_sparql_url="http://ncit.test",
        uberon_store_dir=str(store),
        uberon_sparql_url="http://uberon.test",
    )
    expected = _certified_uberon_observation(
        version_iri=settings.uberon_expected_version_iri
    )
    manifest = _uberon_manifest_for_test(tmp_path / "artifact.json", expected)
    monkeypatch.setattr(
        "backend.repository_metadata.validate_uberon_index_proof",
        lambda path: (manifest, _artifact_for_settings(settings)),
    )

    async def _observe(
        _url: str,
    ) -> tuple[UberonIndexObservation, UberonClassCounts]:
        return expected, _uberon_counts()

    monkeypatch.setattr(
        "backend.repository_metadata.observe_uberon_repository", _observe
    )
    settings.uberon_expected_serving_rows += 1

    result = await RepositoryMetadataService(
        settings=settings, cadsr=_CertifiedCadsr()
    ).uberon()

    assert isinstance(result, RepositoryUnhealthy)
    assert result.reason == "observation-mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("symlink", "message"),
    [
        ("store", "active store is not an exact directory"),
        ("manifest", "active manifest is not an exact regular file"),
    ],
)
async def test_service_refuses_identity_from_symlinked_ncit_proof_paths(
    tmp_path: Path,
    symlink: str,
    message: str,
) -> None:
    active = tmp_path / "qlever-ncit"
    if symlink == "store":
        target = tmp_path / "store-target"
        target.mkdir()
        (target / ".ontoprism-ncit-candidate.json").write_text("{}")
        active.symlink_to(target, target_is_directory=True)
    else:
        active.mkdir()
        target = tmp_path / "manifest-target.json"
        target.write_text("{}")
        (active / ".ontoprism-ncit-candidate.json").symlink_to(target)
    service = RepositoryMetadataService(
        settings=_Settings(
            ncit_store_dir=str(active),
            ncit_sparql_url="http://example.test:7888",
        ),
        cadsr=_CertifiedCadsr(),
    )

    result = await service.ncit()

    assert isinstance(result, RepositoryUnhealthy)
    assert result.reason == "manifest-invalid"
    assert message in result.message
    assert "source_identity" not in result.model_dump()
