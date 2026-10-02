"""Publish Uberon's NCIt cross-references for reciprocal inspection."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from ontolib.repositories.xref.candidate_ingest import _iri_to_curie, fetch_uberon_xrefs
from ontolib.repositories.xref.models import (
    SSSOMRecord,
    UberonPublisherGenerationMetadata,
)
from ontolib.repositories.xref.publication import fail_run_on_error, publish_generation
from ontolib.repositories.xref.source_versions import (
    MappingSourceVersionError,
    MappingSourceVersions,
    read_mapping_source_versions,
)
from ontolib.repositories.xref.vocab import (
    CLOSE_MATCH,
    DATABASE_CROSS_REFERENCE,
    UBERON_CL_CURIE_PREFIXES,
)
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI

if TYPE_CHECKING:
    from ontolib.repositories.xref.store import XrefStore
    from ontolib.terminologies.sparql_http_client import SparqlHttpClient

PUBLISHER_XREF_SOURCE = "uberon-publisher-xref"
_NCIT_CODE = re.compile(r"C[0-9]+")
EXPECTED_SOURCE_CLASSES = 2542
EXPECTED_ASSERTIONS = 2583


class PublisherXrefSourceError(ValueError):
    """A publisher assertion cannot be represented without changing its meaning."""


class CountDelta(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    expected: int
    observed: int
    delta: int
    classification: Literal["unchanged", "increased", "decreased"]


class PublisherXrefCountDriftError(ValueError):
    """The publisher xref inventory differs from the certified expectation."""

    def __init__(
        self, source_class_count: CountDelta, assertion_count: CountDelta
    ) -> None:
        super().__init__("Uberon publisher class/assertion count drift")
        self.source_class_count = source_class_count
        self.assertion_count = assertion_count


class UnresolvedPublisherXref(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    uberon_id: str
    ncit_id: str
    reason: Literal["ncit-target-not-found"] = "ncit-target-not-found"


class PublisherXrefReport(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    uberon_release: str
    ncit_release: str
    uberon_assertion_identity: str
    ncit_target_identity: str
    source_class_count: CountDelta
    assertion_count: CountDelta
    published_assertion_count: int
    unresolved: list[UnresolvedPublisherXref]


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
        if curie is None or not curie.startswith(UBERON_CL_CURIE_PREFIXES):
            raise PublisherXrefSourceError(f"unsupported publisher concept: {upstream}")
        code = xref.removeprefix("NCIT:")
        if not _NCIT_CODE.fullmatch(code):
            raise PublisherXrefSourceError(f"malformed NCIt target: {xref}")
        assertions.append((curie, code))
    return sorted(assertions)


def _rows_identity(rows: object) -> str:
    return hashlib.sha256(
        json.dumps(rows, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _record(
    upstream: str, code: str, *, versions: MappingSourceVersions
) -> SSSOMRecord:
    return SSSOMRecord(
        subject_id=upstream,
        subject_system="uberon-cl",
        predicate_id=CLOSE_MATCH,
        object_id=code,
        object_system="ncit",
        mapping_justification=DATABASE_CROSS_REFERENCE,
        confidence=0.9,
        subject_source_version=versions.upstream_for(upstream),
        object_source_version=versions.ncit,
        author="uberon-publisher-xref",
    )


def _report(
    assertions: list[tuple[str, str]],
    records: list[SSSOMRecord],
    resolved: set[str],
    *,
    ncit_version: str,
    uberon_version: str,
    source_class_count: CountDelta,
    assertion_count: CountDelta,
    uberon_assertion_identity: str,
    ncit_target_identity: str,
) -> PublisherXrefReport:
    return PublisherXrefReport(
        uberon_release=uberon_version,
        ncit_release=ncit_version,
        uberon_assertion_identity=uberon_assertion_identity,
        ncit_target_identity=ncit_target_identity,
        source_class_count=source_class_count,
        assertion_count=assertion_count,
        published_assertion_count=len(records),
        unresolved=[
            UnresolvedPublisherXref(uberon_id=upstream, ncit_id=code)
            for upstream, code in assertions
            if code not in resolved
        ],
    )


def _delta(expected: int, observed: int) -> CountDelta:
    classification: Literal["unchanged", "increased", "decreased"] = "unchanged"
    if observed != expected:
        classification = "increased" if observed > expected else "decreased"
    return CountDelta(
        expected=expected,
        observed=observed,
        delta=observed - expected,
        classification=classification,
    )


async def _source_versions(
    ncit_client: SparqlHttpClient,
    uberon_client: SparqlHttpClient,
    *,
    expected_ncit_version: str,
    expected_uberon_version: str,
) -> MappingSourceVersions:
    try:
        return await read_mapping_source_versions(
            ncit_client,
            uberon_client,
            expected_ncit_version=expected_ncit_version,
            expected_uberon_version=expected_uberon_version,
        )
    except MappingSourceVersionError as exc:
        raise PublisherXrefSourceError(str(exc)) from exc


def _require_unchanged_counts(
    assertions: list[tuple[str, str]], expected: tuple[int, int]
) -> tuple[CountDelta, CountDelta]:
    source_count = _delta(expected[0], len({row[0] for row in assertions}))
    assertion_count = _delta(expected[1], len(assertions))
    if (
        source_count.classification != "unchanged"
        or assertion_count.classification != "unchanged"
    ):
        raise PublisherXrefCountDriftError(source_count, assertion_count)
    return source_count, assertion_count


async def _resolved_targets(
    client: SparqlHttpClient, assertions: list[tuple[str, str]]
) -> set[str]:
    target_codes = {code for _, code in assertions}
    return {
        str(row["code"])
        for row in await client.select(build_ncit_target_validation_query(target_codes))
        if row.get("code")
    }


async def _sources_changed(
    ncit_client: SparqlHttpClient,
    uberon_client: SparqlHttpClient,
    assertions: list[tuple[str, str]],
    *,
    versions: MappingSourceVersions,
    uberon_assertion_identity: str,
    ncit_target_identity: str,
) -> bool:
    assertions_after = _parse_assertions(await fetch_uberon_xrefs(uberon_client))
    resolved_after = await _resolved_targets(ncit_client, assertions)
    try:
        versions_after = await _source_versions(
            ncit_client,
            uberon_client,
            expected_ncit_version=versions.ncit,
            expected_uberon_version=versions.uberon,
        )
    except PublisherXrefSourceError:
        return True
    return any(
        (
            versions_after != versions,
            _rows_identity(assertions_after) != uberon_assertion_identity,
            _rows_identity(sorted(resolved_after)) != ncit_target_identity,
        )
    )


async def publish_uberon_xrefs(
    store: XrefStore,
    ncit_client: SparqlHttpClient,
    uberon_client: SparqlHttpClient,
    *,
    expected_counts: tuple[int, int] = (EXPECTED_SOURCE_CLASSES, EXPECTED_ASSERTIONS),
    ncit_source_identity: str,
    uberon_source_identity: str,
    uberon_serving_identity: str,
    expected_ncit_version: str,
    expected_uberon_version: str,
    run_id: str | None = None,
) -> PublisherXrefReport:
    """Validate and publish every resolvable Uberon-authored NCIt assertion."""
    rid = run_id or uuid.uuid4().hex
    assertions = _parse_assertions(await fetch_uberon_xrefs(uberon_client))
    uberon_assertion_identity = _rows_identity(assertions)
    source_class_count, assertion_count = _require_unchanged_counts(
        assertions, expected_counts
    )
    versions = await _source_versions(
        ncit_client,
        uberon_client,
        expected_ncit_version=expected_ncit_version,
        expected_uberon_version=expected_uberon_version,
    )
    resolved = await _resolved_targets(ncit_client, assertions)
    ncit_target_identity = _rows_identity(sorted(resolved))
    source_metadata = UberonPublisherGenerationMetadata(
        ncit_source_identity=ncit_source_identity,
        uberon_source_identity=uberon_source_identity,
        uberon_serving_identity=uberon_serving_identity,
        uberon_assertion_identity=uberon_assertion_identity,
        ncit_target_identity=ncit_target_identity,
    )
    if await _sources_changed(
        ncit_client,
        uberon_client,
        assertions,
        versions=versions,
        uberon_assertion_identity=uberon_assertion_identity,
        ncit_target_identity=ncit_target_identity,
    ):
        raise PublisherXrefSourceError("publisher source changed during validation")
    records = [
        _record(
            upstream,
            code,
            versions=versions,
        )
        for upstream, code in assertions
        if code in resolved
    ]
    report = _report(
        assertions,
        records,
        resolved,
        ncit_version=versions.ncit,
        uberon_version=versions.uberon,
        source_class_count=source_class_count,
        assertion_count=assertion_count,
        uberon_assertion_identity=uberon_assertion_identity,
        ncit_target_identity=ncit_target_identity,
    )
    await store.upsert_run(
        run_id=rid,
        source=PUBLISHER_XREF_SOURCE,
        ncit_version=versions.ncit,
        source_version=versions.uberon,
    )
    async with fail_run_on_error(store, rid):
        await publish_generation(
            store,
            ncit_client,
            source=PUBLISHER_XREF_SOURCE,
            run_id=rid,
            records=records,
            source_metadata=source_metadata,
        )
        await store.update_run_metrics(rid, report.model_dump(mode="json"))
    return report
