"""Publish Uberon's NCIt cross-references for reciprocal inspection."""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from ontolib.repositories.xref.candidate_ingest import _iri_to_curie, fetch_uberon_xrefs
from ontolib.repositories.xref.models import SSSOMRecord
from ontolib.repositories.xref.publication import publish_generation
from ontolib.repositories.xref.vocab import CLOSE_MATCH, DATABASE_CROSS_REFERENCE
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI

if TYPE_CHECKING:
    from ontolib.repositories.xref.store import XrefStore
    from ontolib.terminologies.sparql_http_client import SparqlHttpClient

PUBLISHER_XREF_SOURCE = "uberon-publisher-xref"
_NCIT_CODE = re.compile(r"C[0-9]+")
EXPECTED_SOURCE_CLASSES = 2577
EXPECTED_ASSERTIONS = 2618


class PublisherXrefSourceError(ValueError):
    """A publisher assertion cannot be represented without changing its meaning."""


class UnresolvedPublisherXref(BaseModel):
    model_config = ConfigDict(frozen=True)
    uberon_id: str
    ncit_id: str
    reason: Literal["ncit-target-not-found"] = "ncit-target-not-found"


class PublisherXrefReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    uberon_release: str
    ncit_release: str
    source_class_count: int
    assertion_count: int
    published_assertion_count: int
    unresolved: list[UnresolvedPublisherXref]
    count_delta: Literal["unchanged", "increased", "decreased"]
    source_class_delta: int
    assertion_delta: int


def build_ncit_target_validation_query(codes: set[str]) -> str:
    """Build one bounded activated-store query for all asserted NCIt targets."""
    iris = " ".join(f"<{NCIT_NS}{code}>" for code in sorted(codes))
    return f"""\
SELECT ?code WHERE {{
  VALUES ?concept {{ {iris} }}
  GRAPH <{STATED_GRAPH_IRI}> {{ ?concept ?predicate ?value }}
  BIND(REPLACE(STR(?concept), ".*#", "") AS ?code)
}} GROUP BY ?code
"""


def _parse_assertions(rows: list[dict[str, str]]) -> list[tuple[str, str]]:
    assertions: list[tuple[str, str]] = []
    for row in rows:
        upstream = row["upstream"]
        xref = row["xref"]
        curie = _iri_to_curie(upstream)
        if curie is None or not curie.startswith(("UBERON:", "CL:")):
            raise PublisherXrefSourceError(f"unsupported publisher concept: {upstream}")
        code = xref.removeprefix("NCIT:")
        if not _NCIT_CODE.fullmatch(code):
            raise PublisherXrefSourceError(f"malformed NCIt target: {xref}")
        assertions.append((curie, code))
    return assertions


def _record(
    upstream: str, code: str, *, ncit_version: str, uberon_version: str
) -> SSSOMRecord:
    return SSSOMRecord(
        subject_id=upstream,
        subject_system="uberon-cl",
        predicate_id=CLOSE_MATCH,
        object_id=code,
        object_system="ncit",
        mapping_justification=DATABASE_CROSS_REFERENCE,
        confidence=0.9,
        subject_source_version=uberon_version,
        object_source_version=ncit_version,
        author="uberon-publisher-xref",
    )


def _report(
    assertions: list[tuple[str, str]],
    records: list[SSSOMRecord],
    resolved: set[str],
    *,
    ncit_version: str,
    uberon_version: str,
) -> PublisherXrefReport:
    source_class_count = len({upstream for upstream, _ in assertions})
    assertion_count = len(assertions)
    deltas = (
        source_class_count - EXPECTED_SOURCE_CLASSES,
        assertion_count - EXPECTED_ASSERTIONS,
    )
    count_delta: Literal["unchanged", "increased", "decreased"] = "unchanged"
    if deltas != (0, 0):
        count_delta = "increased" if deltas[1] > 0 else "decreased"
    return PublisherXrefReport(
        uberon_release=uberon_version,
        ncit_release=ncit_version,
        source_class_count=source_class_count,
        assertion_count=assertion_count,
        published_assertion_count=len(records),
        unresolved=[
            UnresolvedPublisherXref(uberon_id=upstream, ncit_id=code)
            for upstream, code in assertions
            if code not in resolved
        ],
        count_delta=count_delta,
        source_class_delta=deltas[0],
        assertion_delta=deltas[1],
    )


async def publish_uberon_xrefs(
    store: XrefStore,
    ncit_client: SparqlHttpClient,
    uberon_client: SparqlHttpClient,
    *,
    ncit_version: str,
    uberon_version: str,
    run_id: str | None = None,
) -> PublisherXrefReport:
    """Validate and publish every resolvable Uberon-authored NCIt assertion."""
    rid = run_id or uuid.uuid4().hex
    assertions = _parse_assertions(await fetch_uberon_xrefs(uberon_client))
    target_codes = {code for _, code in assertions}
    resolved = {
        str(row["code"])
        for row in await ncit_client.select(
            build_ncit_target_validation_query(target_codes)
        )
        if row.get("code")
    }
    records = [
        _record(
            upstream,
            code,
            ncit_version=ncit_version,
            uberon_version=uberon_version,
        )
        for upstream, code in assertions
        if code in resolved
    ]
    report = _report(
        assertions,
        records,
        resolved,
        ncit_version=ncit_version,
        uberon_version=uberon_version,
    )
    await store.upsert_run(
        run_id=rid,
        source=PUBLISHER_XREF_SOURCE,
        ncit_version=ncit_version,
        source_version=uberon_version,
    )
    await publish_generation(
        store,
        ncit_client,
        source=PUBLISHER_XREF_SOURCE,
        run_id=rid,
        records=records,
    )
    await store.update_run_metrics(rid, report.model_dump(mode="json"))
    return report
