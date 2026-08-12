"""Manifest-bound repository readiness models and pure certification rules."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, PositiveInt

from ontolib.core.exceptions import StorageError
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
from ontolib.terminologies.sparql_http_client import SparqlHttpClient
from ontolib.terminologies.uberon.store import (
    UBERON_INDEX_MANIFEST_FILENAME,
    UberonArtifactError,
    UberonArtifactManifest,
    UberonIndexManifest,
    UberonIndexObservation,
    observe_uberon_index,
    validate_uberon_artifact,
    validate_uberon_index_manifest,
)

if TYPE_CHECKING:
    from ontolib.repositories.cadsr.archive import CadsrSource

RepositoryName = Literal["ncit", "cadsr", "uberon"]
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


class UberonRepositoryReady(_RepositoryModel):
    """Certified immutable Uberon/CL index identity without an activation claim."""

    state: Literal["ready"] = "ready"
    repository: Literal["uberon"] = "uberon"
    source_identity: str = Field(pattern=_SHA256_PATTERN)
    manifest_identity: str = Field(pattern=_SHA256_PATTERN)
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    version_iri: str
    class_counts: UberonClassCounts
    observation: UberonIndexObservation


class RepositoryUnhealthy(_RepositoryModel):
    """Typed refusal with no fields that could be mistaken for an active identity."""

    state: Literal["unhealthy"] = "unhealthy"
    repository: RepositoryName
    reason: RepositoryUnhealthyReason
    message: str


RepositoryMetadata = (
    NcitRepositoryReady
    | CadsrRepositoryReady
    | UberonRepositoryReady
    | RepositoryUnhealthy
)


class _MetadataSettings(Protocol):
    ncit_store_dir: str
    ncit_sparql_url: str
    uberon_store_dir: str
    uberon_sparql_url: str
    uberon_owl_url: str
    uberon_expected_version_iri: str
    uberon_expected_sha256: str


class _CadsrCertification(Protocol):
    def certification(self) -> tuple[CadsrSource, int, str]: ...


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
    observed: UberonIndexObservation | None = None,
) -> UberonRepositoryReady:
    """Bind one ready claim to immutable index, source, and class observations."""
    if manifest.observation.version_iri is None:
        raise RepositoryMetadataError(
            "release-mismatch", "Uberon index has no certified version IRI"
        )
    return UberonRepositoryReady(
        source_identity=manifest.source_identity,
        manifest_identity=manifest_identity,
        source_sha256=source_sha256,
        version_iri=manifest.observation.version_iri,
        class_counts=class_counts,
        observation=observed or manifest.observation,
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


def _load_uberon_manifest(active: Path) -> tuple[UberonIndexManifest, str]:
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
        manifest = validate_uberon_index_manifest(manifest_path)
    except (OSError, UberonArtifactError) as exc:
        raise RepositoryMetadataError("manifest-invalid", str(exc)) from exc
    return manifest, hashlib.sha256(payload).hexdigest()


async def observe_uberon_repository(
    endpoint_url: str,
) -> tuple[UberonIndexObservation, UberonClassCounts]:
    """Return the full live index proof and labelled source class counts."""
    try:
        observation = await observe_uberon_index(endpoint_url)
    except (UberonArtifactError, KeyError, TypeError, ValueError) as exc:
        raise RepositoryMetadataError(
            "observation-mismatch", "live Uberon index observation is malformed"
        ) from exc
    async with SparqlHttpClient.for_qlever(endpoint_url) as client:
        rows = await client.select_once(
            "PREFIX owl: <http://www.w3.org/2002/07/owl#> "
            "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> "
            "SELECT ?source (COUNT(DISTINCT ?concept) AS ?count) WHERE { "
            "?concept a owl:Class ; rdfs:label ?label . "
            "{ FILTER(STRSTARTS(STR(?concept), "
            "'http://purl.obolibrary.org/obo/UBERON_')) "
            "BIND('uberon' AS ?source) } UNION { "
            "FILTER(STRSTARTS(STR(?concept), 'http://purl.obolibrary.org/obo/CL_')) "
            "BIND('cl' AS ?source) } } GROUP BY ?source",
            required_variables={"source", "count"},
        )
    try:
        counts = {row["source"]: int(row["count"]) for row in rows}
        class_counts = UberonClassCounts.model_validate(counts)
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


def _unhealthy(
    repository: RepositoryName,
    reason: RepositoryUnhealthyReason,
    error: BaseException,
) -> RepositoryUnhealthy:
    return RepositoryUnhealthy(
        repository=repository,
        reason=reason,
        message=str(error),
    )


class RepositoryMetadataService:
    """Certify live proxy state without ever inferring an active identity."""

    def __init__(
        self,
        *,
        settings: _MetadataSettings,
        cadsr: _CadsrCertification,
    ) -> None:
        self._settings = settings
        self._cadsr = cadsr

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

    async def uberon(self) -> UberonRepositoryReady | RepositoryUnhealthy:
        """Return an immutable-manifest/live-observation-bound Uberon/CL identity."""
        active = _active_store_path(self._settings.uberon_store_dir)
        try:
            manifest, manifest_identity = _load_uberon_manifest(active)
            artifact = validate_uberon_artifact(Path(manifest.artifact_manifest_path))
            _require_configured_uberon_artifact(artifact, self._settings)
            observation, class_counts = await observe_uberon_repository(
                self._settings.uberon_sparql_url
            )
            if observation.version_iri != manifest.observation.version_iri:
                raise RepositoryMetadataError(
                    "release-mismatch",
                    "live Uberon version does not match the immutable index manifest",
                )
            if observation != manifest.observation:
                raise RepositoryMetadataError(
                    "observation-mismatch",
                    "live Uberon observation does not match the immutable "
                    "index manifest",
                )
            return bind_uberon_repository_metadata(
                manifest,
                manifest_identity=manifest_identity,
                source_sha256=artifact.sha256,
                class_counts=class_counts,
                observed=observation,
            )
        except RepositoryMetadataError as exc:
            return _unhealthy("uberon", exc.reason, exc)
        except UberonArtifactError as exc:
            return _unhealthy("uberon", "manifest-invalid", exc)
        except StorageError as exc:
            return _unhealthy("uberon", "repository-unreachable", exc)
