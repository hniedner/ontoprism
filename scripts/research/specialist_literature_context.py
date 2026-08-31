"""Validate tracked specialist evidence metadata and generate its bound context."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONCEPT_ORDER = ("C27262", "C102870", "C6135", "C4791", "C100054", "C198031", "C35756")


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
    verified_on: str = Field(pattern=r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}$")
    exact_locator: str = Field(min_length=1)
    exact_passage: str = Field(min_length=1)
    supports: str = Field(min_length=1)
    does_not_support: str = Field(min_length=1)
    limitations: str = Field(min_length=1)
    conflicts_or_supersession: str = Field(min_length=1)

    @model_validator(mode="after")
    def _unavailable_sources_cannot_claim_a_quote(self) -> Self:
        if self.status != "cited" and not self.exact_passage.startswith("Unavailable:"):
            raise ValueError(
                "unavailable citation cannot contain a claimed exact passage"
            )
        return self


class LiteratureQuestion(_StrictModel):
    question_id: str = Field(min_length=1)
    pair_keys: tuple[LiteraturePairKey, ...] = Field(min_length=1)
    text: str = Field(min_length=1)
    citation_ids: tuple[str, ...] = Field(min_length=1)


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
        for question in self.questions:
            selected = [citations.get(item) for item in question.citation_ids]
            if None in selected:
                raise ValueError("question references an unknown citation")
            if (
                not any(
                    item is not None and item.status == "cited" for item in selected
                )
                and "supply" not in question.text.lower()
            ):
                raise ValueError(
                    "question backed only by unavailable sources must ask the "
                    "specialist to supply a source"
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
    dossiers: tuple[LiteratureDossierSource, ...]


def _canonical(payload: object) -> bytes:
    return json.dumps(payload, indent=2, sort_keys=True).encode() + b"\n"


def generate_specialist_literature_context(
    source_path: Path, output_path: Path
) -> GeneratedLiteratureContext:
    """Write deterministic decision-free context after strict evidence validation."""
    source_bytes = source_path.read_bytes()
    source = LiteratureContextSource.model_validate_json(source_bytes)
    generated = GeneratedLiteratureContext(
        schema_version=2,
        ncit_version=source.ncit_version,
        evidence_pass=source.evidence_pass,
        source_path=source_path.as_posix(),
        source_identity=hashlib.sha256(source_bytes).hexdigest(),
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
