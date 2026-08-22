"""Publish NCIt's P334 assertions as proposed ICD-O-3.2 alignments."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from ontolib.repositories.xref.models import P334GenerationMetadata, SSSOMRecord
from ontolib.repositories.xref.publication import fail_run_on_error, publish_generation
from ontolib.repositories.xref.vocab import CLOSE_MATCH, DATABASE_CROSS_REFERENCE
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI

if TYPE_CHECKING:
    from ontolib.repositories.icdo.store import (
        CertificationExpectation,
        IcdoCodeResolution,
        IcdoRepository,
    )
    from ontolib.repositories.xref.store import XrefStore
    from ontolib.terminologies.sparql_http_client import SparqlHttpClient

P334_ALIGNMENT_SOURCE = "ncit-p334-icdo32"
EXPECTED_CONCEPTS = 1161
EXPECTED_ASSERTIONS = 1252
_NCIT_CODE = re.compile(r"C[0-9]+")
_ICDO32_CODE = re.compile(r"[0-9]{4}/[0-9]")


class P334SourceError(ValueError):
    """An NCIt P334 source row is malformed and cannot be published safely."""


class P334CountDriftError(ValueError):
    """The active NCIt P334 inventory differs from the certified expectation."""

    def __init__(self, concept_count: CountDelta, assertion_count: CountDelta) -> None:
        super().__init__("NCIt P334 concept/assertion count drift")
        self.concept_count = concept_count
        self.assertion_count = assertion_count


class CountDelta(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    expected: int
    observed: int
    delta: int
    classification: Literal["unchanged", "increased", "decreased"]


class UnresolvedP334Assertion(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    ncit_code: str
    icdo_code: str
    reason: Literal[
        "icdo32-morphology-code-not-found", "invalid-icdo32-morphology-code"
    ] = "icdo32-morphology-code-not-found"


class P334AlignmentReport(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    ncit_release: str
    icdo_edition: Literal["3.2"] = "3.2"
    icdo_generation_id: str
    icdo_serving_sha256: str
    ncit_p334_identity: str
    concept_count: CountDelta
    assertion_count: CountDelta
    published_assertion_count: int
    unresolved: list[UnresolvedP334Assertion]


def build_p334_assertions_query() -> str:
    """Read every P334 assertion from the activated stated graph in one operation."""
    return f"""\
SELECT ?concept ?value WHERE {{
  GRAPH <{STATED_GRAPH_IRI}> {{ ?concept <{NCIT_NS}P334> ?value }}
}} ORDER BY ?concept ?value
"""


def _parse_assertions(rows: list[dict[str, str]]) -> list[tuple[str, str]]:
    assertions: list[tuple[str, str]] = []
    for row in rows:
        concept = row.get("concept", "")
        value = row.get("value", "")
        if not concept.startswith(NCIT_NS):
            raise P334SourceError(f"unsupported P334 concept IRI: {concept}")
        code = concept.removeprefix(NCIT_NS)
        if _NCIT_CODE.fullmatch(code) is None:
            raise P334SourceError(f"malformed P334 NCIt code: {code}")
        if not value:
            raise P334SourceError("missing P334 asserted value")
        assertions.append((code, value))
    return sorted(assertions)


def _assertion_identity(assertions: list[tuple[str, str]]) -> str:
    return hashlib.sha256(
        json.dumps(assertions, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


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


def _record(code: str, value: str, *, ncit_version: str) -> SSSOMRecord:
    return SSSOMRecord(
        subject_id=code,
        subject_system="ncit",
        predicate_id=CLOSE_MATCH,
        object_id=value,
        object_system="icdo",
        mapping_justification=DATABASE_CROSS_REFERENCE,
        confidence=0.9,
        subject_source_version=ncit_version,
        object_source_version="3.2",
        author="ncit-p334",
    )


def _publication_rows(
    assertions: list[tuple[str, str]],
    resolved_codes: set[str],
    *,
    ncit_version: str,
) -> tuple[list[SSSOMRecord], list[UnresolvedP334Assertion]]:
    records: list[SSSOMRecord] = []
    unresolved: list[UnresolvedP334Assertion] = []
    for code, value in assertions:
        if value in resolved_codes:
            records.append(_record(code, value, ncit_version=ncit_version))
            continue
        reason: Literal[
            "icdo32-morphology-code-not-found", "invalid-icdo32-morphology-code"
        ] = (
            "icdo32-morphology-code-not-found"
            if _ICDO32_CODE.fullmatch(value)
            else "invalid-icdo32-morphology-code"
        )
        unresolved.append(
            UnresolvedP334Assertion(
                ncit_code=code,
                icdo_code=value,
                reason=reason,
            )
        )
    return records, unresolved


def _require_unchanged_counts(
    assertions: list[tuple[str, str]], expected: tuple[int, int]
) -> tuple[CountDelta, CountDelta]:
    concept_count = _delta(expected[0], len({code for code, _ in assertions}))
    assertion_count = _delta(expected[1], len(assertions))
    if (
        concept_count.classification != "unchanged"
        or assertion_count.classification != "unchanged"
    ):
        raise P334CountDriftError(concept_count, assertion_count)
    return concept_count, assertion_count


async def _require_unchanged_sources(
    ncit_client: SparqlHttpClient,
    icdo: IcdoRepository,
    *,
    ncit_version: str,
    ncit_p334_identity: str,
    valid_values: set[str],
    icdo_expected: CertificationExpectation,
    resolution: IcdoCodeResolution,
) -> None:
    after = _parse_assertions(await ncit_client.select(build_p334_assertions_query()))
    if (
        await ncit_client.version() != ncit_version
        or _assertion_identity(after) != ncit_p334_identity
    ):
        raise P334SourceError("active NCIt source changed during validation")
    recertified = await icdo.resolve_active_morphology32_codes(
        valid_values, icdo_expected
    )
    if recertified != resolution:
        raise P334SourceError("active ICD-O source changed during validation")


async def publish_p334_alignments(
    store: XrefStore,
    ncit_client: SparqlHttpClient,
    icdo: IcdoRepository,
    *,
    icdo_expected: CertificationExpectation,
    ncit_source_identity: str,
    expected_counts: tuple[int, int] = (EXPECTED_CONCEPTS, EXPECTED_ASSERTIONS),
    run_id: str | None = None,
) -> P334AlignmentReport:
    """Validate and publish all resolvable NCIt P334 assertions deterministically."""
    assertions = _parse_assertions(
        await ncit_client.select(build_p334_assertions_query())
    )
    ncit_p334_identity = _assertion_identity(assertions)
    ncit_version = await ncit_client.version()
    if not ncit_version:
        raise P334SourceError("active NCIt source has no release identity")
    concept_count, assertion_count = _require_unchanged_counts(
        assertions, expected_counts
    )
    valid_values = {value for _, value in assertions if _ICDO32_CODE.fullmatch(value)}
    resolution = await icdo.resolve_active_morphology32_codes(
        valid_values, icdo_expected
    )
    await _require_unchanged_sources(
        ncit_client,
        icdo,
        ncit_version=ncit_version,
        ncit_p334_identity=ncit_p334_identity,
        valid_values=valid_values,
        icdo_expected=icdo_expected,
        resolution=resolution,
    )
    records, unresolved = _publication_rows(
        assertions,
        resolution.resolved_codes,
        ncit_version=ncit_version,
    )
    report = P334AlignmentReport(
        ncit_release=ncit_version,
        icdo_generation_id=resolution.generation_id,
        icdo_serving_sha256=resolution.serving_sha256,
        ncit_p334_identity=ncit_p334_identity,
        concept_count=concept_count,
        assertion_count=assertion_count,
        published_assertion_count=len(records),
        unresolved=unresolved,
    )
    rid = run_id or uuid.uuid4().hex
    await store.upsert_run(
        run_id=rid,
        source=P334_ALIGNMENT_SOURCE,
        ncit_version=ncit_version,
        source_version="3.2",
    )
    async with fail_run_on_error(store, rid):
        await publish_generation(
            store,
            ncit_client,
            source=P334_ALIGNMENT_SOURCE,
            run_id=rid,
            records=records,
            source_metadata=P334GenerationMetadata(
                ncit_source_identity=ncit_source_identity,
                ncit_p334_identity=ncit_p334_identity,
                icdo_generation_identity=resolution.generation_id,
                icdo_serving_identity=resolution.serving_sha256,
            ),
        )
        await store.update_run_metrics(rid, report.model_dump(mode="json"))
    return report
