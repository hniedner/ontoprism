"""Reconcilable publication of immutable xref generations."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ontolib.repositories.xref.ttl_writer import render_ttl
from ontolib.repositories.xref.vocab import NCIT_UPSTREAM_XREF_GRAPH_IRI

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ontolib.repositories.xref.models import SSSOMRecord
    from ontolib.repositories.xref.store import XrefStore
    from ontolib.terminologies.sparql_http_client import SparqlHttpClient

PublicationFailpoint = Literal["after_postgres", "after_rdf", "before_pointer"]


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


def generation_identity(source: str, records: Sequence[SSSOMRecord]) -> tuple[str, str]:
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
    generation = hashlib.sha256(f"{source}\0{content}".encode()).hexdigest()
    return generation, content


async def publish_generation(
    store: XrefStore,
    client: SparqlHttpClient,
    *,
    source: str,
    run_id: str,
    records: Sequence[SSSOMRecord],
    failpoint: PublicationFailpoint | None = None,
) -> PublicationResult:
    """Prepare, materialize, and atomically activate one source generation."""
    generation_id, content_sha256 = generation_identity(source, records)
    graph_iri = generation_graph_iri(source, generation_id)
    async with store.publication_lock(source):
        changed = await store.prepare_generation(
            source=source,
            generation_id=generation_id,
            content_sha256=content_sha256,
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
    return PublicationResult(
        generation_id=generation_id,
        graph_iri=graph_iri,
        changed=changed,
    )
