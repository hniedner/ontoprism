"""Strict, source-bound governance records for missing concepts and relations."""

from __future__ import annotations

import csv
import hashlib
import json
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ontolib.decomposition.minting import MintedConcept, normalize_label

if TYPE_CHECKING:
    from collections.abc import Sequence

ProposalStatus = Literal[
    "proposed",
    "locally-approved",
    "submitted",
    "accepted",
    "rejected",
]
DuplicateResult = Literal["no-equivalent", "equivalent-found", "possible-match"]
MappingPredicate = Literal[
    "exactMatch",
    "closeMatch",
    "broadMatch",
    "narrowMatch",
    "relatedMatch",
]


def _identity(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _text(value: str, field: str) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{field} must be non-empty without outer whitespace")
    return value


def _canonical(values: tuple[str, ...], field: str) -> tuple[str, ...]:
    if not values:
        raise ValueError(f"{field} must not be empty")
    for value in values:
        _text(value, field)
    if len(values) != len(set(values)):
        raise ValueError(f"{field} must be unique")
    return values


def _require_unique(values: Sequence[object], field: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field} must be unique")


def _require_replacement_shape(
    status: ProposalStatus, replacement_ncit_code: str | None
) -> None:
    if status == "accepted" and replacement_ncit_code is None:
        raise ValueError("accepted concept requires replacement NCIt code")
    if status != "accepted" and replacement_ncit_code is not None:
        raise ValueError("only accepted concept may carry replacement NCIt code")


def _require_concept_id(axis: str, preferred_name: str, actual_id: str) -> None:
    expected_id = MintedConcept(axis=axis, label=preferred_name).id
    if actual_id != expected_id:
        raise ValueError(
            "concept proposal must use its deterministic concept proposal id"
        )


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class DuplicateCheck(_StrictModel):
    """One version-pinned search for an already-existing equivalent."""

    resource: str
    version: str
    query: str
    result: DuplicateResult
    candidates: tuple[str, ...] = ()
    evidence_url: str

    @model_validator(mode="after")
    def _validate_text_and_uniqueness(self) -> Self:
        for field in ("resource", "version", "query", "evidence_url"):
            _text(getattr(self, field), field)
        if len(self.candidates) != len(set(self.candidates)):
            raise ValueError("duplicate-check candidates must be unique")
        return self

    @model_validator(mode="after")
    def _validate_result_shape(self) -> Self:
        if self.result == "no-equivalent" and self.candidates:
            raise ValueError("no-equivalent must not carry candidates")
        if self.result != "no-equivalent" and not self.candidates:
            raise ValueError(f"{self.result} requires at least one candidate")
        return self


class CrossOntologyMapping(_StrictModel):
    """One version-pinned external concept mapping with explicit match strength."""

    system: str
    version: str
    concept_id: str
    label: str
    predicate: MappingPredicate
    evidence_url: str

    @model_validator(mode="after")
    def _validate_mapping(self) -> Self:
        for field in (
            "system",
            "version",
            "concept_id",
            "label",
            "evidence_url",
        ):
            _text(getattr(self, field), f"mapping {field}")
        return self


class _Proposal(_StrictModel):
    id: str
    preferred_name: str
    definition: str
    source_roles: tuple[str, ...]
    rationale: str
    duplicate_checks: tuple[DuplicateCheck, ...] = Field(min_length=1)
    submission_target: str
    status: ProposalStatus = "proposed"

    @model_validator(mode="after")
    def _validate_common(self) -> Self:
        for field in (
            "id",
            "preferred_name",
            "definition",
            "rationale",
            "submission_target",
        ):
            _text(getattr(self, field), field)
        _canonical(self.source_roles, "source_roles")
        resources = [check.resource for check in self.duplicate_checks]
        if len(resources) != len(set(resources)):
            raise ValueError("duplicate checks must use unique resources")
        return self


class ConceptProposal(_Proposal):
    """A proposed NCIt class that is not accepted ontology content."""

    kind: Literal["concept"] = "concept"
    axis: str
    parent_concepts: tuple[str, ...]
    semantic_types: tuple[str, ...]
    synonyms: tuple[str, ...] = ()
    source_concepts: tuple[str, ...]
    mappings: tuple[CrossOntologyMapping, ...] = Field(min_length=1)
    replacement_ncit_code: str | None = Field(default=None, pattern=r"^C[0-9]+$")

    @model_validator(mode="after")
    def _validate_concept(self) -> Self:
        _text(self.axis, "axis")
        _canonical(self.parent_concepts, "parent_concepts")
        _canonical(self.semantic_types, "semantic_types")
        _canonical(self.source_concepts, "source_concepts")
        _require_unique(list(self.synonyms), "synonyms")
        mapping_keys = [(item.system, item.concept_id) for item in self.mappings]
        _require_unique(mapping_keys, "cross-ontology mappings")
        _require_replacement_shape(self.status, self.replacement_ncit_code)
        _require_concept_id(self.axis, self.preferred_name, self.id)
        return self


class RelationProposal(_Proposal):
    """A proposed univocal relation awaiting ontology-governance review."""

    kind: Literal["relation"] = "relation"
    domain: str
    range: str
    source_examples: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_relation(self) -> Self:
        _text(self.domain, "domain")
        _text(self.range, "range")
        _canonical(self.source_examples, "source_examples")
        if self.id != relation_proposal_id(self.preferred_name):
            raise ValueError(
                "relation proposal must use its deterministic relation proposal id"
            )
        return self


Proposal = Annotated[ConceptProposal | RelationProposal, Field(discriminator="kind")]


class ProposalRegistry(_StrictModel):
    """One immutable proposal set bound to an identified NCIt source."""

    schema_version: Literal[1] = 1
    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    ontology_version: str
    proposals: tuple[Proposal, ...]
    registry_identity: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")

    def model_post_init(self, _context: object) -> None:
        if not self.registry_identity:
            object.__setattr__(self, "registry_identity", self._expected_identity())

    @model_validator(mode="after")
    def _validate_registry(self) -> Self:
        _text(self.ontology_version, "ontology_version")
        ids = [proposal.id for proposal in self.proposals]
        if len(ids) != len(set(ids)):
            raise ValueError("proposal ids must be unique")
        if self.registry_identity != self._expected_identity():
            raise ValueError("proposal registry identity does not match its payload")
        return self

    def _expected_identity(self) -> str:
        return _identity(self.model_dump(mode="json", exclude={"registry_identity"}))

    def filter(
        self,
        *,
        kind: Literal["concept", "relation"] | None = None,
        status: ProposalStatus | None = None,
    ) -> tuple[Proposal, ...]:
        """Return proposals matching both supplied governance filters."""
        return tuple(
            proposal
            for proposal in self.proposals
            if (kind is None or proposal.kind == kind)
            and (status is None or proposal.status == status)
        )

    def ncit_submission_rows(self) -> tuple[dict[str, str], ...]:
        """Render proposed NCIt classes as deterministic flat submission rows."""
        return tuple(
            {
                "proposal_id": proposal.id,
                "preferred_name": proposal.preferred_name,
                "definition": proposal.definition,
                "parent_concepts": "|".join(proposal.parent_concepts),
                "semantic_types": "|".join(proposal.semantic_types),
                "synonyms": "|".join(proposal.synonyms),
                "source_concepts": "|".join(proposal.source_concepts),
                "source_roles": "|".join(proposal.source_roles),
                "rationale": proposal.rationale,
                "status": proposal.status,
            }
            for proposal in self.proposals
            if isinstance(proposal, ConceptProposal)
            and proposal.submission_target == "NCIt"
            and proposal.status == "proposed"
        )

    def relation_submission_rows(self) -> tuple[dict[str, str], ...]:
        """Render relation proposals as deterministic flat governance rows."""
        return tuple(
            {
                "proposal_id": proposal.id,
                "preferred_name": proposal.preferred_name,
                "definition": proposal.definition,
                "domain": proposal.domain,
                "range": proposal.range,
                "source_roles": "|".join(proposal.source_roles),
                "source_examples": "|".join(proposal.source_examples),
                "rationale": proposal.rationale,
                "status": proposal.status,
                "submission_target": proposal.submission_target,
            }
            for proposal in self.proposals
            if isinstance(proposal, RelationProposal) and proposal.status == "proposed"
        )


def relation_proposal_id(preferred_name: str) -> str:
    """Return a deterministic relation-proposal identifier from its preferred name."""
    normalized = normalize_label(preferred_name)
    digest = hashlib.sha1(f"relation|{normalized}".encode()).hexdigest()[:12]  # noqa: S324
    return f"RELPROP-{digest}"


def resolve_proposal_identifier(registry: ProposalRegistry, identifier: str) -> str:
    """Resolve an accepted placeholder to its assigned NCIt code."""
    for proposal in registry.proposals:
        if proposal.id != identifier:
            continue
        if isinstance(proposal, ConceptProposal) and proposal.replacement_ncit_code:
            return proposal.replacement_ncit_code
        return identifier
    return identifier


def _mapping_iri(mapping: CrossOntologyMapping) -> str:
    if mapping.system == "SNOMED CT US":
        return f"http://snomed.info/id/{mapping.concept_id}"
    if mapping.system == "NCIt":
        return (
            f"http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#{mapping.concept_id}"
        )
    return mapping.concept_id


def _augmented_ttl(registry: ProposalRegistry) -> str:
    lines = [
        "@prefix ncit: <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#> .",
        "@prefix op: <https://w3id.org/ontoprism/vocab#> .",
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
        "",
    ]
    for proposal in registry.proposals:
        if not isinstance(proposal, ConceptProposal) or proposal.status not in {
            "locally-approved",
            "submitted",
            "accepted",
        }:
            continue
        subject = f"op:{proposal.id}"
        lines.extend(
            [
                f"{subject} a owl:Class ;",
                f"  rdfs:label {json.dumps(proposal.preferred_name)} ;",
                f"  op:proposalStatus {json.dumps(proposal.status)} ;",
                f"  rdfs:subClassOf ncit:{proposal.parent_concepts[0]} ;",
            ]
        )
        for index, mapping in enumerate(proposal.mappings):
            suffix = ";" if index < len(proposal.mappings) - 1 else "."
            lines.append(
                f"  skos:{mapping.predicate} <{_mapping_iri(mapping)}> {suffix}"
            )
        lines.append("")
    return "\n".join(lines)


def _csv_text(rows: tuple[dict[str, str], ...], fields: tuple[str, ...]) -> str:
    target = StringIO(newline="")
    writer = csv.DictWriter(target, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return target.getvalue()


def write_submission_exports(registry: ProposalRegistry, directory: str | Path) -> None:
    """Write deterministic proposed-only NCIt and relation submission artifacts."""
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    concept_fields = (
        "proposal_id",
        "preferred_name",
        "definition",
        "parent_concepts",
        "semantic_types",
        "synonyms",
        "source_concepts",
        "source_roles",
        "rationale",
        "status",
    )
    relation_fields = (
        "proposal_id",
        "preferred_name",
        "definition",
        "domain",
        "range",
        "source_roles",
        "source_examples",
        "rationale",
        "status",
        "submission_target",
    )
    concept_rows = registry.ncit_submission_rows()
    relation_rows = registry.relation_submission_rows()
    (root / "ncit-concept-proposals.csv").write_text(
        _csv_text(concept_rows, concept_fields), encoding="utf-8"
    )
    (root / "relation-proposals.csv").write_text(
        _csv_text(relation_rows, relation_fields), encoding="utf-8"
    )
    manifest = {
        "ontology_version": registry.ontology_version,
        "registry_identity": registry.registry_identity,
        "relation_proposals": len(relation_rows),
        "source_identity": registry.source_identity,
        "status": "proposed",
        "concept_proposals": len(concept_rows),
    }
    (root / "submission-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (root / "augmented-ncit-proposals.ttl").write_text(
        _augmented_ttl(registry), encoding="utf-8"
    )
    replacements = {
        proposal.id: proposal.replacement_ncit_code
        for proposal in registry.proposals
        if isinstance(proposal, ConceptProposal)
        and proposal.status == "accepted"
        and proposal.replacement_ncit_code is not None
    }
    (root / "accepted-replacements.json").write_text(
        json.dumps(replacements, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


_TUPLE_FIELDS = (
    "parent_concepts",
    "semantic_types",
    "synonyms",
    "source_concepts",
    "source_roles",
    "source_examples",
    "mappings",
)


def _tuple_value(value: object) -> object:
    return tuple(value) if isinstance(value, list) else value


def _normalize_duplicate_check(value: object) -> object:
    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    normalized["candidates"] = _tuple_value(normalized.get("candidates", ()))
    return normalized


def _normalize_proposal(value: object) -> object:
    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    for field in _TUPLE_FIELDS:
        if field in normalized:
            normalized[field] = _tuple_value(normalized[field])
    checks = normalized.get("duplicate_checks", ())
    normalized["duplicate_checks"] = tuple(
        _normalize_duplicate_check(check) for check in checks
    )
    if "mappings" in normalized:
        normalized["mappings"] = tuple(normalized["mappings"])
    return normalized


def _normalize_registry_json(value: object) -> object:
    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    proposals = normalized.get("proposals", ())
    normalized["proposals"] = tuple(
        _normalize_proposal(proposal) for proposal in proposals
    )
    return normalized


def load_proposal_registry(path: str | Path) -> ProposalRegistry:
    """Load one strict registry while rejecting duplicate JSON object keys."""
    value = json.loads(
        Path(path).read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
    )
    return ProposalRegistry.model_validate(_normalize_registry_json(value))
