"""Read-only, canonical evidence for an interrupted decomposition resume."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text

from ontolib.decomposition import axes
from ontolib.decomposition.extract import semantic_type_of_from_rows
from ontolib.decomposition.provenance_models import RunFingerprint
from ontolib.decomposition.site_resolution import (
    MORPHOLOGY_TO_ORGAN,
    MORPHOLOGY_TO_PRIMARY_SUBSITES,
)
from ontolib.decomposition.stated_queries import build_semantic_type_of_query

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncEngine

Sha256Identity = Annotated[
    str,
    Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
]
PositiveCount = Annotated[int, Field(gt=0)]
NonNegativeCount = Annotated[int, Field(ge=0)]

_FRESHNESS_FIELDS = frozenset(
    ("observed_at", "postgres_reads", "qlever_reads", "proof_identity")
)

SEMANTIC_DEPENDENCIES = (
    Path("ontolib/src/ontolib/decomposition/axes.py"),
    Path("ontolib/src/ontolib/decomposition/axis_contracts.py"),
    Path("ontolib/src/ontolib/decomposition/branches.py"),
    Path("ontolib/src/ontolib/decomposition/extract.py"),
    Path("ontolib/src/ontolib/decomposition/filler_selection.py"),
    Path("ontolib/src/ontolib/decomposition/run.py"),
    Path("ontolib/src/ontolib/decomposition/site_resolution.py"),
    Path("ontolib/src/ontolib/decomposition/stated_queries.py"),
)

PRE_RESUME_SQL = {
    "run": (
        "SELECT id, status, error_type, error_message, ncit_version, "
        "source_identity, fingerprint, fingerprint_sha256 FROM decomp_run "
        "WHERE id = :run_id"
    ),
    "work_items": (
        "SELECT concept_code, ordinal, state, attempt_count, semantic_type, "
        "semantic_types, outcome, is_decomposed, is_residual, "
        "has_complete_definition, constituent_count, minted_count, completed_at "
        "FROM decomp_work_item WHERE run_id = :run_id ORDER BY ordinal"
    ),
    "candidates": (
        "SELECT o.concept_code, o.occurrence_id, o.anchor_code, o.role_code, "
        "o.filler_code, m.filler_code AS morphology_code "
        "FROM decomp_source_occurrence o "
        "JOIN decomp_work_item w USING (run_id, concept_code) "
        "JOIN decomp_constituent m USING (run_id, concept_code) "
        "WHERE o.run_id = :run_id AND w.state = 'complete' "
        "AND o.role_code = 'R101' AND m.axis = 'op:Morphology' "
        "ORDER BY o.concept_code, o.occurrence_id"
    ),
    "integrity": (
        "SELECT "
        "(SELECT count(*) FROM decomp_work_item w WHERE w.run_id = :run_id "
        "AND ((w.state = 'complete' AND (w.completed_at IS NULL OR "
        "w.constituent_count IS NULL OR w.minted_count IS NULL OR "
        "w.outcome IS NULL OR w.semantic_types IS NULL)) OR "
        "(w.state <> 'complete' AND (w.completed_at IS NOT NULL OR "
        "w.constituent_count IS NOT NULL OR w.minted_count IS NOT NULL)))) "
        "AS completion_metadata_mismatch_count, "
        "(SELECT count(*) FROM decomp_work_item w WHERE w.run_id = :run_id "
        "AND w.state = 'complete' AND w.constituent_count <> "
        "(SELECT count(*) FROM decomp_constituent c WHERE c.run_id = w.run_id "
        "AND c.concept_code = w.concept_code)) AS constituent_count_mismatch_count, "
        "(SELECT count(*) FROM decomp_work_item w WHERE w.run_id = :run_id "
        "AND w.state = 'complete' AND w.minted_count <> "
        "(SELECT count(*) FROM decomp_minted_proposal m WHERE m.run_id = w.run_id "
        "AND m.concept_code = w.concept_code)) AS minted_count_mismatch_count, "
        "(SELECT count(*) FROM ("
        "SELECT c.run_id, c.concept_code FROM decomp_constituent c LEFT JOIN "
        "decomp_work_item w USING (run_id, concept_code) WHERE c.run_id = :run_id "
        "AND w.run_id IS NULL UNION ALL "
        "SELECT m.run_id, m.concept_code FROM decomp_minted_proposal m LEFT JOIN "
        "decomp_work_item w USING (run_id, concept_code) WHERE m.run_id = :run_id "
        "AND w.run_id IS NULL UNION ALL "
        "SELECT f.run_id, f.concept_code FROM decomp_definition_fact f LEFT JOIN "
        "decomp_work_item w USING (run_id, concept_code) WHERE f.run_id = :run_id "
        "AND w.run_id IS NULL UNION ALL "
        "SELECT g.run_id, g.concept_code FROM decomp_definition_group g LEFT JOIN "
        "decomp_work_item w USING (run_id, concept_code) WHERE g.run_id = :run_id "
        "AND w.run_id IS NULL UNION ALL "
        "SELECT e.run_id, e.concept_code FROM decomp_definition_group_edge e "
        "LEFT JOIN decomp_work_item w USING (run_id, concept_code) "
        "WHERE e.run_id = :run_id AND w.run_id IS NULL UNION ALL "
        "SELECT o.run_id, o.concept_code FROM decomp_source_occurrence o LEFT JOIN "
        "decomp_work_item w USING (run_id, concept_code) WHERE o.run_id = :run_id "
        "AND w.run_id IS NULL UNION ALL "
        "SELECT co.run_id, co.concept_code FROM decomp_constituent_occurrence co "
        "LEFT JOIN decomp_work_item w USING (run_id, concept_code) "
        "WHERE co.run_id = :run_id AND w.run_id IS NULL) orphan) "
        "AS child_orphan_count"
    ),
}

EXPECTED_RUN_ID = "neoplasm-0e88b7c0-eba0-42e6-8836-fa10f2604f46"
EXPECTED_RELEASE = "26.07d"
EXPECTED_SOURCE_IDENTITY = (
    "b58f48b5c19459c1273f3f4edf3fb67bd6f5e0e4c4d1c501218bf01b04ce6092"
)
EXPECTED_FINGERPRINT_IDENTITY = (
    "d50fb846dd56fff591b148abab4c0f03adf8e41bc38ef5909d8eb9a4f728d67a"
)
EXPECTED_COMPLETED_DIGEST = (
    "2e7f1697b1f6d28af22088d757492a6482bb0ee67d0363ba374b2a30461a7bf9"
)
EXPECTED_PENDING_DIGEST = (
    "8ebd90a8e143c6676d0ac70cdd58b0921724e81037e74ec03a93a175e6acabf1"
)
EXPECTED_WORKLIST_DIGEST = (
    "9b7d28be40d9294df645c9fa5405892d8b0564d62b25dfac2a3d71fc5e7e5dc6"
)
EXPECTED_CANDIDATE_COUNTS = (133, 193, 212, 5)
EXPECTED_CANDIDATE_DIGEST = (
    "8742b60f449a38cc5f640ce1d613fc67d51e4189d30dd792159ba9fb12c144bb"
)
EXPECTED_SENSITIVITY_COUNTS = (135, 215, 245, 7)
EXPECTED_SENSITIVITY_DIGEST = (
    "2658b836b43028df6e7b01dfddac6c7d67f43d722e28463f217e1444af3c3c15"
)
QUERY_VALIDATOR_SYMBOL_NAMES = frozenset(
    {
        "acquire_candidate_evidence",
        "affected_missing_p106",
        "build_semantic_type_of_query",
        "derive_candidate_population",
        "missing_p106_verdict",
        "parse_semantic_type_rows",
        "proof_invariants",
        "statement_is_read_only",
        "validation_authorization",
    }
)

_SQL_WRITE_TOKEN = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|CALL|COPY|GRANT|REVOKE)\b",
    re.IGNORECASE,
)
_SQL_LITERAL = re.compile(r"'(?:''|[^'])*'")


@dataclass(frozen=True, slots=True, order=True)
class CandidateTuple:
    concept_code: str
    filler_code: str
    morphology_code: str
    organ_code: str


@dataclass(frozen=True, slots=True, order=True)
class CandidateOccurrence:
    concept_code: str
    occurrence_id: str
    anchor_code: str
    filler_code: str
    morphology_code: str


@dataclass(frozen=True, slots=True)
class MissingP106Verdict:
    affected: tuple[CandidateOccurrence, ...]

    @property
    def affected_counts(self) -> tuple[int, int, int, int]:
        tuples = {
            (item.concept_code, item.filler_code, item.morphology_code)
            for item in self.affected
        }
        return (
            len({item.concept_code for item in self.affected}),
            len(tuples),
            len(self.affected),
            len({item.filler_code for item in self.affected}),
        )

    @property
    def authorizable(self) -> bool:
        return not self.affected


@dataclass(frozen=True, slots=True)
class CandidatePopulation:
    tuples: tuple[CandidateTuple, ...]
    occurrences: tuple[CandidateOccurrence, ...]

    @property
    def counts(self) -> tuple[int, int, int, int]:
        return (
            len({item.concept_code for item in self.tuples}),
            len(self.tuples),
            len(self.occurrences),
            len({item.filler_code for item in self.tuples}),
        )

    @property
    def identity(self) -> str:
        return candidate_tuple_identity(self.tuples)

    def missing_p106_verdict(
        self, semantic_types: Mapping[str, str | None]
    ) -> MissingP106Verdict:
        expected = {item.filler_code for item in self.tuples}
        if set(semantic_types) != expected:
            raise ValueError("P106 result must cover every candidate filler exactly")
        return MissingP106Verdict(
            affected=tuple(
                item
                for item in self.occurrences
                if semantic_types[item.filler_code] is None
            )
        )


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    production: CandidatePopulation
    route_filter_sensitivity: CandidatePopulation
    validation: MissingP106Verdict
    semantic_types: Mapping[str, str | None]
    postgres_reads: int
    qlever_reads: int


class SelectClient(Protocol):
    async def select(
        self, query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str]]: ...


def derive_candidate_population(
    rows: tuple[CandidateOccurrence, ...],
    *,
    morphology_to_organ: Mapping[str, str],
    morphology_to_subsites: Mapping[str, frozenset[str]],
    lineage_genera: frozenset[str],
    apply_route_filters: bool,
) -> CandidatePopulation:
    """Reproduce morphology-organ branch reachability from source occurrences."""
    grouped = _group_candidate_occurrences(rows)
    selected: list[CandidateOccurrence] = []
    tuples: set[CandidateTuple] = set()
    for (concept_code, morphology_code), occurrences in grouped.items():
        group = _derive_candidate_group(
            concept_code=concept_code,
            morphology_code=morphology_code,
            occurrences=occurrences,
            morphology_to_organ=morphology_to_organ,
            morphology_to_subsites=morphology_to_subsites,
            lineage_genera=lineage_genera,
            apply_route_filters=apply_route_filters,
        )
        selected.extend(group.occurrences)
        tuples.update(group.tuples)
    return CandidatePopulation(
        tuples=tuple(sorted(tuples)), occurrences=tuple(sorted(selected))
    )


def _group_candidate_occurrences(
    rows: tuple[CandidateOccurrence, ...],
) -> dict[tuple[str, str], list[CandidateOccurrence]]:
    grouped: dict[tuple[str, str], list[CandidateOccurrence]] = {}
    for row in rows:
        grouped.setdefault((row.concept_code, row.morphology_code), []).append(row)
    return grouped


def _derive_candidate_group(
    *,
    concept_code: str,
    morphology_code: str,
    occurrences: list[CandidateOccurrence],
    morphology_to_organ: Mapping[str, str],
    morphology_to_subsites: Mapping[str, frozenset[str]],
    lineage_genera: frozenset[str],
    apply_route_filters: bool,
) -> CandidatePopulation:
    organ = morphology_to_organ.get(morphology_code)
    if organ is None:
        return CandidatePopulation((), ())
    subsites = morphology_to_subsites.get(morphology_code, frozenset())
    routed = _routed_occurrences(
        occurrences, subsites, lineage_genera, apply_route_filters
    )
    fillers = {item.filler_code for item in routed}
    if not _has_organ_context(fillers, organ):
        return CandidatePopulation((), ())
    residual = fillers - {organ} - set(subsites)
    selected = tuple(item for item in routed if item.filler_code in residual)
    tuples = tuple(
        CandidateTuple(concept_code, filler, morphology_code, organ)
        for filler in residual
    )
    return CandidatePopulation(tuples, selected)


def _has_organ_context(fillers: set[str], organ: str) -> bool:
    return len(fillers) > 1 and organ in fillers


def _routed_occurrences(
    occurrences: list[CandidateOccurrence],
    subsites: frozenset[str],
    lineage_genera: frozenset[str],
    apply_route_filters: bool,
) -> tuple[CandidateOccurrence, ...]:
    if not apply_route_filters:
        return tuple(occurrences)
    return tuple(
        item
        for item in occurrences
        if item.anchor_code not in lineage_genera and item.filler_code not in subsites
    )


_NCIT_CODE = re.compile(r"^C[0-9]+$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


async def acquire_candidate_evidence(
    engine: AsyncEngine, run_id: str, client: SelectClient
) -> CandidateEvidence:
    """Acquire branch-reachability evidence through read-only boundaries."""
    raw_rows = await _read_candidate_rows(engine, run_id)
    occurrence_rows = _parse_candidate_rows(raw_rows)
    production = _candidate_population(occurrence_rows, apply_route_filters=True)
    sensitivity = _candidate_population(occurrence_rows, apply_route_filters=False)
    semantic_types = await _candidate_semantic_types(client, production)
    return CandidateEvidence(
        production=production,
        route_filter_sensitivity=sensitivity,
        validation=production.missing_p106_verdict(semantic_types),
        semantic_types=semantic_types,
        postgres_reads=1,
        qlever_reads=1,
    )


async def _read_candidate_rows(
    engine: AsyncEngine, run_id: str
) -> tuple[RowMapping, ...]:
    connection = await engine.connect()
    connection = await connection.execution_options(
        isolation_level="REPEATABLE READ", postgresql_readonly=True
    )
    try:
        async with connection.begin():
            result = await connection.execute(
                text(PRE_RESUME_SQL["candidates"]), {"run_id": run_id}
            )
            return tuple(result.mappings().all())
    finally:
        await connection.close()


def _parse_candidate_rows(
    raw_rows: tuple[RowMapping, ...],
) -> tuple[CandidateOccurrence, ...]:
    rows: list[CandidateOccurrence] = []
    morphology_by_concept: dict[str, str] = {}
    for raw in raw_rows:
        values = {
            name: raw[name]
            for name in (
                "concept_code",
                "occurrence_id",
                "anchor_code",
                "filler_code",
                "morphology_code",
            )
        }
        _validate_candidate_row(values)
        concept_code = values["concept_code"]
        morphology_code = values["morphology_code"]
        prior_morphology = morphology_by_concept.setdefault(
            concept_code, morphology_code
        )
        if prior_morphology != morphology_code:
            raise ValueError("completed concept has multiple parent morphologies")
        rows.append(CandidateOccurrence(**values))
    return tuple(rows)


def _validate_candidate_row(values: dict[str, Any]) -> None:
    if not all(isinstance(value, str) for value in values.values()):
        raise ValueError("candidate source row contains a non-string field")
    code_fields = ("concept_code", "anchor_code", "filler_code", "morphology_code")
    codes_are_valid = all(_NCIT_CODE.fullmatch(values[name]) for name in code_fields)
    occurrence_is_valid = _DIGEST.fullmatch(values["occurrence_id"]) is not None
    if not codes_are_valid or not occurrence_is_valid:
        raise ValueError("candidate source row is malformed")


def _candidate_population(
    rows: tuple[CandidateOccurrence, ...], *, apply_route_filters: bool
) -> CandidatePopulation:
    return derive_candidate_population(
        rows,
        morphology_to_organ=MORPHOLOGY_TO_ORGAN,
        morphology_to_subsites=MORPHOLOGY_TO_PRIMARY_SUBSITES,
        lineage_genera=axes.LINEAGE_GENERIC_GENERA,
        apply_route_filters=apply_route_filters,
    )


async def _candidate_semantic_types(
    client: SelectClient, production: CandidatePopulation
) -> dict[str, str | None]:
    filler_codes = sorted({item.filler_code for item in production.tuples})
    semantic_rows = await client.select(
        build_semantic_type_of_query(filler_codes),
        required_variables={"code", "st"},
    )
    parsed = semantic_type_of_from_rows(semantic_rows)
    return {
        code: (
            axes.ORGAN_SEMANTIC_TYPE
            if axes.ORGAN_SEMANTIC_TYPE in parsed.get(code, [])
            else min(parsed[code])
            if parsed.get(code)
            else None
        )
        for code in filler_codes
    }


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def ordered_code_identity(codes: tuple[str, ...]) -> str:
    """Hash persisted ordinal order with the contract's newline encoding."""
    return _sha256("\n".join(codes).encode())


def candidate_tuple_identity(candidates: tuple[CandidateTuple, ...]) -> str:
    """Hash unique candidate tuples in lexical line order."""
    lines = sorted(
        {
            "\t".join(
                (
                    item.concept_code,
                    item.filler_code,
                    item.morphology_code,
                    item.organ_code,
                )
            )
            for item in candidates
        }
    )
    return _sha256("\n".join(lines).encode())


def cohort_identity(
    completed_count: int,
    completed_digest: str,
    pending_count: int,
    pending_digest: str,
) -> str:
    payload = {
        "completed": {
            "label": "pre-fix-completed",
            "count": completed_count,
            "digest": completed_digest,
        },
        "pending": {
            "label": "post-fix-pending",
            "count": pending_count,
            "digest": pending_digest,
        },
    }
    return _sha256(_canonical_json(payload).encode())


def statement_is_read_only(statement: str) -> bool:
    """Accept one SELECT/CTE statement unless it contains a known SQL write keyword."""
    normalized = _SQL_LITERAL.sub("''", statement).strip()
    if ";" in normalized.rstrip(";"):
        return False
    starts_read = normalized.upper().startswith(("SELECT ", "WITH "))
    return starts_read and not _SQL_WRITE_TOKEN.search(normalized)


def affected_missing_p106(
    candidates: tuple[CandidateTuple, ...], semantic_types: Mapping[str, str | None]
) -> tuple[CandidateTuple, ...]:
    """Return candidate tuples whose residual filler lacks stated P106 evidence."""
    expected = {item.filler_code for item in candidates}
    if set(semantic_types) != expected:
        raise ValueError("P106 result must cover every candidate filler exactly")
    affected = {item for item in candidates if semantic_types[item.filler_code] is None}
    return tuple(sorted(affected))


def semantic_dependency_identity(
    root: Path, dependencies: tuple[Path, ...] = SEMANTIC_DEPENDENCIES
) -> tuple[str, dict[str, str]]:
    """Bind the raw bytes of the fixed semantic-dependency declaration."""
    identities = {
        path.as_posix(): _sha256((root / path).read_bytes())
        for path in sorted(dependencies)
    }
    return _sha256(_canonical_json(identities).encode()), identities


def site_table_identity(
    morphology_to_organ: Mapping[str, str],
    morphology_to_subsites: Mapping[str, frozenset[str]],
) -> str:
    payload = {
        "morphology_to_organ": dict(sorted(morphology_to_organ.items())),
        "morphology_to_primary_subsites": {
            key: sorted(value) for key, value in sorted(morphology_to_subsites.items())
        },
    }
    return _sha256(_canonical_json(payload).encode())


def query_validator_identity() -> str:
    """Bind the fixed SQL/SPARQL builder and validator symbol declaration."""
    symbols = {
        "acquire_candidate_evidence": acquire_candidate_evidence,
        "affected_missing_p106": affected_missing_p106,
        "build_semantic_type_of_query": build_semantic_type_of_query,
        "derive_candidate_population": derive_candidate_population,
        "missing_p106_verdict": CandidatePopulation.missing_p106_verdict,
        "parse_semantic_type_rows": semantic_type_of_from_rows,
        "proof_invariants": PreResumeProof.validate_proof_invariants,
        "statement_is_read_only": statement_is_read_only,
        "validation_authorization": (
            PreResumeValidationEvidence.require_safe_authorization
        ),
    }
    if set(symbols) != QUERY_VALIDATOR_SYMBOL_NAMES:
        raise RuntimeError("query/validator identity symbol allowlist drift")
    identities = {
        name: _sha256(inspect.getsource(symbol).strip().encode())
        for name, symbol in sorted(symbols.items())
    }
    identities.update(
        {
            f"sql:{name}": _sha256(statement.encode())
            for name, statement in sorted(PRE_RESUME_SQL.items())
        }
    )
    return _sha256(_canonical_json(identities).encode())


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class PreResumeValidationEvidence(_StrictModel):
    """Observed impact and the authorization verdict it supports."""

    affected_concept_count: NonNegativeCount
    affected_tuple_count: NonNegativeCount
    affected_occurrence_count: NonNegativeCount
    affected_residual_filler_count: NonNegativeCount
    authorizable: bool
    reason: str | None

    @model_validator(mode="after")
    def require_safe_authorization(self) -> Self:
        affected_counts = (
            self.affected_concept_count,
            self.affected_tuple_count,
            self.affected_occurrence_count,
            self.affected_residual_filler_count,
        )
        if any(affected_counts) and self.authorizable:
            raise ValueError("nonzero affected counts cannot be authorizable")
        if any(affected_counts) and (self.reason is None or not self.reason.strip()):
            raise ValueError("reason is required for nonzero affected counts")
        return self


class SemanticDependencyIdentity(_StrictModel):
    path: str = Field(min_length=1)
    identity: Sha256Identity


class RouteFilterSensitivityEvidence(_StrictModel):
    candidate_concept_count: PositiveCount
    candidate_tuple_count: PositiveCount
    candidate_occurrence_count: PositiveCount
    residual_filler_count: PositiveCount
    candidate_tuple_digest: Sha256Identity


class PreResumeProof(_StrictModel):
    """Immutable evidence required before an interrupted run may resume."""

    schema_version: Literal[1]
    run_id: str = Field(min_length=1)
    release: str = Field(min_length=1)
    status: Literal["failed"]
    error_type: str = Field(min_length=1)
    error_message: str = Field(min_length=1)
    source_identity: Sha256Identity
    fingerprint_identity: Sha256Identity
    cohort_identity: Sha256Identity
    worklist_identity: Sha256Identity
    worklist_digest: Sha256Identity
    candidate_identity: Sha256Identity
    candidate_tuple_digest: Sha256Identity
    semantic_identity: Sha256Identity
    query_identity: Sha256Identity
    table_identity: Sha256Identity
    pre_fix_execution_identity: Sha256Identity
    semantic_dependencies: tuple[SemanticDependencyIdentity, ...] = Field(min_length=1)
    completed_cohort_label: Literal["pre-fix-completed"]
    pending_cohort_label: Literal["post-fix-pending"]
    claim: Literal[
        "the patched morphology-organ missing-P106 branch was unreachable for "
        "every pre-fix-completed occurrence"
    ]
    completed_cohort_digest: Sha256Identity
    pending_cohort_digest: Sha256Identity
    candidate_concept_count: PositiveCount
    candidate_tuple_count: PositiveCount
    candidate_occurrence_count: PositiveCount
    residual_filler_denominator_count: PositiveCount
    route_filter_sensitivity: RouteFilterSensitivityEvidence
    completed_count: NonNegativeCount
    pending_count: NonNegativeCount
    worklist_count: NonNegativeCount
    completion_metadata_mismatch_count: NonNegativeCount
    constituent_count_mismatch_count: NonNegativeCount
    minted_count_mismatch_count: NonNegativeCount
    child_orphan_count: NonNegativeCount
    validation: PreResumeValidationEvidence
    postgres_reads: PositiveCount
    qlever_reads: PositiveCount

    @model_validator(mode="after")
    def validate_proof_invariants(self) -> Self:
        _validate_proof_counts(self)
        _validate_proof_identities(self)
        _validate_sensitivity_control(self)
        _validate_proof_integrity(self)
        return self


def _validate_proof_counts(proof: PreResumeProof) -> None:
    if proof.completed_count + proof.pending_count != proof.worklist_count:
        raise ValueError("completed_count + pending_count must equal worklist_count")
    if proof.completed_cohort_digest == proof.pending_cohort_digest:
        raise ValueError("cohort digests must be distinct")


def _validate_proof_identities(proof: PreResumeProof) -> None:
    if proof.worklist_identity != proof.worklist_digest:
        raise ValueError("worklist digest must equal worklist identity")
    if proof.candidate_identity != proof.candidate_tuple_digest:
        raise ValueError("candidate tuple digest must equal candidate identity")
    expected = cohort_identity(
        proof.completed_count,
        proof.completed_cohort_digest,
        proof.pending_count,
        proof.pending_cohort_digest,
    )
    if proof.cohort_identity != expected:
        raise ValueError("cohort identity does not match cohort evidence")


def _validate_sensitivity_control(proof: PreResumeProof) -> None:
    sensitivity = proof.route_filter_sensitivity
    observed = (
        sensitivity.candidate_concept_count,
        sensitivity.candidate_tuple_count,
        sensitivity.candidate_occurrence_count,
        sensitivity.residual_filler_count,
        sensitivity.candidate_tuple_digest,
    )
    production = (
        proof.candidate_concept_count,
        proof.candidate_tuple_count,
        proof.candidate_occurrence_count,
        proof.residual_filler_denominator_count,
        proof.candidate_tuple_digest,
    )
    if observed == production:
        raise ValueError("route-filter sensitivity must exercise a changed population")


def _validate_proof_integrity(proof: PreResumeProof) -> None:
    counts = (
        proof.completion_metadata_mismatch_count,
        proof.constituent_count_mismatch_count,
        proof.minted_count_mismatch_count,
        proof.child_orphan_count,
    )
    if any(counts):
        raise ValueError("completion and child-table integrity must be exact")


def canonical_pre_resume_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the semantic proof payload, excluding invocation-local witnesses."""
    return {
        key: value for key, value in payload.items() if key not in _FRESHNESS_FIELDS
    }


def canonical_pre_resume_json(payload: Mapping[str, Any]) -> str:
    """Serialize semantic proof data deterministically."""
    return json.dumps(
        canonical_pre_resume_payload(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def pre_resume_proof_identity(payload: Mapping[str, Any]) -> str:
    """Bind only canonical semantic proof bytes."""
    encoded = canonical_pre_resume_json(payload).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class _ProofRows:
    run: RowMapping
    work: tuple[RowMapping, ...]
    integrity: dict[str, Any]
    postgres_reads: int


@dataclass(frozen=True, slots=True)
class _WorkCohorts:
    worklist: tuple[str, ...]
    completed: tuple[str, ...]
    pending: tuple[str, ...]
    worklist_digest: str
    completed_digest: str
    pending_digest: str


def _validate_proof_request(
    run_id: str,
    live_source_identity: str,
    live_release: str,
    source_observation_reads: int,
) -> None:
    if run_id != EXPECTED_RUN_ID:
        raise ValueError("pre-resume proof run identity drift")
    if (live_source_identity, live_release) != (
        EXPECTED_SOURCE_IDENTITY,
        EXPECTED_RELEASE,
    ):
        raise ValueError("live NCIt source identity drift")
    if source_observation_reads <= 0:
        raise ValueError("source observation must execute QLever reads")


async def _read_proof_rows(engine: AsyncEngine, run_id: str) -> _ProofRows:
    connection = await engine.connect()
    connection = await connection.execution_options(
        isolation_level="REPEATABLE READ", postgresql_readonly=True
    )
    try:
        async with connection.begin():
            run = (
                (
                    await connection.execute(
                        text(PRE_RESUME_SQL["run"]), {"run_id": run_id}
                    )
                )
                .mappings()
                .one()
            )
            work = tuple(
                (
                    await connection.execute(
                        text(PRE_RESUME_SQL["work_items"]), {"run_id": run_id}
                    )
                )
                .mappings()
                .all()
            )
            integrity = dict(
                (
                    await connection.execute(
                        text(PRE_RESUME_SQL["integrity"]), {"run_id": run_id}
                    )
                )
                .mappings()
                .one()
            )
            return _ProofRows(run, work, integrity, 3)
    finally:
        await connection.close()


def _validated_fingerprint(run: RowMapping) -> RunFingerprint:
    expected = {
        "id": EXPECTED_RUN_ID,
        "status": "failed",
        "error_type": "BrokenPipeError",
        "error_message": "[Errno 32] Broken pipe",
        "ncit_version": EXPECTED_RELEASE,
        "source_identity": EXPECTED_SOURCE_IDENTITY,
        "fingerprint_sha256": EXPECTED_FINGERPRINT_IDENTITY,
    }
    if any(run[name] != value for name, value in expected.items()):
        raise ValueError("persisted run identity or failure snapshot drift")
    fingerprint = RunFingerprint.model_validate_json(
        json.dumps(run["fingerprint"], sort_keys=True)
    )
    if fingerprint.identity != run["fingerprint_sha256"]:
        raise ValueError("persisted fingerprint payload does not match its identity")
    return fingerprint


def _work_cohorts(rows: tuple[RowMapping, ...]) -> _WorkCohorts:
    if any(row["state"] not in {"complete", "pending"} for row in rows):
        raise ValueError("worklist contains a non-complete/non-pending state")
    if any(not _attempt_matches_state(row) for row in rows):
        raise ValueError("work-item attempt counts do not match state")
    worklist = _codes_for_state(rows)
    completed = _codes_for_state(rows, "complete")
    pending = _codes_for_state(rows, "pending")
    result = _WorkCohorts(
        worklist,
        completed,
        pending,
        ordered_code_identity(worklist),
        ordered_code_identity(completed),
        ordered_code_identity(pending),
    )
    _validate_work_cohorts(result)
    return result


def _codes_for_state(
    rows: tuple[RowMapping, ...], state: str | None = None
) -> tuple[str, ...]:
    if state is None:
        return tuple(row["concept_code"] for row in rows)
    return tuple(row["concept_code"] for row in rows if row["state"] == state)


def _attempt_matches_state(row: RowMapping) -> bool:
    return (row["state"] == "complete" and row["attempt_count"] > 0) or (
        row["state"] == "pending" and row["attempt_count"] == 0
    )


def _validate_work_cohorts(cohorts: _WorkCohorts) -> None:
    counts = (len(cohorts.completed), len(cohorts.pending), len(cohorts.worklist))
    digests = (
        cohorts.completed_digest,
        cohorts.pending_digest,
        cohorts.worklist_digest,
    )
    if counts != (5900, 9733, 15633) or digests != (
        EXPECTED_COMPLETED_DIGEST,
        EXPECTED_PENDING_DIGEST,
        EXPECTED_WORKLIST_DIGEST,
    ):
        raise ValueError("worklist count or ordinal digest drift")


def _validate_candidate_evidence(candidate: CandidateEvidence) -> None:
    if (
        candidate.production.counts != EXPECTED_CANDIDATE_COUNTS
        or candidate.production.identity != EXPECTED_CANDIDATE_DIGEST
    ):
        raise ValueError("production candidate denominator drift")
    if (
        candidate.route_filter_sensitivity.counts != EXPECTED_SENSITIVITY_COUNTS
        or candidate.route_filter_sensitivity.identity != EXPECTED_SENSITIVITY_DIGEST
    ):
        raise ValueError("route-filter sensitivity control drift")
    if not candidate.validation.authorizable:
        raise ValueError("missing-P106 affected set is nonzero")


def _build_proof(
    *,
    run_id: str,
    live_release: str,
    live_source_identity: str,
    fingerprint: RunFingerprint,
    cohorts: _WorkCohorts,
    candidate: CandidateEvidence,
    semantic_identity: str,
    dependency_map: dict[str, str],
    postgres_reads: int,
    qlever_reads: int,
) -> PreResumeProof:
    sensitivity = candidate.route_filter_sensitivity
    return PreResumeProof(
        schema_version=1,
        run_id=run_id,
        release=live_release,
        status="failed",
        error_type="BrokenPipeError",
        error_message="[Errno 32] Broken pipe",
        source_identity=live_source_identity,
        fingerprint_identity=fingerprint.identity,
        cohort_identity=cohort_identity(
            len(cohorts.completed),
            cohorts.completed_digest,
            len(cohorts.pending),
            cohorts.pending_digest,
        ),
        worklist_identity=cohorts.worklist_digest,
        worklist_digest=cohorts.worklist_digest,
        candidate_identity=candidate.production.identity,
        candidate_tuple_digest=candidate.production.identity,
        semantic_identity=semantic_identity,
        query_identity=query_validator_identity(),
        table_identity=site_table_identity(
            MORPHOLOGY_TO_ORGAN, MORPHOLOGY_TO_PRIMARY_SUBSITES
        ),
        pre_fix_execution_identity=fingerprint.identity,
        semantic_dependencies=tuple(
            SemanticDependencyIdentity(path=path, identity=identity)
            for path, identity in sorted(dependency_map.items())
        ),
        completed_cohort_label="pre-fix-completed",
        pending_cohort_label="post-fix-pending",
        claim=(
            "the patched morphology-organ missing-P106 branch was unreachable for "
            "every pre-fix-completed occurrence"
        ),
        completed_cohort_digest=cohorts.completed_digest,
        pending_cohort_digest=cohorts.pending_digest,
        candidate_concept_count=candidate.production.counts[0],
        candidate_tuple_count=candidate.production.counts[1],
        candidate_occurrence_count=candidate.production.counts[2],
        residual_filler_denominator_count=candidate.production.counts[3],
        route_filter_sensitivity=RouteFilterSensitivityEvidence(
            candidate_concept_count=sensitivity.counts[0],
            candidate_tuple_count=sensitivity.counts[1],
            candidate_occurrence_count=sensitivity.counts[2],
            residual_filler_count=sensitivity.counts[3],
            candidate_tuple_digest=sensitivity.identity,
        ),
        completed_count=len(cohorts.completed),
        pending_count=len(cohorts.pending),
        worklist_count=len(cohorts.worklist),
        completion_metadata_mismatch_count=0,
        constituent_count_mismatch_count=0,
        minted_count_mismatch_count=0,
        child_orphan_count=0,
        validation=_validation_evidence(candidate.validation),
        postgres_reads=postgres_reads,
        qlever_reads=qlever_reads,
    )


def _validation_evidence(
    verdict: MissingP106Verdict,
) -> PreResumeValidationEvidence:
    counts = verdict.affected_counts
    return PreResumeValidationEvidence(
        affected_concept_count=counts[0],
        affected_tuple_count=counts[1],
        affected_occurrence_count=counts[2],
        affected_residual_filler_count=counts[3],
        authorizable=verdict.authorizable,
        reason=None if verdict.authorizable else "missing-P106 affected set is nonzero",
    )


async def generate_pre_resume_proof(
    *,
    engine: AsyncEngine,
    run_id: str,
    client: SelectClient,
    repo_root: Path,
    live_source_identity: str,
    live_release: str,
    source_observation_reads: int,
) -> dict[str, Any]:
    """Generate fail-closed proof data without changing either source boundary."""
    _validate_proof_request(
        run_id, live_source_identity, live_release, source_observation_reads
    )
    rows = await _read_proof_rows(engine, run_id)
    fingerprint = _validated_fingerprint(rows.run)
    cohorts = _work_cohorts(rows.work)
    if any(rows.integrity.values()):
        raise ValueError("completion or child-table integrity drift")
    candidate = await acquire_candidate_evidence(engine, run_id, client)
    _validate_candidate_evidence(candidate)
    semantic_identity, dependency_map = semantic_dependency_identity(repo_root)
    proof = _build_proof(
        run_id=run_id,
        live_release=live_release,
        live_source_identity=live_source_identity,
        fingerprint=fingerprint,
        cohorts=cohorts,
        candidate=candidate,
        semantic_identity=semantic_identity,
        dependency_map=dependency_map,
        postgres_reads=rows.postgres_reads + candidate.postgres_reads,
        qlever_reads=source_observation_reads + candidate.qlever_reads,
    )
    payload = proof.model_dump(mode="json")
    payload["observed_at"] = datetime.now(UTC).isoformat()
    payload["proof_identity"] = pre_resume_proof_identity(payload)
    return payload


def write_pre_resume_proof(path: Path, payload: Mapping[str, Any]) -> None:
    """Write one deterministic-key proof document to an explicit path."""
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
