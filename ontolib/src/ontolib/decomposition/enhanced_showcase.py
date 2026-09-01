"""Strict, recoverable overlay for the seven-concept enhanced-NCIt showcase."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from importlib.resources import files
from typing import Any, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from rdflib import RDF, Graph, Literal, Namespace, URIRef

from ontolib.decomposition import vocab
from ontolib.decomposition.collapse_policy import (
    CollapseVetoPolicy,
    load_packaged_collapse_veto_policy,
)
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.sparql_transport import safe_iri


class Disposition(StrEnum):
    INCLUDE = "include"
    EXCLUDE = "exclude"
    UNRESOLVED_VISIBLE = "unresolved-visible"


class DecisionAuthority(StrEnum):
    SOURCE_STATED = "source-stated"
    PROJECT_PROVISIONAL = "project-provisional"
    LOCALLY_APPROVED = "locally-approved"
    NCI_ADOPTED = "nci-adopted"


class EvidenceSupport(StrEnum):
    SOURCE_STATED = "source-stated"
    PEER_REVIEWED_SUPPORTED = "peer-reviewed-supported"
    PROJECT_INFERENCE = "project-inference"
    PEER_REVIEWED_NOT_FOUND = "peer-reviewed-not-found"


class RepresentationName(StrEnum):
    ENHANCED_NCIT_SHOWCASE = "enhanced-ncit-showcase"


class SourceRelease(StrEnum):
    NCIT_26_07D = "26.07d"


class OverlayAlgorithm(StrEnum):
    EXACT_AXIS_FILLER_V1 = "exact-axis-filler-overlay-v1"


SHOWCASE_GRAPH_IRI = f"{vocab.DECOMPOSED_GRAPH_IRI}/enhanced-ncit-showcase"
_RESOURCE = "data/enhanced-ncit-showcase.json"
_SHA256 = r"^[0-9a-f]{64}$"
_SHA256_LENGTH = 64
_DECISION_ROW_LENGTH = 11
_CODE = r"^C[0-9]+$"
_AXIS = r"^op:[A-Za-z][A-Za-z0-9]*$"
_EXPECTED_CODES = {
    "C27262",
    "C102870",
    "C6135",
    "C4791",
    "C100054",
    "C198031",
    "C35756",
}
_OP = Namespace(vocab.ONTOPRISM_NS)


class ShowcasePolicyError(ValueError):
    """The showcase policy or its isolated graph is unsafe to activate."""


class _ShowcaseGraphClient(Protocol):
    async def load(
        self,
        data: bytes,
        *,
        content_type: str,
        graph_iri: str | None = None,
        replace: bool = True,
    ) -> None: ...

    async def update(self, update: str) -> None: ...


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class ShowcaseConstituent(_StrictModel):
    axis: str = Field(pattern=_AXIS)
    filler: str = Field(pattern=_CODE)
    label: str | None = None


class ShowcaseDecision(_StrictModel):
    candidate_id: str = Field(pattern=r"^C[0-9]+-P[0-9]+$")
    axis: str = Field(pattern=_AXIS)
    filler: str = Field(pattern=_CODE)
    label: str
    disposition: Disposition
    authority: DecisionAuthority
    support: tuple[EvidenceSupport, ...]
    rationale: str = Field(min_length=1)
    limitations: str = Field(min_length=1)
    source_occurrence_ids: tuple[str, ...] = ()
    group: str | None = None

    @property
    def concept_code(self) -> str:
        return self.candidate_id.split("-", 1)[0]

    @model_validator(mode="after")
    def _validate_authority_support(self) -> Self:
        _validate_support(self)
        _validate_authority(self)
        _validate_occurrences(self.source_occurrence_ids)
        return self


def _validate_support(decision: ShowcaseDecision) -> None:
    if not decision.support or len(set(decision.support)) != len(decision.support):
        raise ValueError("support must be non-empty and unique")
    unsupported = (EvidenceSupport.PEER_REVIEWED_NOT_FOUND,)
    if decision.disposition == Disposition.INCLUDE and decision.support == unsupported:
        raise ValueError("peer-reviewed-not-found cannot solely support inclusion")


def _validate_authority(decision: ShowcaseDecision) -> None:
    if decision.authority == DecisionAuthority.SOURCE_STATED:
        _validate_source_authority(decision)
        return
    if decision.authority == DecisionAuthority.PROJECT_PROVISIONAL:
        _validate_project_authority(decision)


def _validate_source_authority(decision: ShowcaseDecision) -> None:
    missing_support = EvidenceSupport.SOURCE_STATED not in decision.support
    if missing_support or not decision.source_occurrence_ids:
        raise ValueError("source-stated authority requires source support and binding")


def _validate_project_authority(decision: ShowcaseDecision) -> None:
    if decision.disposition != Disposition.INCLUDE:
        return
    missing_inference = EvidenceSupport.PROJECT_INFERENCE not in decision.support
    if missing_inference or not decision.limitations.strip():
        raise ValueError(
            "project-provisional include requires project-inference and limitations"
        )


def _validate_occurrences(occurrences: tuple[str, ...]) -> None:
    for occurrence in occurrences:
        valid_length = len(occurrence) == _SHA256_LENGTH
        valid_characters = all(c in "0123456789abcdef" for c in occurrence)
        if not valid_length or not valid_characters:
            raise ValueError("source occurrence must be a SHA-256 identity")


class ShowcaseConceptPolicy(_StrictModel):
    code: str = Field(pattern=_CODE)
    label: str
    decisions: tuple[ShowcaseDecision, ...]
    groups: tuple[tuple[str, ...], ...] = ()

    @model_validator(mode="after")
    def _validate_complete_partition(self) -> Self:
        _validate_candidate_keys(self)
        _validate_groups(self)
        return self


def _validate_candidate_keys(concept: ShowcaseConceptPolicy) -> None:
    keys = tuple((item.axis, item.filler) for item in concept.decisions)
    if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
        raise ValueError("showcase candidates must be canonical and unique")
    ids = {item.candidate_id for item in concept.decisions}
    if any(not item.startswith(f"{concept.code}-P") for item in ids):
        raise ValueError("candidate ID belongs to a different concept")


def _validate_groups(concept: ShowcaseConceptPolicy) -> None:
    ids = {item.candidate_id for item in concept.decisions}
    grouped = tuple(candidate for group in concept.groups for candidate in group)
    if len(grouped) != len(set(grouped)) or not set(grouped).issubset(ids):
        raise ValueError("showcase groups must reference candidates exactly once")


def _canonical(payload: object) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


class ShowcaseDecisionSet(_StrictModel):
    schema_version: int = Field(ge=1, le=1)
    representation: RepresentationName
    source_release: SourceRelease
    overlay_algorithm: OverlayAlgorithm
    concepts: tuple[ShowcaseConceptPolicy, ...]
    decision_set_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_set(self) -> Self:
        codes = tuple(concept.code for concept in self.concepts)
        if codes != tuple(sorted(_EXPECTED_CODES)):
            raise ValueError(
                "showcase decision set must contain exactly seven canonical concepts"
            )
        expected = hashlib.sha256(
            _canonical(self.model_dump(mode="json", exclude={"decision_set_identity"}))
        ).hexdigest()
        if self.decision_set_identity != expected:
            raise ValueError("showcase decision-set identity differs")
        return self

    def concept(self, code: str) -> ShowcaseConceptPolicy:
        for concept in self.concepts:
            if concept.code == code:
                return concept
        raise ShowcasePolicyError(f"{code} is outside the enhanced-NCIt showcase")


class EnhancedNcitShowcaseView(_StrictModel):
    representation: RepresentationName = RepresentationName.ENHANCED_NCIT_SHOWCASE
    banner: str
    code: str
    base_representation_identity: str = Field(pattern=_SHA256)
    decision_set_identity: str = Field(pattern=_SHA256)
    effective_representation_identity: str = Field(pattern=_SHA256)
    base_constituents: tuple[ShowcaseConstituent, ...]
    effective_constituents: tuple[ShowcaseConstituent, ...]
    unresolved_visible: tuple[ShowcaseDecision, ...]
    decisions: tuple[ShowcaseDecision, ...]


class ShowcaseOrthogonality(_StrictModel):
    concept_roots: tuple[str, ...]
    source_occurrences: tuple[str, ...]
    runtime_keys: tuple[str, ...]


def _expand_resource(payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != {
        "schema_version",
        "representation",
        "source_release",
        "overlay_algorithm",
        "concepts",
    }:
        raise ShowcasePolicyError(
            "packaged showcase decision set has unknown or missing fields"
        )
    concepts = tuple(
        _expand_concept(raw_concept)
        for raw_concept in payload["concepts"]  # type: ignore[union-attr]
    )
    return {
        **payload,
        "representation": RepresentationName(payload["representation"]),
        "source_release": SourceRelease(payload["source_release"]),
        "overlay_algorithm": OverlayAlgorithm(payload["overlay_algorithm"]),
        "concepts": concepts,
    }


def _expand_concept(raw_concept: dict[str, Any]) -> dict[str, Any]:
    if set(raw_concept) != {"code", "label", "groups", "decisions"}:
        raise ShowcasePolicyError(
            "packaged showcase concept has unknown or missing fields"
        )
    return {
        **raw_concept,
        "groups": tuple(tuple(group) for group in raw_concept["groups"]),
        "decisions": tuple(_expand_decision(row) for row in raw_concept["decisions"]),
    }


def _expand_decision(row: list[Any]) -> dict[str, Any]:
    if len(row) != _DECISION_ROW_LENGTH:
        raise ShowcasePolicyError("packaged showcase candidate row is incomplete")
    fields = (
        "candidate_id",
        "axis",
        "filler",
        "label",
        "disposition",
        "authority",
        "support",
        "rationale",
        "limitations",
        "source_occurrence_ids",
        "group",
    )
    decision: dict[str, Any] = dict(zip(fields, row, strict=True))
    decision["disposition"] = Disposition(decision["disposition"])
    decision["authority"] = DecisionAuthority(decision["authority"])
    decision["support"] = tuple(EvidenceSupport(item) for item in decision["support"])
    decision["source_occurrence_ids"] = tuple(decision["source_occurrence_ids"])
    return decision


def load_packaged_showcase_decision_set() -> ShowcaseDecisionSet:
    """Load, expand, identify, and strictly validate the wheel-packaged decision set."""
    try:
        raw = json.loads(
            files("ontolib.decomposition")
            .joinpath(_RESOURCE)
            .read_text(encoding="ascii")
        )
        payload = _expand_resource(raw)
        payload["decision_set_identity"] = hashlib.sha256(
            _canonical(payload)
        ).hexdigest()
        policy = ShowcaseDecisionSet.model_validate(payload)
        qualify_showcase_orthogonality(policy)
        return policy
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        if isinstance(exc, ShowcasePolicyError):
            raise
        raise ShowcasePolicyError("packaged showcase decision set is invalid") from exc


def build_showcase_view(
    code: str,
    base_representation_identity: str,
    base_constituents: tuple[ShowcaseConstituent, ...],
    *,
    policy: ShowcaseDecisionSet,
) -> EnhancedNcitShowcaseView:
    concept = policy.concept(code)
    effective_rows = _apply_decisions(base_constituents, concept.decisions)
    identity_payload = {
        "base_representation_identity": base_representation_identity,
        "overlay_algorithm": policy.overlay_algorithm,
        "decision_set_identity": policy.decision_set_identity,
        "code": code,
        "effective_constituents": [
            row.model_dump(mode="json") for row in effective_rows
        ],
    }
    return _showcase_view(
        code,
        base_representation_identity,
        base_constituents,
        effective_rows,
        concept,
        policy,
        identity_payload,
    )


def _apply_decisions(
    base_constituents: tuple[ShowcaseConstituent, ...],
    decisions: tuple[ShowcaseDecision, ...],
) -> tuple[ShowcaseConstituent, ...]:
    effective = {(item.axis, item.filler): item for item in base_constituents}
    for decision in decisions:
        key = decision.axis, decision.filler
        if decision.disposition == Disposition.EXCLUDE:
            effective.pop(key, None)
        elif decision.disposition == Disposition.INCLUDE:
            effective[key] = ShowcaseConstituent(
                axis=decision.axis, filler=decision.filler, label=decision.label
            )
    return tuple(sorted(effective.values(), key=lambda item: (item.axis, item.filler)))


def _showcase_view(
    code: str,
    base_identity: str,
    base: tuple[ShowcaseConstituent, ...],
    effective: tuple[ShowcaseConstituent, ...],
    concept: ShowcaseConceptPolicy,
    policy: ShowcaseDecisionSet,
    identity_payload: object,
) -> EnhancedNcitShowcaseView:
    return EnhancedNcitShowcaseView(
        banner=(
            "Local recoverable showcase; not scientific publication, NCI adoption, "
            "equivalence, or production ready."
        ),
        code=code,
        base_representation_identity=base_identity,
        decision_set_identity=policy.decision_set_identity,
        effective_representation_identity=hashlib.sha256(
            _canonical(identity_payload)
        ).hexdigest(),
        base_constituents=base,
        effective_constituents=effective,
        unresolved_visible=tuple(
            item
            for item in concept.decisions
            if item.disposition == Disposition.UNRESOLVED_VISIBLE
        ),
        decisions=concept.decisions,
    )


def qualify_showcase_orthogonality(
    policy: ShowcaseDecisionSet,
    *,
    collapse_concept_roots: set[str] | None = None,
    collapse_runtime_keys: set[tuple[str, str, str]] | None = None,
) -> ShowcaseOrthogonality:
    collapse = load_packaged_collapse_veto_policy()
    roots = _collapse_roots(collapse, collapse_concept_roots)
    overlap_roots = tuple(sorted(_EXPECTED_CODES & roots))
    showcase_occurrences = _showcase_occurrences(policy)
    collapse_occurrences = {entry.occurrence_id for entry in collapse.entries}
    overlap_occurrences = tuple(sorted(showcase_occurrences & collapse_occurrences))
    protected_keys = _collapse_runtime_key_space(collapse, collapse_runtime_keys)
    showcase_keys = _showcase_runtime_key_space(policy)
    runtime_keys = tuple(
        "|".join(key) for key in sorted(showcase_keys & protected_keys)
    )
    if overlap_roots or overlap_occurrences or runtime_keys:
        raise ShowcasePolicyError(
            "enhanced showcase overlaps R101 collapse-veto policy"
        )
    return ShowcaseOrthogonality(
        concept_roots=(), source_occurrences=(), runtime_keys=()
    )


def _collapse_roots(
    collapse: CollapseVetoPolicy, override: set[str] | None
) -> set[str]:
    if override is not None:
        return override
    return {entry.concept_code for entry in collapse.entries}


def _collapse_runtime_key_space(
    collapse: CollapseVetoPolicy,
    override: set[tuple[str, str, str]] | None,
) -> set[tuple[str, str, str]]:
    if override is not None:
        return override
    return {
        key
        for entry in collapse.entries
        for key in (
            (entry.concept_code, entry.normalized_axis, entry.broader_code),
            (entry.concept_code, entry.normalized_axis, entry.narrower_code),
        )
    }


def _showcase_runtime_key_space(
    policy: ShowcaseDecisionSet,
) -> set[tuple[str, str, str]]:
    return {
        (concept.code, decision.axis, decision.filler)
        for concept in policy.concepts
        for decision in concept.decisions
    }


def _showcase_occurrences(policy: ShowcaseDecisionSet) -> set[str]:
    return {
        occurrence
        for concept in policy.concepts
        for decision in concept.decisions
        for occurrence in decision.source_occurrence_ids
    }


def serialize_showcase_decision_graph(policy: ShowcaseDecisionSet) -> str:
    graph = Graph()
    graph.bind("op", _OP)
    for concept in policy.concepts:
        for decision in concept.decisions:
            node = URIRef(f"{SHOWCASE_GRAPH_IRI}/decision/{decision.candidate_id}")
            graph.add((node, RDF.type, _OP.ShowcaseDecision))
            values = {
                _OP.concept: URIRef(f"{NCIT_NS}{concept.code}"),
                _OP.axis: URIRef(
                    f"{vocab.ONTOPRISM_NS}{decision.axis.removeprefix('op:')}"
                ),
                _OP.filler: URIRef(f"{NCIT_NS}{decision.filler}"),
                _OP.payload: Literal(
                    json.dumps(
                        decision.model_dump(mode="json"),
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                ),
            }
            for predicate, value in values.items():
                graph.add((node, predicate, value))
    return graph.serialize(format="turtle")


def parse_showcase_decision_graph(turtle: str) -> tuple[ShowcaseDecision, ...]:
    try:
        graph = Graph().parse(data=turtle, format="turtle")
        rows = [
            ShowcaseDecision.model_validate_json(str(payload))
            for payload in graph.objects(None, _OP.payload)
        ]
        if len(rows) != len({row.candidate_id for row in rows}):
            raise ValueError("duplicate showcase decision")
        return tuple(sorted(rows, key=lambda row: row.candidate_id))
    except Exception as exc:
        raise ValueError("stored showcase decision graph is invalid") from exc


def build_showcase_decision_query(concept_code: str) -> str:
    """Read decision payloads separately, preventing base/overlay cross products."""
    concept = safe_iri(concept_code, NCIT_NS)
    return (
        f"SELECT ?payload WHERE {{ GRAPH <{SHOWCASE_GRAPH_IRI}> {{ "
        f"?decision <{_OP.concept}> <{concept}> ; "
        f"<{_OP.payload}> ?payload . }} }}"
    )


def validate_showcase_rows(
    rows: list[dict[str, str]],
) -> tuple[ShowcaseDecision, ...]:
    """Fail closed on malformed rows without replacing package authority."""
    try:
        decisions: list[ShowcaseDecision] = []
        for row in rows:
            if set(row) != {"payload"}:
                raise ValueError("unexpected decision projection")
            decisions.append(ShowcaseDecision.model_validate_json(row["payload"]))
        if len(decisions) != len({decision.candidate_id for decision in decisions}):
            raise ValueError("duplicate showcase decision")
        return tuple(sorted(decisions, key=lambda decision: decision.candidate_id))
    except (KeyError, ValueError) as exc:
        raise ShowcasePolicyError("stored showcase decision graph is invalid") from exc


def require_active_showcase_decisions(
    rows: list[dict[str, str]], expected: tuple[ShowcaseDecision, ...]
) -> None:
    """Require storage to contain the exact packaged decisions served by the API."""
    stored = validate_showcase_rows(rows)
    canonical_expected = tuple(
        sorted(expected, key=lambda decision: decision.candidate_id)
    )
    if stored != canonical_expected:
        raise ShowcasePolicyError(
            "stored showcase decisions differ from the packaged authority"
        )


def showcase_staging_graph_iri(run_id: str) -> str:
    return f"{SHOWCASE_GRAPH_IRI}/staging/{hashlib.sha256(run_id.encode()).hexdigest()}"


def build_showcase_replacement_update(staging_graph: str) -> str:
    if not staging_graph.startswith(f"{SHOWCASE_GRAPH_IRI}/staging/"):
        raise ValueError("showcase staging graph is outside its scoped namespace")
    return (
        f"CLEAR GRAPH <{SHOWCASE_GRAPH_IRI}>; "
        f"ADD GRAPH <{staging_graph}> TO GRAPH <{SHOWCASE_GRAPH_IRI}>; "
        f"DROP GRAPH <{staging_graph}>;"
    )


async def activate_showcase_decision_graph(
    client: _ShowcaseGraphClient, *, run_id: str
) -> ShowcaseDecisionSet:
    """Replace only the isolated showcase graph from its validated package."""
    policy = load_packaged_showcase_decision_set()
    staging = showcase_staging_graph_iri(run_id)
    await client.load(
        serialize_showcase_decision_graph(policy).encode("utf-8"),
        content_type="text/turtle",
        graph_iri=staging,
        replace=True,
    )
    await client.update(build_showcase_replacement_update(staging))
    return policy
