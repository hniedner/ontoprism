"""Validate tracked specialist evidence metadata and generate its bound context."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any, Literal, Self

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

    @model_validator(mode="before")
    @classmethod
    def _restricted_rendering_is_canonical(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("status") == "access-restricted":
            return {
                **value,
                "verified_on": None,
                "exact_locator": "ACCESS RESTRICTED",
                "exact_passage": "NOT VERIFIED",
            }
        return value

    @model_validator(mode="after")
    def _unavailable_sources_cannot_claim_a_quote(self) -> Self:
        if self.status == "access-restricted" and (
            self.verified_on is not None
            or self.exact_locator != "ACCESS RESTRICTED"
            or self.exact_passage != "NOT VERIFIED"
        ):
            raise ValueError(
                "access-restricted citation must render NOT VERIFIED/ACCESS RESTRICTED"
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
        return self


class LiteratureEvidenceClaim(_StrictModel):
    question_id: str = Field(min_length=1)
    pair_key: LiteraturePairKey
    citation_id: str = Field(min_length=1)
    source_fact: str = Field(min_length=1)


class LiteratureQuestion(_StrictModel):
    question_id: str = Field(min_length=1)
    pair_keys: tuple[LiteraturePairKey, ...] = Field(min_length=1)
    text: str = Field(min_length=1)
    claims: tuple[LiteratureEvidenceClaim, ...] = Field(min_length=1)


class LiteratureDossierSource(_StrictModel):
    code: str = Field(pattern=r"^C[0-9]+$")
    exact_label: str = Field(min_length=1)
    exact_definition: str = Field(min_length=1)
    specialty: str = Field(min_length=1)
    factual_context: tuple[str, ...] = Field(min_length=1)
    citations: tuple[LiteratureCitation, ...] = Field(min_length=1)
    questions: tuple[LiteratureQuestion, ...] = Field(min_length=1)

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
        for question in self.questions:
            keys = {(key.axis, key.filler) for key in question.pair_keys}
            claim_keys = {
                (claim.pair_key.axis, claim.pair_key.filler)
                for claim in question.claims
            }
            if claim_keys != keys or any(
                claim.question_id != question.question_id for claim in question.claims
            ):
                raise ValueError(
                    "claim must bind the question's exact pair and identity"
                )
            selected = [citations.get(claim.citation_id) for claim in question.claims]
            if None in selected:
                raise ValueError("question references an unknown citation")
            if not any(
                item is not None and item.status == "cited" for item in selected
            ):
                raise ValueError(
                    "question requires at least one accessible passage-bearing citation"
                )
            if "supply" in question.text.lower():
                raise ValueError(
                    "researchable evidence cannot be delegated to the specialist"
                )
        return self


class LiteratureContextSource(_StrictModel):
    schema_version: Literal[2]
    ncit_version: Literal["26.07d"]
    evidence_pass: Literal["final"]
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
