"""Reconcilable publication of immutable xref generations."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ontolib.repositories.xref.ttl_writer import render_ttl
from ontolib.repositories.xref.vocab import NCIT_UPSTREAM_XREF_GRAPH_IRI

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ontolib.repositories.xref.models import GenerationSourceMetadata, SSSOMRecord
    from ontolib.repositories.xref.store import XrefStore
    from ontolib.terminologies.sparql_http_client import SparqlHttpClient

PublicationFailpoint = Literal["after_postgres", "after_rdf", "before_pointer"]
_ACTIVE_PREDICATE = f"{NCIT_UPSTREAM_XREF_GRAPH_IRI}/activeGeneration"


class XrefPublicationError(RuntimeError):
    """A generation cannot be safely reconciled or activated."""


@dataclass(frozen=True)
class PublicationResult:
    generation_id: str
    graph_iri: str
    changed: bool


def generation_graph_iri(source: str, generation_id: str) -> str:
    """Return the immutable graph IRI for one source generation."""
    if not re.fullmatch(r"[0-9a-f]{64}", generation_id):
        raise ValueError("generation_id must be a lowercase SHA-256")
    component = re.sub(r"[^a-z0-9]+", "-", source.casefold()).strip("-")
    if not component:
        raise ValueError("source must contain an alphanumeric character")
    return f"{NCIT_UPSTREAM_XREF_GRAPH_IRI}/generation/{component}/{generation_id}"


def active_graph_iri(source: str) -> str:
    """Return the stable RDF pointer graph for one source."""
    component = re.sub(r"[^a-z0-9]+", "-", source.casefold()).strip("-")
    if not component:
        raise ValueError("source must contain an alphanumeric character")
    return f"{NCIT_UPSTREAM_XREF_GRAPH_IRI}/active/{component}"


def _active_pointer(source: str, generation_id: str | None) -> bytes:
    if generation_id is None:
        return b""
    graph = generation_graph_iri(source, generation_id)
    subject = active_graph_iri(source)
    return f"<{subject}> <{_ACTIVE_PREDICATE}> <{graph}> .\n".encode()


async def rdf_active_generation(client: SparqlHttpClient, source: str) -> str | None:
    subject = active_graph_iri(source)
    rows = await client.select(
        f"SELECT ?source ?predicate ?g WHERE {{ GRAPH <{subject}> "
        "{ ?source ?predicate ?g } }"
    )
    if not rows:
        return None
    if len(rows) != 1:
        raise XrefPublicationError("RDF active pointer is ambiguous")
    row = rows[0]
    if set(row) != {"source", "predicate", "g"} or (
        row.get("source"),
        row.get("predicate"),
    ) != (subject, _ACTIVE_PREDICATE):
        raise XrefPublicationError("RDF active pointer is invalid")
    graph = row["g"]
    prefix = generation_graph_iri(source, "0" * 64).rsplit("/", 1)[0] + "/"
    generation_id = graph.removeprefix(prefix)
    if (
        graph != prefix + generation_id
        or re.fullmatch(r"[0-9a-f]{64}", generation_id) is None
    ):
        raise XrefPublicationError("RDF active pointer is invalid")
    return generation_id


async def _reconcile_pointers(
    store: XrefStore, client: SparqlHttpClient, source: str
) -> str | None:
    rdf = await rdf_active_generation(client, source)
    postgres = await store.active_generation(source)
    if rdf != postgres:
        await store.set_active_generation(source, rdf, _publication_locked=True)
    return rdf


async def _write_pointer(
    store: XrefStore,
    client: SparqlHttpClient,
    source: str,
    generation_id: str,
) -> None:
    try:
        await client.load(
            _active_pointer(source, generation_id),
            content_type="text/turtle",
            graph_iri=active_graph_iri(source),
            replace=True,
        )
    except asyncio.CancelledError as cancellation:
        task = asyncio.create_task(_reconcile_pointers(store, client, source))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
        except BaseException as reconciliation:
            cancellation.add_note(
                "Xref pointer reconciliation after cancellation failed: "
                f"{type(reconciliation).__name__}: {reconciliation}"
            )
        raise cancellation
    except Exception as original:
        try:
            observed = await rdf_active_generation(client, source)
            await store.set_active_generation(
                source, observed, _publication_locked=True
            )
        except Exception as reconciliation:
            raise original from reconciliation
        if observed != generation_id:
            raise


def generation_identity(
    source: str,
    records: Sequence[SSSOMRecord],
    source_metadata: GenerationSourceMetadata,
) -> tuple[str, str]:
    """Return deterministic generation and exact-content identities."""
    rows = [
        {
            "subject": [r.subject.system, r.subject.version, r.subject.identifier],
            "predicate": r.predicate_id,
            "object": [r.object.system, r.object.version, r.object.identifier],
            "justification": r.mapping_justification,
            "confidence": r.confidence,
            "lifecycle": r.lifecycle_state,
            "review": r.review_status,
            "author": r.author,
            "evidence": [e.as_dict() for e in r.evidence],
        }
        for r in records
    ]
    ordered = sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))
    payload = json.dumps(
        ordered,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    content = hashlib.sha256(payload).hexdigest()
    metadata = source_metadata.model_dump_json(exclude_none=True)
    generation = hashlib.sha256(f"{source}\0{content}\0{metadata}".encode()).hexdigest()
    return generation, content


async def publish_generation(
    store: XrefStore,
    client: SparqlHttpClient,
    *,
    source: str,
    run_id: str,
    records: Sequence[SSSOMRecord],
    source_metadata: GenerationSourceMetadata,
    failpoint: PublicationFailpoint | None = None,
) -> PublicationResult:
    """Prepare, materialize, then reconcile the ordered cross-store activation."""
    generation_id, content_sha256 = generation_identity(
        source, records, source_metadata
    )
    graph_iri = generation_graph_iri(source, generation_id)
    async with store.publication_lock(source):
        await _reconcile_pointers(store, client, source)
        changed = await store.prepare_generation(
            source=source,
            generation_id=generation_id,
            content_sha256=content_sha256,
            source_metadata=source_metadata,
            graph_iri=graph_iri,
            run_id=run_id,
            records=records,
            _publication_locked=True,
        )
        if failpoint == "after_postgres":
            raise XrefPublicationError("injected failure after PostgreSQL write")
        ttl = render_ttl(records) if records else b""
        await client.load(
            ttl.encode() if isinstance(ttl, str) else ttl,
            content_type="text/turtle",
            graph_iri=graph_iri,
            replace=True,
        )
        if failpoint == "after_rdf":
            raise XrefPublicationError("injected failure after RDF write")
        if failpoint == "before_pointer":
            raise XrefPublicationError("injected failure before pointer switch")
        await store.activate_generation(source, generation_id, _publication_locked=True)
        await _write_pointer(store, client, source, generation_id)
    return PublicationResult(
        generation_id=generation_id,
        graph_iri=graph_iri,
        changed=changed,
    )


async def rollback_generation(
    store: XrefStore, client: SparqlHttpClient, source: str
) -> str:
    """Rollback both active pointers, compensating PostgreSQL on RDF failure."""
    async with store.publication_lock(source):
        await _reconcile_pointers(store, client, source)
        predecessor = await store.rollback(source, _publication_locked=True)
        await _write_pointer(store, client, source, predecessor)
        return predecessor
