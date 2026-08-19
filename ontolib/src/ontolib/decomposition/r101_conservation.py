"""Occurrence-ledger evidence for the decomposition-v3 to v4 R101 change."""

from __future__ import annotations

import csv
import gzip
import hashlib
import inspect
import io
import json
import os
import re
import tempfile
import zlib
from collections import Counter, defaultdict
from contextlib import suppress
from itertools import pairwise, product
from typing import TYPE_CHECKING, Literal, Protocol, Self, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import to_jsonable_python

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

_SHA256 = r"^[0-9a-f]{64}$"
_CODE = r"^C[0-9]+$"
R101_CONSERVATION_SCHEMA_VERSION = 3
_GZIP_HEADER_SIZE = 10

STRUCTURAL_KEY_FIELDS = (
    "concept_code",
    "occurrence_id",
    "source_fact_id",
    "source_group_id",
    "anchor_code",
    "depth",
    "role_code",
    "filler_code",
    "structural_path",
    "member_position",
)

Disposition = Literal[
    "projected",
    "unchanged-unprojected",
    "covered-by-retained-r82",
    "unresolved",
]
DispositionReason = Literal[
    "persisted-new-r101-link",
    "explicit-no-old-or-new-links",
    "retained-r82-path",
    "structural-key-mismatch",
    "duplicate-occurrence",
    "unresolved-disposition",
    "reversed-r82",
    "broken-r82-path",
    "r82-depth-exceeded",
    "cross-axis-coverage",
    "source-identity-mismatch",
    "count-mismatch",
    "non-r101-delta",
    "content-authorization-missing",
    "content-authorization-digest-mismatch",
]


class R101ConservationValidationError(ValueError):
    """The source-bound occurrence ledger cannot support the requested claim."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class Pair(_StrictModel):
    axis: str = Field(min_length=1)
    filler_code: str = Field(pattern=r"^(?:C[0-9]+|MINT-[0-9a-f]{12})$")

    def __hash__(self) -> int:
        return hash((self.axis, self.filler_code))


def r101_occurrence_ledger_query() -> str:
    """Return the exact one-query occurrence and non-R101 delta contract."""
    return (
        "WITH old_o AS (SELECT * FROM decomp_source_occurrence "
        "WHERE run_id=:old_run_id AND role_code='R101'), "
        "new_o AS (SELECT * FROM decomp_source_occurrence "
        "WHERE run_id=:new_run_id AND role_code='R101'), "
        "old_links AS (SELECT co.concept_code, co.occurrence_id, "
        "jsonb_agg(jsonb_build_object('axis',co.axis,'filler_code',co.filler_code) "
        "ORDER BY co.axis,co.filler_code) pairs "
        "FROM decomp_constituent_occurrence co JOIN old_o o USING "
        "(concept_code,occurrence_id) WHERE co.run_id=:old_run_id "
        "GROUP BY co.concept_code,co.occurrence_id), "
        "new_links AS (SELECT co.concept_code, co.occurrence_id, "
        "jsonb_agg(jsonb_build_object('axis',co.axis,'filler_code',co.filler_code) "
        "ORDER BY co.axis,co.filler_code) pairs "
        "FROM decomp_constituent_occurrence co JOIN new_o o USING "
        "(concept_code,occurrence_id) WHERE co.run_id=:new_run_id "
        "GROUP BY co.concept_code,co.occurrence_id), "
        "retained AS (SELECT co.concept_code, jsonb_agg(DISTINCT "
        "jsonb_build_object('axis',co.axis,'filler_code',co.filler_code)) pairs "
        "FROM decomp_constituent_occurrence co JOIN new_o o USING "
        "(concept_code,occurrence_id) WHERE co.run_id=:new_run_id "
        "GROUP BY co.concept_code), "
        "old_non AS (SELECT concept_code,axis,filler_code FROM decomp_constituent "
        "WHERE run_id=:old_run_id EXCEPT SELECT "
        "co.concept_code,co.axis,co.filler_code "
        "FROM decomp_constituent_occurrence co JOIN old_o o USING "
        "(concept_code,occurrence_id) WHERE co.run_id=:old_run_id), "
        "new_non AS (SELECT concept_code,axis,filler_code FROM decomp_constituent "
        "WHERE run_id=:new_run_id EXCEPT SELECT "
        "co.concept_code,co.axis,co.filler_code "
        "FROM decomp_constituent_occurrence co JOIN new_o o USING "
        "(concept_code,occurrence_id) WHERE co.run_id=:new_run_id), "
        "non_delta AS (SELECT 'removed' change,concept_code,axis,filler_code FROM "
        "(SELECT * FROM old_non EXCEPT SELECT * FROM new_non) removed UNION ALL "
        "SELECT 'added' change,concept_code,axis,filler_code FROM "
        "(SELECT * FROM new_non EXCEPT SELECT * FROM old_non) added), "
        "delta_evidence AS (SELECT COALESCE(jsonb_agg(jsonb_build_object("
        "'change',change,'concept_code',concept_code,'axis',axis,"
        "'filler_code',filler_code) ORDER BY change,concept_code,axis,filler_code),"
        "'[]'::jsonb) rows FROM non_delta) "
        "SELECT to_jsonb(o)-'run_id' AS old_occurrence, "
        "to_jsonb(n)-'run_id' AS new_occurrence, "
        "COALESCE(ol.pairs,'[]'::jsonb) old_links, "
        "COALESCE(nl.pairs,'[]'::jsonb) new_links, "
        "COALESCE(r.pairs,'[]'::jsonb) retained_links, "
        "delta_evidence.rows non_r101_delta_rows "
        "FROM old_o o FULL OUTER JOIN new_o n USING (concept_code,occurrence_id) "
        "LEFT JOIN old_links ol ON ol.concept_code=o.concept_code AND "
        "ol.occurrence_id=o.occurrence_id LEFT JOIN new_links nl ON "
        "nl.concept_code=n.concept_code AND nl.occurrence_id=n.occurrence_id "
        "LEFT JOIN retained r ON "
        "r.concept_code=COALESCE(o.concept_code,n.concept_code) "
        "CROSS JOIN delta_evidence ORDER BY COALESCE(o.concept_code,n.concept_code), "
        "COALESCE(o.occurrence_id,n.occurrence_id)"
    )


def r101_ledger_query_identity() -> str:
    """Identify the exact SQL that produces occurrence and delta evidence."""
    return hashlib.sha256(r101_occurrence_ledger_query().encode()).hexdigest()


class StructuralOccurrence(_StrictModel):
    concept_code: str = Field(pattern=_CODE)
    occurrence_id: str = Field(pattern=_SHA256)
    source_fact_id: str = Field(pattern=_SHA256)
    source_group_id: str = Field(pattern=_SHA256)
    anchor_code: str = Field(pattern=_CODE)
    depth: int = Field(ge=0)
    role_code: Literal["R101"]
    filler_code: str = Field(pattern=_CODE)
    structural_path: tuple[int, ...] = Field(min_length=1)
    member_position: int = Field(ge=0)

    @model_validator(mode="after")
    def _member_matches_path(self) -> Self:
        if self.structural_path[-1] != self.member_position:
            raise ValueError(
                "structural-key-mismatch: member position is not path tail"
            )
        return self

    @property
    def structural_key(self) -> tuple[object, ...]:
        return tuple(getattr(self, field) for field in STRUCTURAL_KEY_FIELDS)


class OccurrenceInput(_StrictModel):
    old_occurrence: StructuralOccurrence
    new_occurrence: StructuralOccurrence
    old_links: tuple[Pair, ...]
    new_links: tuple[Pair, ...]
    retained_new_r101_links: tuple[Pair, ...]

    @model_validator(mode="after")
    def _links_are_unique(self) -> Self:
        for links in (
            self.old_links,
            self.new_links,
            self.retained_new_r101_links,
        ):
            if len(links) != len(set(links)):
                raise ValueError("duplicate-occurrence: duplicate occurrence link")
        return self


class NonR101DeltaRow(_StrictModel):
    change: Literal["added", "removed"]
    concept_code: str = Field(pattern=_CODE)
    axis: str = Field(min_length=1)
    filler_code: str = Field(pattern=r"^(?:C[0-9]+|MINT-[0-9a-f]{12})$")


def _delta_row_key(row: NonR101DeltaRow) -> tuple[str, str, str, str]:
    return row.change, row.concept_code, row.axis, row.filler_code


class NonR101DeltaEvidence(_StrictModel):
    old_run_id: str = Field(min_length=1)
    new_run_id: str = Field(min_length=1)
    query_identity: str = Field(pattern=_SHA256)
    rows: tuple[NonR101DeltaRow, ...]

    @model_validator(mode="after")
    def _is_exact_canonical_query_result(self) -> Self:
        if self.query_identity != r101_ledger_query_identity():
            raise ValueError("non-R101 delta query identity differs")
        ordered = tuple(sorted(self.rows, key=_delta_row_key))
        if self.rows != ordered or len(self.rows) != len(set(self.rows)):
            raise ValueError("non-R101 delta rows are not canonical and unique")
        return self


class R101LedgerSource(_StrictModel):
    """One-query Postgres boundary result before stated-R82 acquisition."""

    occurrences: tuple[OccurrenceInput, ...]
    non_r101_delta_evidence: NonR101DeltaEvidence
    postgres_query_count: Literal[1] = 1


class R101LedgerConsumerStore(Protocol):
    async def r101_occurrence_ledger(
        self, old_run_id: str, new_run_id: str
    ) -> R101LedgerSource: ...


class R82PathEdge(_StrictModel):
    part_code: str = Field(pattern=_CODE)
    asserted_part_code: str = Field(pattern=_CODE)
    whole_code: str = Field(pattern=_CODE)
    restriction_node_id: str = Field(min_length=1)
    fact_identity: str = Field(pattern=_SHA256)
    source_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _fact_identity_matches_edge(self) -> Self:
        if self.fact_identity != r82_fact_identity(
            self.source_identity,
            self.asserted_part_code,
            self.whole_code,
            self.restriction_node_id,
        ):
            raise ValueError("R82 fact identity does not match asserted edge")
        return self


class R82Path(_StrictModel):
    edges: tuple[R82PathEdge, ...]


class QueryMetrics(_StrictModel):
    postgres_query_count: int = Field(ge=0, le=10)
    qlever_query_count: int = Field(ge=0, le=208)
    max_pair_batch_size: int = Field(ge=0, le=8)
    max_r82_hops: int = Field(ge=1, le=8)
    max_asserted_superclass_hops: int = Field(ge=1, le=20)


class LedgerBuildContext(_StrictModel):
    source_identity: str = Field(pattern=_SHA256)
    source_release_id: str = Field(min_length=1)
    old_run_id: str = Field(min_length=1)
    old_run_fingerprint_identity: str = Field(pattern=_SHA256)
    old_representation_identity: str = Field(pattern=_SHA256)
    old_baseline_identity: str = Field(pattern=_SHA256)
    new_run_id: str = Field(min_length=1)
    new_run_fingerprint_identity: str = Field(pattern=_SHA256)
    new_representation_identity: str = Field(pattern=_SHA256)
    detector_identity: str = Field(pattern=_SHA256)
    pre_resume_proof_identity: str = Field(pattern=_SHA256)
    resume_dry_run_identity: str = Field(pattern=_SHA256)
    mixed_cohort_identity: str = Field(pattern=_SHA256)
    proof_identity: str = Field(pattern=_SHA256)
    adapter_id: str = Field(min_length=1)
    query_metrics: QueryMetrics
    non_r101_delta_evidence: NonR101DeltaEvidence

    @model_validator(mode="after")
    def _proof_binds_prerequisites(self) -> Self:
        if self.detector_identity != r101_detector_identity():
            raise ValueError(
                "detector identity does not match current ledger semantics"
            )
        if self.proof_identity != r101_proof_identity(
            self.pre_resume_proof_identity,
            self.resume_dry_run_identity,
            self.mixed_cohort_identity,
        ):
            raise ValueError(
                "proof identity does not bind prerequisite proof identities"
            )
        if (
            self.non_r101_delta_evidence.old_run_id != self.old_run_id
            or self.non_r101_delta_evidence.new_run_id != self.new_run_id
        ):
            raise ValueError("non-R101 delta evidence does not bind report runs")
        return self


class LedgerOccurrence(StructuralOccurrence):
    disposition: Disposition
    disposition_reason: DispositionReason
    old_links: tuple[Pair, ...]
    new_links: tuple[Pair, ...]
    retained_r82_target: Pair | None
    r82_evidence_kind: Literal["none", "one-step", "closure-only"]
    r82_path: tuple[R82PathEdge, ...]
    path_length: int = Field(ge=0, le=8)
    source_release_id: str = Field(min_length=1)
    adapter_id: str = Field(min_length=1)
    proof_id: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _path_length_matches(self) -> Self:
        if self.path_length != len(self.r82_path):
            raise ValueError("broken-r82-path: path length does not match edges")
        covered = self.disposition == "covered-by-retained-r82"
        if covered != (self.retained_r82_target is not None):
            raise ValueError("unresolved-disposition: retained target mismatch")
        if covered != (self.r82_evidence_kind != "none"):
            raise ValueError("unresolved-disposition: R82 evidence mismatch")
        return self


class LedgerCounts(_StrictModel):
    total: int = Field(ge=0)
    projected: int = Field(ge=0)
    unchanged_unprojected: int = Field(ge=0)
    covered_by_retained_r82: int = Field(ge=0)
    unresolved: int = Field(ge=0)
    one_step: int = Field(ge=0)
    closure_only: int = Field(ge=0)
    non_r101_delta: int = Field(ge=0)


class ContentAuthorization(_StrictModel):
    status: Literal["pending", "authorized", "digest-mismatch"]
    authorized_digest: str | None

    @model_validator(mode="after")
    def _digest_shape(self) -> Self:
        _validate_content_authorization(self)
        return self


class GroupingSubgroup(_StrictModel):
    axis: str
    evidence_kind: Literal["one-step", "closure-only"]
    occurrence_count: int = Field(gt=0)


class GroupingPattern(_StrictModel):
    old_filler_code: str = Field(pattern=_CODE)
    retained_filler_code: str = Field(pattern=_CODE)
    occurrence_count: int = Field(gt=0)
    subgroups: tuple[GroupingSubgroup, ...]


class R101ConservationReport(_StrictModel):
    schema_version: Literal[3]
    source_identity: str = Field(pattern=_SHA256)
    source_release_id: str
    old_run_id: str
    old_run_fingerprint_identity: str = Field(pattern=_SHA256)
    old_representation_identity: str = Field(pattern=_SHA256)
    old_baseline_identity: str = Field(pattern=_SHA256)
    new_run_id: str
    new_run_fingerprint_identity: str = Field(pattern=_SHA256)
    new_representation_identity: str = Field(pattern=_SHA256)
    detector_identity: str = Field(pattern=_SHA256)
    pre_resume_proof_identity: str = Field(pattern=_SHA256)
    resume_dry_run_identity: str = Field(pattern=_SHA256)
    mixed_cohort_identity: str = Field(pattern=_SHA256)
    proof_identity: str = Field(pattern=_SHA256)
    structural_key_fields: tuple[str, ...]
    mechanical_status: Literal["complete", "incomplete"]
    content_authorization: ContentAuthorization
    publication_gate: Literal["blocked", "eligible"]
    counts: LedgerCounts
    query_metrics: QueryMetrics
    non_r101_delta_evidence: NonR101DeltaEvidence
    grouping_presentation: tuple[GroupingPattern, ...]
    occurrences: tuple[LedgerOccurrence, ...]
    json_identity: str = Field(pattern=_SHA256)
    tsv_identity: str = Field(pattern=_SHA256)
    report_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _report_is_self_consistent(self) -> Self:
        _validate_report_bindings(self)
        _validate_report_paths(self)
        complete = _validate_report_counts(self)
        _validate_report_authorization(self, complete)
        _validate_report_identities(self)
        return self


def _validate_content_authorization(authorization: ContentAuthorization) -> None:
    digest = authorization.authorized_digest
    if digest is not None and not re.fullmatch(_SHA256, digest):
        raise ValueError("authorization digest must be SHA-256")
    if authorization.status == "pending" and digest is not None:
        raise ValueError("pending authorization cannot carry a digest")
    if authorization.status != "pending" and digest is None:
        raise ValueError(f"{authorization.status} authorization requires a digest")


def _validate_report_bindings(report: R101ConservationReport) -> None:
    if report.detector_identity != r101_detector_identity():
        raise ValueError("detector identity does not match current ledger semantics")
    expected_proof = r101_proof_identity(
        report.pre_resume_proof_identity,
        report.resume_dry_run_identity,
        report.mixed_cohort_identity,
    )
    if report.proof_identity != expected_proof:
        raise ValueError("proof identity does not bind prerequisite proof identities")
    if report.structural_key_fields != STRUCTURAL_KEY_FIELDS:
        raise ValueError("structural-key-mismatch: key field declaration differs")
    if (
        report.non_r101_delta_evidence.old_run_id != report.old_run_id
        or report.non_r101_delta_evidence.new_run_id != report.new_run_id
    ):
        raise ValueError("non-R101 delta evidence does not bind report runs")


def _validate_report_paths(report: R101ConservationReport) -> None:
    for occurrence in report.occurrences:
        _validate_report_path(occurrence, report)


def _validate_report_path(
    occurrence: LedgerOccurrence, report: R101ConservationReport
) -> None:
    target = occurrence.retained_r82_target
    if target is None:
        _validate_uncovered_path(occurrence)
        return
    same_axis = tuple(pair for pair in occurrence.old_links if pair.axis == target.axis)
    if not same_axis:
        raise ValueError("cross-axis-coverage: retained target differs from old axis")
    old = _old_pair_at_path_endpoint(occurrence, same_axis)
    refusal = _path_refusal(
        R82Path(edges=occurrence.r82_path),
        expected_part=target.filler_code,
        expected_whole=old.filler_code,
        source_identity=report.source_identity,
        max_r82_hops=report.query_metrics.max_r82_hops,
    )
    if refusal is not None:
        raise ValueError(f"{refusal}: report R82 path is invalid")
    _validate_r82_evidence_kind(occurrence)


def _validate_r82_evidence_kind(occurrence: LedgerOccurrence) -> None:
    expected_kind = "one-step" if occurrence.path_length == 1 else "closure-only"
    if occurrence.r82_evidence_kind != expected_kind:
        raise ValueError("broken-r82-path: evidence kind differs from path depth")


def _validate_uncovered_path(occurrence: LedgerOccurrence) -> None:
    if occurrence.r82_path:
        raise ValueError("broken-r82-path: uncovered occurrence carries a path")


def _old_pair_at_path_endpoint(
    occurrence: LedgerOccurrence, same_axis: tuple[Pair, ...]
) -> Pair:
    return next(
        (
            pair
            for pair in same_axis
            if occurrence.r82_path
            and pair.filler_code == occurrence.r82_path[-1].whole_code
        ),
        same_axis[0],
    )


def _validate_report_counts(report: R101ConservationReport) -> bool:
    expected = _ledger_counts(
        report.occurrences, len(report.non_r101_delta_evidence.rows)
    )
    if report.counts != expected:
        raise ValueError("count-mismatch: report counts differ from occurrences")
    complete = expected.unresolved == 0 and expected.non_r101_delta == 0
    expected_status = "complete" if complete else "incomplete"
    if report.mechanical_status != expected_status:
        raise ValueError("count-mismatch: mechanical status differs from counts")
    return complete


def _validate_report_authorization(
    report: R101ConservationReport, complete: bool
) -> None:
    authorization_matches = (
        report.content_authorization.status == "authorized"
        and report.content_authorization.authorized_digest == report.json_identity
    )
    expected_gate = "eligible" if complete and authorization_matches else "blocked"
    if report.publication_gate != expected_gate:
        raise ValueError("content authorization does not match publication gate")


def _validate_report_identities(report: R101ConservationReport) -> None:
    if report.json_identity != _json_identity(report):
        raise ValueError("source-identity-mismatch: JSON ledger identity differs")
    if report.tsv_identity != _sha256(_tsv_content(report.occurrences)):
        raise ValueError("source-identity-mismatch: TSV ledger identity differs")
    if report.report_identity != _report_identity(report):
        raise ValueError("source-identity-mismatch: report identity differs")


def _canonical(value: object) -> bytes:
    return json.dumps(
        to_jsonable_python(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def r82_fact_identity(
    source_identity: str,
    asserted_part_code: str,
    whole_code: str,
    restriction_node_id: str,
) -> str:
    """Identify one replayable stated R82 restriction from its exact bindings."""
    return _sha256(
        _canonical(
            {
                "asserted_part": asserted_part_code,
                "restriction_node": restriction_node_id,
                "role_code": "R82",
                "source_identity": source_identity,
                "whole": whole_code,
            }
        )
    )


def _semantic_payload(
    report: R101ConservationReport | dict[str, object],
) -> dict[str, object]:
    payload = (
        report.model_dump(mode="json")
        if isinstance(report, R101ConservationReport)
        else dict(report)
    )
    for field in (
        "content_authorization",
        "publication_gate",
        "json_identity",
        "tsv_identity",
        "report_identity",
    ):
        payload.pop(field, None)
    return payload


def _json_identity(report: R101ConservationReport | dict[str, object]) -> str:
    return _sha256(_canonical(_semantic_payload(report)))


def _report_identity(report: R101ConservationReport | dict[str, object]) -> str:
    payload = (
        report.model_dump(mode="json")
        if isinstance(report, R101ConservationReport)
        else dict(report)
    )
    payload.pop("report_identity", None)
    return _sha256(_canonical(payload))


def _unresolved(
    occurrence: StructuralOccurrence,
    item: OccurrenceInput,
    context: LedgerBuildContext,
    reason: DispositionReason,
) -> LedgerOccurrence:
    return LedgerOccurrence(
        **occurrence.model_dump(),
        disposition="unresolved",
        disposition_reason=reason,
        old_links=item.old_links,
        new_links=item.new_links,
        retained_r82_target=None,
        r82_evidence_kind="none",
        r82_path=(),
        path_length=0,
        source_release_id=context.source_release_id,
        adapter_id=context.adapter_id,
        proof_id=context.proof_identity,
    )


def _path_refusal(  # noqa: PLR0911
    path: R82Path,
    *,
    expected_part: str,
    expected_whole: str,
    source_identity: str,
    max_r82_hops: int,
) -> DispositionReason | None:
    edges = path.edges
    if not edges:
        return "broken-r82-path"
    if len(edges) > max_r82_hops:
        return "r82-depth-exceeded"
    if not _path_source_matches(edges, source_identity):
        return "source-identity-mismatch"
    if _path_is_reversed(edges, expected_part, expected_whole):
        return "reversed-r82"
    if not _path_has_expected_endpoints(edges, expected_part, expected_whole):
        return "broken-r82-path"
    if not _path_is_connected(edges):
        return "broken-r82-path"
    return None


def _path_has_expected_endpoints(
    edges: tuple[R82PathEdge, ...], expected_part: str, expected_whole: str
) -> bool:
    return (
        edges[0].part_code == expected_part and edges[-1].whole_code == expected_whole
    )


def _path_source_matches(edges: tuple[R82PathEdge, ...], source_identity: str) -> bool:
    return all(edge.source_identity == source_identity for edge in edges)


def _path_is_reversed(
    edges: tuple[R82PathEdge, ...], expected_part: str, expected_whole: str
) -> bool:
    return (
        edges[0].part_code == expected_whole and edges[-1].whole_code == expected_part
    )


def _path_is_connected(edges: tuple[R82PathEdge, ...]) -> bool:
    return all(left.whole_code == right.part_code for left, right in pairwise(edges))


def _classify(
    item: OccurrenceInput,
    paths: dict[tuple[str, str], R82Path],
    context: LedgerBuildContext,
) -> LedgerOccurrence:
    occurrence = item.old_occurrence
    if item.new_links:
        return LedgerOccurrence(
            **occurrence.model_dump(),
            disposition="projected",
            disposition_reason="persisted-new-r101-link",
            old_links=item.old_links,
            new_links=item.new_links,
            retained_r82_target=None,
            r82_evidence_kind="none",
            r82_path=(),
            path_length=0,
            source_release_id=context.source_release_id,
            adapter_id=context.adapter_id,
            proof_id=context.proof_identity,
        )
    if not item.old_links:
        return LedgerOccurrence(
            **occurrence.model_dump(),
            disposition="unchanged-unprojected",
            disposition_reason="explicit-no-old-or-new-links",
            old_links=(),
            new_links=(),
            retained_r82_target=None,
            r82_evidence_kind="none",
            r82_path=(),
            path_length=0,
            source_release_id=context.source_release_id,
            adapter_id=context.adapter_id,
            proof_id=context.proof_identity,
        )

    covered, first_refusal = _find_r82_coverage(item, paths, context)
    if covered is not None:
        retained, path = covered
        kind: Literal["one-step", "closure-only"] = (
            "one-step" if len(path.edges) == 1 else "closure-only"
        )
        return LedgerOccurrence(
            **occurrence.model_dump(),
            disposition="covered-by-retained-r82",
            disposition_reason="retained-r82-path",
            old_links=item.old_links,
            new_links=(),
            retained_r82_target=retained,
            r82_evidence_kind=kind,
            r82_path=path.edges,
            path_length=len(path.edges),
            source_release_id=context.source_release_id,
            adapter_id=context.adapter_id,
            proof_id=context.proof_identity,
        )
    return _unresolved(
        occurrence,
        item,
        context,
        first_refusal or "unresolved-disposition",
    )


def _find_r82_coverage(
    item: OccurrenceInput,
    paths: dict[tuple[str, str], R82Path],
    context: LedgerBuildContext,
) -> tuple[tuple[Pair, R82Path] | None, DispositionReason | None]:
    old_links = sorted(item.old_links, key=lambda pair: (pair.axis, pair.filler_code))
    retained_links = sorted(
        item.retained_new_r101_links,
        key=lambda pair: (pair.axis, pair.filler_code),
    )
    first_refusal: DispositionReason | None = None
    for old, retained in product(old_links, retained_links):
        if retained.axis != old.axis:
            first_refusal = first_refusal or "cross-axis-coverage"
            continue
        path = paths.get((retained.filler_code, old.filler_code))
        if path is None:
            continue
        refusal = _path_refusal(
            path,
            expected_part=retained.filler_code,
            expected_whole=old.filler_code,
            source_identity=context.source_identity,
            max_r82_hops=context.query_metrics.max_r82_hops,
        )
        if refusal is None:
            return (retained, path), first_refusal
        first_refusal = first_refusal or refusal
    return None, first_refusal


def _ledger_counts(
    occurrences: tuple[LedgerOccurrence, ...], non_r101_delta: int
) -> LedgerCounts:
    dispositions = Counter(item.disposition for item in occurrences)
    evidence = Counter(item.r82_evidence_kind for item in occurrences)
    return LedgerCounts(
        total=len(occurrences),
        projected=dispositions["projected"],
        unchanged_unprojected=dispositions["unchanged-unprojected"],
        covered_by_retained_r82=dispositions["covered-by-retained-r82"],
        unresolved=dispositions["unresolved"],
        one_step=evidence["one-step"],
        closure_only=evidence["closure-only"],
        non_r101_delta=non_r101_delta,
    )


def _grouping(occurrences: tuple[LedgerOccurrence, ...]) -> tuple[GroupingPattern, ...]:
    groups: dict[tuple[str, str], list[LedgerOccurrence]] = defaultdict(list)
    for item in filter(_is_groupable, occurrences):
        target = cast("Pair", item.retained_r82_target)
        groups[(item.old_links[0].filler_code, target.filler_code)].append(item)
    result: list[GroupingPattern] = []
    for (old, retained), items in sorted(groups.items()):
        subgroup_counts = Counter(
            (item.old_links[0].axis, item.r82_evidence_kind) for item in items
        )
        result.append(
            GroupingPattern(
                old_filler_code=old,
                retained_filler_code=retained,
                occurrence_count=len(items),
                subgroups=tuple(
                    GroupingSubgroup(
                        axis=axis,
                        evidence_kind=kind,  # type: ignore[arg-type]
                        occurrence_count=count,
                    )
                    for (axis, kind), count in sorted(subgroup_counts.items())
                ),
            )
        )
    return tuple(result)


def _is_groupable(item: LedgerOccurrence) -> bool:
    return (
        item.r82_evidence_kind in {"one-step", "closure-only"}
        and len(item.old_links) == 1
        and item.retained_r82_target is not None
    )


_TSV_FIELDS = (
    *STRUCTURAL_KEY_FIELDS,
    "disposition",
    "disposition_reason",
    "old_links",
    "new_links",
    "retained_r82_target",
    "r82_evidence_kind",
    "r82_path",
    "path_length",
    "source_release_id",
    "adapter_id",
    "proof_id",
)


def _tsv_content(occurrences: tuple[LedgerOccurrence, ...]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output, fieldnames=_TSV_FIELDS, dialect="excel-tab", lineterminator="\n"
    )
    writer.writeheader()
    for occurrence in occurrences:
        row = occurrence.model_dump(mode="json")
        for field in (
            "structural_path",
            "old_links",
            "new_links",
            "retained_r82_target",
            "r82_path",
        ):
            row[field] = json.dumps(row[field], sort_keys=True, separators=(",", ":"))
        writer.writerow(cast("Any", row))
    return output.getvalue().encode("utf-8")


def r101_ledger_tsv_bytes(report: R101ConservationReport) -> bytes:
    """Serialize every semantic occurrence field in deterministic lossless TSV."""
    return _tsv_content(report.occurrences)


def read_r101_ledger_tsv(content: bytes) -> tuple[LedgerOccurrence, ...]:
    """Read the purpose-specific TSV, rejecting missing or additional columns."""
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")), dialect="excel-tab")
    if tuple(reader.fieldnames or ()) != _TSV_FIELDS:
        raise R101ConservationValidationError("structural-key-mismatch: TSV columns")
    rows: list[LedgerOccurrence] = []
    for raw in reader:
        values: dict[str, object] = dict(raw)
        for field in (
            "structural_path",
            "old_links",
            "new_links",
            "retained_r82_target",
            "r82_path",
        ):
            values[field] = json.loads(raw[field])
        for field in ("structural_path", "old_links", "new_links", "r82_path"):
            values[field] = tuple(cast("list[object]", values[field]))
        values["depth"] = int(raw["depth"])
        values["member_position"] = int(raw["member_position"])
        values["path_length"] = int(raw["path_length"])
        rows.append(LedgerOccurrence.model_validate(values))
    return tuple(rows)


def build_r101_occurrence_ledger(
    inputs: tuple[OccurrenceInput, ...],
    *,
    paths: dict[tuple[str, str], R82Path],
    context: LedgerBuildContext,
) -> R101ConservationReport:
    """Build one total, deterministic and source-bound occurrence ledger."""
    ordered = sorted(inputs, key=lambda item: item.old_occurrence.structural_key)
    _validate_input_inventory(ordered)
    occurrences = tuple(_classify(item, paths, context) for item in ordered)
    non_r101_delta_count = len(context.non_r101_delta_evidence.rows)
    if non_r101_delta_count:
        occurrences = tuple(
            item.model_copy(
                update={
                    "disposition": "unresolved",
                    "disposition_reason": "non-r101-delta",
                    "retained_r82_target": None,
                    "r82_evidence_kind": "none",
                    "r82_path": (),
                    "path_length": 0,
                }
            )
            for item in occurrences
        )
    counts = _ledger_counts(occurrences, non_r101_delta_count)
    payload: dict[str, object] = {
        "schema_version": R101_CONSERVATION_SCHEMA_VERSION,
        **context.model_dump(exclude={"adapter_id"}),
        "structural_key_fields": STRUCTURAL_KEY_FIELDS,
        "mechanical_status": "complete"
        if counts.unresolved == 0 and counts.non_r101_delta == 0
        else "incomplete",
        "counts": counts,
        "grouping_presentation": _grouping(occurrences),
        "occurrences": occurrences,
    }
    json_identity = _json_identity(payload)
    tsv_identity = _sha256(_tsv_content(occurrences))
    authorization = ContentAuthorization(status="pending", authorized_digest=None)
    complete: dict[str, object] = {
        **payload,
        "content_authorization": authorization,
        "publication_gate": "blocked",
        "json_identity": json_identity,
        "tsv_identity": tsv_identity,
    }
    complete["report_identity"] = _report_identity(complete)
    return R101ConservationReport.model_validate(complete)


def _validate_input_inventory(inputs: list[OccurrenceInput]) -> None:
    seen: set[tuple[object, ...]] = set()
    for item in inputs:
        if item.old_occurrence.structural_key != item.new_occurrence.structural_key:
            raise R101ConservationValidationError("structural-key-mismatch")
        key = item.old_occurrence.structural_key
        if key in seen:
            raise R101ConservationValidationError("duplicate-occurrence")
        seen.add(key)


def validate_r101_publication(report: R101ConservationReport) -> None:
    """Refuse publication unless mechanics and exact-digest authorization both pass."""
    if report.counts.non_r101_delta:
        raise R101ConservationValidationError("non-r101-delta")
    if report.mechanical_status != "complete":
        raise R101ConservationValidationError("unresolved-disposition")
    if report.content_authorization.status == "pending":
        raise R101ConservationValidationError("content-authorization-missing")
    if report.content_authorization.status == "digest-mismatch":
        raise R101ConservationValidationError("content-authorization-digest-mismatch")
    if report.publication_gate != "eligible":
        raise R101ConservationValidationError("content-authorization-missing")


async def validate_r101_consumer_dry_run(
    report: R101ConservationReport,
    store: R101LedgerConsumerStore,
) -> str:
    """Reload the persisted inventory and return the ledger digest readied for use."""
    source = await store.r101_occurrence_ledger(report.old_run_id, report.new_run_id)
    source_by_key = _index_source_inventory(source)
    _validate_consumer_inventory(report, source, source_by_key)
    return report.json_identity


def _index_source_inventory(
    source: R101LedgerSource,
) -> dict[tuple[object, ...], OccurrenceInput]:
    source_by_key = {
        item.old_occurrence.structural_key: item for item in source.occurrences
    }
    if len(source_by_key) != len(source.occurrences):
        raise R101ConservationValidationError("duplicate-occurrence")
    return source_by_key


def _validate_consumer_inventory(
    report: R101ConservationReport,
    source: R101LedgerSource,
    source_by_key: dict[tuple[object, ...], OccurrenceInput],
) -> None:
    if set(source_by_key) != {item.structural_key for item in report.occurrences}:
        raise R101ConservationValidationError("source occurrence inventory mismatch")
    if source.non_r101_delta_evidence != report.non_r101_delta_evidence:
        raise R101ConservationValidationError("non-R101 inventory mismatch")
    for occurrence in report.occurrences:
        if not _source_links_match(
            source_by_key[occurrence.structural_key], occurrence
        ):
            raise R101ConservationValidationError("source link inventory mismatch")


def _source_links_match(source: OccurrenceInput, occurrence: LedgerOccurrence) -> bool:
    retained_matches = (
        occurrence.retained_r82_target is None
        or occurrence.retained_r82_target in source.retained_new_r101_links
    )
    return (
        source.old_links == occurrence.old_links
        and source.new_links == occurrence.new_links
        and retained_matches
    )


def r101_detector_identity() -> str:
    """Identify the exact classifier and strict occurrence-ledger schema served."""
    from ontolib.decomposition import stated_queries  # noqa: PLC0415

    return _sha256(
        _canonical(
            {
                "semantic_sources": {
                    value.__name__: inspect.getsource(value)
                    for value in (
                        StructuralOccurrence,
                        OccurrenceInput,
                        NonR101DeltaRow,
                        NonR101DeltaEvidence,
                        R82PathEdge,
                        LedgerBuildContext,
                        LedgerOccurrence,
                        ContentAuthorization,
                        R101ConservationReport,
                        r101_occurrence_ledger_query,
                        r101_ledger_query_identity,
                        r82_fact_identity,
                        _json_identity,
                        _report_identity,
                        _validate_report_paths,
                        _validate_report_path,
                        _validate_r82_evidence_kind,
                        _validate_uncovered_path,
                        _old_pair_at_path_endpoint,
                        _path_refusal,
                        _classify,
                        _ledger_counts,
                        _grouping,
                        _tsv_content,
                        build_r101_occurrence_ledger,
                        validate_r101_publication,
                        validate_r101_consumer_dry_run,
                    )
                },
                "stated_path_sources": {
                    value.__name__: inspect.getsource(value)
                    for value in (
                        stated_queries.build_part_of_candidate_paths_query,
                        stated_queries.build_part_of_expansion_query,
                        stated_queries._resolve_path_batch,
                        stated_queries.resolve_part_of_paths,
                    )
                },
                "report_schema": R101ConservationReport.model_json_schema(),
                "schema_version": R101_CONSERVATION_SCHEMA_VERSION,
            }
        )
    )


def r101_proof_identity(*identities: str) -> str:
    """Bind the complete ordered set of prerequisite proof identities."""
    if not identities or any(
        re.fullmatch(_SHA256, item) is None for item in identities
    ):
        raise R101ConservationValidationError("source-identity-mismatch")
    return _sha256(_canonical(identities))


def load_r101_conservation_report(path: Path) -> R101ConservationReport:
    """Load one metadata-free gzip member containing a strict schema-3 report."""
    if not path.name.endswith(".json.gz"):
        raise R101ConservationValidationError("report path must end in .json.gz")
    content = _decompress_report(path.read_bytes())
    try:
        json.loads(content, object_pairs_hook=_unique_json_object)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise R101ConservationValidationError("invalid JSON report") from error
    return R101ConservationReport.model_validate_json(content)


def _decompress_report(compressed: bytes) -> bytes:
    if (
        len(compressed) < _GZIP_HEADER_SIZE
        or compressed[:3] != b"\x1f\x8b\x08"
        or compressed[3] != 0
    ):
        raise R101ConservationValidationError("invalid gzip report")
    decompressor = zlib.decompressobj(wbits=31)
    try:
        content = decompressor.decompress(compressed) + decompressor.flush()
    except zlib.error as error:
        raise R101ConservationValidationError("invalid gzip report") from error
    if not decompressor.eof or decompressor.unused_data:
        raise R101ConservationValidationError(
            "gzip report contains trailing data or multiple members"
        )
    return content


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise R101ConservationValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _temporary(path: Path, content: bytes) -> str:
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return name


def write_r101_occurrence_ledger(
    json_path: Path,
    report: R101ConservationReport,
) -> None:
    """Atomically replace a deterministic compressed canonical JSON report."""
    if not json_path.name.endswith(".json.gz"):
        raise R101ConservationValidationError("report path must end in .json.gz")
    json_content = (
        json.dumps(
            report.model_dump(mode="json"),
            sort_keys=True,
            indent=2,
            ensure_ascii=True,
        ).encode()
        + b"\n"
    )
    tsv_content = r101_ledger_tsv_bytes(report)
    if _sha256(tsv_content) != report.tsv_identity:
        raise R101ConservationValidationError("source-identity-mismatch")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    staged = _temporary(json_path, gzip.compress(json_content, mtime=0))
    try:
        os.replace(staged, json_path)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(staged)
