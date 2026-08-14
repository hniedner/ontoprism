"""Manifest-bound repository readiness models and pure certification rules."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    model_validator,
)
from sqlalchemy.exc import SQLAlchemyError

from backend.icdo_datasets import ServedIcdoDataset
from ontolib.core.exceptions import StorageError
from ontolib.repositories.icdo.store import (
    CertificationExpectation,
    IcdoCertificationError,
    IcdoManifest,
)
from ontolib.terminologies.ncit.activation import (
    ActivationJournal,
    ActivationJournalError,
    read_activation_journal,
)
from ontolib.terminologies.ncit.client import NCIT_NAMED_GRAPHS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.ncit.sibling_store import (
    CANDIDATE_MANIFEST_FILENAME,
    CandidateObservation,
    NcitSiblingStoreManifest,
    SiblingStoreValidationError,
    observation_without_graphs,
    observe_ncit_candidate,
    validate_ncit_sibling_manifest,
)
from ontolib.terminologies.uberon.store import (
    UBERON_INDEX_MANIFEST_FILENAME,
    CertifiedUberonIndexObservation,
    UberonArtifactError,
    UberonArtifactManifest,
    UberonIndexManifest,
    UberonIndexObservation,
    UberonServingFingerprint,
    observe_uberon_index,
    validate_uberon_index_proof,
)

if TYPE_CHECKING:
    from ontolib.repositories.cadsr.archive import CadsrSource

RepositoryName = Literal["ncit", "cadsr", "uberon", "icdo"]
RepositoryUnhealthyReason = Literal[
    "manifest-missing",
    "manifest-invalid",
    "activation-incomplete",
    "activation-mismatch",
    "release-mismatch",
    "observation-mismatch",
    "repository-unreachable",
]
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class RepositoryMetadataError(RuntimeError):
    """A repository cannot make a certified ready claim."""

    def __init__(self, reason: RepositoryUnhealthyReason, message: str) -> None:
        super().__init__(message)
        self.reason: RepositoryUnhealthyReason = reason


class _RepositoryModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class NcitRepositoryReady(_RepositoryModel):
    """Certified live NCIt identity and its complete graph observation."""

    state: Literal["ready"] = "ready"
    repository: Literal["ncit"] = "ncit"
    source_identity: str = Field(pattern=_SHA256_PATTERN)
    manifest_identity: str = Field(pattern=_SHA256_PATTERN)
    release: str
    activated_at: AwareDatetime
    observation: CandidateObservation


class CadsrSourceMetadata(_RepositoryModel):
    """Authoritative caDSR archive provenance persisted in the active SQLite DB."""

    url: str
    downloaded_at: str
    etag: str | None
    last_modified: str | None
    archive_size: int = Field(gt=0)
    archive_sha256: str = Field(pattern=_SHA256_PATTERN)
    member_count: int = Field(gt=0)
    member_names_sha256: str = Field(pattern=_SHA256_PATTERN)
    first_member_timestamp: str
    last_member_timestamp: str


class CadsrRepositoryReady(_RepositoryModel):
    """Certified active caDSR source and canonical serving-content identity."""

    state: Literal["ready"] = "ready"
    repository: Literal["cadsr"] = "cadsr"
    source_identity: str = Field(pattern=_SHA256_PATTERN)
    manifest_identity: str = Field(pattern=_SHA256_PATTERN)
    item_count: int = Field(gt=0)
    source: CadsrSourceMetadata


class UberonClassCounts(_RepositoryModel):
    uberon: PositiveInt
    cl: PositiveInt
    uberon_searchable: PositiveInt
    cl_searchable: PositiveInt


class UberonRepositoryReady(_RepositoryModel):
    """Certified installed Uberon/CL index and exact serving identity."""

    state: Literal["ready"] = "ready"
    repository: Literal["uberon"] = "uberon"
    source_identity: str = Field(pattern=_SHA256_PATTERN)
    manifest_identity: str = Field(pattern=_SHA256_PATTERN)
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    version_iri: str
    activated_at: AwareDatetime
    class_counts: UberonClassCounts
    observation: CertifiedUberonIndexObservation

    @model_validator(mode="after")
    def _consistent_observation(self) -> UberonRepositoryReady:
        serving = self.observation.serving
        expected_counts = UberonClassCounts(
            uberon=serving.uberon_classes,
            cl=serving.cl_classes,
            uberon_searchable=serving.uberon_searchable_classes,
            cl_searchable=serving.cl_searchable_classes,
        )
        if self.version_iri != self.observation.version_iri:
            raise ValueError("Uberon ready versions must agree")
        if self.class_counts != expected_counts:
            raise ValueError("Uberon ready class counts must match serving proof")
        return self


class IcdoRepositoryReady(_RepositoryModel):
    """Certified active ICD-O dataset and exact serving identity."""

    state: Literal["ready"] = "ready"
    repository: Literal["icdo"] = "icdo"
    edition: Literal["3.2", "4.0"]
    axis: Literal["morphology", "topography"]
    source_identity: str = Field(pattern=_SHA256_PATTERN)
    serving_identity: str = Field(pattern=_SHA256_PATTERN)
    activation_identity: str = Field(pattern=_SHA256_PATTERN)
    row_count: PositiveInt
    activated_at: AwareDatetime


class RepositoryUnhealthy[RepositoryNameT: RepositoryName](_RepositoryModel):
    """Typed refusal with no fields that could be mistaken for an active identity."""

    state: Literal["unhealthy"] = "unhealthy"
    repository: RepositoryNameT
    reason: RepositoryUnhealthyReason
    message: str


RepositoryMetadata = (
    NcitRepositoryReady
    | CadsrRepositoryReady
    | UberonRepositoryReady
    | IcdoRepositoryReady
    | RepositoryUnhealthy[Literal["ncit"]]
    | RepositoryUnhealthy[Literal["cadsr"]]
    | RepositoryUnhealthy[Literal["uberon"]]
    | RepositoryUnhealthy[Literal["icdo"]]
)


class _MetadataSettings(Protocol):
    ncit_store_dir: str
    ncit_sparql_url: str
    uberon_store_dir: str
    uberon_sparql_url: str
    uberon_owl_url: str
    uberon_expected_version_iri: str
    uberon_expected_sha256: str
    uberon_expected_serving_sha256: str
    uberon_expected_serving_rows: int
    uberon_expected_uberon_classes: int
    uberon_expected_cl_classes: int
    uberon_expected_uberon_searchable_classes: int
    uberon_expected_cl_searchable_classes: int
    icdo_32_morphology_source_sha256: str
    icdo_32_morphology_serving_sha256: str
    icdo_40_source_sha256: str
    icdo_40_morphology_serving_sha256: str
    icdo_40_topography_serving_sha256: str


class _CadsrCertification(Protocol):
    def certification(self) -> tuple[CadsrSource, int, str]: ...


class _IcdoCertification(Protocol):
    async def certified_metadata(
        self,
        edition: str,
        axis: str,
        expected: CertificationExpectation,
    ) -> IcdoManifest | None: ...


def _require_complete_activation(
    manifest: NcitSiblingStoreManifest,
    journal: ActivationJournal,
) -> AwareDatetime:
    if journal.phase != "complete" or journal.activated_at is None:
        raise RepositoryMetadataError(
            "activation-incomplete",
            "NCIt activation journal is not complete with an activation timestamp",
        )
    journal_identity = (
        journal.active_path,
        journal.candidate_owner,
        journal.candidate_source_identity,
    )
    manifest_identity = (
        manifest.candidate_path,
        manifest.owner,
        manifest.source_identity,
    )
    if journal_identity != manifest_identity:
        raise RepositoryMetadataError(
            "activation-mismatch",
            "NCIt active manifest does not match the completed activation journal",
        )
    return journal.activated_at


def _require_same_release(
    manifest: NcitSiblingStoreManifest,
    observed: CandidateObservation,
) -> None:
    versions = {
        observed.default_version,
        observed.stated_version,
        manifest.ontology_version,
    }
    if None in versions or len(versions) != 1:
        raise RepositoryMetadataError(
            "release-mismatch",
            "NCIt default, stated, and active-manifest releases differ",
        )


def _require_manifest_observation(
    manifest: NcitSiblingStoreManifest,
    observed: CandidateObservation,
) -> None:
    unexpected = {
        graph.graph_iri
        for graph in observed.named_graphs
        if graph.graph_iri not in NCIT_NAMED_GRAPHS
    }
    additive = set(NCIT_NAMED_GRAPHS) - {STATED_GRAPH_IRI}
    base = observation_without_graphs(observed, additive)
    if unexpected or base != manifest.observation:
        raise RepositoryMetadataError(
            "observation-mismatch",
            "live NCIt graph/count observation does not match the active manifest",
        )


def bind_ncit_repository_metadata(
    manifest: NcitSiblingStoreManifest,
    *,
    manifest_identity: str,
    journal: ActivationJournal,
    observed: CandidateObservation,
) -> NcitRepositoryReady:
    """Bind one ready claim to the active proof, activation, and live service."""
    activated_at = _require_complete_activation(manifest, journal)
    _require_same_release(manifest, observed)
    _require_manifest_observation(manifest, observed)
    return NcitRepositoryReady(
        source_identity=manifest.source_identity,
        manifest_identity=manifest_identity,
        release=manifest.ontology_version,
        activated_at=activated_at,
        observation=observed,
    )


def bind_cadsr_repository_metadata(
    source: CadsrSource,
    *,
    item_count: int,
    source_fingerprint: str,
) -> CadsrRepositoryReady:
    """Bind one caDSR ready claim to persisted archive and canonical row identity."""
    return CadsrRepositoryReady(
        source_identity=source.archive_sha256,
        manifest_identity=source_fingerprint,
        item_count=item_count,
        source=CadsrSourceMetadata.model_validate(asdict(source)),
    )


def bind_uberon_repository_metadata(
    manifest: UberonIndexManifest,
    *,
    manifest_identity: str,
    source_sha256: str,
    class_counts: UberonClassCounts,
) -> UberonRepositoryReady:
    """Bind one ready claim to immutable index, source, and class observations."""
    if manifest.observation.version_iri is None:
        raise RepositoryMetadataError(
            "release-mismatch", "Uberon index has no certified version IRI"
        )
    if manifest.observation.serving is None:
        raise RepositoryMetadataError(
            "observation-mismatch", "Uberon manifest has no serving-content proof"
        )
    source_identity = hashlib.sha256(
        json.dumps(
            {
                "index_source_identity": manifest.source_identity,
                "serving": manifest.observation.serving.model_dump(mode="json"),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return UberonRepositoryReady(
        source_identity=source_identity,
        manifest_identity=manifest_identity,
        source_sha256=source_sha256,
        version_iri=manifest.observation.version_iri,
        activated_at=manifest.installed_at,
        class_counts=class_counts,
        observation=manifest.observation,
    )


def _active_store_path(configured: str) -> Path:
    path = Path(configured)
    return path if path.is_absolute() else Path.cwd() / path


def _load_ncit_manifest(active: Path) -> tuple[NcitSiblingStoreManifest, str]:
    manifest_path = active / CANDIDATE_MANIFEST_FILENAME
    if not manifest_path.exists():
        raise RepositoryMetadataError(
            "manifest-missing", "NCIt active store has no candidate manifest"
        )
    if active.is_symlink() or not active.is_dir():
        raise RepositoryMetadataError(
            "manifest-invalid", "NCIt active store is not an exact directory"
        )
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RepositoryMetadataError(
            "manifest-invalid", "NCIt active manifest is not an exact regular file"
        )
    try:
        payload = manifest_path.read_bytes()
        manifest = validate_ncit_sibling_manifest(manifest_path)
    except (OSError, SiblingStoreValidationError) as exc:
        raise RepositoryMetadataError("manifest-invalid", str(exc)) from exc
    return manifest, hashlib.sha256(payload).hexdigest()


def _load_ncit_journal(active: Path) -> ActivationJournal:
    journal_path = active.parent / f".{active.name}.activation.json"
    try:
        return read_activation_journal(journal_path)
    except ActivationJournalError as exc:
        raise RepositoryMetadataError("activation-incomplete", str(exc)) from exc


def _load_uberon_manifest(
    active: Path,
) -> tuple[UberonIndexManifest, UberonArtifactManifest, str]:
    manifest_path = active / UBERON_INDEX_MANIFEST_FILENAME
    if not manifest_path.exists():
        raise RepositoryMetadataError(
            "manifest-missing", "Uberon active store has no index manifest"
        )
    if active.is_symlink() or not active.is_dir():
        raise RepositoryMetadataError(
            "manifest-invalid", "Uberon active store is not an exact directory"
        )
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RepositoryMetadataError(
            "manifest-invalid", "Uberon index manifest is not an exact regular file"
        )
    try:
        payload = manifest_path.read_bytes()
        manifest, artifact = validate_uberon_index_proof(manifest_path)
    except (OSError, UberonArtifactError) as exc:
        raise RepositoryMetadataError("manifest-invalid", str(exc)) from exc
    return manifest, artifact, hashlib.sha256(payload).hexdigest()


async def observe_uberon_repository(
    endpoint_url: str,
) -> tuple[UberonIndexObservation, UberonClassCounts]:
    """Return the live sentinel and serving-content observation."""
    try:
        observation = await observe_uberon_index(endpoint_url)
    except (UberonArtifactError, KeyError, TypeError, ValueError) as exc:
        raise RepositoryMetadataError(
            "observation-mismatch", "live Uberon index observation is malformed"
        ) from exc
    try:
        if observation.serving is None:
            raise ValueError("missing serving fingerprint")
        class_counts = UberonClassCounts(
            uberon=observation.serving.uberon_classes,
            cl=observation.serving.cl_classes,
            uberon_searchable=observation.serving.uberon_searchable_classes,
            cl_searchable=observation.serving.cl_searchable_classes,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RepositoryMetadataError(
            "observation-mismatch", "live Uberon/CL class observation is malformed"
        ) from exc
    return observation, class_counts


def _require_configured_uberon_artifact(
    artifact: UberonArtifactManifest, settings: _MetadataSettings
) -> None:
    configured = (
        settings.uberon_owl_url,
        settings.uberon_expected_version_iri,
        settings.uberon_expected_sha256,
    )
    observed = (artifact.source_url, artifact.version_iri, artifact.sha256)
    if observed != configured:
        raise RepositoryMetadataError(
            "release-mismatch",
            "Uberon artifact does not match the configured URL, version, and SHA-256",
        )


def _configured_uberon_serving(
    settings: _MetadataSettings,
) -> UberonServingFingerprint:
    return UberonServingFingerprint(
        rows=settings.uberon_expected_serving_rows,
        sha256=settings.uberon_expected_serving_sha256,
        uberon_classes=settings.uberon_expected_uberon_classes,
        cl_classes=settings.uberon_expected_cl_classes,
        uberon_searchable_classes=(settings.uberon_expected_uberon_searchable_classes),
        cl_searchable_classes=settings.uberon_expected_cl_searchable_classes,
    )


def _require_certified_uberon_observation(
    manifest: UberonIndexManifest,
    observation: UberonIndexObservation,
    settings: _MetadataSettings,
) -> None:
    if observation.version_iri != manifest.observation.version_iri:
        raise RepositoryMetadataError(
            "release-mismatch",
            "live Uberon version does not match the immutable index manifest",
        )
    if observation.model_dump() != manifest.observation.model_dump():
        raise RepositoryMetadataError(
            "observation-mismatch",
            "live Uberon observation does not match the immutable index manifest",
        )
    if observation.serving != _configured_uberon_serving(settings):
        raise RepositoryMetadataError(
            "observation-mismatch",
            "live Uberon serving content does not match the configured release",
        )


def _unhealthy[RepositoryNameT: RepositoryName](
    repository: RepositoryNameT,
    reason: RepositoryUnhealthyReason,
    error: BaseException,
) -> RepositoryUnhealthy[RepositoryNameT]:
    return RepositoryUnhealthy[RepositoryNameT](
        repository=repository,
        reason=reason,
        message=str(error),
    )


def _select_uberon_static_proof(
    active: Path,
    cached: tuple[UberonIndexManifest, UberonArtifactManifest, str] | None,
    *,
    force: bool,
) -> tuple[UberonIndexManifest, UberonArtifactManifest, str]:
    if force or cached is None:
        return _load_uberon_manifest(active)
    return cached


async def _select_uberon_live_observation(
    manifest: UberonIndexManifest,
    cached: tuple[str, UberonIndexObservation, UberonClassCounts] | None,
    endpoint_url: str,
    *,
    force: bool,
) -> tuple[str, UberonIndexObservation, UberonClassCounts]:
    del cached, force
    observation, class_counts = await observe_uberon_repository(endpoint_url)
    return manifest.source_identity, observation, class_counts


class RepositoryMetadataService:
    """Certify live proxy state without ever inferring an active identity."""

    def __init__(
        self,
        *,
        settings: _MetadataSettings,
        cadsr: _CadsrCertification,
        icdo: _IcdoCertification | None = None,
    ) -> None:
        self._settings = settings
        self._cadsr = cadsr
        self._icdo = icdo
        self._uberon_static_proof: (
            tuple[UberonIndexManifest, UberonArtifactManifest, str] | None
        ) = None
        self._uberon_live_observation: (
            tuple[str, UberonIndexObservation, UberonClassCounts] | None
        ) = None
        self._icdo_access: (
            tuple[IcdoRepositoryReady | RepositoryUnhealthy[Literal["icdo"]], ...]
            | None
        ) = None

    async def ncit(self) -> NcitRepositoryReady | RepositoryUnhealthy:
        """Return a manifest/journal/live-observation-bound NCIt identity."""
        active = _active_store_path(self._settings.ncit_store_dir)
        try:
            manifest, manifest_identity = _load_ncit_manifest(active)
            journal = _load_ncit_journal(active)
            observed = await observe_ncit_candidate(self._settings.ncit_sparql_url)
            return bind_ncit_repository_metadata(
                manifest,
                manifest_identity=manifest_identity,
                journal=journal,
                observed=observed,
            )
        except RepositoryMetadataError as exc:
            return _unhealthy("ncit", exc.reason, exc)
        except StorageError as exc:
            return _unhealthy("ncit", "repository-unreachable", exc)

    def cadsr(self) -> CadsrRepositoryReady | RepositoryUnhealthy:
        """Return a provenance/serving-content-bound caDSR identity."""
        try:
            source, item_count, fingerprint = self._cadsr.certification()
            return bind_cadsr_repository_metadata(
                source,
                item_count=item_count,
                source_fingerprint=fingerprint,
            )
        except sqlite3.OperationalError as exc:
            return _unhealthy("cadsr", "repository-unreachable", exc)
        except (OSError, ValueError) as exc:
            return _unhealthy("cadsr", "manifest-invalid", exc)

    async def uberon(
        self, *, force: bool = False
    ) -> UberonRepositoryReady | RepositoryUnhealthy[Literal["uberon"]]:
        """Return an immutable-manifest/live-observation-bound Uberon/CL identity."""
        active = _active_store_path(self._settings.uberon_store_dir)
        try:
            self._uberon_static_proof = _select_uberon_static_proof(
                active, self._uberon_static_proof, force=force
            )
            manifest, artifact, manifest_identity = self._uberon_static_proof
            _require_configured_uberon_artifact(artifact, self._settings)
            self._uberon_live_observation = await _select_uberon_live_observation(
                manifest,
                self._uberon_live_observation,
                self._settings.uberon_sparql_url,
                force=force,
            )
            _, observation, class_counts = self._uberon_live_observation
            _require_certified_uberon_observation(manifest, observation, self._settings)
            return bind_uberon_repository_metadata(
                manifest,
                manifest_identity=manifest_identity,
                source_sha256=artifact.sha256,
                class_counts=class_counts,
            )
        except RepositoryMetadataError as exc:
            return _unhealthy("uberon", exc.reason, exc)
        except StorageError as exc:
            return _unhealthy("uberon", "repository-unreachable", exc)

    async def icdo(
        self,
        edition: Literal["3.2", "4.0"],
        axis: Literal["morphology", "topography"],
    ) -> IcdoRepositoryReady | RepositoryUnhealthy[Literal["icdo"]]:
        """Return an active-row/manifest/configuration-bound ICD-O identity."""
        if self._icdo is None:
            return _unhealthy(
                "icdo",
                "repository-unreachable",
                RuntimeError("ICD-O repository is unavailable"),
            )
        try:
            manifest = await self._icdo.certified_metadata(
                edition, axis, icdo_expectation(self._settings, edition, axis)
            )
            if manifest is None:
                raise RepositoryMetadataError(
                    "repository-unreachable", "ICD-O dataset is unavailable"
                )
            return _bind_icdo_repository_metadata(manifest)
        except IcdoCertificationError as exc:
            return _unhealthy("icdo", "observation-mismatch", exc)
        except SQLAlchemyError as exc:
            return _unhealthy("icdo", "repository-unreachable", exc)
        except RepositoryMetadataError as exc:
            return _unhealthy("icdo", exc.reason, exc)
        except ValueError as exc:
            return _unhealthy("icdo", "manifest-invalid", exc)

    async def icdo_access(
        self, *, force: bool = False
    ) -> tuple[IcdoRepositoryReady | RepositoryUnhealthy[Literal["icdo"]], ...]:
        """Certify all served ICD-O datasets once for the process access marker."""
        if force or self._icdo_access is None:
            results: list[
                IcdoRepositoryReady | RepositoryUnhealthy[Literal["icdo"]]
            ] = []
            for dataset in ServedIcdoDataset:
                results.append(await self.icdo(dataset.edition, dataset.axis))
            self._icdo_access = tuple(results)
        return self._icdo_access


def icdo_expectation(
    settings: _MetadataSettings,
    edition: Literal["3.2", "4.0"],
    axis: Literal["morphology", "topography"],
) -> CertificationExpectation:
    configured = {
        ("3.2", "morphology"): (
            settings.icdo_32_morphology_source_sha256,
            settings.icdo_32_morphology_serving_sha256,
            1143,
        ),
        ("4.0", "morphology"): (
            settings.icdo_40_source_sha256,
            settings.icdo_40_morphology_serving_sha256,
            2390,
        ),
        ("4.0", "topography"): (
            settings.icdo_40_source_sha256,
            settings.icdo_40_topography_serving_sha256,
            406,
        ),
    }
    source, serving, count = configured[(edition, axis)]
    return CertificationExpectation(
        source_sha256=source,
        edition=edition,
        axis=axis,
        row_count=count,
        serving_sha256=serving,
    )


def _bind_icdo_repository_metadata(manifest: IcdoManifest) -> IcdoRepositoryReady:
    return IcdoRepositoryReady(
        edition=manifest.edition,
        axis=manifest.axis,
        source_identity=manifest.source_sha256,
        serving_identity=manifest.serving_sha256,
        activation_identity=manifest.generation_id,
        row_count=manifest.row_count,
        activated_at=manifest.published_at,
    )
