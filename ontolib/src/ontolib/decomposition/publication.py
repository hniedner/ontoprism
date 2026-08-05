"""Validated, reconcilable publication of decomposition artifacts and graphs."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import rdflib
from pydantic import ValidationError
from rdflib import Literal, URIRef

from ontolib.decomposition import vocab
from ontolib.decomposition.provenance import RunStateError
from ontolib.decomposition.provenance_models import (
    PersistedRunMetrics,
    PublicationMarkerSnapshot,
)
from ontolib.terminologies.namespaces import NCIT_NS

if TYPE_CHECKING:
    from collections.abc import Awaitable, Collection, Mapping, Sequence
    from contextlib import AbstractAsyncContextManager
    from pathlib import Path
    from typing import BinaryIO

    from rdflib.term import Node

    from ontolib.decomposition.provenance_models import RunSummary


class PublicationGraphClient(Protocol):
    """Graph operations required by the publication protocol."""

    def select_once(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> Awaitable[Sequence[Mapping[str, str | None]]]: ...

    async def load(
        self,
        data: bytes | BinaryIO,
        *,
        content_type: str,
        graph_iri: str | None = None,
        replace: bool = True,
    ) -> None: ...

    async def update(self, update: str) -> None: ...


class PublicationProvenance(Protocol):
    """Persistence operations required by the publication protocol."""

    def publication_lock(self) -> AbstractAsyncContextManager[None]: ...

    async def get_run(self, run_id: str) -> RunSummary | None: ...

    async def begin_publication(
        self,
        run_id: str,
        *,
        representation_identity: str,
        artifact_path: str,
        built_at: datetime,
        predecessor: PublicationMarkerSnapshot | None,
    ) -> None: ...

    async def record_publication_failure(
        self,
        run_id: str,
        error: BaseException,
    ) -> None: ...

    async def finish_run(
        self,
        run_id: str,
        *,
        source_identity: str,
        metrics: dict[str, object],
        representation_identity: str | None = None,
    ) -> bool: ...


class PublicationValidationError(RuntimeError):
    """A rendered artifact or persisted marker cannot be trusted for publication."""


class PublicationPreflightError(PublicationValidationError):
    """Publication failed before its retryable intent was confirmed journaled."""


class PublicationFinalizationError(RuntimeError):
    """Publication completed, but releasing its PostgreSQL advisory lock failed."""


class PublicationMarker(PublicationMarkerSnapshot):
    """Identity committed inside the public named graph."""

    @property
    def built_at_lexical(self) -> str:
        """Canonical UTC lexical form used in the graph marker."""
        return self.built_at.astimezone(UTC).isoformat().replace("+00:00", "Z")


_EXPECTED_MARKER_PREDICATES = {
    str(rdflib.RDF.type),
    vocab.PUBLICATION_RUN,
    vocab.PUBLICATION_SOURCE_IDENTITY,
    vocab.PUBLICATION_REPRESENTATION_IDENTITY,
    vocab.PUBLICATION_BUILT_AT,
}


def _concept_code(subject: Node) -> str:
    raw = str(subject)
    if not isinstance(subject, URIRef) or not raw.startswith(NCIT_NS):
        raise PublicationValidationError(
            "representation-status subject is not an NCIt concept IRI"
        )
    code = raw.removeprefix(NCIT_NS)
    if not code:
        raise PublicationValidationError(
            "representation-status subject has an empty NCIt concept code"
        )
    return code


def _read_artifact_graph(artifact: Path) -> tuple[bytes, rdflib.Graph]:
    try:
        payload = artifact.read_bytes()
    except OSError as exc:
        raise PublicationValidationError(
            f"decomposition artifact could not be read: {exc}"
        ) from exc
    graph = rdflib.Graph()
    try:
        graph.parse(data=payload, format="turtle")
    except Exception as exc:
        raise PublicationValidationError(
            f"decomposition artifact is not valid Turtle: {exc}"
        ) from exc
    return payload, graph


def _validated_concept_subjects(
    graph: rdflib.Graph,
    expected_codes: Collection[str],
) -> set[Node]:
    representation = URIRef(vocab.REPRESENTATION_STATUS)
    legacy = Literal(vocab.LEGACY_PRECOORDINATED)
    subjects = set(graph.subjects(representation, legacy))
    actual_codes = {_concept_code(subject) for subject in subjects}
    if actual_codes != set(expected_codes):
        raise PublicationValidationError(
            "decomposition artifact does not contain the expected concept set"
        )
    return subjects


def _validate_run_membership(
    graph: rdflib.Graph,
    subjects: set[Node],
    run_id: str,
) -> None:
    decomposed_by = URIRef(vocab.DECOMPOSED_BY)
    expected_run = Literal(run_id)
    for subject in subjects:
        if set(graph.objects(subject, decomposed_by)) != {expected_run}:
            raise PublicationValidationError(
                "decomposition artifact does not bind every concept to the "
                "expected run identifier"
            )
    extra_run_subjects = set(graph.subjects(decomposed_by, None)) - subjects
    if extra_run_subjects:
        raise PublicationValidationError(
            "decomposition artifact binds an unexpected subject to a run identifier"
        )


def validate_artifact(
    artifact: Path,
    *,
    expected_codes: Collection[str],
    run_id: str,
) -> str:
    """Validate exact concept/run membership and return the byte identity."""
    identity, _payload = _validated_artifact_payload(
        artifact,
        expected_codes=expected_codes,
        run_id=run_id,
    )
    return identity


def _validated_artifact_payload(
    artifact: Path,
    *,
    expected_codes: Collection[str],
    run_id: str,
) -> tuple[str, bytes]:
    payload, graph = _read_artifact_graph(artifact)
    if any(graph.triples((URIRef(vocab.PUBLICATION_MARKER), None, None))):
        raise PublicationValidationError(
            "decomposition artifact contains the reserved publication marker"
        )
    subjects = _validated_concept_subjects(graph, expected_codes)
    _validate_run_membership(graph, subjects, run_id)
    return hashlib.sha256(payload).hexdigest(), payload


def staging_graph_iri(run_id: str) -> str:
    """Return an injection-safe, unique graph IRI for one run."""
    digest = hashlib.sha256(run_id.encode()).hexdigest()
    return f"{vocab.DECOMPOSED_GRAPH_IRI}/staging/{digest}"


def build_replacement_update(marker: PublicationMarker, staging_graph: str) -> str:
    """Build the one-transaction public replacement and marker commit."""
    if staging_graph != staging_graph_iri(marker.run_id):
        raise ValueError("staging graph is not the marker's run-scoped staging graph")
    public = vocab.DECOMPOSED_GRAPH_IRI
    marker_subject = vocab.PUBLICATION_MARKER
    return f"""
CLEAR GRAPH <{public}>;
ADD GRAPH <{staging_graph}> TO GRAPH <{public}>;
DROP GRAPH <{staging_graph}>;
INSERT DATA {{
  GRAPH <{public}> {{
    <{marker_subject}> a <{vocab.PUBLICATION_CLASS}> ;
      <{vocab.PUBLICATION_RUN}> "{marker.run_id}" ;
      <{vocab.PUBLICATION_SOURCE_IDENTITY}> "{marker.source_identity}" ;
      <{vocab.PUBLICATION_REPRESENTATION_IDENTITY}>
        "{marker.representation_identity}" ;
      <{vocab.PUBLICATION_BUILT_AT}>
        "{marker.built_at_lexical}"^^<http://www.w3.org/2001/XMLSchema#dateTime> .
  }}
}}
""".strip()


def _marker_query() -> str:
    return f"""
SELECT ?predicate ?value WHERE {{
  GRAPH <{vocab.DECOMPOSED_GRAPH_IRI}> {{
    <{vocab.PUBLICATION_MARKER}> ?predicate ?value
  }}
}}
""".strip()


def _require_complete_marker_shape(
    rows: Sequence[Mapping[str, str | None]],
) -> None:
    if len(rows) != len(_EXPECTED_MARKER_PREDICATES):
        raise PublicationValidationError(
            "public decomposition graph has a partial or ambiguous publication marker"
        )
    predicates = [row.get("predicate") for row in rows]
    if any(predicate is None for predicate in predicates):
        raise PublicationValidationError(
            "public decomposition graph has a partial or ambiguous publication marker"
        )
    if set(predicates) != _EXPECTED_MARKER_PREDICATES:
        raise PublicationValidationError(
            "public decomposition graph has a partial or ambiguous publication marker"
        )


def _bound_marker_values(
    rows: Sequence[Mapping[str, str | None]],
) -> dict[str, str]:
    values = {
        str(row["predicate"]): row.get("value")
        for row in rows
        if row["predicate"] is not None
    }
    if any(value is None for value in values.values()):
        raise PublicationValidationError(
            "public decomposition graph has an unbound publication marker value"
        )
    return {predicate: str(value) for predicate, value in values.items()}


def _parse_marker_values(values: Mapping[str, str]) -> PublicationMarker:
    try:
        return PublicationMarker(
            run_id=values[vocab.PUBLICATION_RUN],
            source_identity=values[vocab.PUBLICATION_SOURCE_IDENTITY],
            representation_identity=values[vocab.PUBLICATION_REPRESENTATION_IDENTITY],
            built_at=datetime.fromisoformat(
                values[vocab.PUBLICATION_BUILT_AT].replace("Z", "+00:00")
            ),
        )
    except (ValueError, ValidationError) as exc:
        raise PublicationValidationError(
            "public decomposition graph has an invalid publication marker"
        ) from exc


async def read_publication_marker(
    client: PublicationGraphClient,
) -> PublicationMarker | None:
    """Read exactly one complete marker, rejecting partial or ambiguous state."""
    rows = await client.select_once(
        _marker_query(),
        required_variables={"predicate", "value"},
    )
    if not rows:
        return None
    _require_complete_marker_shape(rows)
    values = _bound_marker_values(rows)
    if values[str(rdflib.RDF.type)] != vocab.PUBLICATION_CLASS:
        raise PublicationValidationError(
            "public decomposition graph has the wrong publication marker type"
        )
    return _parse_marker_values(values)


def _cleanup_failed_publication_file(
    file_descriptor: int,
    stream: BinaryIO | None,
    temporary: Path,
    original: BaseException,
) -> None:
    try:
        if stream is None:
            os.close(file_descriptor)
        elif not stream.closed:
            stream.close()
    except BaseException as close_error:
        original.add_note(
            "Closing the failed publication file also failed: "
            f"{type(close_error).__name__}: {close_error}"
        )
    try:
        if temporary.exists():
            temporary.unlink()
    except BaseException as cleanup_error:
        original.add_note(
            "Removing the failed publication file also failed: "
            f"{type(cleanup_error).__name__}: {cleanup_error}"
        )


def _fsync_publication_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(directory, flags)
    try:
        os.fsync(directory_fd)
    except BaseException as original:
        try:
            os.close(directory_fd)
        except BaseException as close_error:
            original.add_note(
                "Closing the publication directory also failed: "
                f"{type(close_error).__name__}: {close_error}"
            )
        raise
    else:
        os.close(directory_fd)


def _durable_write(payload: bytes, destination: Path) -> None:
    """Atomically publish sealed bytes and fsync the containing directory entry."""
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
    )
    temporary = destination.parent / os.path.basename(temporary_name)
    stream = None
    try:
        stream = os.fdopen(file_descriptor, "wb")
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
        stream.close()
        os.replace(temporary, destination)
    except BaseException as original:
        _cleanup_failed_publication_file(
            file_descriptor,
            stream,
            temporary,
            original,
        )
        raise
    _fsync_publication_directory(destination.parent)


async def _record_failure_without_masking(
    provenance: PublicationProvenance,
    run_id: str,
    original: BaseException,
) -> None:
    task = asyncio.create_task(provenance.record_publication_failure(run_id, original))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError as cancellation:
        try:
            await task
        except BaseException as journal_error:
            cancellation.add_note(
                "Recording the publication failure during cancellation also failed: "
                f"{type(journal_error).__name__}: {journal_error}"
            )
        raise cancellation


async def _replace_graph(
    client: PublicationGraphClient,
    payload: bytes,
    marker: PublicationMarker,
    *,
    predecessor: PublicationMarker | None,
) -> None:
    current = await read_publication_marker(client)
    if current not in (marker, predecessor):
        raise PublicationValidationError(
            "public decomposition marker is neither this publication intent nor "
            "its persisted predecessor"
        )
    staging_graph = staging_graph_iri(marker.run_id)
    await client.load(
        payload,
        content_type="text/turtle",
        graph_iri=staging_graph,
        replace=True,
    )
    try:
        await client.update(build_replacement_update(marker, staging_graph))
    except asyncio.CancelledError:
        raise
    except BaseException as original:
        # A matching marker existed before this update, so it cannot prove a replay
        # committed. Fail closed and let the next retry reapply the sealed graph.
        if current != marker and await _replacement_committed(client, marker, original):
            return
        raise


async def _replacement_committed(
    client: PublicationGraphClient,
    marker: PublicationMarker,
    original: BaseException,
) -> bool:
    """Resolve an ambiguous transport failure without masking cancellation."""
    try:
        return await read_publication_marker(client) == marker
    except asyncio.CancelledError:
        raise
    except BaseException as reconciliation_error:
        original.add_note(
            "Reading the publication marker during reconciliation also failed: "
            f"{type(reconciliation_error).__name__}: {reconciliation_error}"
        )
        raise original from reconciliation_error


def _marker_for_run(
    summary: RunSummary,
    *,
    run_id: str,
    source_identity: str,
    representation_identity: str,
) -> tuple[PublicationMarker, bool]:
    retrying = summary.publication_state in {"publishing", "failed"}
    built_at = summary.publication_built_at if retrying else datetime.now(UTC)
    if built_at is None:
        raise PublicationValidationError(
            "persisted publication intent has no build timestamp"
        )
    return (
        PublicationMarker(
            run_id=run_id,
            source_identity=source_identity,
            representation_identity=representation_identity,
            built_at=built_at,
        ),
        retrying,
    )


async def _publish_started_artifact(
    *,
    marker: PublicationMarker,
    artifact: Path,
    payload: bytes,
    destination: Path,
    metrics: dict[str, object],
    load_to_store: bool,
    predecessor: PublicationMarker | None,
    client: PublicationGraphClient,
    provenance: PublicationProvenance,
) -> None:
    try:
        if load_to_store:
            await _replace_graph(client, payload, marker, predecessor=predecessor)
        _durable_write(payload, destination)
        artifact.unlink()
        finished = await provenance.finish_run(
            marker.run_id,
            source_identity=marker.source_identity,
            metrics=metrics,
            representation_identity=marker.representation_identity,
        )
        if not finished:
            raise RunStateError(
                f"finish_run found no decomp_run row for run_id={marker.run_id!r}"
            )
    except BaseException as original:
        try:
            await _record_failure_without_masking(
                provenance,
                marker.run_id,
                original,
            )
        except asyncio.CancelledError as cancellation:
            cancellation.add_note(
                "Publication failed while cancellation interrupted failure journaling: "
                f"{type(original).__name__}: {original}"
            )
            raise cancellation from original
        except BaseException as failure_error:
            original.add_note(
                "Recording the publication failure also failed: "
                f"{type(failure_error).__name__}: {failure_error}"
            )
        raise


async def _prepare_publication_intent(
    *,
    run_id: str,
    source_identity: str,
    representation_identity: str,
    destination: Path,
    load_to_store: bool,
    client: PublicationGraphClient,
    provenance: PublicationProvenance,
) -> tuple[PublicationMarker, PublicationMarker | None]:
    summary = await provenance.get_run(run_id)
    if summary is None:
        raise RunStateError(f"decomposition run {run_id!r} does not exist")
    if summary.source_identity != source_identity:
        raise RunStateError(
            "publication source identity does not match the persisted run"
        )
    marker, retrying = _marker_for_run(
        summary,
        run_id=run_id,
        source_identity=source_identity,
        representation_identity=representation_identity,
    )
    if retrying:
        if not summary.publication_predecessor_captured:
            raise RunStateError(
                "publication intent has no captured predecessor and cannot be retried "
                "safely"
            )
        predecessor = (
            PublicationMarker.model_validate(
                summary.publication_predecessor.model_dump()
            )
            if summary.publication_predecessor is not None
            else None
        )
    else:
        predecessor = await read_publication_marker(client) if load_to_store else None
    return marker, predecessor


async def _begin_matches_persisted_intent(
    *,
    run_id: str,
    source_identity: str,
    representation_identity: str,
    destination: Path,
    marker: PublicationMarker,
    predecessor: PublicationMarker | None,
    provenance: PublicationProvenance,
    original: Exception,
) -> bool:
    try:
        committed = await provenance.get_run(run_id)
    except Exception as reconciliation_error:
        original.add_note(
            "Reading the publication intent during begin reconciliation also "
            f"failed: {type(reconciliation_error).__name__}: {reconciliation_error}"
        )
        raise PublicationPreflightError(str(original)) from original
    predecessor_snapshot = (
        predecessor.model_dump(mode="json") if predecessor is not None else None
    )
    return committed is not None and (
        committed.status,
        committed.source_identity,
        committed.publication_state,
        committed.representation_identity,
        committed.publication_artifact_path,
        committed.publication_built_at,
        committed.publication_predecessor_captured,
        (
            committed.publication_predecessor.model_dump(mode="json")
            if committed.publication_predecessor is not None
            else None
        ),
    ) == (
        "running",
        source_identity,
        "publishing",
        representation_identity,
        str(destination),
        marker.built_at,
        True,
        predecessor_snapshot,
    )


async def _begin_publication(
    *,
    run_id: str,
    source_identity: str,
    representation_identity: str,
    destination: Path,
    load_to_store: bool,
    client: PublicationGraphClient,
    provenance: PublicationProvenance,
) -> tuple[PublicationMarker, PublicationMarker | None]:
    try:
        marker, predecessor = await _prepare_publication_intent(
            run_id=run_id,
            source_identity=source_identity,
            representation_identity=representation_identity,
            destination=destination,
            load_to_store=load_to_store,
            client=client,
            provenance=provenance,
        )
    except Exception as exc:
        raise PublicationPreflightError(str(exc)) from exc

    try:
        await provenance.begin_publication(
            run_id,
            representation_identity=representation_identity,
            artifact_path=str(destination),
            built_at=marker.built_at,
            predecessor=predecessor,
        )
    except Exception as original:
        if not await _begin_matches_persisted_intent(
            run_id=run_id,
            source_identity=source_identity,
            representation_identity=representation_identity,
            destination=destination,
            marker=marker,
            predecessor=predecessor,
            provenance=provenance,
            original=original,
        ):
            raise PublicationPreflightError(str(original)) from original
    return marker, predecessor


async def publish_artifact(
    *,
    run_id: str,
    source_identity: str,
    artifact: Path,
    destination: Path,
    expected_codes: Collection[str],
    metrics: dict[str, object],
    load_to_store: bool,
    client: PublicationGraphClient,
    provenance: PublicationProvenance,
) -> PublicationMarker:
    """Publish a complete file/graph and only then complete its run journal."""
    try:
        validated_metrics = PersistedRunMetrics.model_validate(metrics).model_dump(
            exclude_unset=True
        )
        representation_identity, payload = _validated_artifact_payload(
            artifact,
            expected_codes=expected_codes,
            run_id=run_id,
        )
    except (PublicationValidationError, ValidationError) as exc:
        raise PublicationPreflightError(str(exc)) from exc
    journaled = False
    completed = False
    try:
        async with provenance.publication_lock():
            marker, predecessor = await _begin_publication(
                run_id=run_id,
                source_identity=source_identity,
                representation_identity=representation_identity,
                destination=destination,
                load_to_store=load_to_store,
                client=client,
                provenance=provenance,
            )
            journaled = True
            await _publish_started_artifact(
                marker=marker,
                artifact=artifact,
                payload=payload,
                destination=destination,
                metrics=validated_metrics,
                load_to_store=load_to_store,
                predecessor=predecessor,
                client=client,
                provenance=provenance,
            )
            completed = True
    except Exception as exc:
        if completed:
            raise PublicationFinalizationError(
                "publication completed but lock release failed"
            ) from exc
        if not journaled and not isinstance(exc, PublicationPreflightError):
            raise PublicationPreflightError(str(exc)) from exc
        raise
    return marker
