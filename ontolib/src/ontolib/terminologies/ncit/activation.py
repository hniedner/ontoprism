"""Crash-safe activation of a certified NCIt QLever sibling index."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)

from ontolib.decomposition.complete_definition import read_complete_definition
from ontolib.decomposition.publication import (
    PublicationGraphClient,
    PublicationMarker,
    build_replacement_update,
    read_publication_marker,
    staging_graph_iri,
)
from ontolib.terminologies.ncit.client import NCIT_NAMED_GRAPHS, ncit_sparql_client
from ontolib.terminologies.ncit.graph_store import NcitGraphStore
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.ncit.sibling_store import (
    CANDIDATE_MANIFEST_FILENAME,
    OWNER_MARKER_FILENAME,
    QLEVER_IMAGE,
    QLEVER_INDEX_VERSION,
    CandidateObservation,
    CandidateValidationPolicy,
    NcitSiblingStoreManifest,
    observe_ncit_candidate,
    run_docker,
    validate_ncit_sibling_manifest,
)

if TYPE_CHECKING:
    from ontolib.decomposition.provenance_models import RunSummary

ActivationPhase = Literal[
    "preflight",
    "publication-paused",
    "service-stopped",
    "rollback-staged",
    "candidate-activated",
    "service-restarted",
    "health-validated",
    "rollback-cleaned",
    "publication-resumed",
    "complete",
    "rolled-back",
]

_FORWARD_TRANSITIONS: dict[ActivationPhase, ActivationPhase] = {
    "preflight": "publication-paused",
    "publication-paused": "service-stopped",
    "service-stopped": "rollback-staged",
    "rollback-staged": "candidate-activated",
    "candidate-activated": "service-restarted",
    "service-restarted": "health-validated",
    "health-validated": "rollback-cleaned",
    "rollback-cleaned": "publication-resumed",
    "publication-resumed": "complete",
}

# ``activated_at`` is stamped at the ``health-validated`` transition, so it must be set
# from that phase onward and unset before it. ``rolled-back`` is deliberately in neither
# set: a rollback can occur on either side of the health-validation boundary.
_ACTIVATED_PHASES: frozenset[ActivationPhase] = frozenset(
    {"health-validated", "rollback-cleaned", "publication-resumed", "complete"}
)
_PRE_ACTIVATION_PHASES: frozenset[ActivationPhase] = frozenset(
    {
        "preflight",
        "publication-paused",
        "service-stopped",
        "rollback-staged",
        "candidate-activated",
        "service-restarted",
    }
)

QLEVER_REQUIRED_STORE_FILES = frozenset(
    {
        "ncit.index.ops",
        "ncit.index.ops.meta",
        "ncit.index.osp",
        "ncit.index.osp.meta",
        "ncit.index.patterns",
        "ncit.index.pos",
        "ncit.index.pos.meta",
        "ncit.index.pso",
        "ncit.index.pso.meta",
        "ncit.index.sop",
        "ncit.index.sop.meta",
        "ncit.index.spo",
        "ncit.index.spo.meta",
        "ncit.internal.index.pos",
        "ncit.internal.index.pos.meta",
        "ncit.internal.index.pso",
        "ncit.internal.index.pso.meta",
        "ncit.meta-data.json",
        "ncit.vocabulary.codebooks",
        "ncit.vocabulary.words.external",
        "ncit.vocabulary.words.external.offsets",
        "ncit.vocabulary.words.internal",
        "ncit.vocabulary.words.internal.ids",
    }
)
_QLEVER_OPTIONAL_STORE_FILES = {
    "ncit.index.resource-usage-log.tsv",
    "ncit.metrics-log.jsonl",
    "ncit.server.resource-usage-log.tsv",
    "ncit.update-triples",
}
_OWNER = re.compile(r"[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
_NCIT_CODE = re.compile(r"C[0-9]+")
_STORE_PATH_COUNT = 3
EXPECTED_C27262_DEFINITION_IDENTITY = (
    "9ce79377f03d6f15130d065567509a435ccb2793920c19c8846292cbf8685b5c"
)


class ActivationJournalError(RuntimeError):
    """The persisted activation journal is missing or invalid."""


class ActivationTransitionError(ActivationJournalError):
    """The requested phase does not follow the persisted activation state."""


class ActivationPreflightError(RuntimeError):
    """The exact candidate/active QLever paths are unsafe to activate."""


class ActivationServiceError(RuntimeError):
    """The exact QLever Compose service could not be safely controlled."""


class ActivationHealthError(RuntimeError):
    """The restarted service does not expose the certified candidate behavior."""


class ActivationRolledBackError(RuntimeError):
    """Activation failed, and the previous certified store was restored."""


class ActivationProjectionError(RuntimeError):
    """The authoritative mutable projection cannot be safely reconciled."""


def _require_qlever_identity(
    image: str,
    image_id: str,
    index_version: str,
    index_basename: str,
) -> None:
    # `image` pins the multi-architecture OCI manifest digest. Docker's `Image`
    # field is the platform-specific runtime image/config digest and therefore
    # legitimately differs between amd64 and arm64. Require a valid observed ID
    # here; candidate/active equality and live-container inspection bind that ID
    # at the activation boundaries.
    if image != QLEVER_IMAGE:
        raise ValueError("activation QLever image identity is not pinned")
    if _IMAGE_ID.fullmatch(image_id) is None:
        raise ValueError("activation QLever image ID is invalid")
    if index_version != QLEVER_INDEX_VERSION or index_basename != "ncit":
        raise ValueError("activation QLever index identity is not pinned")


class ActivationService(Protocol):
    """Stopped-service filesystem swap boundary."""

    def stop(self, active_path: Path) -> None: ...

    def restart(self, active_path: Path) -> None: ...


PausePublication = Callable[[], AbstractAsyncContextManager[None]]


class ProjectionProvenance(Protocol):
    """PostgreSQL read needed to bind the live projection marker."""

    async def get_run(self, run_id: str) -> RunSummary | None: ...


class _DockerRun(Protocol):
    def __call__(
        self, *args: str, check: bool = ...
    ) -> subprocess.CompletedProcess[str]: ...


class _ContainerHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    Status: str


class _ContainerState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    Running: bool
    Status: str
    Health: _ContainerHealth | None = None


class _ContainerMount(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    Type: str
    Source: str
    Destination: str
    RW: bool


class _ContainerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    Image: str
    Cmd: list[str]
    Labels: dict[str, str]


class _ContainerInspection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    Id: str
    Name: str
    Image: str
    State: _ContainerState
    Mounts: list[_ContainerMount]
    Config: _ContainerConfig


class QleverServiceContract(BaseModel):
    """Pinned executable/index identity plus one exact Compose service identity."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    service_name: str
    container_name: str
    image: str
    image_id: str
    index_version: str
    index_basename: str

    @field_validator("service_name", "container_name")
    @classmethod
    def _safe_compose_identity(cls, value: str) -> str:
        if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value) is None:
            raise ValueError("QLever Compose identity is unsafe")
        return value

    @model_validator(mode="after")
    def _pinned_executable_identity(self) -> QleverServiceContract:
        _require_qlever_identity(
            self.image,
            self.image_id,
            self.index_version,
            self.index_basename,
        )
        return self

    @classmethod
    def production(cls) -> QleverServiceContract:
        """Return the #163 production service contract."""
        return cls(
            service_name="qlever-ncit",
            container_name="ontoprism-qlever-ncit",
            image=QLEVER_IMAGE,
            image_id="sha256:" + QLEVER_IMAGE.rsplit("@sha256:", 1)[1],
            index_version=QLEVER_INDEX_VERSION,
            index_basename="ncit",
        )


def _inspection_document(payload: str) -> dict[str, object]:
    raw = json.loads(payload)
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
        raise ValueError("expected one container inspection object")
    return raw[0]


def _inspection_mapping(document: dict[str, object], name: str) -> dict[str, object]:
    value = document.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"container inspection {name} is malformed")
    return value


def _inspection_mounts(document: dict[str, object]) -> list[dict[str, object]]:
    value = document.get("Mounts")
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("container inspection mounts are malformed")
    return value


def _project_inspection(document: dict[str, object]) -> dict[str, object]:
    state = _inspection_mapping(document, "State")
    config = _inspection_mapping(document, "Config")
    health = state.get("Health")
    return {
        "Id": document.get("Id"),
        "Name": document.get("Name"),
        "Image": document.get("Image"),
        "State": {
            "Running": state.get("Running"),
            "Status": state.get("Status"),
            "Health": (
                {"Status": health.get("Status")} if isinstance(health, dict) else None
            ),
        },
        "Mounts": [
            {
                "Type": mount.get("Type"),
                "Source": mount.get("Source"),
                "Destination": mount.get("Destination"),
                "RW": mount.get("RW"),
            }
            for mount in _inspection_mounts(document)
        ],
        "Config": {
            "Image": config.get("Image"),
            "Cmd": config.get("Cmd"),
            "Labels": config.get("Labels"),
        },
    }


def _parse_container_inspection(payload: str) -> _ContainerInspection:
    try:
        return _ContainerInspection.model_validate(
            _project_inspection(_inspection_document(payload))
        )
    except (ValueError, ValidationError) as exc:
        raise ActivationServiceError(
            "NCIt QLever container inspection is malformed"
        ) from exc


def _require_container_identity(
    details: _ContainerInspection,
    contract: QleverServiceContract,
) -> None:
    if details.Image != contract.image_id or details.Config.Image != contract.image:
        raise ActivationServiceError("NCIt QLever image identity does not match")
    if details.Name != f"/{contract.container_name}":
        raise ActivationServiceError("NCIt QLever container name does not match")
    service = details.Config.Labels.get("com.docker.compose.service")
    if service != contract.service_name:
        raise ActivationServiceError("NCIt QLever Compose service label does not match")


def _require_container_mount(
    details: _ContainerInspection,
    active_path: Path,
) -> None:
    mounts = [mount for mount in details.Mounts if mount.Destination == "/data"]
    if len(mounts) != 1:
        raise ActivationServiceError("NCIt QLever active mount does not match")
    mount = mounts[0]
    exact_source = Path(mount.Source).resolve() == active_path.resolve()
    if mount.Type != "bind" or not exact_source or not mount.RW:
        raise ActivationServiceError("NCIt QLever active mount does not match")


def _require_server_command(
    details: _ContainerInspection,
    contract: QleverServiceContract,
) -> None:
    expected = f"qlever-server -i {contract.index_basename}"
    if expected not in " ".join(details.Config.Cmd):
        raise ActivationServiceError("NCIt QLever server command does not match")


class DockerComposeNcitService:
    """Exact stop/recreate contract for the pinned NCIt QLever service."""

    def __init__(
        self,
        *,
        project_directory: Path,
        contract: QleverServiceContract | None = None,
        docker_run: _DockerRun = run_docker,
        readiness_attempts: int = 30,
        readiness_interval: float = 1.0,
    ) -> None:
        if readiness_attempts < 1 or readiness_interval < 0:
            raise ValueError("invalid QLever service readiness policy")
        self._project_directory = project_directory.resolve()
        self._contract = contract or QleverServiceContract.production()
        self._docker_run = docker_run
        self._readiness_attempts = readiness_attempts
        self._readiness_interval = readiness_interval
        self._stopped_container_id: str | None = None

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return self._docker_run(*args)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ActivationServiceError(
                f"Docker command failed: {' '.join(args)}: {exc}"
            ) from exc

    def _inspect(self, active_path: Path) -> _ContainerInspection:
        inspected = self._run("inspect", self._contract.container_name)
        details = _parse_container_inspection(inspected.stdout)
        _require_container_identity(details, self._contract)
        _require_container_mount(details, active_path)
        _require_server_command(details, self._contract)
        return details

    def _compose(self, *args: str) -> None:
        self._run(
            "compose",
            "--project-directory",
            str(self._project_directory),
            *args,
        )

    def stop(self, active_path: Path) -> None:
        """Stop the exact running service and prove it is no longer running."""
        before = self._inspect(active_path)
        self._stopped_container_id = before.Id
        if before.State.Running:
            self._compose("stop", self._contract.service_name)
        stopped = self._inspect(active_path)
        if stopped.State.Running:
            raise ActivationServiceError("NCIt QLever service remained running")

    def restart(self, active_path: Path) -> None:
        """Force-recreate the service so Docker rebinds the renamed host path."""
        self._compose(
            "up",
            "-d",
            "--force-recreate",
            "--no-deps",
            self._contract.service_name,
        )
        last: _ContainerInspection | None = None
        for attempt in range(self._readiness_attempts):
            last = self._inspect(active_path)
            if self._is_recreated_and_healthy(last):
                return
            if attempt + 1 < self._readiness_attempts:
                time.sleep(self._readiness_interval)
        state = "missing" if last is None else last.State.Status
        raise ActivationServiceError(
            f"NCIt QLever service did not become healthy: {state}"
        )

    def _is_recreated_and_healthy(self, details: _ContainerInspection) -> bool:
        health = details.State.Health
        if not details.State.Running or health is None or health.Status != "healthy":
            return False
        if self._stopped_container_id == details.Id:
            raise ActivationServiceError("NCIt QLever service was not recreated")
        return True


class ActivationStoreProof(BaseModel):
    """Activation-relevant fields extracted from a validated store manifest."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    path: str
    owner: str
    source_identity: str
    store_format_identity: str
    qlever_image: str
    qlever_image_id: str
    qlever_index_version: str
    qlever_index_basename: str

    @field_validator("path")
    @classmethod
    def _absolute_path(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("activation store proof path must be absolute")
        return value

    @field_validator("owner")
    @classmethod
    def _valid_owner(cls, value: str) -> str:
        if _OWNER.fullmatch(value) is None:
            raise ValueError("activation store owner is invalid")
        return value

    @field_validator("source_identity", "store_format_identity")
    @classmethod
    def _valid_identity(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("activation store identity must be a lowercase SHA-256")
        return value

    @model_validator(mode="after")
    def _pinned_qlever_identity(self) -> ActivationStoreProof:
        _require_qlever_identity(
            self.qlever_image,
            self.qlever_image_id,
            self.qlever_index_version,
            self.qlever_index_basename,
        )
        return self


class ProjectionPlan(BaseModel):
    """PostgreSQL-bound artifact required after the immutable base swap."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    run_id: str
    source_identity: str
    representation_identity: str
    artifact_path: str
    built_at: AwareDatetime

    @field_validator("run_id")
    @classmethod
    def _run_id_is_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("projection run ID cannot be empty")
        return value

    @field_validator("source_identity", "representation_identity")
    @classmethod
    def _projection_identity_is_sha256(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("projection identities must be lowercase SHA-256")
        return value

    @field_validator("artifact_path")
    @classmethod
    def _artifact_path_is_absolute(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("projection artifact path must be absolute")
        return value


class ActivationJournal(BaseModel):
    """Exact paths and current durable phase of one activation attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    phase: ActivationPhase
    active_path: str
    candidate_path: str
    rollback_path: str
    candidate_manifest_path: str
    candidate_manifest_sha256: str
    candidate_owner: str
    active_owner: str
    candidate_source_identity: str
    active_source_identity: str
    store_format_identity: str
    qlever_image: str
    qlever_image_id: str
    qlever_index_version: str
    qlever_index_basename: str
    projection: ProjectionPlan | None = None
    activated_at: AwareDatetime | None = None

    @field_validator(
        "active_path",
        "candidate_path",
        "rollback_path",
        "candidate_manifest_path",
    )
    @classmethod
    def _path_is_absolute(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("activation journal paths must be absolute")
        return value

    @field_validator("candidate_owner", "active_owner")
    @classmethod
    def _owner_is_exact(cls, value: str) -> str:
        if _OWNER.fullmatch(value) is None:
            raise ValueError("activation journal owners must be lowercase UUID hex")
        return value

    @field_validator(
        "candidate_manifest_sha256",
        "candidate_source_identity",
        "active_source_identity",
        "store_format_identity",
    )
    @classmethod
    def _identity_is_sha256(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("activation journal identities must be lowercase SHA-256")
        return value

    @model_validator(mode="after")
    def _store_paths_are_distinct(self) -> ActivationJournal:
        store_paths = {self.active_path, self.candidate_path, self.rollback_path}
        if len(store_paths) != _STORE_PATH_COUNT:
            raise ValueError("activation store paths must be distinct")
        _require_qlever_identity(
            self.qlever_image,
            self.qlever_image_id,
            self.qlever_index_version,
            self.qlever_index_basename,
        )
        return self

    @model_validator(mode="after")
    def _activated_at_matches_phase(self) -> ActivationJournal:
        # ``activated_at`` is stamped exactly at the ``health-validated`` transition and
        # inherited by every later forward phase, so it must be present from then on and
        # absent before. ``rolled-back`` is terminal from either side of that boundary
        # (a rollback before or after health-validation), so it constrains neither.
        if self.phase in _ACTIVATED_PHASES and self.activated_at is None:
            raise ValueError(
                "activated_at is required once activation is health-validated"
            )
        if self.phase in _PRE_ACTIVATION_PHASES and self.activated_at is not None:
            raise ValueError(
                "activated_at must be unset before activation is health-validated"
            )
        return self


ActivationStep = Callable[[ActivationJournal], Awaitable[None]]


def validate_projection_artifact(plan: ProjectionPlan) -> bytes:
    """Read and identity-check the exact PostgreSQL-bound RDF artifact."""
    try:
        payload = Path(plan.artifact_path).read_bytes()
    except OSError as exc:
        raise ActivationProjectionError(
            f"projection artifact is unreadable: {plan.artifact_path}"
        ) from exc
    identity = hashlib.sha256(payload).hexdigest()
    if identity != plan.representation_identity:
        raise ActivationProjectionError(
            "projection artifact identity does not match the PostgreSQL journal"
        )
    return payload


def _require_published_projection_run(
    marker: PublicationMarker,
    run: RunSummary,
) -> None:
    if run.id != marker.run_id or run.status != "complete":
        raise ActivationProjectionError(
            "projection marker does not identify a complete PostgreSQL run"
        )
    if run.publication_state != "published":
        raise ActivationProjectionError("projection run is not published")
    _require_matching_projection_identity(marker, run)
    if run.publication_artifact_path is None:
        raise ActivationProjectionError("projection run has no artifact path")


def _require_matching_projection_identity(
    marker: PublicationMarker,
    run: RunSummary,
) -> None:
    if run.source_identity != marker.source_identity:
        raise ActivationProjectionError("projection source identity does not match")
    if run.representation_identity != marker.representation_identity:
        raise ActivationProjectionError(
            "projection representation identity does not match"
        )
    if run.publication_built_at != marker.built_at:
        raise ActivationProjectionError("projection build timestamp does not match")


def projection_plan_from_run(
    marker: PublicationMarker,
    run: RunSummary,
) -> ProjectionPlan:
    """Bind the live marker to its exact complete PostgreSQL publication row."""
    _require_published_projection_run(marker, run)
    if run.publication_artifact_path is None:  # narrowed by the fail-closed validator
        raise AssertionError("projection artifact path was not narrowed")
    plan = ProjectionPlan(
        run_id=marker.run_id,
        source_identity=marker.source_identity,
        representation_identity=marker.representation_identity,
        artifact_path=str(Path(run.publication_artifact_path).resolve()),
        built_at=marker.built_at,
    )
    validate_projection_artifact(plan)
    return plan


async def reconcile_projection_with_client(
    plan: ProjectionPlan,
    client: PublicationGraphClient,
) -> None:
    """Replay and verify the exact authoritative projection after base activation."""
    payload = validate_projection_artifact(plan)
    staging_graph = staging_graph_iri(plan.run_id)
    expected = PublicationMarker(
        run_id=plan.run_id,
        source_identity=plan.source_identity,
        representation_identity=plan.representation_identity,
        built_at=plan.built_at,
    )
    try:
        await client.load(
            payload,
            content_type="text/turtle",
            graph_iri=staging_graph,
            replace=True,
        )
        await client.update(build_replacement_update(expected, staging_graph))
        observed = await read_publication_marker(client)
    except Exception as exc:
        raise ActivationProjectionError(
            f"projection reconciliation failed: {type(exc).__name__}: {exc}"
        ) from exc
    if observed != expected:
        raise ActivationProjectionError(
            "reconciled projection marker does not match the PostgreSQL journal"
        )


async def capture_projection_plan(
    endpoint_url: str,
    provenance: ProjectionProvenance,
) -> ProjectionPlan | None:
    """Bind the currently composed graph to its authoritative PostgreSQL row."""
    async with ncit_sparql_client(endpoint_url) as client:
        marker = await read_publication_marker(client)
    if marker is None:
        return None
    run = await provenance.get_run(marker.run_id)
    if run is None:
        raise ActivationProjectionError(
            "live projection marker has no PostgreSQL publication run"
        )
    return projection_plan_from_run(marker, run)


def bind_projection_plan(
    journal_path: Path,
    journal: ActivationJournal,
    projection: ProjectionPlan | None,
) -> ActivationJournal:
    """Fsync the pre-stop projection plan into the activation journal."""
    if journal.phase != "preflight":
        raise ActivationTransitionError(
            f"activation phase {journal.phase!r} cannot bind a projection"
        )
    bound = journal.model_copy(update={"projection": projection})
    write_activation_journal(journal_path, bound)
    return bound


async def reconcile_projection_at_endpoint(
    endpoint_url: str,
    journal: ActivationJournal,
) -> None:
    """Restore the bound mutable projection, or prove that none is expected."""
    async with ncit_sparql_client(endpoint_url) as client:
        if journal.projection is not None:
            await reconcile_projection_with_client(journal.projection, client)
            return
        if await read_publication_marker(client) is not None:
            raise ActivationProjectionError(
                "restarted base exposes an unbound decomposition projection"
            )


def validate_projection_health(
    plan: ProjectionPlan | None,
    marker: PublicationMarker | None,
) -> None:
    """Require the composed projection marker bound before the service stop."""
    expected = None
    if plan is not None:
        expected = PublicationMarker(
            run_id=plan.run_id,
            source_identity=plan.source_identity,
            representation_identity=plan.representation_identity,
            built_at=plan.built_at,
        )
    if marker != expected:
        raise ActivationHealthError(
            "restarted service projection marker does not match the activation journal"
        )


async def validate_active_store_health(
    endpoint_url: str,
    journal: ActivationJournal,
    *,
    expected_source_identity: str,
) -> None:
    """Run the production source, definition, and browse health workload."""
    manifest_path = Path(journal.active_path) / CANDIDATE_MANIFEST_FILENAME
    manifest = validate_ncit_sibling_manifest(manifest_path)
    if manifest.source_identity != expected_source_identity:
        raise ActivationHealthError(
            "active store source identity does not match the activation journal"
        )
    observed = await observe_ncit_candidate(endpoint_url)
    async with ncit_sparql_client(endpoint_url) as client:
        complete = await read_complete_definition(client.select_once, "C27262")
        page = await NcitGraphStore(client).list_concepts(limit=5)
        projection_marker = await read_publication_marker(client)
    validate_activation_health(
        expected=manifest.observation,
        observed=observed,
        complete_definition_identity=complete.identity,
        browse_codes=tuple(hit.code for hit in page.hits),
    )
    validate_projection_health(journal.projection, projection_marker)


def _require_exact_store_directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ActivationPreflightError(
            f"QLever store path is not an exact directory: {path}"
        )


def _require_store_owner(path: Path, owner: str) -> None:
    try:
        marker = (path / OWNER_MARKER_FILENAME).read_text().strip()
    except OSError as exc:
        raise ActivationPreflightError(
            f"QLever owner marker is unreadable: {path}"
        ) from exc
    if marker != owner:
        raise ActivationPreflightError(f"QLever owner marker does not match: {path}")


def _require_store_inventory(path: Path) -> None:
    allowed = (
        QLEVER_REQUIRED_STORE_FILES
        | _QLEVER_OPTIONAL_STORE_FILES
        | {OWNER_MARKER_FILENAME, CANDIDATE_MANIFEST_FILENAME}
    )
    entries = {entry.name: entry for entry in path.iterdir()}
    missing = sorted(QLEVER_REQUIRED_STORE_FILES - entries.keys())
    if missing:
        raise ActivationPreflightError(
            f"QLever store is missing required index files: {', '.join(missing)}"
        )
    unexpected = sorted(entries.keys() - allowed)
    if unexpected:
        raise ActivationPreflightError(
            f"unexpected QLever store entry: {', '.join(unexpected)}"
        )
    unsafe = sorted(name for name, entry in entries.items() if not _regular_file(entry))
    if unsafe:
        raise ActivationPreflightError(
            f"QLever store entries must be regular files: {', '.join(unsafe)}"
        )


def _regular_file(path: Path) -> bool:
    return not path.is_symlink() and path.is_file()


def _verify_store_directory(proof: ActivationStoreProof) -> Path:
    path = Path(proof.path)
    _require_exact_store_directory(path)
    _require_store_owner(path, proof.owner)
    _require_store_inventory(path)
    return path


def _require_configured_active_path(active_path: Path, expected: Path) -> None:
    configured = expected.resolve()
    if active_path.resolve() != configured:
        raise ActivationPreflightError(
            "candidate manifest active path does not match configured active path: "
            f"{active_path} != {configured}"
        )


def _require_candidate_manifest_path(manifest: Path, candidate: Path) -> None:
    expected = candidate / CANDIDATE_MANIFEST_FILENAME
    if manifest.is_symlink() or not manifest.is_file():
        raise ActivationPreflightError("candidate manifest is not an exact file")
    if manifest.resolve() != expected.resolve():
        raise ActivationPreflightError(
            "candidate manifest path does not match the exact candidate directory"
        )


def _require_candidate_sibling_path(
    candidate: Path,
    active: Path,
    owner: str,
) -> None:
    expected_name = f".{active.name}.candidate-{owner}"
    if candidate.parent != active.parent or candidate.name != expected_name:
        raise ActivationPreflightError(
            "candidate path is not the owner-bound sibling of the active store"
        )


def _require_same_store_format(
    candidate: ActivationStoreProof,
    active: ActivationStoreProof,
) -> None:
    if candidate.store_format_identity != active.store_format_identity:
        raise ActivationPreflightError(
            "candidate and active store format identities differ"
        )


def _require_same_qlever_identity(
    candidate: ActivationStoreProof,
    active: ActivationStoreProof,
) -> None:
    candidate_identity = (
        candidate.qlever_image,
        candidate.qlever_image_id,
        candidate.qlever_index_version,
        candidate.qlever_index_basename,
    )
    active_identity = (
        active.qlever_image,
        active.qlever_image_id,
        active.qlever_index_version,
        active.qlever_index_basename,
    )
    if candidate_identity != active_identity:
        raise ActivationPreflightError(
            "candidate and active QLever executable/index identities differ"
        )


def _require_same_filesystem(candidate: Path, active: Path) -> None:
    devices = {
        candidate.stat().st_dev,
        active.stat().st_dev,
        active.parent.stat().st_dev,
    }
    if len(devices) != 1:
        raise ActivationPreflightError(
            "candidate and active QLever stores are on different filesystems"
        )


def _require_activation_headroom(parent: Path, minimum_free_bytes: int) -> None:
    if minimum_free_bytes < 0:
        raise ValueError("minimum activation free space cannot be negative")
    if shutil.disk_usage(parent).free < minimum_free_bytes:
        raise ActivationPreflightError(
            "insufficient free-space headroom for activation"
        )


def _require_absent_activation_path(path: Path, description: str) -> None:
    if path.exists() or path.is_symlink():
        raise ActivationPreflightError(f"{description} already exists: {path}")


def _base_observation(observation: CandidateObservation) -> CandidateObservation:
    stated = tuple(
        graph
        for graph in observation.named_graphs
        if graph.graph_iri == STATED_GRAPH_IRI
    )
    return observation.model_copy(update={"named_graphs": stated})


def _require_known_named_graphs(observation: CandidateObservation) -> None:
    unexpected = sorted(
        graph.graph_iri
        for graph in observation.named_graphs
        if graph.graph_iri not in NCIT_NAMED_GRAPHS
    )
    if unexpected:
        raise ActivationHealthError(
            "restarted service exposes an unexpected named graph: "
            + ", ".join(unexpected)
        )


def validate_activation_health(
    *,
    expected: CandidateObservation,
    observed: CandidateObservation,
    complete_definition_identity: str,
    browse_codes: tuple[str, ...],
) -> None:
    """Validate the base source, C27262 definition, and bounded browse liveness."""
    _require_known_named_graphs(observed)
    if _base_observation(observed) != expected:
        raise ActivationHealthError("restarted service base observation does not match")
    if complete_definition_identity != EXPECTED_C27262_DEFINITION_IDENTITY:
        raise ActivationHealthError("restarted service C27262 identity does not match")
    if not browse_codes or any(
        _NCIT_CODE.fullmatch(code) is None for code in browse_codes
    ):
        raise ActivationHealthError("restarted service bounded browse query is empty")


def preflight_activation(
    *,
    candidate_manifest_path: Path,
    candidate: ActivationStoreProof,
    active: ActivationStoreProof,
    expected_active_path: Path,
    minimum_free_bytes: int = 64 * 1024 * 1024,
) -> tuple[Path, ActivationJournal]:
    """Bind exact owned paths and durably create the preflight journal."""
    candidate_path = _verify_store_directory(candidate)
    active_path = _verify_store_directory(active)
    _require_configured_active_path(active_path, expected_active_path)
    _require_candidate_manifest_path(candidate_manifest_path, candidate_path)
    _require_candidate_sibling_path(candidate_path, active_path, candidate.owner)
    _require_same_store_format(candidate, active)
    _require_same_qlever_identity(candidate, active)
    _require_same_filesystem(candidate_path, active_path)
    _require_activation_headroom(active_path.parent, minimum_free_bytes)
    rollback_path = (
        active_path.parent / f".{active_path.name}.rollback-{candidate.owner}"
    )
    _require_absent_activation_path(rollback_path, "exact rollback path")
    journal_path = active_path.parent / f".{active_path.name}.activation.json"
    _require_absent_activation_path(journal_path, "activation journal")
    journal = ActivationJournal(
        phase="preflight",
        active_path=str(active_path.resolve()),
        candidate_path=str(candidate_path.resolve()),
        rollback_path=str(rollback_path.resolve()),
        candidate_manifest_path=str(candidate_manifest_path.resolve()),
        candidate_manifest_sha256=hashlib.sha256(
            candidate_manifest_path.read_bytes()
        ).hexdigest(),
        candidate_owner=candidate.owner,
        active_owner=active.owner,
        candidate_source_identity=candidate.source_identity,
        active_source_identity=active.source_identity,
        store_format_identity=candidate.store_format_identity,
        qlever_image=candidate.qlever_image,
        qlever_image_id=candidate.qlever_image_id,
        qlever_index_version=candidate.qlever_index_version,
        qlever_index_basename=candidate.qlever_index_basename,
    )
    write_activation_journal(journal_path, journal)
    return journal_path, journal


def _store_proof_from_manifest(
    manifest: NcitSiblingStoreManifest,
) -> ActivationStoreProof:
    return ActivationStoreProof(
        path=manifest.candidate_path,
        owner=manifest.owner,
        source_identity=manifest.source_identity,
        store_format_identity=manifest.loader.store_format_identity,
        qlever_image=manifest.loader.image,
        qlever_image_id=manifest.loader.image_id,
        qlever_index_version=manifest.loader.cli_version,
        qlever_index_basename="ncit",
    )


def _resume_existing_journal(
    journal_path: Path,
    candidate_manifest_path: Path,
    active_path: Path,
) -> ActivationJournal:
    journal = read_activation_journal(journal_path)
    if journal.active_path != str(active_path.resolve()):
        raise ActivationPreflightError(
            "existing activation journal targets a different active path"
        )
    supplied = str(candidate_manifest_path.resolve())
    if journal.candidate_manifest_path != supplied:
        raise ActivationPreflightError(
            "existing activation journal targets a different candidate manifest"
        )
    if candidate_manifest_path.is_file():
        observed = hashlib.sha256(candidate_manifest_path.read_bytes()).hexdigest()
        if observed != journal.candidate_manifest_sha256:
            raise ActivationPreflightError(
                "candidate manifest changed after activation preflight"
            )
    return journal


def _archive_terminal_journal(
    journal_path: Path,
    journal: ActivationJournal,
) -> None:
    if journal.phase not in {"complete", "rolled-back"}:
        raise ActivationPreflightError(
            "existing nonterminal activation journal targets a different "
            "candidate manifest"
        )
    history = journal_path.with_name(
        f"{journal_path.stem}-{journal.candidate_owner}-{journal.phase}"
        f"{journal_path.suffix}"
    )
    _require_absent_activation_path(history, "activation journal history path")
    journal_path.replace(history)
    _fsync_directory(journal_path.parent)


def _read_existing_activation_journal(
    journal_path: Path,
) -> ActivationJournal | None:
    if not (journal_path.exists() or journal_path.is_symlink()):
        return None
    return read_activation_journal(journal_path)


def _resume_matching_activation(
    journal_path: Path,
    existing: ActivationJournal,
    candidate_manifest_path: Path,
    active_path: Path,
) -> ActivationJournal | None:
    supplied = str(candidate_manifest_path.resolve())
    if existing.candidate_manifest_path != supplied:
        return None
    return _resume_existing_journal(
        journal_path,
        candidate_manifest_path,
        active_path,
    )


def _require_replaceable_terminal_journal(
    existing: ActivationJournal,
    active_path: Path,
) -> ActivationJournal:
    if existing.active_path != str(active_path):
        raise ActivationPreflightError(
            "existing activation journal targets a different active path"
        )
    if existing.phase not in {"complete", "rolled-back"}:
        raise ActivationPreflightError(
            "existing nonterminal activation journal targets a different "
            "candidate manifest"
        )
    return existing


def prepare_activation_journal(
    candidate_manifest_path: Path,
    *,
    expected_active_path: Path,
    minimum_free_bytes: int = 64 * 1024 * 1024,
    expected_policy: CandidateValidationPolicy | None = None,
) -> tuple[Path, ActivationJournal]:
    """Validate #181 proofs or resume their exact existing activation journal."""
    active_path = expected_active_path.resolve()
    journal_path = active_path.parent / f".{active_path.name}.activation.json"
    terminal_journal: ActivationJournal | None = None
    existing = _read_existing_activation_journal(journal_path)
    if existing is not None:
        resumed = _resume_matching_activation(
            journal_path,
            existing,
            candidate_manifest_path,
            active_path,
        )
        if resumed is not None:
            return journal_path, resumed
        terminal_journal = _require_replaceable_terminal_journal(existing, active_path)
    candidate = validate_ncit_sibling_manifest(
        candidate_manifest_path,
        expected_policy=expected_policy,
    )
    active_manifest_path = active_path / CANDIDATE_MANIFEST_FILENAME
    active = validate_ncit_sibling_manifest(
        active_manifest_path,
        expected_policy=expected_policy,
    )
    if active.active_store_path != str(active_path):
        raise ActivationPreflightError(
            "active store manifest does not bind the configured active path"
        )
    if terminal_journal is not None:
        _archive_terminal_journal(journal_path, terminal_journal)
    return preflight_activation(
        candidate_manifest_path=candidate_manifest_path,
        candidate=_store_proof_from_manifest(candidate),
        active=_store_proof_from_manifest(active),
        expected_active_path=active_path,
        minimum_free_bytes=minimum_free_bytes,
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_activation_journal(path: Path, journal: ActivationJournal) -> None:
    """Atomically persist and fsync a complete activation phase."""
    path = path.absolute()
    if path.is_symlink():
        raise ActivationJournalError(
            f"activation journal path cannot be a symlink: {path}"
        )
    if path.exists() and not path.is_file():
        raise ActivationJournalError(
            f"activation journal path is not an exact regular file: {path}"
        )
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise ActivationJournalError(
            f"activation journal parent is not an exact directory: {path.parent}"
        )
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x") as stream:
            stream.write(journal.model_dump_json(indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def read_activation_journal(path: Path) -> ActivationJournal:
    """Load one strict activation journal without recovering guessed fields."""
    if path.is_symlink() or not path.is_file():
        raise ActivationJournalError(
            f"activation journal is not an exact regular file: {path}"
        )
    try:
        return ActivationJournal.model_validate_json(path.read_text())
    except (OSError, ValidationError, ValueError) as exc:
        raise ActivationJournalError(
            f"activation journal is missing or invalid: {path}: {exc}"
        ) from exc


def transition_activation_journal(
    path: Path,
    journal: ActivationJournal,
    phase: ActivationPhase,
) -> ActivationJournal:
    """Persist exactly the next legal forward phase."""
    expected = _FORWARD_TRANSITIONS.get(journal.phase)
    if expected != phase:
        raise ActivationTransitionError(
            f"activation phase {journal.phase!r} cannot transition to {phase!r}"
        )
    updates: dict[str, object] = {"phase": phase}
    if phase == "health-validated" and journal.activated_at is None:
        updates["activated_at"] = datetime.now(UTC)
    transitioned = journal.model_copy(update=updates)
    write_activation_journal(path, transitioned)
    return transitioned


def _durable_rewrite_candidate_path(manifest_path: Path, candidate_path: Path) -> None:
    try:
        document = json.loads(manifest_path.read_text())
        if not isinstance(document, dict) or not isinstance(
            document.get("candidate_path"), str
        ):
            raise ValueError("candidate_path is missing")
    except (OSError, ValueError) as exc:
        raise ActivationJournalError(
            f"cannot rewrite relocated candidate manifest {manifest_path}: {exc}"
        ) from exc
    document["candidate_path"] = str(candidate_path.resolve())
    temporary = manifest_path.with_name(
        f".{manifest_path.name}.{uuid4().hex}.activation.tmp"
    )
    try:
        with temporary.open("x") as stream:
            stream.write(json.dumps(document, indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(manifest_path)
        _fsync_directory(manifest_path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _journal_store_proof(
    journal: ActivationJournal,
    *,
    path: Path,
    owner: str,
    source_identity: str,
) -> ActivationStoreProof:
    return ActivationStoreProof(
        path=str(path.resolve()),
        owner=owner,
        source_identity=source_identity,
        store_format_identity=journal.store_format_identity,
        qlever_image=journal.qlever_image,
        qlever_image_id=journal.qlever_image_id,
        qlever_index_version=journal.qlever_index_version,
        qlever_index_basename=journal.qlever_index_basename,
    )


def stage_active_store_for_rollback(
    journal_path: Path,
    journal: ActivationJournal,
) -> ActivationJournal:
    """Rename the exact stopped active store to the bound rollback path."""
    if journal.phase != "service-stopped":
        raise ActivationTransitionError(
            f"activation phase {journal.phase!r} cannot stage rollback"
        )
    active = Path(journal.active_path)
    rollback = Path(journal.rollback_path)
    if active.exists() or active.is_symlink():
        _verify_store_directory(
            _journal_store_proof(
                journal,
                path=active,
                owner=journal.active_owner,
                source_identity=journal.active_source_identity,
            )
        )
        if rollback.exists() or rollback.is_symlink():
            raise ActivationPreflightError(
                f"exact rollback path already exists: {rollback}"
            )
        active.replace(rollback)
        _fsync_directory(active.parent)
    else:
        _verify_store_directory(
            _journal_store_proof(
                journal,
                path=rollback,
                owner=journal.active_owner,
                source_identity=journal.active_source_identity,
            )
        )
    return transition_activation_journal(journal_path, journal, "rollback-staged")


def activate_candidate_store(
    journal_path: Path,
    journal: ActivationJournal,
) -> ActivationJournal:
    """Rename the exact candidate into the configured active path."""
    if journal.phase != "rollback-staged":
        raise ActivationTransitionError(
            f"activation phase {journal.phase!r} cannot activate candidate"
        )
    candidate = Path(journal.candidate_path)
    active = Path(journal.active_path)
    if candidate.exists() or candidate.is_symlink():
        _verify_store_directory(
            _journal_store_proof(
                journal,
                path=candidate,
                owner=journal.candidate_owner,
                source_identity=journal.candidate_source_identity,
            )
        )
        if active.exists() or active.is_symlink():
            raise ActivationPreflightError(
                f"active path unexpectedly exists before candidate activation: {active}"
            )
        candidate.replace(active)
        _fsync_directory(active.parent)
    else:
        _verify_store_directory(
            _journal_store_proof(
                journal,
                path=active,
                owner=journal.candidate_owner,
                source_identity=journal.candidate_source_identity,
            )
        )
    _durable_rewrite_candidate_path(
        active / CANDIDATE_MANIFEST_FILENAME,
        active,
    )
    return transition_activation_journal(journal_path, journal, "candidate-activated")


def restore_rollback_store(
    journal_path: Path,
    journal: ActivationJournal,
) -> ActivationJournal:
    """Restore the exact old active store and preserve the failed candidate."""
    if journal.phase not in {
        "service-stopped",
        "rollback-staged",
        "candidate-activated",
        "service-restarted",
    }:
        raise ActivationTransitionError(
            f"activation phase {journal.phase!r} cannot restore rollback"
        )
    _restore_rollback_paths(journal)
    restored = journal.model_copy(update={"phase": "rolled-back"})
    write_activation_journal(journal_path, restored)
    return restored


def _restore_rollback_paths(journal: ActivationJournal) -> None:
    active = Path(journal.active_path)
    candidate = Path(journal.candidate_path)
    rollback = Path(journal.rollback_path)
    if rollback.exists():
        _verify_store_directory(
            _journal_store_proof(
                journal,
                path=rollback,
                owner=journal.active_owner,
                source_identity=journal.active_source_identity,
            )
        )
        if active.exists():
            _verify_store_directory(
                _journal_store_proof(
                    journal,
                    path=active,
                    owner=journal.candidate_owner,
                    source_identity=journal.candidate_source_identity,
                )
            )
            if candidate.exists() or candidate.is_symlink():
                raise ActivationPreflightError(
                    f"candidate path unexpectedly exists during rollback: {candidate}"
                )
            active.replace(candidate)
            _fsync_directory(active.parent)
            _durable_rewrite_candidate_path(
                candidate / CANDIDATE_MANIFEST_FILENAME,
                candidate,
            )
        rollback.replace(active)
        _fsync_directory(active.parent)
    else:
        _verify_store_directory(
            _journal_store_proof(
                journal,
                path=active,
                owner=journal.active_owner,
                source_identity=journal.active_source_identity,
            )
        )


def _require_candidate_active_after_cleanup(journal: ActivationJournal) -> None:
    _verify_store_directory(
        _journal_store_proof(
            journal,
            path=Path(journal.active_path),
            owner=journal.candidate_owner,
            source_identity=journal.candidate_source_identity,
        )
    )


def _remove_verified_rollback(journal: ActivationJournal, rollback: Path) -> None:
    try:
        marker = (rollback / OWNER_MARKER_FILENAME).read_text().strip()
    except OSError as exc:
        raise ActivationPreflightError("rollback owner marker is unreadable") from exc
    if rollback.is_symlink() or not rollback.is_dir() or marker != journal.active_owner:
        raise ActivationPreflightError("rollback owner marker does not match")
    _verify_store_directory(
        _journal_store_proof(
            journal,
            path=rollback,
            owner=journal.active_owner,
            source_identity=journal.active_source_identity,
        )
    )
    shutil.rmtree(rollback)
    _fsync_directory(rollback.parent)


def cleanup_rollback_store(
    journal_path: Path,
    journal: ActivationJournal,
) -> ActivationJournal:
    """Delete only the journal-bound, independently owner-verified rollback."""
    if journal.phase != "health-validated":
        raise ActivationTransitionError(
            f"activation phase {journal.phase!r} cannot clean rollback"
        )
    rollback = Path(journal.rollback_path)
    if rollback.exists() or rollback.is_symlink():
        _remove_verified_rollback(journal, rollback)
    else:
        _require_candidate_active_after_cleanup(journal)
    return transition_activation_journal(journal_path, journal, "rollback-cleaned")


class _ActivationRun:
    def __init__(
        self,
        *,
        journal_path: Path,
        service: ActivationService,
        reconcile_projection: ActivationStep,
        validate_health: ActivationStep,
        validate_rollback_health: ActivationStep,
    ) -> None:
        self.journal_path = journal_path
        self.service = service
        self.reconcile_projection = reconcile_projection
        self.validate_health = validate_health
        self.validate_rollback_health = validate_rollback_health


async def _pause_step(
    run: _ActivationRun,
    journal: ActivationJournal,
) -> ActivationJournal:
    return transition_activation_journal(
        run.journal_path, journal, "publication-paused"
    )


async def _stop_step(
    run: _ActivationRun,
    journal: ActivationJournal,
) -> ActivationJournal:
    run.service.stop(Path(journal.active_path))
    return transition_activation_journal(run.journal_path, journal, "service-stopped")


async def _stage_step(
    run: _ActivationRun,
    journal: ActivationJournal,
) -> ActivationJournal:
    return stage_active_store_for_rollback(run.journal_path, journal)


async def _activate_step(
    run: _ActivationRun,
    journal: ActivationJournal,
) -> ActivationJournal:
    return activate_candidate_store(run.journal_path, journal)


async def _restart_step(
    run: _ActivationRun,
    journal: ActivationJournal,
) -> ActivationJournal:
    run.service.restart(Path(journal.active_path))
    return transition_activation_journal(run.journal_path, journal, "service-restarted")


async def _health_step(
    run: _ActivationRun,
    journal: ActivationJournal,
) -> ActivationJournal:
    await run.reconcile_projection(journal)
    await run.validate_health(journal)
    return transition_activation_journal(run.journal_path, journal, "health-validated")


async def _cleanup_step(
    run: _ActivationRun,
    journal: ActivationJournal,
) -> ActivationJournal:
    return cleanup_rollback_store(run.journal_path, journal)


_ACTIVATION_STEPS: dict[
    ActivationPhase,
    Callable[[_ActivationRun, ActivationJournal], Awaitable[ActivationJournal]],
] = {
    "preflight": _pause_step,
    "publication-paused": _stop_step,
    "service-stopped": _stage_step,
    "rollback-staged": _activate_step,
    "candidate-activated": _restart_step,
    "service-restarted": _health_step,
    "health-validated": _cleanup_step,
}
_ROLLBACKABLE_PHASES = frozenset(
    {
        "publication-paused",
        "service-stopped",
        "rollback-staged",
        "candidate-activated",
        "service-restarted",
    }
)


async def _advance_activation(
    run: _ActivationRun,
    journal: ActivationJournal,
) -> ActivationJournal:
    handler = _ACTIVATION_STEPS.get(journal.phase)
    while handler is not None:
        journal = await handler(run, journal)
        handler = _ACTIVATION_STEPS.get(journal.phase)
    return journal


async def _recover_previous_store(
    run: _ActivationRun,
    journal: ActivationJournal,
    original: BaseException,
) -> None:
    try:
        if journal.phase != "publication-paused":
            run.service.stop(Path(journal.active_path))
        _restore_rollback_paths(journal)
        run.service.restart(Path(journal.active_path))
        await run.validate_rollback_health(journal)
    except BaseException as recovery_error:
        original.add_note(
            "Activation rollback also failed: "
            f"{type(recovery_error).__name__}: {recovery_error}"
        )
        raise original from recovery_error


async def _activate_while_publication_paused(
    run: _ActivationRun,
    journal: ActivationJournal,
) -> tuple[ActivationJournal, BaseException | None]:
    try:
        return await _advance_activation(run, journal), None
    except BaseException as original:
        persisted = read_activation_journal(run.journal_path)
        if persisted.phase not in _ROLLBACKABLE_PHASES:
            raise
        await _recover_previous_store(run, persisted, original)
        return persisted, original


def _mark_rolled_back(
    journal_path: Path,
    journal: ActivationJournal,
) -> ActivationJournal:
    rolled_back = journal.model_copy(update={"phase": "rolled-back"})
    write_activation_journal(journal_path, rolled_back)
    return rolled_back


async def run_journaled_activation(
    journal_path: Path,
    *,
    service: ActivationService,
    pause_publication: PausePublication,
    reconcile_projection: ActivationStep,
    validate_health: ActivationStep,
    validate_rollback_health: ActivationStep,
) -> ActivationJournal:
    """Resume one exact journal until it completes or safely rolls back."""
    journal = read_activation_journal(journal_path)
    if journal.phase in {"complete", "rolled-back"}:
        return journal
    if journal.phase == "publication-resumed":
        return transition_activation_journal(journal_path, journal, "complete")
    run = _ActivationRun(
        journal_path=journal_path,
        service=service,
        reconcile_projection=reconcile_projection,
        validate_health=validate_health,
        validate_rollback_health=validate_rollback_health,
    )
    failure: BaseException | None = None
    async with pause_publication():
        journal, failure = await _activate_while_publication_paused(run, journal)
    if failure is not None:
        _mark_rolled_back(journal_path, journal)
        raise ActivationRolledBackError(
            f"activation rolled back: {failure}"
        ) from failure
    journal = transition_activation_journal(
        journal_path, journal, "publication-resumed"
    )
    return transition_activation_journal(journal_path, journal, "complete")
