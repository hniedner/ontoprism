# ruff: noqa: E501
"""Generate write-free local specialist packets and validate returned reviews."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import date
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONCEPT_ORDER = (
    "C27262",
    "C102870",
    "C6135",
    "C4791",
    "C100054",
    "C198031",
    "C35756",
)
_SHA256 = r"^[0-9a-f]{64}$"
_MINT = re.compile(r"MINT-[0-9a-f]{12}")
_GROUP_PACKET_SCHEMA = 4
_DIAGNOSTIC_SCHEMA = 3


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class Citation(_StrictModel):
    citation_id: str = Field(min_length=1)
    status: Literal["cited", "not-found", "access-restricted"]
    authority_order: int = Field(ge=1)
    citation: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    passage: str = Field(min_length=1)
    support: str = Field(min_length=1)
    non_support: str = Field(min_length=1)
    limitations: str = Field(min_length=1)
    newer_evidence: str = Field(min_length=1)


class ClinicalQuestion(_StrictModel):
    question_id: str = Field(min_length=1)
    pair_ids: tuple[str, ...] = Field(min_length=1)
    text: str = Field(min_length=1)
    literature_citation_ids: tuple[str, ...] = Field(min_length=1)


class LiteratureDossier(_StrictModel):
    code: str = Field(pattern=r"^C[0-9]+$")
    exact_label: str = Field(min_length=1)
    exact_definition: str = Field(min_length=1)
    specialty: str = Field(min_length=1)
    factual_context: tuple[str, ...] = Field(min_length=1)
    citations: tuple[Citation, ...] = Field(min_length=1)
    questions: tuple[ClinicalQuestion, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_questions(self) -> Self:
        citations = {item.citation_id: item for item in self.citations}
        if len(citations) != len(self.citations):
            raise ValueError("literature citation IDs must be unique")
        if tuple(item.authority_order for item in self.citations) != tuple(
            sorted(item.authority_order for item in self.citations)
        ):
            raise ValueError("literature dossier must be ordered by authority")
        for question in self.questions:
            selected = [
                citations.get(item) for item in question.literature_citation_ids
            ]
            if None in selected:
                raise ValueError("clinical question cites absent literature")
            if (
                not any(
                    item is not None and item.status == "cited" for item in selected
                )
                and "supply source" not in question.text.lower()
            ):
                raise ValueError(
                    "question without cited evidence requires specialist to supply source"
                )
        return self


class FinalLiteratureContext(_StrictModel):
    schema_version: Literal[1]
    evidence_pass: Literal["final"]
    verified_on: Literal["2026-08-30"]
    dossiers: tuple[LiteratureDossier, ...] = Field(min_length=7, max_length=7)

    @model_validator(mode="after")
    def _validate_order(self) -> Self:
        if tuple(item.code for item in self.dossiers) != CONCEPT_ORDER:
            raise ValueError(
                "literature dossiers are not in the approved seven-row order"
            )
        return self


class PairEvidence(_StrictModel):
    pair_id: str
    axis: str
    filler_code: str
    label: str
    relation: Literal[
        "expected-matched-scoreable",
        "expected-emitted-review-bearing",
        "expected-not-emitted",
        "current-only-scoreable",
    ]
    source_occurrence_evidence: str
    current_projection_status: str
    axis_range_verdict: Literal["valid", "invalid", "unknown"]
    engineering_only: bool = False


class ClinicalPairAssessment(_StrictModel):
    pair_id: str
    status: Literal[
        "UNIVERSAL-DEFINING",
        "UNIVERSAL-NONDEFINING",
        "CHARACTERISTIC-NONUNIVERSAL",
        "CLASSIFICATION-DEPENDENT",
        "INAPPLICABLE",
        "UNRESOLVED",
    ]
    citations: tuple[str, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1)


class ClinicalStageA(_StrictModel):
    reviewer_name: str = Field(min_length=1)
    specialty: str = Field(min_length=1)
    review_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    conflict_of_interest: str = Field(min_length=1)
    source_confirmation: str = Field(min_length=1)
    assessments: tuple[ClinicalPairAssessment, ...] = Field(min_length=1)
    clinical_stage: Literal["SUFFICIENT-FOR-ONTOLOGY-REVIEW", "DEFERRED"]
    blocker: str | None

    @model_validator(mode="after")
    def _validate_outcome(self) -> Self:
        unresolved = any(item.status == "UNRESOLVED" for item in self.assessments)
        if unresolved and self.clinical_stage != "DEFERRED":
            raise ValueError("an unresolved assessment requires DEFERRED Stage A")
        if (self.clinical_stage == "DEFERRED") != (self.blocker is not None):
            raise ValueError("DEFERRED Stage A requires exactly one blocker statement")
        date.fromisoformat(self.review_date)
        return self


class OntologyPairDecision(_StrictModel):
    pair_id: str
    relation: Literal[
        "expected-matched-scoreable",
        "expected-emitted-review-bearing",
        "expected-not-emitted",
        "current-only-scoreable",
    ]
    action: Literal[
        "RETAIN-SCOREABLE",
        "REMOVE-FROM-PROJECTION",
        "RE-AXIS",
        "PROMOTE-SCOREABLE",
        "ADD-SCOREABLE",
        "OMIT",
    ]
    target_axis: str | None
    target_range_verdict: Literal["valid", "invalid", "unknown"] | None
    group_assignment: str | None
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_action(self) -> Self:
        allowed = {
            "expected-matched-scoreable": {
                "RETAIN-SCOREABLE",
                "REMOVE-FROM-PROJECTION",
                "RE-AXIS",
            },
            "expected-emitted-review-bearing": {
                "PROMOTE-SCOREABLE",
                "REMOVE-FROM-PROJECTION",
                "RE-AXIS",
            },
            "expected-not-emitted": {"ADD-SCOREABLE", "OMIT", "RE-AXIS"},
            "current-only-scoreable": {
                "RETAIN-SCOREABLE",
                "REMOVE-FROM-PROJECTION",
                "RE-AXIS",
            },
        }
        if self.action not in allowed[self.relation]:
            raise ValueError("ontology action is not allowed for this pair relation")
        if self.action == "RE-AXIS" and self.target_range_verdict != "valid":
            raise ValueError(
                "RE-AXIS requires a valid stored target-axis range verdict"
            )
        if self.action != "RE-AXIS" and (
            self.target_axis is not None or self.target_range_verdict is not None
        ):
            raise ValueError("only RE-AXIS may name a target axis and range verdict")
        if self.action in {"ADD-SCOREABLE", "PROMOTE-SCOREABLE", "RE-AXIS"} and (
            not self.group_assignment
        ):
            raise ValueError(
                "added, promoted, or re-axis pairs require group assignment"
            )
        return self


class OntologyStageB(_StrictModel):
    reviewer_name: str = Field(min_length=1)
    review_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    conflict_of_interest: str = Field(min_length=1)
    row_outcome: Literal["RESOLVED", "DEFERRED"]
    decisions: tuple[OntologyPairDecision, ...]
    blocker: str | None

    @model_validator(mode="after")
    def _validate_outcome(self) -> Self:
        if (self.row_outcome == "DEFERRED") != (self.blocker is not None):
            raise ValueError("DEFERRED Stage B requires exactly one blocker statement")
        date.fromisoformat(self.review_date)
        return self


class RowPacket(_StrictModel):
    code: str
    label: str
    definition: str
    pairs: tuple[PairEvidence, ...]
    clinically_asked_pairs: tuple[str, ...]
    engineering_only_pairs: tuple[str, ...]


class PacketIndexEntry(_StrictModel):
    code: str
    path: str
    sha256: str = Field(pattern=_SHA256)


class PacketIndex(_StrictModel):
    schema_version: Literal[1]
    ncit_version: Literal["26.07d"]
    literature_context_identity: str = Field(pattern=_SHA256)
    input_identities: dict[str, str]
    packets: tuple[PacketIndexEntry, ...]


class GenerationValidation(_StrictModel):
    schema_version: Literal[1]
    valid: Literal[True]
    producing_command: str
    generated_files: tuple[str, ...]
    suppressed_unregistered_mints: tuple[str, ...]
    packet_visible_registered_mints: tuple[str, ...]
    readiness: Literal[False]
    writes_performed: Literal[False]


class CompletionValidation(_StrictModel):
    valid: Literal[True]
    completed_codes: tuple[str, ...] = Field(min_length=1)
    writes_performed: Literal[False]


def _canonical_json(model: BaseModel) -> bytes:
    return (
        json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True).encode()
        + b"\n"
    )


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _pair_evidence_lines(
    code: str,
    raw_inputs: tuple[object, ...],
    registered_mints: set[str],
) -> list[str]:
    group_packet = next(
        (
            item
            for item in raw_inputs
            if isinstance(item, dict)
            and item.get("schema_version") == _GROUP_PACKET_SCHEMA
            and "review_boundary" in item
        ),
        None,
    )
    diagnostic = next(
        (
            item
            for item in raw_inputs
            if isinstance(item, dict)
            and item.get("schema_version") == _DIAGNOSTIC_SCHEMA
            and "candidate_rows" in item
        ),
        None,
    )
    if not isinstance(group_packet, dict):
        return [
            "Pair inventory input was not supplied to this model-level rendering call."
        ]
    concept = next(item for item in group_packet["concepts"] if item["code"] == code)
    occurrences: dict[tuple[str, str], list[dict[str, object]]] = {}
    for group in concept["actual_groups"]:
        for item in group["pairs"]:
            occurrences.setdefault(tuple(item["pair"]), []).extend(
                item.get("occurrences", ())
            )
    for item in concept["non_scoreable_emitted_pairs"]:
        occurrences.setdefault(tuple(item["pair"]), []).extend(
            item["source_occurrences"]
        )
    ranges = {}
    candidates = {}
    if isinstance(diagnostic, dict):
        ranges = {
            (item["axis"], item["filler"]): item
            for item in diagnostic["range_diagnostics"]
            if item["code"] == code
        }
        candidates = {
            (item["expected"]["axis"], item["expected"]["filler"]): item
            for item in diagnostic["candidate_rows"]
            if item["code"] == code
        }
    relation_labels = (
        ("expected_matched_scoreable", "expected matched scoreable"),
        ("expected_emitted_review_bearing", "expected emitted review-bearing"),
        ("expected_not_emitted", "expected not emitted"),
        ("current_only_scoreable", "current-only scoreable"),
    )
    relation_by_pair = {
        tuple(pair): (field, relation)
        for field, relation in relation_labels
        for pair in concept["pair_relations"][field]
    }
    expected_pairs = sorted(
        pair
        for pair, (field, _relation) in relation_by_pair.items()
        if field != "current_only_scoreable"
    )
    ordered_pairs = (
        *expected_pairs,
        *sorted(concept["pair_relations"]["current_only_scoreable"]),
    )
    lines = [
        "| Pair | Current relation | Source occurrence evidence | Current projection | Axis range |",
        "|---|---|---|---|---|",
    ]
    for pair_number, (axis, filler) in enumerate(ordered_pairs, start=1):
        field, relation = relation_by_pair[(axis, filler)]
        if filler.startswith("MINT-") and filler not in registered_mints:
            continue
        pair = (axis, filler)
        exact_occurrences = occurrences.get(pair, [])
        if exact_occurrences:
            source = "; ".join(
                "occurrence_id={occurrence_id}, root_code={root_code}, "
                "source_fact_id={source_fact_id}, source_group_id={source_group_id}, "
                "anchor_code={anchor_code}, depth={depth}, role_code={role_code}, "
                "filler_code={filler_code}, structural_path={structural_path}, "
                "member_position={member_position}".format(**item)
                for item in exact_occurrences
            )
        elif pair in candidates:
            source = json.dumps(candidates[pair]["source_evidence"], sort_keys=True)
        else:
            source = "unavailable: no matching stated definition occurrence"
        range_row = ranges.get(pair)
        range_status = (
            range_row["verdict"]["status"] if range_row is not None else "unknown"
        )
        projection = (
            range_row["current_projection_status"]
            if range_row is not None
            else (
                "scoreable-release-bound"
                if field in {"expected_matched_scoreable", "current_only_scoreable"}
                else "not-emitted"
            )
        )
        lines.append(
            f"| P{pair_number}: `{axis} {filler}` | {relation} | {source} | {projection} | {range_status} |"
        )
    lines.extend(
        [
            "",
            "**Current scoreable baseline partition:** "
            + json.dumps(concept["actual_partition"], sort_keys=True),
            "",
            "**Historical proposal warning and partition:** This is an admitted historical review witness, not source-stated grouping or authority. "
            + json.dumps(concept["expected_partition"], sort_keys=True),
        ]
    )
    return lines


def _render_packet(dossier: LiteratureDossier, pair_evidence_lines: list[str]) -> bytes:
    lines = [
        f"# {dossier.code} — {dossier.exact_label}",
        "",
        "**Blank specialist packet.** Local OntoPrism review only. This packet is write-free and cannot authorize publication, NCI adoption, or equivalence. Source release: **NCIt 26.07d**.",
        "",
        "`CADSR-USAGE: NOT QUERIED`; caDSR usage cannot support any rationale.",
        "",
        "## Source-bound concept",
        "",
        f"**Exact label:** {dossier.exact_label}",
        f"**Exact definition:** {dossier.exact_definition}",
        "",
        "## Audited factual context",
        "",
        *(f"- {item}" for item in dossier.factual_context),
        "",
        "## Pair inventory and independent evidence",
        "",
        *pair_evidence_lines,
        "",
        "Source occurrence evidence, current projection status, and axis-range evidence are independent. Source evidence proves only the cited stated fact and structural coordinate; it does not prove clinical universality, grouping, axis admission, or ontology action.",
        "",
        "`op:NormalTissueOrigin` is non-defining. Axis governance and fallback are provisional. R101/R88 routing is many-to-many where relevant. D23 permits the same code on multiple axes; duplicate display alone is not a defect.",
        "",
        "`GROUP-TOGETHER` means current normalized relationship-group co-membership only; it does not mean equivalence, adoption, or assessment association. No new axis or assessment-level association is offered.",
        "",
        "## Literature dossier (authority order)",
        "",
        "| Citation status and citation | Exact locator | Exact passage | Support | Does not support | Limitations | Newer evidence |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in dossier.citations:
        lines.append(
            f"| {item.status}: {item.citation} | {item.locator} | {item.passage} | {item.support} | {item.non_support} | {item.limitations} | {item.newer_evidence} |"
        )
    lines.extend(
        [
            "",
            "## Stage A — Clinical review",
            "",
            f"Required specialty: {dossier.specialty}",
            "Reviewer identity: ______  Specialty: ______  Date: ______  COI: ______",
            "Source confirmations/citations/rationale: ______",
            "",
            "Allowed status per asked pair: `UNIVERSAL-DEFINING | UNIVERSAL-NONDEFINING | CHARACTERISTIC-NONUNIVERSAL | CLASSIFICATION-DEPENDENT | INAPPLICABLE | UNRESOLVED`.",
            *(
                f"- {question.question_id} ({', '.join(question.pair_ids)}): {question.text} — status/citations/rationale: ______"
                for question in dossier.questions
            ),
            "",
            "`CLINICAL-STAGE: SUFFICIENT-FOR-ONTOLOGY-REVIEW | DEFERRED`: ______",
            "Any UNRESOLVED answer requires DEFERRED plus blocker, specialist/source needed, and next action. Stage A contains no ontology actions.",
            "",
            "```STAGE-A-RESPONSE",
            '{"blank": true}',
            "```",
            "",
            "## Stage B — Ontology review",
            "",
            "Requires a completed, sufficient Stage A. Ontology SME identity: ______  Date: ______  COI: ______",
            "`ROW-OUTCOME: RESOLVED | DEFERRED`: ______",
            "Any unresolved pair forces whole-row DEFERRED and forbids a terminal delta/final partition. `needs-new-representation` is an allowed deferred blocker; assessment association is only an unimplemented future possibility, never an action.",
            "",
            "Actions are relation-specific. RE-AXIS may target only an existing axis with a stored valid range verdict. Engineering-only pairs have no ontology action. Baseline plus delta is reconstructed mechanically; group assignment is required for additions, promotions, re-axis, and disputed grouping.",
            "",
            "```STAGE-B-RESPONSE",
            '{"blank": true}',
            "```",
            "",
            "## Consequences",
            "",
            "Source facts are preserved. REMOVE-FROM-PROJECTION or OMIT affects only the curated projection. needsReview and scoreability alter precision/recall; group-only changes affect partition agreement. In every outcome readiness remains false, no NCI adoption occurs, and this packet performs no write.",
            "",
            "Stage A signature: ______  Stage B signature: ______",
            "The same person may fill both only by independently completing both role blocks.",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def generate_specialist_review_packets(
    *,
    literature_context_path: Path,
    proposal_registry_path: Path,
    output_directory: Path,
    producing_command: str,
    additional_input_paths: tuple[Path, ...] = (),
) -> PacketIndex:
    """Read each explicit input once and replace the complete output directory."""
    paths = (literature_context_path, proposal_registry_path, *additional_input_paths)
    if len({path.resolve() for path in paths}) != len(paths):
        raise ValueError("specialist packet input paths must be unique")
    payloads = {str(path): path.read_bytes() for path in paths}
    context = FinalLiteratureContext.model_validate_json(
        payloads[str(literature_context_path)]
    )
    registry_payload = json.loads(payloads[str(proposal_registry_path)])
    registered = {
        item["id"] for item in registry_payload.get("proposals", ()) if "id" in item
    }
    all_mints = set().union(
        *(
            set(_MINT.findall(payload.decode("utf-8", errors="ignore")))
            for payload in payloads.values()
        )
    )
    suppressed = tuple(sorted(all_mints - registered))
    raw_inputs = tuple(
        json.loads(payload)
        for path, payload in payloads.items()
        if path not in {str(literature_context_path), str(proposal_registry_path)}
    )
    packet_payloads = {
        f"{dossier.code}.md": _render_packet(
            dossier,
            _pair_evidence_lines(dossier.code, raw_inputs, registered),
        )
        for dossier in context.dossiers
    }
    entries = tuple(
        PacketIndexEntry(
            code=code, path=f"{code}.md", sha256=_sha(packet_payloads[f"{code}.md"])
        )
        for code in CONCEPT_ORDER
    )
    identities = {str(path): _sha(payloads[str(path)]) for path in paths}
    index = PacketIndex(
        schema_version=1,
        ncit_version="26.07d",
        literature_context_identity=identities[str(literature_context_path)],
        input_identities=identities,
        packets=entries,
    )
    packet_payloads["index.json"] = _canonical_json(index)
    validation = GenerationValidation(
        schema_version=1,
        valid=True,
        producing_command=producing_command,
        generated_files=(*packet_payloads, "generation-validation.json"),
        suppressed_unregistered_mints=suppressed,
        packet_visible_registered_mints=(),
        readiness=False,
        writes_performed=False,
    )
    packet_payloads["generation-validation.json"] = _canonical_json(validation)
    expected = set(packet_payloads)
    if (
        output_directory.is_dir()
        and {item.name for item in output_directory.iterdir()} == expected
        and all(
            (output_directory / name).read_bytes() == payload
            for name, payload in packet_payloads.items()
        )
    ):
        return index
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{output_directory.name}.", dir=output_directory.parent
        )
    )
    backup = output_directory.with_name(f".{output_directory.name}.previous")
    try:
        for name, payload in packet_payloads.items():
            (temporary / name).write_bytes(payload)
        if backup.exists():
            shutil.rmtree(backup)
        if output_directory.exists():
            os.replace(output_directory, backup)
        os.replace(temporary, output_directory)
        if backup.exists():
            shutil.rmtree(backup)
    except BaseException:
        if not output_directory.exists() and backup.exists():
            os.replace(backup, output_directory)
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return index


def validate_completion(
    stage_a: ClinicalStageA,
    stage_b: OntologyStageB,
    *,
    clinically_asked_pairs: tuple[str, ...],
    engineering_only_pairs: tuple[str, ...] = (),
) -> bool:
    if stage_a.clinical_stage != "SUFFICIENT-FOR-ONTOLOGY-REVIEW" and stage_b.decisions:
        raise ValueError("Stage B decisions require sufficient Stage A")
    asked = set(clinically_asked_pairs)
    assessed = {item.pair_id for item in stage_a.assessments}
    decided = {item.pair_id for item in stage_b.decisions}
    engineering = set(engineering_only_pairs)
    if not asked <= assessed:
        raise ValueError("clinically asked pair lacks Stage A assessment")
    if not asked <= decided | engineering:
        raise ValueError(
            "inert clinical question lacks action or engineering-only status"
        )
    if decided & engineering:
        raise ValueError("engineering-only pair cannot have an ontology action")
    if (
        stage_b.row_outcome == "RESOLVED"
        and stage_a.clinical_stage != "SUFFICIENT-FOR-ONTOLOGY-REVIEW"
    ):
        raise ValueError("resolved Stage B requires sufficient Stage A")
    return True


def validate_specialist_review_packet_directory(
    directory: Path,
) -> CompletionValidation:
    """Validate identities and strict returned Stage A/Stage B JSON response blocks."""
    index = PacketIndex.model_validate_json((directory / "index.json").read_bytes())
    for entry in index.packets:
        payload = (directory / entry.path).read_bytes()
        text = payload.decode("utf-8")
        blocks = re.findall(r"```STAGE-([AB])-RESPONSE\n(.*?)\n```", text, re.DOTALL)
        if tuple(kind for kind, _ in blocks) != ("A", "B"):
            raise ValueError(
                f"returned packet response blocks are missing: {entry.path}"
            )
        normalized = re.sub(
            r"(```STAGE-[AB]-RESPONSE\n).*?(\n```)",
            r'\1{"blank": true}\2',
            text,
            flags=re.DOTALL,
        ).encode("utf-8")
        if _sha(normalized) != entry.sha256:
            raise ValueError(f"returned packet identity mismatch: {entry.path}")
        if any(json.loads(body).get("blank") is True for _, body in blocks):
            raise ValueError(f"returned packet is still blank: {entry.path}")
        stage_a = ClinicalStageA.model_validate_json(blocks[0][1])
        stage_b = OntologyStageB.model_validate_json(blocks[1][1])
        validate_completion(
            stage_a,
            stage_b,
            clinically_asked_pairs=tuple(item.pair_id for item in stage_a.assessments),
        )
    return CompletionValidation(
        valid=True,
        completed_codes=tuple(entry.code for entry in index.packets),
        writes_performed=False,
    )
