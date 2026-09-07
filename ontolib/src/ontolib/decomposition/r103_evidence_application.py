"""Source/policy-separated evidence application for the NCIt R103 review."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import defaultdict, deque
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal, Protocol, Self
from xml.etree.ElementTree import ParseError

from defusedxml.ElementTree import iterparse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import to_jsonable_python

from ontolib.decomposition.axes import (
    UNSUPPORTED_FILLERS_BY_CONCEPT_ROLE,
    is_unsupported_filler,
)
from ontolib.decomposition.models import (
    canonical_definition_fact_id,
    canonical_definition_group_id,
    canonical_source_occurrence_id,
)
from ontolib.decomposition.proposal_registry_migration import (
    load_proposal_registry_migration_envelope,
    validate_historical_migration_artifact,
    validate_migrated_proposal_registry,
)
from ontolib.decomposition.r103_review_promotion import (
    load_r103_promoted_review_revision,
)
from ontolib.terminologies.ncit.client import ncit_sparql_client

_NCIT = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#"
_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_RDFS = "http://www.w3.org/2000/01/rdf-schema#"
_OWL = "http://www.w3.org/2002/07/owl#"
_ABOUT = f"{{{_RDF}}}about"
_RESOURCE = f"{{{_RDF}}}resource"
_CLASS = f"{{{_OWL}}}Class"
_OBJECT_PROPERTY = f"{{{_OWL}}}ObjectProperty"
_RESTRICTION = f"{{{_OWL}}}Restriction"
_ON_PROPERTY = f"{{{_OWL}}}onProperty"
_SOME_VALUES = f"{{{_OWL}}}someValuesFrom"
_SUBCLASS = f"{{{_RDFS}}}subClassOf"
_EQUIVALENT = f"{{{_OWL}}}equivalentClass"
_INTERSECTION = f"{{{_OWL}}}intersectionOf"
_LABEL = f"{{{_RDFS}}}label"
_P97 = f"{{{_NCIT}}}P97"
_P106 = f"{{{_NCIT}}}P106"
_CODE = re.compile(r"^C[0-9]+$")
_ROLE = re.compile(r"^R[0-9]+$")
_SHA256 = r"^[0-9a-f]{64}$"
_TARGET_FILLERS = frozenset({"C12950", "C34228"})
_CORROBORATION_REFERENCE_COUNT = 5
_TOOL_VERSION = "ontoprism-r103-evidence-application-v1"
_STATED_GRAPH = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-stated.owl"

SOURCE_INVENTORY_QUERY = """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX n: <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#>
SELECT DISTINCT ?subject ?role ?filler ?encoding WHERE {
 GRAPH <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-stated.owl> {
  VALUES ?filler { n:C12950 n:C34228 }
  VALUES ?role { n:R103 }
  { ?subject rdfs:subClassOf ?restriction .
    ?restriction owl:onProperty ?role ; owl:someValuesFrom ?filler .
    BIND("direct-subclass-restriction" AS ?encoding) }
  UNION
  { ?subject owl:equivalentClass/owl:intersectionOf/rdf:rest*/rdf:first ?restriction .
    ?restriction owl:onProperty ?role ; owl:someValuesFrom ?filler .
    BIND("equivalent-class-intersection" AS ?encoding) }
 } } ORDER BY ?subject ?role ?filler ?encoding LIMIT {limit}"""

CANDIDATE_COUNT_QUERY = """PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX n: <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#>
SELECT (COUNT(DISTINCT ?candidate) AS ?count) WHERE {
 GRAPH <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-stated.owl> {
  ?candidate rdfs:subClassOf+ n:C12950 . FILTER(isIRI(?candidate)) }
}"""

CANDIDATE_PAGE_QUERY = """PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX n: <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#>
SELECT DISTINCT ?candidate WHERE {
 GRAPH <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-stated.owl> {
  ?candidate rdfs:subClassOf+ n:C12950 . FILTER(isIRI(?candidate)) }
} ORDER BY ?candidate LIMIT {limit} OFFSET {offset}"""


class SelectClient(Protocol):
    async def select(
        self, query: str, *, required_variables: set[str] = ...
    ) -> list[dict[str, str]]: ...


async def query_source_inventory(
    client: SelectClient, *, row_bound: int
) -> tuple[tuple[str, str, str, str], ...]:
    """Execute the source inventory query with a bound-plus-one overflow sentinel."""
    if row_bound <= 0:
        raise R103ApplicationError("source inventory bound must be positive")
    query = SOURCE_INVENTORY_QUERY.replace("{limit}", str(row_bound + 1))
    rows = await client.select(
        query, required_variables={"subject", "role", "filler", "encoding"}
    )
    if len(rows) > row_bound:
        raise R103ApplicationError("source inventory exceeds bound")
    return tuple(
        (
            _ncit_code(row["subject"]),
            _ncit_code(row["role"], role=True),
            _ncit_code(row["filler"]),
            row["encoding"],
        )
        for row in rows
    )


async def query_candidate_codes(
    client: SelectClient, *, row_bound: int, page_size: int = 500
) -> tuple[tuple[str], ...]:
    """Count then page the complete named stated descendant set within a hard bound."""
    if row_bound <= 0 or page_size <= 0:
        raise R103ApplicationError("candidate query bounds are invalid")
    if page_size > row_bound:
        raise R103ApplicationError("candidate page exceeds row bound")
    count_rows = await client.select(
        CANDIDATE_COUNT_QUERY, required_variables={"count"}
    )
    count = _candidate_count(count_rows, row_bound)
    codes = await _candidate_pages(client, count=count, page_size=page_size)
    if len(codes) != count:
        raise R103ApplicationError("candidate page/count parity differs")
    return tuple((code,) for code in codes)


def _candidate_count(rows: list[dict[str, str]], row_bound: int) -> int:
    if len(rows) != 1:
        raise R103ApplicationError("candidate count query shape differs")
    try:
        count = int(rows[0]["count"])
    except (KeyError, ValueError) as error:
        raise R103ApplicationError("candidate count is malformed") from error
    if count > row_bound:
        raise R103ApplicationError("candidate enumeration exceeds bound")
    return count


async def _candidate_pages(
    client: SelectClient, *, count: int, page_size: int
) -> list[str]:
    codes: list[str] = []
    for offset in range(0, count, page_size):
        query = CANDIDATE_PAGE_QUERY.replace("{limit}", str(page_size)).replace(
            "{offset}", str(offset)
        )
        rows = await client.select(query, required_variables={"candidate"})
        codes.extend(_ncit_code(row["candidate"]) for row in rows)
    return codes


class R103ApplicationError(ValueError):
    """R103 machine evidence is malformed, incomplete, or cross-boundary stale."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


def _canonical(value: object) -> bytes:
    return json.dumps(
        to_jsonable_python(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def _identity(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise R103ApplicationError(f"cannot read evidence input: {path}") from error


def _load_json(path: Path) -> object:
    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise R103ApplicationError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicates
        )
    except R103ApplicationError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise R103ApplicationError(f"invalid JSON evidence: {path}") from error


def _tool_identity() -> str:
    return _identity(
        {
            "tool_version": _TOOL_VERSION,
            "module_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
    )


class _SourceBinding(_StrictModel):
    release: Literal["26.07d"]
    source_identity: str = Field(pattern=_SHA256)
    source_manifest_identity: str = Field(pattern=_SHA256)
    source_artifact_identity: str = Field(pattern=_SHA256)
    source_artifact_sha256: str = Field(pattern=_SHA256)
    source_artifact_size: int = Field(gt=0)
    stated_graph_iri: str = Field(min_length=1)
    query_identity: str = Field(pattern=_SHA256)
    tool_identity: str = Field(pattern=_SHA256)


class R103SourceRow(_SourceBinding):
    row_identity: str = Field(pattern=_SHA256)
    subject_code: str = Field(pattern=r"^C[0-9]+$")
    role_code: Literal["R103"]
    filler_code: Literal["C12950", "C34228"]
    encoding: Literal["equivalent-class-intersection", "direct-subclass-restriction"]
    structural_path: tuple[int, ...] = Field(min_length=1)
    member_position: int = Field(ge=0)
    source_fact_identity: str = Field(pattern=_SHA256)
    source_group_identity: str = Field(pattern=_SHA256)
    source_occurrence_identity: str = Field(pattern=_SHA256)
    complete_definition_identity: str = Field(pattern=_SHA256)
    subject_labels: tuple[str, ...]
    subject_p97: tuple[str, ...]
    role_labels: tuple[str, ...]
    role_p97: tuple[str, ...]
    filler_labels: tuple[str, ...]
    filler_p97: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_row(self) -> Self:
        if self.structural_path[-1] != self.member_position:
            raise ValueError("source member/path mismatch")
        if self.row_identity != _identity(self.model_dump(exclude={"row_identity"})):
            raise ValueError("source row identity differs")
        return self


class R103SourceInventory(_SourceBinding):
    schema_version: Literal[1]
    row_count: int = Field(ge=0)
    row_bound: int = Field(gt=0)
    qlever_count: int = Field(ge=0)
    xml_count: int = Field(ge=0)
    qlever_query_body: str = Field(min_length=1)
    xml_scanner: Literal["streaming-rdfxml"]
    parity: Literal["canonical-qlever-xml-equal"]
    rows: tuple[R103SourceRow, ...]
    artifact_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_inventory(self) -> Self:
        if not (
            self.row_count == self.qlever_count == self.xml_count == len(self.rows)
        ):
            raise ValueError("source inventory counts differ")
        if self.row_count > self.row_bound:
            raise ValueError("source inventory exceeds bound")
        occurrences = [row.source_occurrence_identity for row in self.rows]
        if len(occurrences) != len(set(occurrences)):
            raise ValueError("duplicate semantic source occurrence")
        if self.artifact_identity != _identity(
            self.model_dump(exclude={"artifact_identity"})
        ):
            raise ValueError("source inventory identity differs")
        return self


class CandidateRow(_StrictModel):
    row_identity: str = Field(pattern=_SHA256)
    code: str = Field(pattern=r"^C[0-9]+$")
    labels: tuple[str, ...]
    p97: tuple[str, ...]
    p106: tuple[str, ...]
    path: tuple[str, ...] = Field(min_length=2)
    path_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_candidate(self) -> Self:
        if self.path[0] != "C12950" or self.path[-1] != self.code:
            raise ValueError("candidate path does not bind root and candidate")
        if self.path_identity != _identity(self.path):
            raise ValueError("candidate path identity differs")
        if self.row_identity != _identity(self.model_dump(exclude={"row_identity"})):
            raise ValueError("candidate row identity differs")
        return self


class R103CandidateArtifact(_SourceBinding):
    schema_version: Literal[1]
    root_code: Literal["C12950"]
    count_query_body: str
    page_query_body: str
    page_size: int = Field(gt=0)
    candidate_count: int = Field(ge=0)
    qlever_count: int = Field(ge=0)
    xml_count: int = Field(ge=0)
    limitations: tuple[str, ...] = Field(min_length=1)
    status: Literal["candidates-enumerated-human-selection-required"]
    candidates: tuple[CandidateRow, ...]
    artifact_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_candidates(self) -> Self:
        if not (
            self.candidate_count
            == self.qlever_count
            == self.xml_count
            == len(self.candidates)
        ):
            raise ValueError("candidate counts differ")
        codes = [row.code for row in self.candidates]
        if codes != sorted(codes) or len(codes) != len(set(codes)):
            raise ValueError("candidate rows are duplicate or unordered")
        if self.artifact_identity != _identity(
            self.model_dump(exclude={"artifact_identity"})
        ):
            raise ValueError("candidate artifact identity differs")
        return self


AuthorityKind = Literal["carried-forward-predecessor-decision", "new-human-decision"]


class AuthorityEntry(_StrictModel):
    entry_identity: str = Field(pattern=_SHA256)
    subject_code: Literal["C2860", "C3264", "C3716"]
    role_code: Literal["R103"]
    filler_code: Literal["C12950", "C34228"]
    authority_kind: AuthorityKind
    effective_decision_identity: str = Field(pattern=_SHA256)
    predecessor_decision_identity: str = Field(pattern=_SHA256)
    superseded_predecessor_decision_identity: str | None = Field(
        default=None, pattern=_SHA256
    )
    transcription_authority: Literal["explicit-human-instruction"] | None = None
    software_authorship: Literal[False]
    source_occurrence_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_kind(self) -> Self:
        is_new = self.authority_kind == "new-human-decision"
        if is_new != (self.transcription_authority is not None):
            raise ValueError("authority discriminator and transcription differ")
        if is_new != (self.superseded_predecessor_decision_identity is not None):
            raise ValueError("authority discriminator and predecessor differ")
        if self.entry_identity != _identity(
            self.model_dump(exclude={"entry_identity"})
        ):
            raise ValueError("authority entry identity differs")
        return self


class R103AuthorityArtifact(_StrictModel):
    schema_version: Literal[1]
    source_inventory_identity: str = Field(pattern=_SHA256)
    historical_rev1_file_sha256: str = Field(pattern=_SHA256)
    historical_rev2_file_sha256: str = Field(pattern=_SHA256)
    historical_rev1_artifact_identity: str = Field(pattern=_SHA256)
    historical_rev2_artifact_identity: str = Field(pattern=_SHA256)
    migration_envelope_identity: str = Field(pattern=_SHA256)
    entries: tuple[AuthorityEntry, AuthorityEntry, AuthorityEntry]
    artifact_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_authority(self) -> Self:
        expected = (
            ("C2860", "carried-forward-predecessor-decision"),
            ("C3264", "new-human-decision"),
            ("C3716", "carried-forward-predecessor-decision"),
        )
        if (
            tuple((row.subject_code, row.authority_kind) for row in self.entries)
            != expected
        ):
            raise ValueError("authority inventory differs")
        if self.artifact_identity != _identity(
            self.model_dump(exclude={"artifact_identity"})
        ):
            raise ValueError("authority artifact identity differs")
        return self


class CorroborationReference(_StrictModel):
    first_author: str = Field(min_length=1)
    title: str = Field(min_length=1)
    doi: str = Field(min_length=1)
    pmid: str = Field(pattern=r"^[0-9]+$")


class R103CorroborationNormalization(_StrictModel):
    schema_version: Literal[1]
    authority_artifact_identity: str = Field(pattern=_SHA256)
    effective_decision_identity: str = Field(pattern=_SHA256)
    historical_artifact_sha256: str = Field(pattern=_SHA256)
    historical_corroboration_identity: str = Field(pattern=_SHA256)
    relationship: Literal["corroboration-not-proof"]
    evidence_kind: Literal["reviewer-supplied-references"]
    upstream_verified: Literal[False]
    references: tuple[
        CorroborationReference,
        CorroborationReference,
        CorroborationReference,
        CorroborationReference,
        CorroborationReference,
    ]
    artifact_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_normalization(self) -> Self:
        pmids = [item.pmid for item in self.references]
        if len(pmids) != len(set(pmids)):
            raise ValueError("duplicate corroboration PMID")
        if self.artifact_identity != _identity(
            self.model_dump(exclude={"artifact_identity"})
        ):
            raise ValueError("corroboration normalization identity differs")
        return self


class R103AppliedPolicyReport(_StrictModel):
    schema_version: Literal[1]
    source_inventory_identity: str = Field(pattern=_SHA256)
    authority_artifact_identity: str = Field(pattern=_SHA256)
    c3264_decision_identity: str = Field(pattern=_SHA256)
    proposal_registry_identity: str = Field(pattern=_SHA256)
    proposal_registry_schema_version: Literal[2]
    migration_envelope_identity: str = Field(pattern=_SHA256)
    policy_identity: str = Field(pattern=_SHA256)
    oracle_file_sha256_before: str = Field(pattern=_SHA256)
    oracle_file_sha256_after: str = Field(pattern=_SHA256)
    c3264_source_retrievable: Literal[True]
    c3264_effective_projected: Literal[False]
    c2860_effective_projected: Literal[True]
    c3716_effective_projected: Literal[True]
    exclusion_propagated: Literal[False]
    official_source_preserved: Literal[True]
    proposal_created: Literal[False]
    nci_adoption_inferred: Literal[False]
    authorization: Literal[False]
    publication_attempted: Literal[False]
    artifact_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_report(self) -> Self:
        if self.oracle_file_sha256_before != self.oracle_file_sha256_after:
            raise ValueError("oracle changed during R103 application")
        if self.artifact_identity != _identity(
            self.model_dump(exclude={"artifact_identity"})
        ):
            raise ValueError("applied-policy report identity differs")
        return self


class _Entity:
    def __init__(self, code: str) -> None:
        self.code = code
        self.labels: tuple[str, ...] = ()
        self.p97: tuple[str, ...] = ()
        self.p106: tuple[str, ...] = ()
        self.parents: tuple[str, ...] = ()
        self.restrictions: list[tuple[str, str, str, int]] = []
        self.definition_members: tuple[tuple[str, ...], ...] = ()


def _ncit_code(value: str | None, *, role: bool = False) -> str:
    if not isinstance(value, str) or not value.startswith(_NCIT):
        raise R103ApplicationError(f"malformed or non-NCIt source IRI: {value!r}")
    code = value.removeprefix(_NCIT)
    pattern = _ROLE if role else _CODE
    if pattern.fullmatch(code) is None:
        raise R103ApplicationError("malformed NCIt source code")
    return code


def _text_values(element: Any, tag: str) -> tuple[str, ...]:
    values = [(item.text or "").strip() for item in element.findall(tag)]
    values = [item for item in values if item]
    if len(values) != len(set(values)):
        raise R103ApplicationError("duplicate source text value")
    return tuple(values)


def _restriction(restriction: Any) -> tuple[str, str]:
    properties = restriction.findall(_ON_PROPERTY)
    fillers = restriction.findall(_SOME_VALUES)
    if len(properties) != 1 or len(fillers) != 1:
        raise R103ApplicationError("malformed source restriction")
    return (
        _ncit_code(properties[0].get(_RESOURCE), role=True),
        _ncit_code(fillers[0].get(_RESOURCE)),
    )


def _scan_owl(path: Path) -> dict[str, _Entity]:
    entities: dict[str, _Entity] = {}
    class_depth = 0
    try:
        iterator = iterparse(path, events=("start", "end"))
        for event, element in iterator:
            class_depth, selected = _select_entity_end(event, element.tag, class_depth)
            if not selected or element.get(_ABOUT) is None:
                continue
            code = _ncit_code(element.get(_ABOUT), role=element.tag == _OBJECT_PROPERTY)
            if code in entities:
                raise R103ApplicationError(f"duplicate source entity: {code}")
            entity = _parse_entity(element, code)
            entities[code] = entity
            element.clear()
    except R103ApplicationError:
        raise
    except (OSError, ParseError, ValueError) as error:
        raise R103ApplicationError("invalid stated RDF/XML") from error
    return entities


def _select_entity_end(event: str, tag: str, depth: int) -> tuple[int, bool]:
    if tag == _CLASS:
        if event == "start":
            return depth + 1, False
        return depth - 1, depth == 1
    return depth, event == "end" and tag == _OBJECT_PROPERTY


def _parse_entity(element: Any, code: str) -> _Entity:
    entity = _Entity(code)
    entity.labels = _text_values(element, _LABEL)
    entity.p97 = _text_values(element, _P97)
    entity.p106 = _text_values(element, _P106)
    entity.parents = _named_parents(element)
    direct_members = _direct_members(element, entity)
    entity.definition_members = _equivalent_members(element, entity) or tuple(
        direct_members
    )
    return entity


def _named_parents(element: Any) -> tuple[str, ...]:
    return tuple(
        _ncit_code(item.get(_RESOURCE))
        for item in element.findall(_SUBCLASS)
        if item.get(_RESOURCE) is not None
    )


def _direct_members(element: Any, entity: _Entity) -> list[tuple[str, ...]]:
    members: list[tuple[str, ...]] = [
        ("genus", parent, "primitive") for parent in entity.parents
    ]
    for position, subclass in enumerate(element.findall(_SUBCLASS)):
        for item in subclass.findall(_RESTRICTION):
            role_code, filler_code = _restriction(item)
            entity.restrictions.append(
                ("direct-subclass-restriction", role_code, filler_code, position)
            )
            members.append(("restriction", role_code, filler_code))
    return members


def _equivalent_members(element: Any, entity: _Entity) -> tuple[tuple[str, ...], ...]:
    intersections = element.findall(f"./{_EQUIVALENT}/{_CLASS}/{_INTERSECTION}")
    if len(intersections) > 1:
        raise R103ApplicationError("multiple equivalent intersections")
    if not intersections:
        return ()
    members = list(intersections[0])
    parsed = _restriction_members(members)
    target = not _TARGET_FILLERS.isdisjoint(
        filler for role, filler in parsed.values() if role == "R103"
    )
    return _strict_equivalent_members(members, parsed, entity) if target else ()


def _restriction_members(members: list[Any]) -> dict[int, tuple[str, str]]:
    return {
        position: _restriction(member)
        for position, member in enumerate(members)
        if member.tag == _RESTRICTION
    }


def _strict_equivalent_members(
    members: list[Any], parsed: dict[int, tuple[str, str]], entity: _Entity
) -> tuple[tuple[str, ...], ...]:
    result: list[tuple[str, ...]] = []
    for position, member in enumerate(members):
        if member.tag == _RESTRICTION:
            role_code, filler_code = parsed[position]
            result.append(("restriction", role_code, filler_code))
            entity.restrictions.append(
                ("equivalent-class-intersection", role_code, filler_code, position)
            )
            continue
        if member.tag not in {f"{{{_RDF}}}Description", _CLASS}:
            raise R103ApplicationError("unsupported equivalent intersection member")
        genus = _ncit_code(member.get(_ABOUT) or member.get(_RESOURCE))
        result.append(("genus", genus, "primitive"))
    return tuple(result)


def _manifest_binding(
    path: Path, owl_path: Path, query_identity: str
) -> dict[str, object]:
    value = _load_json(path)
    if not isinstance(value, dict):
        raise R103ApplicationError("source manifest must be an object")
    stated = _manifest_object(value, "stated_artifact")
    layout = _manifest_object(value, "graph_layout")
    if value.get("ontology_version") != "26.07d":
        raise R103ApplicationError("source release differs")
    actual_sha = _sha256(owl_path)
    actual_size = _file_size(owl_path)
    if stated.get("sha256") != actual_sha or stated.get("size_bytes") != actual_size:
        raise R103ApplicationError("stated source identity mismatch")
    result = {
        "release": "26.07d",
        "source_identity": value.get("source_identity"),
        "source_manifest_identity": _identity(value),
        "source_artifact_identity": stated.get("artifact_identity"),
        "source_artifact_sha256": actual_sha,
        "source_artifact_size": actual_size,
        "stated_graph_iri": layout.get("stated_graph_iri"),
        "query_identity": query_identity,
        "tool_identity": _tool_identity(),
    }
    try:
        return _SourceBinding.model_validate(result).model_dump()
    except ValidationError as error:
        raise R103ApplicationError(str(error)) from error


def _manifest_object(value: dict[str, object], key: str) -> dict[str, object]:
    item = value.get(key)
    if not isinstance(item, dict):
        raise R103ApplicationError("source manifest lacks stated source binding")
    return item


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError as error:
        raise R103ApplicationError("cannot stat stated source") from error


def _source_semantics(
    rows: tuple[tuple[str, str, str, str], ...], row_bound: int
) -> tuple[tuple[str, str, str, str], ...]:
    if len(rows) > row_bound:
        raise R103ApplicationError("source inventory exceeds bound")
    normalized: list[tuple[str, str, str, str]] = []
    for subject, role, filler, encoding in rows:
        _validate_source_semantic(subject, role, filler, encoding)
        normalized.append((subject, role, filler, encoding))
    ordered = tuple(sorted(normalized))
    if len(ordered) != len(set(ordered)):
        raise R103ApplicationError("duplicate semantic source occurrence")
    return ordered


def _validate_source_semantic(
    subject: str, role: str, filler: str, encoding: str
) -> None:
    if _CODE.fullmatch(subject) is None or role != "R103":
        raise R103ApplicationError("malformed or non-target QLever source row")
    if filler not in _TARGET_FILLERS:
        raise R103ApplicationError("malformed or non-target QLever source row")
    if encoding not in {
        "equivalent-class-intersection",
        "direct-subclass-restriction",
    }:
        raise R103ApplicationError("unknown QLever source encoding")


def build_source_inventory(
    owl_path: Path,
    manifest_path: Path,
    *,
    qlever_rows: tuple[tuple[str, str, str, str], ...],
    row_bound: int,
) -> R103SourceInventory:
    """Compare bounded QLever results with an independent streaming RDF/XML scan."""
    if row_bound <= 0:
        raise R103ApplicationError("source inventory bound must be positive")
    query = SOURCE_INVENTORY_QUERY.replace("{limit}", str(row_bound + 1))
    query_identity = _identity({"body": query, "bound": row_bound})
    binding = _manifest_binding(manifest_path, owl_path, query_identity)
    entities = _scan_owl(owl_path)
    xml_observed = _target_xml_rows(entities)
    xml_semantics = tuple(row[:4] for row in xml_observed)
    qlever_semantics = _source_semantics(qlever_rows, row_bound)
    if len(xml_observed) > row_bound:
        raise R103ApplicationError("source inventory exceeds bound")
    if xml_semantics != qlever_semantics:
        raise R103ApplicationError("source inventory QLever/XML parity differs")
    output_rows = _source_rows(xml_observed, entities, binding)
    payload = {
        "schema_version": 1,
        **binding,
        "row_count": len(output_rows),
        "row_bound": row_bound,
        "qlever_count": len(qlever_semantics),
        "xml_count": len(xml_observed),
        "qlever_query_body": query,
        "xml_scanner": "streaming-rdfxml",
        "parity": "canonical-qlever-xml-equal",
        "rows": tuple(output_rows),
    }
    return R103SourceInventory.model_validate(
        {**payload, "artifact_identity": _identity(payload)}
    )


def _target_xml_rows(
    entities: dict[str, _Entity],
) -> list[tuple[str, str, str, str, int]]:
    rows = [
        (entity.code, role, filler, encoding, position)
        for entity in entities.values()
        for encoding, role, filler, position in entity.restrictions
        if role == "R103" and filler in _TARGET_FILLERS
    ]
    return sorted(rows)


def _source_rows(
    observed: list[tuple[str, str, str, str, int]],
    entities: dict[str, _Entity],
    binding: dict[str, object],
) -> list[R103SourceRow]:
    return [_source_row(item, entities, binding) for item in observed]


def _source_row(
    observed: tuple[str, str, str, str, int],
    entities: dict[str, _Entity],
    binding: dict[str, object],
) -> R103SourceRow:
    subject, role, filler, encoding, position = observed
    entity = entities[subject]
    filler_entity = entities.get(filler)
    role_entity = entities.get("R103")
    signatures = tuple(":".join(member) for member in entity.definition_members)
    group = canonical_definition_group_id(subject, signatures or (encoding,))
    fact = canonical_definition_fact_id(subject, group, "restriction", role, filler)
    path = (position,)
    row_payload = {
        **binding,
        "subject_code": subject,
        "role_code": role,
        "filler_code": filler,
        "encoding": encoding,
        "structural_path": path,
        "member_position": position,
        "source_fact_identity": fact,
        "source_group_identity": group,
        "source_occurrence_identity": canonical_source_occurrence_id(
            subject, fact, path
        ),
        "complete_definition_identity": _complete_definition_identity(entity, group),
        "subject_labels": entity.labels,
        "subject_p97": entity.p97,
        "role_labels": role_entity.labels if role_entity else (),
        "role_p97": role_entity.p97 if role_entity else (),
        "filler_labels": filler_entity.labels if filler_entity else (),
        "filler_p97": filler_entity.p97 if filler_entity else (),
    }
    return R103SourceRow.model_validate(
        {**row_payload, "row_identity": _identity(row_payload)}
    )


def _complete_definition_identity(entity: _Entity, group: str) -> str:
    members = tuple(
        (
            member,
            canonical_definition_fact_id(
                entity.code,
                group,
                "genus" if member[0] == "genus" else "restriction",
                *member[1:],
            ),
        )
        for member in entity.definition_members
    )
    return _identity({"root_code": entity.code, "group_id": group, "members": members})


def _shortest_paths(entities: dict[str, _Entity]) -> dict[str, tuple[str, ...]]:
    children: dict[str, set[str]] = defaultdict(set)
    for code, entity in entities.items():
        for parent in entity.parents:
            children[parent].add(code)
    paths: dict[str, tuple[str, ...]] = {"C12950": ("C12950",)}
    queue = deque(["C12950"])
    while queue:
        parent = queue.popleft()
        for child in sorted(children[parent]):
            candidate = (*paths[parent], child)
            previous = paths.get(child)
            if previous is None or (len(candidate), candidate) < (
                len(previous),
                previous,
            ):
                paths[child] = candidate
                queue.append(child)
    paths.pop("C12950", None)
    return paths


def build_candidate_artifact(
    owl_path: Path,
    manifest_path: Path,
    *,
    qlever_rows: tuple[tuple[str], ...],
    row_bound: int,
    page_size: int | None = None,
) -> R103CandidateArtifact:
    """Enumerate all named stated C12950 descendants without selecting one."""
    observed_page_size = _candidate_page_size(row_bound, page_size, len(qlever_rows))
    query_identity = _identity(
        {
            "count": CANDIDATE_COUNT_QUERY,
            "page": CANDIDATE_PAGE_QUERY,
            "page_size": observed_page_size,
        }
    )
    binding = _manifest_binding(manifest_path, owl_path, query_identity)
    entities = _scan_owl(owl_path)
    paths = _shortest_paths(entities)
    qlever_codes = _candidate_codes(qlever_rows)
    if qlever_codes != tuple(sorted(paths)):
        raise R103ApplicationError("candidate QLever/XML parity differs")
    candidates = _candidate_models(qlever_codes, entities, paths)
    payload = {
        "schema_version": 1,
        **binding,
        "root_code": "C12950",
        "count_query_body": CANDIDATE_COUNT_QUERY,
        "page_query_body": CANDIDATE_PAGE_QUERY,
        "page_size": observed_page_size,
        "candidate_count": len(candidates),
        "qlever_count": len(qlever_codes),
        "xml_count": len(paths),
        "limitations": (
            "Named stated rdfs:subClassOf+ descendants only; no inferred descendants.",
            "Enumeration supplies evidence for human selection and makes no "
            "equivalence or suitability verdict.",
        ),
        "status": "candidates-enumerated-human-selection-required",
        "candidates": tuple(candidates),
    }
    return R103CandidateArtifact.model_validate(
        {**payload, "artifact_identity": _identity(payload)}
    )


def _candidate_page_size(row_bound: int, page_size: int | None, count: int) -> int:
    observed = min(500, row_bound) if page_size is None else page_size
    if row_bound <= 0 or count > row_bound:
        raise R103ApplicationError("candidate enumeration exceeds bound")
    if observed <= 0 or observed > row_bound:
        raise R103ApplicationError("candidate enumeration exceeds bound")
    return observed


def _candidate_codes(rows: tuple[tuple[str], ...]) -> tuple[str, ...]:
    codes = tuple(sorted(row[0] for row in rows))
    if any(_CODE.fullmatch(code) is None for code in codes):
        raise R103ApplicationError("malformed or non-NCIt candidate")
    if len(codes) != len(set(codes)):
        raise R103ApplicationError("duplicate QLever candidate")
    return codes


def _candidate_models(
    codes: tuple[str, ...],
    entities: dict[str, _Entity],
    paths: dict[str, tuple[str, ...]],
) -> list[CandidateRow]:
    result: list[CandidateRow] = []
    for code in codes:
        entity = entities[code]
        path = paths[code]
        payload = {
            "code": code,
            "labels": entity.labels,
            "p97": entity.p97,
            "p106": entity.p106,
            "path": path,
            "path_identity": _identity(path),
        }
        result.append(
            CandidateRow.model_validate({**payload, "row_identity": _identity(payload)})
        )
    return result


def build_authority_artifact(
    rev1_path: Path,
    rev2_path: Path,
    migration_path: Path,
    source_inventory: R103SourceInventory,
) -> R103AuthorityArtifact:
    """Normalize historical decision authority without restating human prose."""
    try:
        R103SourceInventory.model_validate(source_inventory.model_dump(mode="python"))
    except ValidationError as error:
        raise R103ApplicationError(str(error)) from error
    revision = load_r103_promoted_review_revision(rev2_path)
    migration = load_proposal_registry_migration_envelope(migration_path)
    validate_historical_migration_artifact(migration, "r103-review-state", rev1_path)
    validate_historical_migration_artifact(migration, "r103-review-revision", rev2_path)
    predecessor = revision.predecessor.registry.decisions
    effective = revision.registry.decisions
    source_by_subject = {row.subject_code: row for row in source_inventory.rows}
    if set(source_by_subject) != {"C2860", "C3264", "C3716"}:
        raise R103ApplicationError("authority/source inventory join differs")
    entries = _authority_entries(predecessor, effective, source_by_subject)
    payload = {
        "schema_version": 1,
        "source_inventory_identity": source_inventory.artifact_identity,
        "historical_rev1_file_sha256": _sha256(rev1_path),
        "historical_rev2_file_sha256": _sha256(rev2_path),
        "historical_rev1_artifact_identity": revision.predecessor.artifact_identity,
        "historical_rev2_artifact_identity": revision.artifact_identity,
        "migration_envelope_identity": migration.envelope_identity,
        "entries": tuple(entries),
    }
    return R103AuthorityArtifact.model_validate(
        {**payload, "artifact_identity": _identity(payload)}
    )


def _authority_entries(
    predecessor: tuple[Any, ...],
    effective: tuple[Any, ...],
    source_by_subject: dict[str, R103SourceRow],
) -> list[AuthorityEntry]:
    result: list[AuthorityEntry] = []
    for index, (old, new) in enumerate(zip(predecessor, effective, strict=True)):
        source = _authority_source(new, source_by_subject)
        is_new = index == 1
        item = {
            "subject_code": new.subject_code,
            "role_code": new.role_code,
            "filler_code": new.filler_code,
            "authority_kind": "new-human-decision"
            if is_new
            else "carried-forward-predecessor-decision",
            "effective_decision_identity": new.decision_identity,
            "predecessor_decision_identity": old.decision_identity,
            "superseded_predecessor_decision_identity": old.decision_identity
            if is_new
            else None,
            "transcription_authority": "explicit-human-instruction" if is_new else None,
            "software_authorship": False,
            "source_occurrence_identity": source.source_occurrence_identity,
        }
        result.append(
            AuthorityEntry.model_validate({**item, "entry_identity": _identity(item)})
        )
    return result


def _authority_source(new: Any, rows: dict[str, R103SourceRow]) -> R103SourceRow:
    source = rows.get(new.subject_code)
    expected = (new.subject_code, new.role_code, new.filler_code)
    if source is None:
        raise R103ApplicationError("authority/source inventory join differs")
    if (source.subject_code, source.role_code, source.filler_code) != expected:
        raise R103ApplicationError("authority/source inventory join differs")
    return source


def build_corroboration_normalization(
    historical_path: Path, authority: R103AuthorityArtifact
) -> R103CorroborationNormalization:
    """Downgrade unretained PubMed metadata to reviewer-supplied references."""
    value, references = _historical_references(historical_path)
    c3264 = authority.entries[1]
    if c3264.subject_code != "C3264":
        raise R103ApplicationError("authority C3264 decision is absent")
    historical_identity = value.get("corroboration_identity")
    if not isinstance(historical_identity, str):
        raise R103ApplicationError("historical corroboration identity absent")
    payload = {
        "schema_version": 1,
        "authority_artifact_identity": authority.artifact_identity,
        "effective_decision_identity": c3264.effective_decision_identity,
        "historical_artifact_sha256": _sha256(historical_path),
        "historical_corroboration_identity": historical_identity,
        "relationship": "corroboration-not-proof",
        "evidence_kind": "reviewer-supplied-references",
        "upstream_verified": False,
        "references": references,
    }
    return R103CorroborationNormalization.model_validate(
        {**payload, "artifact_identity": _identity(payload)}
    )


def _historical_references(
    path: Path,
) -> tuple[dict[str, object], tuple[CorroborationReference, ...]]:
    value = _load_json(path)
    if not isinstance(value, dict):
        raise R103ApplicationError("historical corroboration shape differs")
    citations = value.get("citations")
    if not isinstance(citations, list):
        raise R103ApplicationError("historical corroboration shape differs")
    if len(citations) != _CORROBORATION_REFERENCE_COUNT:
        raise R103ApplicationError("historical corroboration citation count differs")
    try:
        references = tuple(
            CorroborationReference.model_validate(item) for item in citations
        )
    except ValidationError as error:
        raise R103ApplicationError(str(error)) from error
    return value, references


def build_applied_policy_report(
    *,
    inventory: R103SourceInventory,
    authority: R103AuthorityArtifact,
    proposal_registry_path: Path,
    migration_path: Path,
    oracle_path: Path,
) -> R103AppliedPolicyReport:
    """Apply normalized authority to current policy while preserving official source."""
    _validate_application_inputs(inventory, authority)
    migration = load_proposal_registry_migration_envelope(migration_path)
    registry = validate_migrated_proposal_registry(migration, proposal_registry_path)
    before = _sha256(oracle_path)
    _validate_current_policy()
    after = _sha256(oracle_path)
    payload = {
        "schema_version": 1,
        "source_inventory_identity": inventory.artifact_identity,
        "authority_artifact_identity": authority.artifact_identity,
        "c3264_decision_identity": authority.entries[1].effective_decision_identity,
        "proposal_registry_identity": registry.registry_identity,
        "proposal_registry_schema_version": 2,
        "migration_envelope_identity": migration.envelope_identity,
        "policy_identity": _identity(
            sorted(
                (subject, role, sorted(fillers))
                for (
                    subject,
                    role,
                ), fillers in UNSUPPORTED_FILLERS_BY_CONCEPT_ROLE.items()
            )
        ),
        "oracle_file_sha256_before": before,
        "oracle_file_sha256_after": after,
        "c3264_source_retrievable": True,
        "c3264_effective_projected": False,
        "c2860_effective_projected": True,
        "c3716_effective_projected": True,
        "exclusion_propagated": False,
        "official_source_preserved": True,
        "proposal_created": False,
        "nci_adoption_inferred": False,
        "authorization": False,
        "publication_attempted": False,
    }
    return R103AppliedPolicyReport.model_validate(
        {**payload, "artifact_identity": _identity(payload)}
    )


def _validate_application_inputs(
    inventory: R103SourceInventory, authority: R103AuthorityArtifact
) -> None:
    source_assertions = {
        (row.subject_code, row.role_code, row.filler_code) for row in inventory.rows
    }
    required = {
        ("C2860", "R103", "C12950"),
        ("C3264", "R103", "C12950"),
        ("C3716", "R103", "C34228"),
    }
    if not required <= source_assertions:
        raise R103ApplicationError("applied policy source assertions are incomplete")
    if authority.source_inventory_identity != inventory.artifact_identity:
        raise R103ApplicationError("applied policy authority/source binding differs")


def _validate_current_policy() -> None:
    observed = (
        is_unsupported_filler("C3264", "R103", "C12950"),
        is_unsupported_filler("C2860", "R103", "C12950"),
        is_unsupported_filler("C3716", "R103", "C34228"),
        UNSUPPORTED_FILLERS_BY_CONCEPT_ROLE.get(("C3264", "R103")),
    )
    expected = (True, False, False, frozenset({"C12950"}))
    if observed != expected:
        raise R103ApplicationError("current R103 policy differs from authority")


def write_artifact(path: Path, artifact: BaseModel) -> None:
    """Atomically write canonical deterministic JSON."""
    content = (
        json.dumps(
            artifact.model_dump(mode="json"),
            sort_keys=True,
            indent=2,
            ensure_ascii=True,
        ).encode("ascii")
        + b"\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staging = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(staging)
        raise


def _load_model[ArtifactT: BaseModel](path: Path, model: type[ArtifactT]) -> ArtifactT:
    try:
        return model.model_validate_json(_canonical(_load_json(path)))
    except ValidationError as error:
        raise R103ApplicationError(str(error)) from error


def load_source_inventory(path: Path) -> R103SourceInventory:
    return _load_model(path, R103SourceInventory)


def load_candidate_artifact(path: Path) -> R103CandidateArtifact:
    return _load_model(path, R103CandidateArtifact)


def load_authority_artifact(path: Path) -> R103AuthorityArtifact:
    return _load_model(path, R103AuthorityArtifact)


def load_corroboration_normalization(
    path: Path, authority: R103AuthorityArtifact
) -> R103CorroborationNormalization:
    value = _load_model(path, R103CorroborationNormalization)
    if (
        value.authority_artifact_identity != authority.artifact_identity
        or value.effective_decision_identity
        != authority.entries[1].effective_decision_identity
    ):
        raise R103ApplicationError("corroboration authority binding differs")
    return value


def load_applied_policy_report(path: Path) -> R103AppliedPolicyReport:
    return _load_model(path, R103AppliedPolicyReport)


async def generate_r103_evidence_application(
    *,
    endpoint: str,
    owl_path: Path,
    manifest_path: Path,
    rev1_path: Path,
    rev2_path: Path,
    historical_corroboration_path: Path,
    proposal_registry_path: Path,
    migration_path: Path,
    oracle_path: Path,
    output_directory: Path,
) -> tuple[
    R103SourceInventory,
    R103CandidateArtifact,
    R103AuthorityArtifact,
    R103CorroborationNormalization,
    R103AppliedPolicyReport,
]:
    """Generate all current #294 machine artifacts from their named inputs."""
    async with ncit_sparql_client(endpoint, query_timeout=180.0) as client:
        inventory_rows = await query_source_inventory(client, row_bound=100)
        candidate_rows = await query_candidate_codes(
            client, row_bound=10_000, page_size=500
        )
    inventory = build_source_inventory(
        owl_path, manifest_path, qlever_rows=inventory_rows, row_bound=100
    )
    candidates = build_candidate_artifact(
        owl_path,
        manifest_path,
        qlever_rows=candidate_rows,
        row_bound=10_000,
        page_size=500,
    )
    authority = build_authority_artifact(
        rev1_path, rev2_path, migration_path, inventory
    )
    corroboration = build_corroboration_normalization(
        historical_corroboration_path, authority
    )
    application = build_applied_policy_report(
        inventory=inventory,
        authority=authority,
        proposal_registry_path=proposal_registry_path,
        migration_path=migration_path,
        oracle_path=oracle_path,
    )
    outputs: tuple[tuple[str, BaseModel], ...] = (
        ("r103-source-inventory-26.07d.json", inventory),
        ("r103-c12950-candidates-26.07d.json", candidates),
        ("r103-authority-normalized-26.07d.json", authority),
        ("r103-corroboration-normalized-26.07d.json", corroboration),
        ("r103-applied-policy-26.07d.json", application),
    )
    for name, artifact in outputs:
        write_artifact(output_directory / name, artifact)
    return inventory, candidates, authority, corroboration, application
