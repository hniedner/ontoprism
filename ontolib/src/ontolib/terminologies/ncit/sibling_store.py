"""Offline construction and certification of inactive NCIt sibling stores."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import subprocess
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ontolib.core.exceptions import StorageError
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.owl_download import (
    OwlArtifactPairManifest,
    OwlArtifactRecord,
    validate_ncit_owl_pair,
)
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection
    from contextlib import AbstractContextManager

CANDIDATE_MANIFEST_FILENAME = ".ontoprism-ncit-candidate.json"
REJECTED_CANDIDATE_FILENAME = ".ontoprism-ncit-rejected.json"
OWNER_MARKER_FILENAME = ".ontoprism-ncit-owner"
CANDIDATE_MANIFEST_SCHEMA_VERSION = 1
OXIGRAPH_IMAGE = (
    "ghcr.io/oxigraph/oxigraph@sha256:"
    "cc943499d4724fbb348c75c623335c69a047de71c59852413b0d0467d3caebe3"
)
OXIGRAPH_CLI_VERSION = "oxigraph 0.5.3"

_OWNER = re.compile(r"[0-9a-f]{32}")
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
_DOCKER_PORT = re.compile(r"127\.0\.0\.1:(\d+)")
_OWL_NS = "http://www.w3.org/2002/07/owl#"
_RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"


class SiblingStoreValidationError(RuntimeError):
    """An NCIt sibling candidate cannot be certified for later activation."""


class _StrictProofModel(BaseModel):
    """Reject proof fields that are unknown or require type coercion."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


def _identity(payload: object) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


class LoaderIdentity(_StrictProofModel):
    """Exact executable identity that determines the Oxigraph store format."""

    image: str
    image_id: str
    cli_version: str
    store_format_identity: str = ""

    def model_post_init(self, _context: object) -> None:
        if not self.store_format_identity:
            object.__setattr__(
                self,
                "store_format_identity",
                _identity(
                    {
                        "image": self.image,
                        "image_id": self.image_id,
                        "cli_version": self.cli_version,
                    }
                ),
            )


class CandidateGraph(_StrictProofModel):
    """One named graph and its exact observed triple count."""

    graph_iri: str
    triples: int


class CandidateObservation(_StrictProofModel):
    """Production-shaped facts read from a directly queryable candidate."""

    default_triples: int
    stated_triples: int
    named_graphs: tuple[CandidateGraph, ...]
    default_version: str | None
    stated_version: str | None
    restriction_count: int
    has_required_restriction: bool
    default_has_stated_only_sentinel: bool
    stated_has_stated_only_sentinel: bool


class CandidateValidationPolicy(_StrictProofModel):
    """Release-independent safety bounds for a full NCIt store."""

    min_default_triples: int = 12_000_000
    max_default_triples: int = 14_000_000
    min_stated_triples: int = 10_000_000
    max_stated_triples: int = 12_000_000
    min_restrictions: int = 100_000
    max_restrictions: int = 250_000

    @model_validator(mode="after")
    def _bounds_are_ordered(self) -> CandidateValidationPolicy:
        """Reject inverted or non-positive bounds, which can never gate anything."""
        pairs = (
            ("default_triples", self.min_default_triples, self.max_default_triples),
            ("stated_triples", self.min_stated_triples, self.max_stated_triples),
            ("restrictions", self.min_restrictions, self.max_restrictions),
        )
        for name, low, high in pairs:
            if low < 1:
                raise ValueError(f"min_{name} must be positive")
            if low > high:
                raise ValueError(f"min_{name} exceeds max_{name}")
        return self


class CandidateGraphLayout(_StrictProofModel):
    """The only accepted graph assignment for a sibling store."""

    default_graph: str = "inferred"
    stated_graph_iri: str = STATED_GRAPH_IRI


class CandidateArtifact(_StrictProofModel):
    """Artifact identity copied into the candidate proof for independent consumers."""

    variant: Literal["stated", "inferred"]
    path: str
    size_bytes: int
    sha256: str
    artifact_identity: str


class _DockerConfig(BaseModel):
    labels: dict[str, str] = Field(alias="Labels")


class _DockerMount(BaseModel):
    source: str = Field(alias="Source")
    destination: str = Field(alias="Destination")


class _DockerInspection(BaseModel):
    container_id: str = Field(alias="Id")
    config: _DockerConfig = Field(alias="Config")
    mounts: tuple[_DockerMount, ...] = Field(alias="Mounts")


class NcitSiblingStoreManifest(_StrictProofModel):
    """Persisted proof that an inactive store passed every construction gate."""

    schema_version: int = CANDIDATE_MANIFEST_SCHEMA_VERSION
    owner: str
    candidate_path: str
    active_store_path: str
    pair_manifest_path: str
    pair_manifest_identity: str
    ontology_version: str
    ontology_iri: str
    stated_artifact: CandidateArtifact
    inferred_artifact: CandidateArtifact
    source_identity: str
    loader: LoaderIdentity
    graph_layout: CandidateGraphLayout
    validation_policy: CandidateValidationPolicy
    observation: CandidateObservation


class SiblingStoreRuntime(Protocol):
    """External Oxigraph lifecycle used by the fail-closed builder."""

    def identify_loader(self) -> LoaderIdentity: ...

    def load(
        self,
        pair: OwlArtifactPairManifest,
        candidate_path: Path,
        owner: str,
    ) -> None: ...

    async def observe(
        self,
        candidate_path: Path,
        owner: str,
        observer: Callable[[str], Awaitable[CandidateObservation]],
    ) -> CandidateObservation: ...


class DockerRun(Protocol):
    """The injectable Docker CLI boundary."""

    def __call__(
        self, *args: str, check: bool = ...
    ) -> subprocess.CompletedProcess[str]: ...


class CandidateQueryClient(Protocol):
    """The bounded SELECT surface used by scalar candidate checks."""

    async def select_once(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str]]: ...


def run_docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run the locally installed Docker CLI without a shell."""
    executable = shutil.which("docker")
    if executable is None:
        raise SiblingStoreValidationError(
            "Docker is required for NCIt sibling-store construction"
        )
    return subprocess.run(  # noqa: S603
        [executable, *args],
        check=check,
        capture_output=True,
        text=True,
    )


async def _wait_until_ready(
    endpoint_url: str,
    *,
    timeout_seconds: float = 30,
    retry_delay_seconds: float = 0.1,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last_error: Exception | None = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            async with OxigraphHttpClient(
                endpoint_url, connect_timeout=1, query_timeout=1
            ) as client:
                await client.ask("ASK {}")
                return
        except (OSError, StorageError) as exc:
            last_error = exc
            await asyncio.sleep(retry_delay_seconds)
    raise SiblingStoreValidationError(
        "candidate Oxigraph server did not become ready"
    ) from last_error


def _no_connection_audit(url: str) -> AbstractContextManager[None]:
    del url
    return nullcontext()


def _parse_container_inspection(
    inspected: subprocess.CompletedProcess[str],
) -> _DockerInspection:
    try:
        document = json.loads(inspected.stdout)
        return _DockerInspection.model_validate(document[0])
    except (IndexError, KeyError, TypeError, ValueError, ValidationError) as exc:
        raise SiblingStoreValidationError(
            "candidate container inspection was malformed"
        ) from exc


def _require_inspected_container_id(
    actual_id: str,
    expected_id: str | None,
) -> None:
    if _CONTAINER_ID.fullmatch(actual_id) is None:
        raise SiblingStoreValidationError(
            "candidate container inspection has an invalid immutable ID"
        )
    if expected_id is not None and actual_id != expected_id:
        raise SiblingStoreValidationError(
            "candidate container identity changed before teardown"
        )


def _require_inspected_owner(
    details: _DockerInspection,
    marker: str,
    owner: str,
) -> None:
    if (
        details.config.labels.get("org.ontoprism.candidate-owner") != owner
        or marker != owner
    ):
        raise SiblingStoreValidationError(
            "candidate container owner identity does not match"
        )


def _require_inspected_mount(
    details: _DockerInspection,
    candidate_path: Path,
) -> None:
    mounted = next(
        (mount.source for mount in details.mounts if mount.destination == "/data"),
        None,
    )
    if mounted is None or Path(mounted).resolve() != candidate_path.resolve():
        raise SiblingStoreValidationError(
            "candidate container data mount does not match"
        )


def _valid_container_id(value: str | None) -> str | None:
    if value is not None and _CONTAINER_ID.fullmatch(value) is not None:
        return value
    return None


class DockerOxigraphRuntime:
    """Pinned Oxigraph CLI loader and owner-verified temporary query server."""

    def __init__(
        self,
        *,
        docker_run: DockerRun = run_docker,
        connection_scope: Callable[[str], AbstractContextManager[None]] = (
            _no_connection_audit
        ),
        wait_until_ready: Callable[[str], Awaitable[None]] = _wait_until_ready,
    ) -> None:
        self._docker_run = docker_run
        self._connection_scope = connection_scope
        self._wait_until_ready = wait_until_ready

    def identify_loader(self) -> LoaderIdentity:
        """Require the exact digest and CLI version that own the store format."""
        inspected = self._docker_run("image", "inspect", OXIGRAPH_IMAGE)
        try:
            document = json.loads(inspected.stdout)
            details = document[0]
            image_id = details["Id"]
            repo_digests = details["RepoDigests"]
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise SiblingStoreValidationError(
                "Docker returned malformed Oxigraph image identity"
            ) from exc
        if (
            not isinstance(image_id, str)
            or _IMAGE_ID.fullmatch(image_id) is None
            or not isinstance(repo_digests, list)
            or OXIGRAPH_IMAGE not in repo_digests
        ):
            raise SiblingStoreValidationError(
                "local Oxigraph image does not carry the pinned digest"
            )
        version = self._docker_run(
            "run", "--rm", OXIGRAPH_IMAGE, "--version"
        ).stdout.strip()
        if version != OXIGRAPH_CLI_VERSION:
            raise SiblingStoreValidationError(
                f"Oxigraph CLI version drift: {version!r} != {OXIGRAPH_CLI_VERSION!r}"
            )
        return LoaderIdentity(
            image=OXIGRAPH_IMAGE,
            image_id=image_id,
            cli_version=version,
        )

    def _load_command(
        self,
        artifact_path: Path,
        candidate_path: Path,
        owner: str,
        *,
        graph_iri: str | None,
    ) -> tuple[str, ...]:
        graph = () if graph_iri is None else ("--graph", graph_iri)
        return (
            "run",
            "--rm",
            "--label",
            f"org.ontoprism.candidate-owner={owner}",
            "--mount",
            f"type=bind,src={candidate_path.resolve()},dst=/data",
            "--mount",
            f"type=bind,src={artifact_path.resolve()},dst=/input.owl,readonly",
            OXIGRAPH_IMAGE,
            "load",
            "--location",
            "/data",
            "--file",
            "/input.owl",
            "--format",
            "application/rdf+xml",
            *graph,
            "--non-atomic",
        )

    def load(
        self,
        pair: OwlArtifactPairManifest,
        candidate_path: Path,
        owner: str,
    ) -> None:
        """Load the paired files offline into their fixed graph assignments."""
        _validate_owner(owner)
        for artifact, graph in (
            (Path(pair.inferred.file_path), None),
            (Path(pair.stated.file_path), STATED_GRAPH_IRI),
        ):
            command = self._load_command(
                artifact, candidate_path, owner, graph_iri=graph
            )
            try:
                self._docker_run(*command)
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or exc.stdout or "").strip()
                raise SiblingStoreValidationError(
                    f"offline Oxigraph load failed: {detail}"
                ) from exc

    def _server_command(self, candidate_path: Path, owner: str) -> tuple[str, ...]:
        return (
            "run",
            "--detach",
            "--name",
            f"ontoprism-ncit-candidate-{owner}",
            "--label",
            f"org.ontoprism.candidate-owner={owner}",
            "--publish",
            "127.0.0.1::7878",
            "--mount",
            f"type=bind,src={candidate_path.resolve()},dst=/data",
            OXIGRAPH_IMAGE,
            "serve",
            "--location",
            "/data",
            "--bind",
            "0.0.0.0:7878",
        )

    def _owned_container_id(
        self,
        inspected: subprocess.CompletedProcess[str],
        candidate_path: Path,
        owner: str,
        *,
        expected_id: str | None,
    ) -> str:
        details = _parse_container_inspection(inspected)
        marker = (candidate_path / OWNER_MARKER_FILENAME).read_text().strip()
        _require_inspected_container_id(details.container_id, expected_id)
        _require_inspected_owner(details, marker, owner)
        _require_inspected_mount(details, candidate_path)
        return details.container_id

    def _remove_owned_container(
        self,
        target: str,
        candidate_path: Path,
        owner: str,
        *,
        expected_id: str | None,
        allow_absent: bool,
    ) -> None:
        inspected = self._docker_run("inspect", target, check=not allow_absent)
        if inspected.returncode != 0:
            detail = (inspected.stderr or inspected.stdout).strip()
            if allow_absent and (
                "no such object" in detail.lower()
                or "no such container" in detail.lower()
            ):
                return
            raise SiblingStoreValidationError(
                f"candidate container inspection failed: {detail}"
            )
        actual_id = self._owned_container_id(
            inspected,
            candidate_path,
            owner,
            expected_id=expected_id,
        )
        self._docker_run("rm", "--force", actual_id)

    def _finish_observation(
        self,
        container_id: str | None,
        candidate_path: Path,
        owner: str,
        original_error: BaseException | None,
    ) -> None:
        valid_id = _valid_container_id(container_id)
        target = valid_id or f"ontoprism-ncit-candidate-{owner}"
        try:
            self._remove_owned_container(
                target,
                candidate_path,
                owner,
                expected_id=valid_id,
                allow_absent=valid_id is None,
            )
        except BaseException as cleanup_error:
            if original_error is None:
                raise
            original_error.add_note(
                "Candidate container teardown also failed: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            )

    async def observe(
        self,
        candidate_path: Path,
        owner: str,
        observer: Callable[[str], Awaitable[CandidateObservation]],
    ) -> CandidateObservation:
        """Serve on random loopback and remove only the verified container."""
        _validate_owner(owner)
        container_id: str | None = None
        original_error: BaseException | None = None
        try:
            started = self._docker_run(*self._server_command(candidate_path, owner))
            container_id = started.stdout.strip()
            if _CONTAINER_ID.fullmatch(container_id) is None:
                raise SiblingStoreValidationError(
                    f"unexpected candidate container ID: {container_id!r}"
                )
            published = self._docker_run(
                "port", container_id, "7878/tcp"
            ).stdout.strip()
            match = _DOCKER_PORT.fullmatch(published)
            if match is None:
                raise SiblingStoreValidationError(
                    f"unexpected candidate port mapping: {published!r}"
                )
            endpoint = f"http://127.0.0.1:{match.group(1)}"
            with self._connection_scope(endpoint):
                await self._wait_until_ready(endpoint)
                return await observer(endpoint)
        except BaseException as exc:
            original_error = exc
            raise
        finally:
            self._finish_observation(
                container_id, candidate_path, owner, original_error
            )


def _validate_owner(owner: str) -> None:
    if _OWNER.fullmatch(owner) is None:
        raise SiblingStoreValidationError(
            "candidate owner must be 32 lowercase hexadecimal characters"
        )


def _require_between(name: str, value: int, minimum: int, maximum: int) -> None:
    if not minimum <= value <= maximum:
        raise SiblingStoreValidationError(
            f"{name} {value} is outside [{minimum}, {maximum}]"
        )


def _validate_observed_versions(
    observation: CandidateObservation,
    ontology_version: str,
) -> None:
    if observation.default_version != ontology_version:
        raise SiblingStoreValidationError(
            "default graph ontology version does not match the artifact pair"
        )
    if observation.stated_version != ontology_version:
        raise SiblingStoreValidationError(
            "stated graph ontology version does not match the artifact pair"
        )


def _validate_observed_graphs(observation: CandidateObservation) -> None:
    expected_graph = (
        CandidateGraph(
            graph_iri=STATED_GRAPH_IRI,
            triples=observation.stated_triples,
        ),
    )
    if observation.named_graphs == expected_graph:
        return
    if (
        len(observation.named_graphs) == 1
        and observation.named_graphs[0].graph_iri == STATED_GRAPH_IRI
    ):
        raise SiblingStoreValidationError(
            "stated graph count does not match the named-graph inventory"
        )
    raise SiblingStoreValidationError("candidate named-graph layout is invalid")


def _validate_observed_sentinels(observation: CandidateObservation) -> None:
    if not observation.has_required_restriction:
        raise SiblingStoreValidationError(
            "candidate lacks the required C6135 restriction"
        )
    if observation.default_has_stated_only_sentinel:
        raise SiblingStoreValidationError(
            "default graph unexpectedly contains the stated-only sentinel"
        )
    if not observation.stated_has_stated_only_sentinel:
        raise SiblingStoreValidationError("stated graph lacks the stated-only sentinel")


def _validate_observation(
    observation: CandidateObservation,
    ontology_version: str,
    policy: CandidateValidationPolicy,
) -> None:
    _validate_observed_versions(observation, ontology_version)
    _validate_observed_graphs(observation)
    _require_between(
        "default graph triple count",
        observation.default_triples,
        policy.min_default_triples,
        policy.max_default_triples,
    )
    _require_between(
        "stated graph triple count",
        observation.stated_triples,
        policy.min_stated_triples,
        policy.max_stated_triples,
    )
    _require_between(
        "restriction count",
        observation.restriction_count,
        policy.min_restrictions,
        policy.max_restrictions,
    )
    _validate_observed_sentinels(observation)


async def _select_int(client: CandidateQueryClient, query: str, variable: str) -> int:
    rows = await client.select_once(query, required_variables={variable})
    if len(rows) != 1 or variable not in rows[0]:
        raise StorageError(f"candidate query returned no unique {variable!r} binding")
    try:
        return int(rows[0][variable])
    except ValueError as exc:
        raise StorageError(
            f"candidate query returned non-integer {variable!r} binding"
        ) from exc


async def _select_version(
    client: CandidateQueryClient, graph: str | None
) -> str | None:
    pattern = "?ont a owl:Ontology ; owl:versionInfo ?version ."
    body = pattern if graph is None else f"GRAPH <{graph}> {{ {pattern} }}"
    rows = await client.select_once(
        f"PREFIX owl: <{_OWL_NS}> SELECT ?version WHERE {{ {body} }} LIMIT 2",
        required_variables={"version"},
    )
    if not rows:
        return None
    if len(rows) != 1 or not rows[0].get("version"):
        raise StorageError("candidate graph has no unique ontology version")
    return rows[0]["version"]


async def observe_ncit_candidate(endpoint_url: str) -> CandidateObservation:
    """Read every candidate invariant through one-attempt SPARQL queries."""
    async with OxigraphHttpClient(endpoint_url) as client:
        default_triples = await _select_int(
            client,
            "SELECT (COUNT(*) AS ?count) WHERE { ?s ?p ?o }",
            "count",
        )
        stated_triples = await _select_int(
            client,
            "SELECT (COUNT(*) AS ?count) WHERE { "
            f"GRAPH <{STATED_GRAPH_IRI}> {{ ?s ?p ?o }} }}",
            "count",
        )
        graph_rows = await client.select_once(
            "SELECT ?graph (COUNT(*) AS ?count) WHERE { "
            "GRAPH ?graph { ?s ?p ?o } } GROUP BY ?graph ORDER BY ?graph",
            required_variables={"graph", "count"},
        )
        named_graphs = tuple(
            CandidateGraph(graph_iri=row["graph"], triples=int(row["count"]))
            for row in graph_rows
        )
        restriction_count = await _select_int(
            client,
            f"PREFIX owl: <{_OWL_NS}> SELECT (COUNT(DISTINCT ?restriction) AS ?count) "
            f"WHERE {{ GRAPH <{STATED_GRAPH_IRI}> {{ ?restriction a owl:Restriction ; "
            "owl:onProperty ?property ; owl:someValuesFrom ?filler . } }",
            "count",
        )
        required = await client.ask_once(
            f"PREFIX owl: <{_OWL_NS}> PREFIX rdf: <{_RDF_NS}> ASK {{ "
            f"GRAPH <{STATED_GRAPH_IRI}> {{ <{NCIT_NS}C6135> "
            "owl:equivalentClass/owl:intersectionOf/rdf:rest*/rdf:first ?restriction . "
            f"?restriction a owl:Restriction ; owl:onProperty <{NCIT_NS}R88> ; "
            f"owl:someValuesFrom <{NCIT_NS}C27970> . }} }}"
        )
        sentinel = (
            f"<{NCIT_NS}C14806> <{_OWL_NS}deprecated> "
            '"true"^^<http://www.w3.org/2001/XMLSchema#boolean>'
        )
        default_sentinel = await client.ask_once(f"ASK {{ {sentinel} }}")
        stated_sentinel = await client.ask_once(
            f"ASK {{ GRAPH <{STATED_GRAPH_IRI}> {{ {sentinel} }} }}"
        )
        return CandidateObservation(
            default_triples=default_triples,
            stated_triples=stated_triples,
            named_graphs=named_graphs,
            default_version=await _select_version(client, None),
            stated_version=await _select_version(client, STATED_GRAPH_IRI),
            restriction_count=restriction_count,
            has_required_restriction=required,
            default_has_stated_only_sentinel=default_sentinel,
            stated_has_stated_only_sentinel=stated_sentinel,
        )


def _write_json(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        if isinstance(payload, BaseModel):
            value = payload.model_dump(mode="json")
        else:
            value = payload
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _source_identity(
    *,
    pair_manifest_identity: str,
    ontology_version: str,
    ontology_iri: str,
    stated_artifact: CandidateArtifact,
    inferred_artifact: CandidateArtifact,
    loader: LoaderIdentity,
    graph_layout: CandidateGraphLayout,
    validation_policy: CandidateValidationPolicy,
    observation: CandidateObservation,
) -> str:
    def stable_artifact(artifact: CandidateArtifact) -> dict[str, object]:
        return {
            "variant": artifact.variant,
            "size_bytes": artifact.size_bytes,
            "sha256": artifact.sha256,
            "artifact_identity": artifact.artifact_identity,
        }

    return _identity(
        {
            "schema_version": CANDIDATE_MANIFEST_SCHEMA_VERSION,
            "pair_manifest_identity": pair_manifest_identity,
            "ontology_version": ontology_version,
            "ontology_iri": ontology_iri,
            "stated_artifact": stable_artifact(stated_artifact),
            "inferred_artifact": stable_artifact(inferred_artifact),
            "loader": loader.model_dump(mode="json"),
            "graph_layout": graph_layout.model_dump(mode="json"),
            "validation_policy": validation_policy.model_dump(mode="json"),
            "observation": observation.model_dump(mode="json"),
        }
    )


def _candidate_artifact(
    record: OwlArtifactRecord,
    variant: Literal["stated", "inferred"],
) -> CandidateArtifact:
    if record.variant != variant:
        raise SiblingStoreValidationError(
            f"NCIt artifact variants are swapped: expected {variant}, "
            f"record declares {record.variant}"
        )
    return CandidateArtifact(
        variant=variant,
        path=record.file_path,
        size_bytes=record.size_bytes,
        sha256=record.owl_sha256,
        artifact_identity=record.artifact_identity,
    )


def _read_sibling_manifest(manifest_path: Path) -> NcitSiblingStoreManifest:
    try:
        return NcitSiblingStoreManifest.model_validate_json(manifest_path.read_text())
    except (OSError, ValueError) as exc:
        raise SiblingStoreValidationError(
            f"unreadable NCIt sibling manifest {manifest_path}: {exc}"
        ) from exc


def _validate_manifest_owner(
    manifest_path: Path,
    manifest: NcitSiblingStoreManifest,
) -> None:
    candidate = Path(manifest.candidate_path).resolve()
    if (
        manifest_path.name != CANDIDATE_MANIFEST_FILENAME
        or manifest_path.resolve().parent != candidate
    ):
        raise SiblingStoreValidationError(
            "candidate manifest path does not match candidate path"
        )
    try:
        marker = (candidate / OWNER_MARKER_FILENAME).read_text().strip()
    except OSError as exc:
        raise SiblingStoreValidationError(
            "candidate owner marker is missing or unreadable"
        ) from exc
    _validate_owner(manifest.owner)
    if marker != manifest.owner:
        raise SiblingStoreValidationError("candidate owner marker does not match")
    if (candidate / REJECTED_CANDIDATE_FILENAME).exists():
        raise SiblingStoreValidationError(
            "candidate is marked rejected and is never activatable"
        )


def _validate_manifest_runtime(manifest: NcitSiblingStoreManifest) -> None:
    exact_loader = LoaderIdentity(
        image=manifest.loader.image,
        image_id=manifest.loader.image_id,
        cli_version=manifest.loader.cli_version,
    )
    if (
        manifest.loader != exact_loader
        or manifest.loader.image != OXIGRAPH_IMAGE
        or manifest.loader.cli_version != OXIGRAPH_CLI_VERSION
    ):
        raise SiblingStoreValidationError(
            "candidate loader identity does not match the pinned runtime"
        )
    if manifest.graph_layout != CandidateGraphLayout():
        raise SiblingStoreValidationError("candidate graph layout is invalid")


def _manifest_source_identity(manifest: NcitSiblingStoreManifest) -> str:
    return _source_identity(
        pair_manifest_identity=manifest.pair_manifest_identity,
        ontology_version=manifest.ontology_version,
        ontology_iri=manifest.ontology_iri,
        stated_artifact=manifest.stated_artifact,
        inferred_artifact=manifest.inferred_artifact,
        loader=manifest.loader,
        graph_layout=manifest.graph_layout,
        validation_policy=manifest.validation_policy,
        observation=manifest.observation,
    )


def validate_ncit_sibling_manifest(
    manifest_path: Path,
    *,
    expected_policy: CandidateValidationPolicy | None = None,
) -> NcitSiblingStoreManifest:
    """Revalidate an owner-marked candidate proof without the source files.

    ``expected_policy`` defaults to the production bounds and the manifest's own
    ``validation_policy`` must equal it. The recorded policy is never allowed to
    define the bounds it is checked against: a forged proof could otherwise certify a
    near-empty store under bounds it supplied itself, and the source identity would
    still be self-consistent because it hashes the policy in.
    """
    manifest = _read_sibling_manifest(manifest_path)
    if manifest.schema_version != CANDIDATE_MANIFEST_SCHEMA_VERSION:
        raise SiblingStoreValidationError(
            f"unsupported NCIt sibling schema {manifest.schema_version}"
        )
    required_policy = expected_policy or CandidateValidationPolicy()
    if manifest.validation_policy != required_policy:
        raise SiblingStoreValidationError(
            "candidate validation policy does not match the expected bounds"
        )
    _validate_manifest_owner(manifest_path, manifest)
    _validate_manifest_runtime(manifest)
    if (
        manifest.stated_artifact.variant != "stated"
        or manifest.inferred_artifact.variant != "inferred"
    ):
        raise SiblingStoreValidationError("candidate artifact variants are swapped")
    _validate_observation(
        manifest.observation,
        manifest.ontology_version,
        required_policy,
    )
    if manifest.source_identity != _manifest_source_identity(manifest):
        raise SiblingStoreValidationError(
            "candidate source identity does not match its proof"
        )
    return manifest


async def build_ncit_sibling_store(
    pair_manifest_path: Path,
    *,
    active_store_path: Path,
    runtime: SiblingStoreRuntime,
    owner: str | None = None,
    policy: CandidateValidationPolicy | None = None,
) -> NcitSiblingStoreManifest:
    """Build and certify an inactive candidate beside the configured active store."""
    pair = validate_ncit_owl_pair(pair_manifest_path)
    owner = owner or uuid4().hex
    _validate_owner(owner)
    active = active_store_path.resolve()
    if not active.is_dir():
        raise SiblingStoreValidationError(
            f"configured active store is not an existing directory: {active}"
        )
    candidate = active.parent / f".{active.name}.candidate-{owner}"
    if candidate.exists():
        raise SiblingStoreValidationError(f"candidate path already exists: {candidate}")
    loader = runtime.identify_loader()
    candidate.mkdir(mode=0o700)
    (candidate / OWNER_MARKER_FILENAME).write_text(owner + "\n")
    try:
        runtime.load(pair, candidate, owner)
        observation = await runtime.observe(candidate, owner, observe_ncit_candidate)
        validation_policy = policy or CandidateValidationPolicy()
        _validate_observation(observation, pair.ontology_version, validation_policy)
        layout = CandidateGraphLayout()
        stated_artifact = _candidate_artifact(pair.stated, "stated")
        inferred_artifact = _candidate_artifact(pair.inferred, "inferred")
        manifest = NcitSiblingStoreManifest(
            owner=owner,
            candidate_path=str(candidate.resolve()),
            active_store_path=str(active),
            pair_manifest_path=str(pair_manifest_path.resolve()),
            pair_manifest_identity=pair.manifest_identity,
            ontology_version=pair.ontology_version,
            ontology_iri=pair.ontology_iri,
            stated_artifact=stated_artifact,
            inferred_artifact=inferred_artifact,
            source_identity=_source_identity(
                pair_manifest_identity=pair.manifest_identity,
                ontology_version=pair.ontology_version,
                ontology_iri=pair.ontology_iri,
                stated_artifact=stated_artifact,
                inferred_artifact=inferred_artifact,
                loader=loader,
                graph_layout=layout,
                validation_policy=validation_policy,
                observation=observation,
            ),
            loader=loader,
            graph_layout=layout,
            validation_policy=validation_policy,
            observation=observation,
        )
        _write_json(candidate / CANDIDATE_MANIFEST_FILENAME, manifest)
        return validate_ncit_sibling_manifest(
            candidate / CANDIDATE_MANIFEST_FILENAME,
            expected_policy=validation_policy,
        )
    except BaseException as exc:
        try:
            _write_json(
                candidate / REJECTED_CANDIDATE_FILENAME,
                {
                    "schema_version": CANDIDATE_MANIFEST_SCHEMA_VERSION,
                    "owner": owner,
                    "candidate_path": str(candidate.resolve()),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
        except OSError as marker_error:
            exc.add_note(
                f"Failed to persist candidate rejection marker: {marker_error}"
            )
        raise
