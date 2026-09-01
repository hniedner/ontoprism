"""Validate tracked specialist evidence metadata and generate its bound context."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONCEPT_ORDER = ("C27262", "C102870", "C6135", "C4791", "C100054", "C198031", "C35756")
_CONTROLLING_AUTHORITY_MAX = 2


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class LiteraturePairKey(_StrictModel):
    axis: str = Field(min_length=1)
    filler: str = Field(pattern=r"^(?:C[0-9]+|MINT-[0-9a-f]{12})$")


class LiteratureCitation(_StrictModel):
    citation_id: str = Field(min_length=1)
    status: Literal["cited", "not-found", "access-restricted"]
    authority_class: str = Field(min_length=1)
    authority_order: int = Field(ge=1)
    bibliography: str = Field(min_length=1)
    url: str = Field(min_length=1)
    doi: str | None
    pmid: str | None
    verified_on: str | None
    exact_locator: str = Field(min_length=1)
    exact_passage: str = Field(min_length=1)
    supports: str = Field(min_length=1)
    does_not_support: str = Field(min_length=1)
    limitations: str = Field(min_length=1)
    conflicts_or_supersession: str = Field(min_length=1)

    @model_validator(mode="after")
    def _unavailable_sources_cannot_claim_a_quote(self) -> Self:
        if self.status == "access-restricted" and (
            self.verified_on is not None
            or self.exact_locator != "ACCESS RESTRICTED"
            or self.exact_passage != "NOT VERIFIED"
        ):
            raise ValueError(
                "access-restricted citation must have no verified date and must use "
                "ACCESS RESTRICTED/NOT VERIFIED"
            )
        if self.status == "not-found":
            raise ValueError("research-gap/not-found citations are not dispatchable")
        if self.status == "cited" and (
            self.verified_on is None
            or self.exact_passage == "NOT VERIFIED"
            or not self.url.startswith("https://")
        ):
            raise ValueError(
                "cited source requires an accessible exact passage and URL"
            )
        if self.status == "cited" and self.verified_on is not None:
            try:
                date.fromisoformat(self.verified_on)
            except ValueError as exc:
                raise ValueError("cited source requires an ISO verified date") from exc
        return self


class LiteratureEvidenceClaim(_StrictModel):
    question_id: str = Field(min_length=1)
    pair_key: LiteraturePairKey
    citation_id: str = Field(min_length=1)
    support_excerpt: str = Field(min_length=1)
    supported_claim: str = Field(min_length=1)
    evidence_terms: tuple[str, ...] = Field(min_length=1)
    target_axis: str | None = None
    source_concept_code: str | None = Field(default=None, pattern=r"^C[0-9]+$")
    source_concept_identity: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class LiteratureSourceConcept(_StrictModel):
    code: str = Field(pattern=r"^C[0-9]+$")
    exact_label: str = Field(min_length=1)
    exact_definition: str = Field(min_length=1)
    source_concept_identity: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _identity_binds_exact_source_metadata(self) -> Self:
        expected = source_concept_identity(
            self.code, self.exact_label, self.exact_definition
        )
        if self.source_concept_identity != expected:
            raise ValueError(
                "source concept identity does not bind its exact metadata; "
                f"expected {expected}"
            )
        return self


_ALLOWED_EVIDENCE_KINDS = (
    "classification",
    "clinical guideline",
    "manual",
    "government",
    "ncit",
    "open-access",
    "peer-reviewed",
    "systematic review",
)


def _normalized_evidence_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def source_concept_identity(code: str, label: str, definition: str) -> str:
    """Bind an NCIt code to its exact stated preferred label and P97 definition."""
    payload = json.dumps(
        {"code": code, "exact_definition": definition, "exact_label": label},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


class LiteratureQuestion(_StrictModel):
    question_id: str = Field(min_length=1)
    pair_keys: tuple[LiteraturePairKey, ...] = Field(min_length=1)
    text: str = Field(min_length=1)
    claims: tuple[LiteratureEvidenceClaim, ...] = Field(min_length=1)
    source_concept_binding_required: bool = False


def citation_supports_pair(
    *,
    target: LiteraturePairKey,
    question: LiteratureQuestion,
    claim: LiteratureEvidenceClaim,
    citation: LiteratureCitation,
) -> bool:
    """Check exact source/excerpt binding, not automated clinical entailment."""
    if (
        target not in question.pair_keys
        or claim not in question.claims
        or claim.question_id != question.question_id
        or claim.pair_key != target
        or claim.citation_id != citation.citation_id
        or citation.status != "cited"
        or citation.verified_on is None
        or citation.exact_passage == "NOT VERIFIED"
        or not citation.exact_locator.strip()
        or not citation.exact_passage.strip()
        or not any(
            kind in citation.authority_class.lower() for kind in _ALLOWED_EVIDENCE_KINDS
        )
    ):
        return False
    if question.source_concept_binding_required and (
        claim.target_axis != target.axis
        or claim.source_concept_code != target.filler
        or claim.source_concept_identity is None
    ):
        return False
    if claim.support_excerpt not in citation.exact_passage:
        return False
    passage = _normalized_evidence_text(citation.exact_passage)
    supported_claim = _normalized_evidence_text(claim.supported_claim)
    normalized_terms = tuple(
        _normalized_evidence_text(term) for term in claim.evidence_terms
    )
    if (
        any(not term for term in normalized_terms)
        or any(term not in passage for term in normalized_terms)
        or any(term not in supported_claim for term in normalized_terms)
    ):
        return False
    contradiction = citation.does_not_support.lower().strip().rstrip(".")
    prefix = "does not support "
    if contradiction.startswith(prefix):
        unsupported = " ".join(re.findall(r"[a-z0-9]+", contradiction[len(prefix) :]))
        if unsupported and unsupported in supported_claim:
            return False
    return True


def _validate_question_bindings(
    question: LiteratureQuestion,
    citations: dict[str, LiteratureCitation],
    source_concepts: dict[str, LiteratureSourceConcept],
) -> None:
    keys = {(key.axis, key.filler) for key in question.pair_keys}
    claim_keys = {
        (claim.pair_key.axis, claim.pair_key.filler) for claim in question.claims
    }
    if claim_keys != keys or any(
        claim.question_id != question.question_id for claim in question.claims
    ):
        raise ValueError("claim must bind the question's exact pair and identity")
    for claim in question.claims:
        if question.source_concept_binding_required:
            source = source_concepts.get(claim.source_concept_code or "")
            if (
                source is None
                or claim.source_concept_code != claim.pair_key.filler
                or claim.target_axis != claim.pair_key.axis
                or claim.source_concept_identity != source.source_concept_identity
            ):
                raise ValueError(
                    "claim source concept must bind the exact question pair"
                )
        citation = citations.get(claim.citation_id)
        if citation is None:
            raise ValueError("question references an unknown citation")
        target = (
            next(
                key
                for key in question.pair_keys
                if key.filler == claim.source_concept_code
            )
            if claim.source_concept_code is not None
            else claim.pair_key
        )
        if not citation_supports_pair(
            target=target, question=question, claim=claim, citation=citation
        ):
            raise ValueError(
                "every pair claim requires its exact accessible "
                "passage-bearing citation"
            )
    if "supply" in question.text.lower():
        raise ValueError("researchable evidence cannot be delegated to the specialist")


class LiteratureDossierSource(_StrictModel):
    code: str = Field(pattern=r"^C[0-9]+$")
    exact_label: str = Field(min_length=1)
    exact_definition: str = Field(min_length=1)
    specialty: str = Field(min_length=1)
    factual_context: tuple[str, ...] = Field(min_length=1)
    citations: tuple[LiteratureCitation, ...] = Field(min_length=1)
    questions: tuple[LiteratureQuestion, ...] = Field(min_length=1)
    context_pair_keys: tuple[LiteraturePairKey, ...] = ()
    source_concepts: tuple[LiteratureSourceConcept, ...] = ()

    @model_validator(mode="after")
    def _references_are_closed(self) -> Self:
        citations = {item.citation_id: item for item in self.citations}
        if len(citations) != len(self.citations):
            raise ValueError("literature citation IDs must be unique")
        if tuple(item.authority_order for item in self.citations) != tuple(
            sorted(item.authority_order for item in self.citations)
        ):
            raise ValueError("citations must be in authority order")
        if not any(
            citation.status == "cited"
            and citation.authority_order <= _CONTROLLING_AUTHORITY_MAX
            for citation in self.citations
        ):
            raise ValueError("row requires a passage-bearing controlling citation")
        asked_keys = {
            (key.axis, key.filler)
            for question in self.questions
            for key in question.pair_keys
        }
        context_keys = {(key.axis, key.filler) for key in self.context_pair_keys}
        if (
            len(context_keys) != len(self.context_pair_keys)
            or asked_keys & context_keys
        ):
            raise ValueError("asked and context pair keys must be unique and disjoint")
        source_concepts = {item.code: item for item in self.source_concepts}
        if len(source_concepts) != len(self.source_concepts):
            raise ValueError("source concept codes must be unique")
        for question in self.questions:
            _validate_question_bindings(question, citations, source_concepts)
        return self


class OncologyAccessibleEvidenceRecord(_StrictModel):
    source_id: str = Field(min_length=1)
    code: str = Field(pattern=r"^C[0-9]+$")
    url: str = Field(pattern=r"^https://")
    checked_on: str
    exact_short_passage: str = Field(min_length=1, max_length=700)
    pair_keys: tuple[LiteraturePairKey, ...] = Field(min_length=1)
    material_scope: str = Field(min_length=1)

    @model_validator(mode="after")
    def _checked_date_is_real_date(self) -> Self:
        date.fromisoformat(self.checked_on)
        return self


class LiteratureContextSource(_StrictModel):
    schema_version: Literal[2]
    ncit_version: Literal["26.07d"]
    evidence_pass: Literal["final"]
    oncology_accessible_evidence_records: tuple[
        OncologyAccessibleEvidenceRecord, ...
    ] = Field(min_length=6, max_length=6)
    dossiers: tuple[LiteratureDossierSource, ...] = Field(min_length=7, max_length=7)

    @model_validator(mode="after")
    def _seven_rows_are_ordered(self) -> Self:
        if tuple(row.code for row in self.dossiers) != CONCEPT_ORDER:
            raise ValueError(
                "literature dossiers are not in the approved seven-row order"
            )
        return self


class GeneratedLiteratureContext(_StrictModel):
    schema_version: Literal[2]
    ncit_version: Literal["26.07d"]
    evidence_pass: Literal["final"]
    source_path: str
    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_on: str
    oncology_accessible_evidence_records: tuple[OncologyAccessibleEvidenceRecord, ...]
    dossiers: tuple[LiteratureDossierSource, ...]


def _canonical(payload: object) -> bytes:
    return json.dumps(payload, indent=2, sort_keys=True).encode() + b"\n"


def _portable_source_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return f"external/{path.name}"


def generate_specialist_literature_context(
    source_path: Path, output_path: Path
) -> GeneratedLiteratureContext:
    """Write deterministic decision-free context after strict evidence validation."""
    source_bytes = source_path.read_bytes()
    source = LiteratureContextSource.model_validate_json(source_bytes)
    generated_on = date.today().isoformat()
    generated = GeneratedLiteratureContext(
        schema_version=2,
        ncit_version=source.ncit_version,
        evidence_pass=source.evidence_pass,
        source_path=_portable_source_path(source_path),
        source_identity=hashlib.sha256(source_bytes).hexdigest(),
        generated_on=generated_on,
        oncology_accessible_evidence_records=source.oncology_accessible_evidence_records,
        dossiers=source.dossiers,
    )
    payload = _canonical(generated.model_dump(mode="json"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not output_path.exists() or output_path.read_bytes() != payload:
        output_path.write_bytes(payload)
    return generated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    generate_specialist_literature_context(args.source, args.output)


if __name__ == "__main__":
    main()
