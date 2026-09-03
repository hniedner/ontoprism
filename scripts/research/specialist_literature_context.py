"""Validate tracked specialist evidence metadata and generate its bound context."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONCEPT_ORDER = ("C27262", "C102870", "C6135", "C4791", "C100054", "C198031", "C35756")
_CONTROLLING_AUTHORITY_MAX = 2
_MIN_FEATURE_LENGTH = 3
_DISTINCT_PAIR_THRESHOLD = 2


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


LEXICAL_EVIDENCE_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "this",
        "to",
        "was",
        "were",
        "with",
    }
)


def normalize_lexical_evidence(value: str) -> str:
    """Canonicalize lexical evidence without inferring synonyms or entailment."""
    folded = unicodedata.normalize("NFKC", value).casefold()
    dashed = "".join(
        "-" if unicodedata.category(char) == "Pd" else char for char in folded
    )
    joined = re.sub(r"(?<=\w)-(?=\w)", "", dashed)
    return " ".join(re.findall(r"[^\W_]+", joined, flags=re.UNICODE))


class LiteratureEvidenceSignature(_StrictModel):
    required_source_features: tuple[str, ...] = Field(min_length=1, max_length=8)
    passage_scope: Literal["exclusive", "shared-context"]

    @model_validator(mode="after")
    def _features_are_non_vacuous_and_unique(self) -> Self:
        normalized = tuple(
            normalize_lexical_evidence(feature)
            for feature in self.required_source_features
        )
        if any(
            len(feature) < _MIN_FEATURE_LENGTH
            or all(token in LEXICAL_EVIDENCE_STOPWORDS for token in feature.split())
            for feature in normalized
        ):
            raise ValueError(
                "lexical evidence features must be non-vacuous, at least three "
                "characters, and not stopword-only"
            )
        if len(set(normalized)) != len(normalized):
            raise ValueError(
                "lexical evidence features must be unique after normalization"
            )
        return self


class LiteratureEvidenceClaim(_StrictModel):
    question_id: str = Field(min_length=1)
    pair_key: LiteraturePairKey
    citation_id: str = Field(min_length=1)
    support_excerpt: str = Field(min_length=1)
    supported_claim: str = Field(min_length=1)
    source_concept_code: str = Field(pattern=r"^C[0-9]+$")
    source_concept_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_signature: LiteratureEvidenceSignature


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


def source_concept_identity(code: str, label: str, definition: str) -> str:
    """Bind an NCIt code to its exact stated preferred label and P97 definition."""
    payload = json.dumps(
        {"code": code, "exact_definition": definition, "exact_label": label},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


class LiteratureWithheldPair(_StrictModel):
    pair_key: LiteraturePairKey
    reason: str = Field(min_length=1)


class LiteratureQuestion(_StrictModel):
    question_id: str = Field(min_length=1)
    pair_keys: tuple[LiteraturePairKey, ...] = Field(min_length=1)
    text: str = Field(min_length=1)
    claims: tuple[LiteratureEvidenceClaim, ...] = ()
    withheld_pairs: tuple[LiteratureWithheldPair, ...] = ()

    @model_validator(mode="after")
    def _claimed_and_withheld_pairs_cover_the_question(self) -> Self:
        keys = {(key.axis, key.filler) for key in self.pair_keys}
        claimed = {
            (claim.pair_key.axis, claim.pair_key.filler) for claim in self.claims
        }
        withheld = {
            (item.pair_key.axis, item.pair_key.filler) for item in self.withheld_pairs
        }
        if claimed & withheld or claimed | withheld != keys:
            raise ValueError(
                "claimed and withheld pairs must be an exact disjoint cover"
            )
        if len(withheld) != len(self.withheld_pairs):
            raise ValueError("withheld pair keys must be unique")
        return self


def citation_supports_pair(
    *,
    target: LiteraturePairKey,
    question: LiteratureQuestion,
    claim: LiteratureEvidenceClaim,
    citation: LiteratureCitation,
    source_concept: LiteratureSourceConcept,
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
    if (
        claim.source_concept_code != target.filler
        or source_concept.code != target.filler
        or claim.source_concept_identity != source_concept.source_concept_identity
    ):
        return False
    if claim.support_excerpt not in citation.exact_passage:
        return False
    surfaces = (
        normalize_lexical_evidence(
            f"{source_concept.exact_label} {source_concept.exact_definition}"
        ),
        normalize_lexical_evidence(claim.support_excerpt),
        normalize_lexical_evidence(claim.supported_claim),
        normalize_lexical_evidence(citation.exact_passage),
    )
    normalized_features = tuple(
        normalize_lexical_evidence(feature)
        for feature in claim.evidence_signature.required_source_features
    )
    if any(
        feature not in surface
        for feature in normalized_features
        for surface in surfaces
    ):
        return False
    supported_claim = normalize_lexical_evidence(claim.supported_claim)
    contradiction = citation.does_not_support.lower().strip().rstrip(".")
    prefix = "does not support "
    if contradiction.startswith(prefix):
        unsupported = normalize_lexical_evidence(contradiction[len(prefix) :])
        if unsupported and unsupported in supported_claim:
            return False
    return True


def _validate_question_bindings(
    question: LiteratureQuestion,
    citations: dict[str, LiteratureCitation],
    source_concepts: dict[str, LiteratureSourceConcept],
) -> None:
    if any(claim.question_id != question.question_id for claim in question.claims):
        raise ValueError("claim must bind the question's exact pair and identity")
    for claim in question.claims:
        source = source_concepts.get(claim.source_concept_code)
        if (
            source is None
            or claim.source_concept_code != claim.pair_key.filler
            or claim.source_concept_identity != source.source_concept_identity
        ):
            raise ValueError("claim source concept must bind the exact question pair")
        citation = citations.get(claim.citation_id)
        if citation is None:
            raise ValueError("question references an unknown citation")
        if not citation_supports_pair(
            target=claim.pair_key,
            question=question,
            claim=claim,
            citation=citation,
            source_concept=source,
        ):
            raise ValueError(
                "every lexical feature must appear in the exact NCIt label and P97, "
                "support excerpt, supported claim, and citation exact passage"
            )
    if "supply" in question.text.lower():
        raise ValueError("researchable evidence cannot be delegated to the specialist")


def _validate_duplicate_evidence(
    questions: tuple[LiteratureQuestion, ...],
) -> None:
    evidence_uses: dict[tuple[str, str], list[LiteratureEvidenceClaim]] = {}
    for question in questions:
        for claim in question.claims:
            evidence_uses.setdefault(
                (
                    claim.citation_id,
                    normalize_lexical_evidence(claim.support_excerpt),
                ),
                [],
            ).append(claim)
    for claims in evidence_uses.values():
        pair_keys = {(claim.pair_key.axis, claim.pair_key.filler) for claim in claims}
        if len(pair_keys) < _DISTINCT_PAIR_THRESHOLD:
            continue
        if any(
            claim.evidence_signature.passage_scope == "exclusive" for claim in claims
        ):
            raise ValueError(
                "exclusive evidence cannot be shared across distinct pairs"
            )
        signatures = {
            tuple(
                normalize_lexical_evidence(feature)
                for feature in claim.evidence_signature.required_source_features
            )
            for claim in claims
        }
        supported_claims = {
            normalize_lexical_evidence(claim.supported_claim) for claim in claims
        }
        if len(signatures) != len(claims) or len(supported_claims) != len(claims):
            raise ValueError(
                "shared-context evidence requires distinct valid signatures and "
                "distinct claims; identical bindings across pairs are forbidden"
            )


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
        _validate_duplicate_evidence(self.questions)
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
    schema_version: Literal[3]
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
    schema_version: Literal[3]
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
        schema_version=3,
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
