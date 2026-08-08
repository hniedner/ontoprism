from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import TYPE_CHECKING, cast

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from scripts.adjudication import main as adjudication_main
from scripts.research import golden_review
from scripts.research.golden_review import (
    GoldenSetValidationError,
    evaluate_adjudication,
    export_row_decisions,
    import_adjudication_workbook,
    load_adjudication,
    load_row_decisions,
    load_scorable_golden,
    read_json_without_duplicates,
    write_evaluation_report,
)

from ontolib.decomposition.minting import MintedConcept
from ontolib.decomposition.proposal_registry import (
    ConceptProposal,
    CrossOntologyMapping,
    DuplicateCheck,
    ProposalRegistry,
    ProposalStatus,
    RelationProposal,
    relation_proposal_id,
)

if TYPE_CHECKING:
    from pathlib import Path

_EXCEL_MAX_COLUMN = 16384
_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_DIGEST_C = "c" * 64
_DETECTOR_IDENTITY = "6" * 64
_REQUIRED_SEEDS = ("C4791", "C35756", "C89995")


def _constituent(
    axis: str = "op:StageValue",
    filler: str = "C27970",
    *,
    group: str | None = None,
    needs_review: bool = False,
    provenance_status: str | None = None,
    proposal_id: str | None = None,
) -> dict[str, object]:
    status = provenance_status or (
        "locally-approved" if filler.startswith("MINT-") else "ncit-26.07d"
    )
    return {
        "axis": axis,
        "filler": filler,
        "relationship_group": group,
        "needs_review": needs_review,
        "provenance_status": status,
        "proposal_id": proposal_id or (filler if filler.startswith("MINT-") else None),
    }


def _proposal_registry(
    *, status: ProposalStatus = "locally-approved"
) -> ProposalRegistry:
    name = "associated prior disease"
    relation = RelationProposal(
        id=relation_proposal_id(name),
        axis="op:AssociatedPriorDisease",
        preferred_name=name,
        definition="Relates a disease to a distinct disease present earlier.",
        domain="C7057",
        range="C7057",
        source_roles=("R126",),
        source_examples=("C100051->C27262",),
        rationale="The source role conflates several disease relationships.",
        duplicate_checks=(
            DuplicateCheck(
                resource="RO",
                version="2026-08-05",
                query=name,
                result="no-equivalent",
                evidence_url="https://example.test/ro-review",
            ),
        ),
        submission_target="RO",
        status=status,
    )
    return ProposalRegistry(
        source_identity=_DIGEST_A,
        ontology_version="26.07d",
        proposals=(relation,),
    )


def _metric_proposal_registry() -> ProposalRegistry:
    approved = _proposal_registry().proposals[0]
    name = "caused by associated disease"
    proposed = RelationProposal(
        id=relation_proposal_id(name),
        axis="op:CausedByAssociatedDisease",
        preferred_name=name,
        definition="Relates a disease to another disease stated to cause it.",
        domain="C7057",
        range="C7057",
        source_roles=("R126",),
        source_examples=("C0->C999999",),
        rationale="Direct causation requires a univocal relation.",
        duplicate_checks=(
            DuplicateCheck(
                resource="RO",
                version="2026-08-05",
                query=name,
                result="no-equivalent",
                evidence_url="https://example.test/ro-review",
            ),
        ),
        submission_target="RO",
    )
    return ProposalRegistry(
        source_identity=_DIGEST_A,
        ontology_version="26.07d",
        proposals=(approved, proposed),
    )


_EMPTY_PROPOSAL_REGISTRY = ProposalRegistry(
    source_identity=_DIGEST_A,
    ontology_version="26.07d",
    proposals=(),
)


def _engine_constituent(
    axis: str = "op:StageValue",
    filler: str = "C27970",
    *,
    group: str | None = None,
    needs_review: bool = False,
) -> dict[str, object]:
    return {
        "axis": axis,
        "filler": filler,
        "relationship_group": group,
        "needs_review": needs_review,
    }


def _accepted(
    code: str,
    *,
    outcome: str = "decomposed",
    constituents: list[dict[str, object]] | None = None,
    semantic_types: list[str] | None = None,
) -> dict[str, object]:
    return {
        "code": code,
        "label": f"Reviewed {code}",
        "adjudication": {
            "status": "accepted",
            "rationale": "Reviewed against the stated NCIt definition.",
        },
        "expected": {
            "outcome": outcome,
            "semantic_types": semantic_types or ["Neoplastic Process"],
            "constituents": (
                constituents
                if constituents is not None
                else ([] if outcome != "decomposed" else [_constituent()])
            ),
        },
    }


def _artifact(
    concepts: list[dict[str, object]],
    *,
    engine_evidence_identity: str | None = None,
    corpus_evidence_identity: str | None = None,
    proposal_registry: ProposalRegistry = _EMPTY_PROPOSAL_REGISTRY,
) -> dict[str, object]:
    payload = {
        "_meta": {
            "schema_version": 3,
            "status": "SME-ADJUDICATED",
            "ncit_version": "26.07d",
            "source_identity": _DIGEST_A,
            "sample_manifest_identity": _DIGEST_B,
            "run_id": "neoplasm-run-1",
            "run_fingerprint_identity": _DIGEST_C,
            "engine_artifact_identity": "d" * 64,
            "engine_evidence_identity": (
                engine_evidence_identity
                or cast("str", _engine_evidence()["evidence_identity"])
            ),
            "corpus_evidence_identity": (
                corpus_evidence_identity
                or cast("str", _corpus_evidence()["evidence_identity"])
            ),
            "detector_identity": _DETECTOR_IDENTITY,
            "proposal_registry_identity": proposal_registry.registry_identity,
            "workbook_identity": "e" * 64,
            "reviewer": {
                "name": "Example Reviewer",
                "qualification_or_role": "NCIt ontology curator",
                "reviewed_at": "2026-07-30",
            },
        },
        "concepts": concepts,
    }
    payload["artifact_identity"] = _identity(payload)
    return payload


def _identity(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
    ).hexdigest()


def _sign(value: dict[str, object]) -> dict[str, object]:
    value.pop("evidence_identity", None)
    value["evidence_identity"] = _identity(value)
    return value


def _resign_artifact(value: dict[str, object]) -> dict[str, object]:
    value.pop("artifact_identity", None)
    value["artifact_identity"] = _identity(value)
    return value


def _bind_artifact_to_engine(
    concepts: list[dict[str, object]],
    engine: dict[str, object],
    corpus: dict[str, object] | None = None,
    proposal_registry: ProposalRegistry = _EMPTY_PROPOSAL_REGISTRY,
) -> dict[str, object]:
    bound_corpus = corpus or _corpus_evidence()
    return _artifact(
        concepts,
        engine_evidence_identity=cast("str", engine["evidence_identity"]),
        corpus_evidence_identity=cast("str", bound_corpus["evidence_identity"]),
        proposal_registry=proposal_registry,
    )


def _corpus_evidence(
    *, denominator: list[str] | None = None, residual: list[str] | None = None
) -> dict[str, object]:
    return _sign(
        {
            "name": "issue-154-corpus-sample",
            "source_identity": _DIGEST_A,
            "sample_manifest_identity": "9" * 64,
            "run_id": "sample",
            "run_fingerprint_identity": "8" * 64,
            "engine_artifact_identity": "7" * 64,
            "detector_identity": _DETECTOR_IDENTITY,
            "denominator_codes": denominator or ["C1"],
            "residual_codes": residual or [],
        }
    )


def _m1_concepts() -> list[dict[str, object]]:
    codes = [f"C{index}" for index in range(17)] + list(_REQUIRED_SEEDS)
    return [_accepted(code) for code in codes]


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.mark.unit
def test_adjudication_rejects_draft_duplicate_keys_and_duplicate_concepts(
    tmp_path: Path,
) -> None:
    draft = tmp_path / "draft.json"
    _write_json(draft, {"_meta": {"status": "AUTO-DRAFT"}, "concepts": []})
    with pytest.raises(GoldenSetValidationError, match="not SME-adjudicated"):
        load_adjudication(draft)

    duplicate_key = tmp_path / "duplicate-key.json"
    duplicate_key.write_text('{"_meta": {}, "_meta": {}, "concepts": []}')
    with pytest.raises(GoldenSetValidationError, match="duplicate JSON key: _meta"):
        load_adjudication(duplicate_key)

    duplicate_concept = tmp_path / "duplicate-concept.json"
    concepts = _m1_concepts()
    concepts[-1] = concepts[0]
    _write_json(duplicate_concept, _artifact(concepts))
    with pytest.raises(GoldenSetValidationError, match="concept codes must be unique"):
        load_adjudication(duplicate_concept)


@pytest.mark.unit
def test_adjudication_rejects_tampered_payload_and_empty_accepted_cohort(
    tmp_path: Path,
) -> None:
    tampered = _artifact(_m1_concepts())
    tampered["concepts"][0]["label"] = "Tampered after signature"
    path = tmp_path / "tampered.json"
    _write_json(path, tampered)
    with pytest.raises(GoldenSetValidationError, match="identity does not match"):
        load_adjudication(path)

    concepts = [
        {
            "code": concept["code"],
            "label": concept["label"],
            "adjudication": {
                "status": "rejected",
                "rationale": "Unsuitable for this oracle.",
            },
            "expected": None,
        }
        for concept in _m1_concepts()
    ]
    path = tmp_path / "no-accepted.json"
    _write_json(path, _artifact(concepts))
    with pytest.raises(GoldenSetValidationError, match="at least one accepted"):
        load_adjudication(path)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value["_meta"].update({"unknown": "x"}), "extra"),
        (
            lambda value: value["_meta"]["reviewer"].update(
                {"qualification_or_role": ""}
            ),
            "qualification",
        ),
        (
            lambda value: value["concepts"][0].update({"unexpected": "x"}),
            "extra",
        ),
        (
            lambda value: value["concepts"][0]["expected"]["constituents"].append(
                _constituent(group="another-group")
            ),
            "axis/filler pairs must be unique",
        ),
        (
            lambda value: value["concepts"][0]["expected"].update(
                {"outcome": "residual"}
            ),
            "non-decomposed expectation",
        ),
        (
            lambda value: value["concepts"][0]["expected"]["constituents"][0].update(
                {"filler": "not-an-ncit-code"}
            ),
            "NCIt constituent filler",
        ),
    ],
)
def test_adjudication_schema_rejects_untrusted_shapes(
    tmp_path: Path,
    mutate: object,
    message: str,
) -> None:
    payload = _artifact(_m1_concepts())
    mutate(payload)  # type: ignore[operator]
    _resign_artifact(payload)
    path = tmp_path / "invalid.json"
    _write_json(path, payload)

    with pytest.raises(GoldenSetValidationError, match=message):
        load_adjudication(path)


@pytest.mark.unit
def test_nonaccepted_decisions_require_rationale_and_cannot_claim_expectations(
    tmp_path: Path,
) -> None:
    concepts = _m1_concepts()
    concepts[0] = {
        "code": "C0",
        "label": "Unsuitable",
        "adjudication": {"status": "rejected", "rationale": ""},
        "expected": None,
    }
    path = tmp_path / "missing-rationale.json"
    _write_json(path, _artifact(concepts))
    with pytest.raises(GoldenSetValidationError, match="rationale"):
        load_adjudication(path)

    concepts[0]["adjudication"]["rationale"] = "Unsuitable source case."
    concepts[0]["expected"] = _accepted("C0")["expected"]
    _write_json(path, _artifact(concepts))
    with pytest.raises(GoldenSetValidationError, match="must not define expected"):
        load_adjudication(path)


@pytest.mark.unit
def test_m1_cohort_validation_is_mandatory(tmp_path: Path) -> None:
    too_small = tmp_path / "small.json"
    _write_json(too_small, _artifact(_m1_concepts()[:19]))
    with pytest.raises(GoldenSetValidationError, match="20 to 50"):
        load_adjudication(too_small)

    missing_seed = tmp_path / "missing-seed.json"
    _write_json(
        missing_seed,
        _artifact([_accepted(f"C{index}") for index in range(20)]),
    )
    with pytest.raises(GoldenSetValidationError, match="C35756, C4791, C89995"):
        load_adjudication(missing_seed)


@pytest.mark.unit
def test_scorable_view_uses_accepted_decisions_and_retains_review_exclusions(
    tmp_path: Path,
) -> None:
    concepts = _m1_concepts()
    concepts[0] = _accepted(
        "C0",
        constituents=[
            _constituent(),
            _constituent(
                "op:AssociatedRegion",
                "C12418",
                needs_review=True,
            ),
        ],
    )
    concepts[1] = {
        "code": "C1",
        "label": "Rejected",
        "adjudication": {
            "status": "rejected",
            "rationale": "Unsuitable for this oracle.",
        },
        "expected": None,
    }
    path = tmp_path / "adjudicated.json"
    _write_json(path, _artifact(concepts))

    golden = load_scorable_golden(path)

    assert "C1" not in golden.expected
    assert golden.expected["C0"] == frozenset({("op:StageValue", "C27970")})
    assert golden.review_exclusions["C0"] == frozenset(
        {("op:AssociatedRegion", "C12418")}
    )
    assert golden.reviewer_qualification == "NCIt ontology curator"
    assert golden.expectations["C0"].constituents[0].provenance_status == (
        "ncit-26.07d"
    )


@pytest.mark.unit
def test_augmented_expectation_is_bound_to_matching_registry_record(
    tmp_path: Path,
) -> None:
    registry = _proposal_registry()
    proposal_id = registry.proposals[0].id
    concepts = _m1_concepts()
    concepts[0] = _accepted(
        "C0",
        constituents=[
            _constituent(
                "op:AssociatedPriorDisease",
                "C27262",
                provenance_status="locally-approved",
                proposal_id=proposal_id,
            )
        ],
    )
    path = tmp_path / "adjudicated.json"
    _write_json(path, _artifact(concepts, proposal_registry=registry))

    artifact = load_adjudication(path, registry)

    assert artifact.meta.proposal_registry_identity == registry.registry_identity
    assert artifact.concepts[0].expected is not None
    assert artifact.concepts[0].expected.constituents[0].proposal_id == proposal_id

    concepts[0]["expected"]["constituents"][0]["proposal_id"] = "RELPROP-unknown"
    _write_json(path, _artifact(concepts, proposal_registry=registry))
    with pytest.raises(GoldenSetValidationError, match="unknown proposal"):
        load_adjudication(path, registry)


def _accepted_concept_registry() -> ProposalRegistry:
    name = "Malignant Non-Seminomatous Germ Cell"
    concept = ConceptProposal(
        id=MintedConcept(axis="op:CellType", label=name).id,
        axis="op:CellType",
        preferred_name=name,
        definition="A malignant germ cell with non-seminomatous differentiation.",
        parent_concepts=("C12917",),
        semantic_types=("Cell",),
        source_concepts=("C27787",),
        source_roles=("R105",),
        rationale="No existing NCIt cell concept expresses the required intersection.",
        duplicate_checks=(
            DuplicateCheck(
                resource="NCIt",
                version="26.07d",
                query=name,
                result="no-equivalent",
                evidence_url="https://example.test/ncit-review",
            ),
        ),
        mappings=(
            CrossOntologyMapping(
                system="SNOMED CT US",
                version="2025-09-01",
                concept_id="128766005",
                label="Germ cell tumor, nonseminomatous",
                predicate="relatedMatch",
                evidence_url="https://example.test/snomed/128766005",
            ),
        ),
        submission_target="NCIt",
        status="accepted",
        replacement_ncit_code="C999999",
    )
    return ProposalRegistry(
        source_identity=_DIGEST_A,
        ontology_version="26.07d",
        proposals=(concept,),
    )


@pytest.mark.unit
def test_accepted_proposal_expectation_must_cite_the_replacement_code(
    tmp_path: Path,
) -> None:
    """`accepted-in-ncit` is the terminal lifecycle step (D60).

    Once NCI assigns a real code, the augmented expectation must cite that code,
    not the MINT-* placeholder that stood in for it.
    """
    registry = _accepted_concept_registry()
    proposal = registry.proposals[0]
    path = tmp_path / "adjudicated.json"

    def _write(filler: str) -> None:
        concepts = _m1_concepts()
        concepts[0] = _accepted(
            "C0",
            constituents=[
                _constituent(
                    "op:CellType",
                    filler,
                    provenance_status="accepted-in-ncit",
                    proposal_id=proposal.id,
                )
            ],
        )
        _write_json(path, _artifact(concepts, proposal_registry=registry))

    _write("C999999")
    artifact = load_adjudication(path, registry)
    assert artifact.concepts[0].expected is not None
    assert artifact.concepts[0].expected.constituents[0].filler == "C999999"

    _write(proposal.id)
    with pytest.raises(
        GoldenSetValidationError, match="proposal filler does not match"
    ):
        load_adjudication(path, registry)


@pytest.mark.unit
def test_augmented_expectation_rejects_missing_id_status_or_axis_binding(
    tmp_path: Path,
) -> None:
    local_registry = _proposal_registry()
    proposal_id = local_registry.proposals[0].id
    base = _constituent(
        "op:AssociatedPriorDisease",
        "C27262",
        provenance_status="locally-approved",
        proposal_id=proposal_id,
    )
    path = tmp_path / "invalid-adjudicated.json"

    for mutation, registry, message in (
        ({"proposal_id": None}, local_registry, "proposal ID"),
        ({}, _proposal_registry(status="submitted"), "status"),
        ({"axis": "op:CausedByAssociatedDisease"}, local_registry, "axis"),
        ({"filler": "free text"}, local_registry, "NCIt code"),
    ):
        constituent = base | mutation
        concepts = _m1_concepts()
        concepts[0] = _accepted("C0", constituents=[constituent])
        _write_json(path, _artifact(concepts, proposal_registry=registry))
        with pytest.raises(GoldenSetValidationError, match=message):
            load_adjudication(path, registry)


def _create_workbook(
    path: Path, *, pending: bool = False, formula: bool = False
) -> None:
    wb = Workbook()
    start = wb.active
    start.title = "START HERE"
    reviewer = wb.create_sheet("Reviewer & Attestation")
    reviewer["B5"] = "Example Reviewer"
    reviewer["B6"] = "NCIt ontology curator"
    reviewer["B7"] = date(2026, 7, 30)
    reviewer["B8"] = "NCIt 26.07d"
    reviewer["B9"] = "ATTESTED"
    concepts = wb.create_sheet("Concept Decisions")
    concept_headers = [
        "Order",
        "Concept Code",
        "Source Label",
        "Source Semantic Types",
        "Expected Semantic Types",
        "Engine Suggested Outcome",
        "SME Decision Status",
        "Expected Outcome",
        "Rationale / Required Follow-up",
        "Source Reviewed?",
        "Concept Complete?",
    ]
    for column, header in enumerate(concept_headers, start=1):
        concepts.cell(4, column, header)
    codes = [f"C{index}" for index in range(17)] + list(_REQUIRED_SEEDS)
    for order, code in enumerate(codes, start=1):
        row = order + 4
        values = [
            order,
            code,
            f"Reviewed {code}",
            "Neoplastic Process",
            "Neoplastic Process",
            "decomposed",
            "PENDING" if pending and order == 1 else "accepted",
            "decomposed",
            "Reviewed against the stated source.",
            "YES",
            "YES",
        ]
        for column, value in enumerate(values, start=1):
            concepts.cell(row, column, value)
    constituents = wb.create_sheet("Constituent Decisions")
    constituent_headers = [
        "Concept Order",
        "Concept Code",
        "Source Label",
        "Row Type",
        "Engine Axis",
        "Engine Filler",
        "Engine Filler Label",
        "Engine Group",
        "Engine needs_review",
        "SME Action",
        "Expected Axis",
        "Expected Filler",
        "Expected Group",
        "Expected needs_review",
        "Expected Provenance Status",
        "Expected Proposal ID",
        "SME Notes",
        "Row Complete?",
    ]
    for column, header in enumerate(constituent_headers, start=1):
        constituents.cell(4, column, header)
    for order, code in enumerate(codes, start=1):
        row = order + 4
        values = [
            order,
            code,
            f"Reviewed {code}",
            "ENGINE SUGGESTION",
            "op:StageValue",
            "C27970",
            "Stage III",
            None,
            "FALSE",
            "include",
            "op:StageValue",
            "C27970",
            None,
            "FALSE",
            "ncit-26.07d",
            None,
            "",
            "YES",
        ]
        for column, value in enumerate(values, start=1):
            constituents.cell(row, column, value)
    wb.create_sheet("Validation Summary")
    wb.create_sheet("Worked Examples")
    wb.create_sheet("Prior SME Evidence")
    evidence = wb.create_sheet("Source & Run Evidence")
    rows = [
        ("NCIt release", "26.07d"),
        ("Source identity", _DIGEST_A),
        ("Sample identity", _DIGEST_B),
        ("Engine run", "neoplasm-run-1"),
        ("Run fingerprint identity", _DIGEST_C),
        ("Artifact SHA-256", "d" * 64),
        ("Engine evidence identity", _engine_evidence()["evidence_identity"]),
        ("Corpus evidence identity", _corpus_evidence()["evidence_identity"]),
        ("Detector identity", _DETECTOR_IDENTITY),
        ("Proposal registry identity", _EMPTY_PROPOSAL_REGISTRY.registry_identity),
    ]
    for row, (key, value) in enumerate(rows, start=5):
        evidence.cell(row, 1, key)
        evidence.cell(row, 2, value)
    if formula:
        concepts["I5"] = '=CONCAT("not", " authored")'
    wb.save(path)


@pytest.mark.unit
def test_openpyxl_contract_preserves_formula_and_boolean_cell_types(
    tmp_path: Path,
) -> None:
    path = tmp_path / "contract.xlsx"
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "=1+1"
    ws["A2"] = True
    wb.save(path)

    loaded = load_workbook(path, data_only=False)

    assert loaded.active["A1"].data_type == "f"
    assert loaded.active["A2"].data_type == "b"


@pytest.mark.unit
def test_workbook_import_preserves_reviewer_values_and_provenance(
    tmp_path: Path,
) -> None:
    workbook = tmp_path / "review.xlsx"
    _create_workbook(workbook)

    artifact = import_adjudication_workbook(workbook)

    assert artifact.meta.reviewer.name == "Example Reviewer"
    assert artifact.meta.reviewer.qualification_or_role == "NCIt ontology curator"
    assert artifact.meta.workbook_identity != _DIGEST_A
    assert artifact.meta.run_fingerprint_identity == _DIGEST_C
    assert artifact.concepts[0].expected is not None
    assert artifact.concepts[0].expected.constituents[0].needs_review is False


@pytest.mark.unit
def test_workbook_import_rejects_hidden_reviewer_schema_column(tmp_path: Path) -> None:
    workbook = tmp_path / "review.xlsx"
    _create_workbook(workbook)
    hidden = load_workbook(workbook)
    hidden["Constituent Decisions"].column_dimensions["J"].hidden = True
    hidden.save(workbook)

    with pytest.raises(GoldenSetValidationError, match="hidden reviewer columns"):
        import_adjudication_workbook(workbook)


@pytest.mark.unit
def test_workbook_import_rejects_a_grouped_hidden_reviewer_column(
    tmp_path: Path,
) -> None:
    """openpyxl stores a grouped hide under the range's first letter only.

    `column_dimensions["T"]` on an `S:T` group auto-creates a fresh default with
    `hidden=False`, so a per-letter probe lets a reviewer conceal a data column by
    hiding a range whose first column is blank -- and the SME then attests to
    a sheet they could not fully see.
    """
    workbook = tmp_path / "review.xlsx"
    _create_workbook(workbook)
    hidden = load_workbook(workbook)
    sheet = hidden["Constituent Decisions"]
    # "T" carries concealed reviewer content; "S" is the blank leading column of the
    # hidden range, which is the only key openpyxl stores the group under.
    sheet["T5"] = "concealed reviewer note"
    sheet.column_dimensions.group("S", "T", hidden=True)
    assert sheet.column_dimensions["T"].hidden is False
    hidden.save(workbook)

    with pytest.raises(GoldenSetValidationError, match="hidden reviewer columns"):
        import_adjudication_workbook(workbook)


@pytest.mark.unit
def test_trailing_hidden_columns_neither_reject_nor_materialize_the_grid(
    tmp_path: Path,
) -> None:
    """Excel writes `<col min="8" max="16384" hidden="1"/>` for a trailing hide.

    That is an ordinary, harmless reviewer action. An uncapped expansion would
    report ~16k indexes and, once the caller reads cells, materialise the whole
    grid -- turning a legitimate import into a hang rather than a verdict.
    """
    workbook = tmp_path / "review.xlsx"
    _create_workbook(workbook)
    hidden = load_workbook(workbook)
    sheet = hidden["Concept Decisions"]
    used_columns = sheet.max_column
    sheet.column_dimensions.group(
        get_column_letter(used_columns + 1), "XFD", hidden=True
    )
    hidden.save(workbook)

    reloaded = load_workbook(workbook)["Concept Decisions"]
    assert (
        reloaded.column_dimensions[get_column_letter(used_columns + 1)].max
        == _EXCEL_MAX_COLUMN
    )
    # Bounded by the used range, so the caller never materialises the full grid.
    assert (
        max(
            golden_review.hidden_column_indexes(reloaded, limit=reloaded.max_column),
            default=0,
        )
        <= used_columns
    )
    assert reloaded.max_column == used_columns

    assert import_adjudication_workbook(workbook).concepts


@pytest.mark.unit
def test_a_visible_column_dimension_is_not_reported_as_hidden(tmp_path: Path) -> None:
    """Only the reject direction was pinned; a width change must still import.

    openpyxl populates `min`/`max` on every `<col>` element it reads, so a gate
    that ignored `hidden` would reject every workbook a reviewer had ever resized.
    """
    workbook = tmp_path / "review.xlsx"
    _create_workbook(workbook)
    widened = load_workbook(workbook)
    widened["Concept Decisions"].column_dimensions["A"].width = 40
    widened.save(workbook)

    assert import_adjudication_workbook(workbook).concepts


@pytest.mark.unit
def test_hidden_column_without_a_stored_range_is_still_reported() -> None:
    """An in-memory hide leaves `min`/`max` unset until `reindex()` at save time.

    Skipping those dimensions would let the anti-tamper helper degrade to
    "nothing is hidden". Not reachable through ``load_workbook`` -- a file's
    ``<col>`` always carries ``min`` -- but the helper must not depend on that.
    """
    sheet = Workbook().active
    assert sheet is not None
    sheet["D1"] = "concealed"
    sheet.column_dimensions["D"].hidden = True
    assert sheet.column_dimensions["D"].min is None

    assert golden_review.hidden_column_indexes(sheet, limit=sheet.max_column) == {4}


@pytest.mark.unit
def test_metric_modality_rejects_unknown_normalized_axis() -> None:
    with pytest.raises(GoldenSetValidationError, match="unknown normalized axis"):
        golden_review._pair_modality(("op:NormalTissueOrgin", "C49276"))


@pytest.mark.unit
def test_workbook_import_hashes_the_parsed_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbook = tmp_path / "review.xlsx"
    _create_workbook(workbook)
    snapshot = workbook.read_bytes()
    real_load_workbook = golden_review.load_workbook

    def replace_after_load(source: object, **kwargs: object) -> Workbook:
        loaded = real_load_workbook(source, **kwargs)
        workbook.write_bytes(b"changed after the review snapshot was opened")
        return loaded

    monkeypatch.setattr(golden_review, "load_workbook", replace_after_load)

    artifact = import_adjudication_workbook(workbook)

    assert artifact.meta.workbook_identity == hashlib.sha256(snapshot).hexdigest()


@pytest.mark.unit
def test_workbook_import_allows_semantic_bundle_decision_sheet(tmp_path: Path) -> None:
    workbook = tmp_path / "semantic-review.xlsx"
    _create_workbook(workbook)
    loaded = load_workbook(workbook)
    loaded.create_sheet("Semantic Bundle Decisions")
    loaded.save(workbook)

    artifact = import_adjudication_workbook(workbook)

    assert artifact.meta.reviewer.name == "Example Reviewer"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("pending", "formula", "message"),
    [
        (True, False, "pending adjudication"),
        (False, True, "formula cells are not permitted"),
    ],
)
def test_workbook_import_fails_closed_on_unresolved_or_computed_input(
    tmp_path: Path,
    pending: bool,
    formula: bool,
    message: str,
) -> None:
    workbook = tmp_path / "invalid.xlsx"
    _create_workbook(workbook, pending=pending, formula=formula)

    with pytest.raises(GoldenSetValidationError, match=message):
        import_adjudication_workbook(workbook)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda wb: setattr(
                wb["Concept Decisions"].row_dimensions[5], "hidden", True
            ),
            "hidden concept rows",
        ),
        (
            lambda wb: setattr(wb["Worked Examples"], "sheet_state", "hidden"),
            "reviewer input sheet must be visible",
        ),
        (
            lambda wb: (
                setattr(wb["Reviewer & Attestation"]["A16"], "value", "Hidden note"),
                setattr(
                    wb["Reviewer & Attestation"].row_dimensions[16],
                    "hidden",
                    True,
                ),
            ),
            "hidden reviewer rows",
        ),
        (
            lambda wb: setattr(
                wb["Constituent Decisions"].row_dimensions[5], "hidden", True
            ),
            "hidden constituent rows",
        ),
        (
            lambda wb: setattr(
                wb["Constituent Decisions"]["D5"], "value", "UNRECOGNIZED"
            ),
            "invalid row type",
        ),
        (
            lambda wb: setattr(wb["Constituent Decisions"]["B5"], "value", "C999999"),
            "unknown concepts",
        ),
        (
            lambda wb: setattr(wb["Concept Decisions"]["B5"], "value", None),
            "populated concept row has blank concept code",
        ),
        (
            lambda wb: setattr(wb["Constituent Decisions"]["B5"], "value", None),
            "populated constituent row has blank concept code",
        ),
        (
            lambda wb: (
                setattr(wb["Source & Run Evidence"]["A11"], "value", "Source identity"),
                setattr(wb["Source & Run Evidence"]["B11"], "value", _DIGEST_A),
            ),
            "duplicate Source & Run Evidence key",
        ),
    ],
)
def test_workbook_import_rejects_hidden_duplicate_or_orphaned_truth(
    tmp_path: Path,
    mutation: object,
    message: str,
) -> None:
    workbook_path = tmp_path / "invalid-structure.xlsx"
    _create_workbook(workbook_path)
    workbook = load_workbook(workbook_path)
    mutation(workbook)  # type: ignore[operator]
    workbook.save(workbook_path)

    with pytest.raises(GoldenSetValidationError, match=message):
        import_adjudication_workbook(workbook_path)


@pytest.mark.unit
def test_workbook_import_requires_reviewer_authored_expected_semantic_types(
    tmp_path: Path,
) -> None:
    workbook_path = tmp_path / "missing-expected-types.xlsx"
    _create_workbook(workbook_path)
    workbook = load_workbook(workbook_path)
    workbook["Concept Decisions"].delete_cols(5)
    workbook.save(workbook_path)

    with pytest.raises(GoldenSetValidationError, match="Expected Semantic Types"):
        import_adjudication_workbook(workbook_path)


def _engine_evidence(
    *, wrong_group: bool = False, wrong_identity: bool = False
) -> dict:
    codes = [f"C{index}" for index in range(17)] + list(_REQUIRED_SEEDS)
    return _sign(
        {
            "schema_version": 1,
            "ncit_version": "26.07d",
            "source_identity": _DIGEST_A,
            "sample_manifest_identity": _DIGEST_B,
            "run_id": "neoplasm-run-1",
            "run_fingerprint_identity": ("f" * 64 if wrong_identity else _DIGEST_C),
            "engine_artifact_identity": "d" * 64,
            "detector_identity": _DETECTOR_IDENTITY,
            "concepts": [
                {
                    "code": code,
                    "outcome": "decomposed",
                    "semantic_types": ["Neoplastic Process"],
                    "constituents": [
                        {
                            "axis": "op:StageValue",
                            "filler": "C27970",
                            "relationship_group": (
                                "actual" if wrong_group and code == "C0" else None
                            ),
                            "needs_review": False,
                        }
                    ],
                }
                for code in codes
            ],
            "residual_precoordinated_codes": codes,
        }
    )


@pytest.mark.unit
def test_evaluation_reports_outcomes_groups_and_d21_exclusions(tmp_path: Path) -> None:
    concepts = _m1_concepts()
    concepts[0] = _accepted(
        "C0",
        constituents=[
            _constituent(group="expected"),
            _constituent(
                "op:AssociatedRegion",
                "C12418",
                needs_review=True,
            ),
        ],
    )
    engine = _engine_evidence()
    engine["concepts"][0]["constituents"].append(
        _engine_constituent("op:AssociatedRegion", "C12418")
    )
    _sign(engine)
    corpus = _corpus_evidence(denominator=["C10", "C11"], residual=["C10"])
    artifact_path = tmp_path / "artifact.json"
    _write_json(artifact_path, _bind_artifact_to_engine(concepts, engine, corpus))

    report = evaluate_adjudication(load_adjudication(artifact_path), engine, corpus)

    first = report["concepts"][0]
    assert first["pair_score"]["ncit_bound"]["extra"] == []
    assert first["pair_score"]["augmented"]["extra"] == []
    assert first["expected_review_exclusions"] == [["op:AssociatedRegion", "C12418"]]
    assert first["group_match"] is False
    assert first["outcome_match"] is True
    assert report["residual_comparison"]["adjudication"]["count"] == 20
    assert report["residual_comparison"]["corpus_sample"]["count"] == 1
    assert report["residual_comparison"]["rates_averaged"] is False


@pytest.mark.unit
def test_evaluation_derives_ncit_bound_and_augmented_views_from_provenance(
    tmp_path: Path,
) -> None:
    registry = _metric_proposal_registry()
    approved, proposed = registry.proposals
    concepts = _m1_concepts()
    concepts[0] = _accepted(
        "C0",
        constituents=[
            _constituent("op:CellType", "C36903"),
            _constituent(
                "op:AssociatedPriorDisease",
                "C27262",
                provenance_status="locally-approved",
                proposal_id=approved.id,
            ),
            _constituent(
                "op:CausedByAssociatedDisease",
                "C999999",
                provenance_status="proposed",
                proposal_id=proposed.id,
            ),
        ],
    )
    engine = _engine_evidence()
    engine["concepts"][0]["constituents"] = [
        {
            "axis": "op:CellType",
            "filler": "C36903",
            "relationship_group": None,
            "needs_review": False,
        }
    ]
    _sign(engine)
    artifact_path = tmp_path / "artifact.json"
    _write_json(
        artifact_path,
        _bind_artifact_to_engine(
            concepts,
            engine,
            proposal_registry=registry,
        ),
    )

    report = evaluate_adjudication(
        load_adjudication(artifact_path, registry), engine, _corpus_evidence()
    )

    assert report["pair_micro"]["ncit_bound"] == {
        "expected": 20,
        "actual": 20,
        "true_positive": 20,
        "precision": 1.0,
        "recall": 1.0,
    }
    assert report["pair_micro"]["augmented"] == {
        "expected": 21,
        "actual": 20,
        "true_positive": 20,
        "precision": 1.0,
        "recall": 20 / 21,
    }
    assert report["pair_micro"]["defining_only"] == {
        "expected": 20,
        "actual": 20,
        "true_positive": 20,
        "precision": 1.0,
        "recall": 1.0,
    }
    assert report["pair_micro"]["non_defining"] == {
        "expected": 0,
        "actual": 0,
        "true_positive": 0,
        "precision": None,
        "recall": None,
    }
    assert report["expected_pair_provenance"] == {
        "locally-approved": 1,
        "ncit-26.07d": 20,
        "proposed": 1,
    }
    assert report["pair_by_axis"]["ncit_bound"]["op:CellType"] == {
        "expected": 1,
        "actual": 1,
        "true_positive": 1,
        "precision": 1.0,
        "recall": 1.0,
    }
    assert report["pair_by_axis"]["augmented"]["op:CellType"] == {
        "expected": 1,
        "actual": 1,
        "true_positive": 1,
        "precision": 1.0,
        "recall": 1.0,
    }
    assert report["pair_by_axis"]["augmented"]["op:AssociatedPriorDisease"] == {
        "expected": 1,
        "actual": 0,
        "true_positive": 0,
        "precision": None,
        "recall": 0.0,
    }
    for view in ("ncit_bound", "augmented", "defining_only", "non_defining"):
        assert {
            field: sum(axis[field] for axis in report["pair_by_axis"][view].values())
            for field in ("expected", "actual", "true_positive")
        } == {
            field: report["pair_micro"][view][field]
            for field in ("expected", "actual", "true_positive")
        }
    assert report["expected_pair_deferrals"] == {
        "augmented": {"deferred": 0, "engine_matches": 0, "expected": 21},
        "defining_only": {"deferred": 0, "engine_matches": 0, "expected": 20},
        "ncit_bound": {"deferred": 0, "engine_matches": 0, "expected": 20},
        "non_defining": {"deferred": 0, "engine_matches": 0, "expected": 0},
    }
    assert report["proposal_governance"] == {
        "augmented_expected": 1,
        "distinct_engine_proposals": 0,
        "engine_emissions": 0,
        "expected_by_status": {"locally-approved": 1, "proposed": 1},
    }
    first = report["concepts"][0]["pair_score"]
    assert first["ncit_bound"]["missing"] == []
    assert first["augmented"]["missing"] == [["op:AssociatedPriorDisease", "C27262"]]


@pytest.mark.unit
def test_evaluation_derives_non_defining_stratum_from_axis_contract(
    tmp_path: Path,
) -> None:
    concepts = _m1_concepts()
    concepts[0] = _accepted(
        "C0",
        constituents=[
            _constituent("op:CellType", "C36903"),
            _constituent("op:NormalTissueOrigin", "C12345"),
        ],
    )
    engine = _engine_evidence()
    engine["concepts"][0]["constituents"] = [
        _engine_constituent("op:CellType", "C36903"),
        _engine_constituent("op:NormalTissueOrigin", "C12345"),
    ]
    _sign(engine)
    artifact_path = tmp_path / "artifact.json"
    _write_json(artifact_path, _bind_artifact_to_engine(concepts, engine))

    report = evaluate_adjudication(
        load_adjudication(artifact_path), engine, _corpus_evidence()
    )

    assert report["pair_micro"]["defining_only"] == {
        "expected": 20,
        "actual": 20,
        "true_positive": 20,
        "precision": 1.0,
        "recall": 1.0,
    }
    assert report["pair_micro"]["non_defining"] == {
        "expected": 1,
        "actual": 1,
        "true_positive": 1,
        "precision": 1.0,
        "recall": 1.0,
    }
    first = report["concepts"][0]["pair_score"]
    assert first["defining_only"]["expected"] == 1
    assert first["non_defining"]["expected"] == 1
    assert report["concepts"][0]["expected_pair_modality"] == [
        {"axis": "op:CellType", "filler": "C36903", "modality": "asserted"},
        {
            "axis": "op:NormalTissueOrigin",
            "filler": "C12345",
            "modality": "non-defining",
        },
    ]


@pytest.mark.unit
def test_local_relation_with_ncit_filler_remains_in_ncit_bound_view(
    tmp_path: Path,
) -> None:
    concepts = _m1_concepts()
    concepts[0] = _accepted(
        "C0",
        constituents=[_constituent("op:AssociatedPriorDisease", "C3270")],
    )
    engine = _engine_evidence()
    engine["concepts"][0]["constituents"] = [
        _engine_constituent("op:AssociatedPriorDisease", "C3270")
    ]
    _sign(engine)
    artifact_path = tmp_path / "artifact.json"
    _write_json(artifact_path, _bind_artifact_to_engine(concepts, engine))

    report = evaluate_adjudication(
        load_adjudication(artifact_path), engine, _corpus_evidence()
    )

    assert report["concepts"][0]["pair_score"]["ncit_bound"]["true_positive"] == 1


@pytest.mark.unit
def test_reviewer_resolution_scores_an_engine_flagged_pair(tmp_path: Path) -> None:
    concepts = _m1_concepts()
    engine = _engine_evidence()
    engine["concepts"][0]["constituents"][0]["needs_review"] = True
    _sign(engine)
    artifact_path = tmp_path / "artifact.json"
    _write_json(artifact_path, _bind_artifact_to_engine(concepts, engine))

    report = evaluate_adjudication(
        load_adjudication(artifact_path), engine, _corpus_evidence()
    )

    first = report["concepts"][0]
    assert first["engine_review_flags"] == [["op:StageValue", "C27970"]]
    assert first["pair_score"]["ncit_bound"]["true_positive"] == 1
    assert report["expected_pair_deferrals"]["ncit_bound"] == {
        "deferred": 0,
        "engine_matches": 0,
        "expected": 20,
    }


@pytest.mark.unit
def test_engine_flagged_sme_rejected_pair_is_a_false_positive(tmp_path: Path) -> None:
    concepts = _m1_concepts()
    engine = _engine_evidence()
    engine["concepts"][0]["constituents"][0]["filler"] = "C27971"
    engine["concepts"][0]["constituents"][0]["needs_review"] = True
    _sign(engine)
    artifact_path = tmp_path / "artifact.json"
    _write_json(artifact_path, _bind_artifact_to_engine(concepts, engine))

    report = evaluate_adjudication(
        load_adjudication(artifact_path), engine, _corpus_evidence()
    )

    first = report["concepts"][0]
    assert first["engine_review_flags"] == [["op:StageValue", "C27971"]]
    assert first["pair_score"]["ncit_bound"]["extra"] == [["op:StageValue", "C27971"]]
    assert first["pair_score"]["ncit_bound"]["precision"] == 0.0


@pytest.mark.unit
def test_group_comparison_ignores_group_names_but_not_membership(
    tmp_path: Path,
) -> None:
    concepts = _m1_concepts()
    concepts[0] = _accepted(
        "C0",
        constituents=[
            _constituent(group="expected-name"),
            _constituent("op:StageValue", "C27971", group="expected-name"),
        ],
    )
    engine = _engine_evidence()
    engine["concepts"][0]["constituents"] = [
        _engine_constituent(group="different-name"),
        _engine_constituent("op:StageValue", "C27971", group="different-name"),
    ]
    _sign(engine)
    corpus = _corpus_evidence()
    artifact_path = tmp_path / "artifact.json"
    _write_json(artifact_path, _bind_artifact_to_engine(concepts, engine, corpus))

    report = evaluate_adjudication(load_adjudication(artifact_path), engine, corpus)

    assert report["concepts"][0]["group_match"] is True
    assert report["group_partition_agreement"] == {
        "concepts_agree": 20,
        "concepts_disagree": 0,
    }

    engine = _engine_evidence()
    engine["concepts"][0]["constituents"] = [
        _engine_constituent(group="first"),
        _engine_constituent("op:StageValue", "C27971", group="second"),
    ]
    _sign(engine)
    artifact_path = tmp_path / "different-partition.json"
    _write_json(artifact_path, _bind_artifact_to_engine(concepts, engine, corpus))

    report = evaluate_adjudication(load_adjudication(artifact_path), engine, corpus)

    assert report["concepts"][0]["group_match"] is False
    assert report["group_partition_agreement"] == {
        "concepts_agree": 19,
        "concepts_disagree": 1,
    }


@pytest.mark.unit
def test_evaluation_rejects_identity_drift_and_invalid_residual_subset(
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / "artifact.json"
    _write_json(artifact_path, _artifact(_m1_concepts()))
    artifact = load_adjudication(artifact_path)
    corpus = _corpus_evidence(denominator=["C1"], residual=["C2"])

    with pytest.raises(GoldenSetValidationError, match="run fingerprint identity"):
        evaluate_adjudication(artifact, _engine_evidence(wrong_identity=True), corpus)
    with pytest.raises(
        GoldenSetValidationError, match="residual codes must be a subset"
    ):
        evaluate_adjudication(artifact, _engine_evidence(), corpus)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("ncit_version", "26.05d", "NCIt version"),
        ("source_identity", "f" * 64, "source identity"),
        ("sample_manifest_identity", "f" * 64, "sample manifest identity"),
        ("run_id", "neoplasm-run-9", "run id"),
        ("run_fingerprint_identity", "f" * 64, "run fingerprint identity"),
        ("engine_artifact_identity", "f" * 64, "engine artifact identity"),
        ("detector_identity", "f" * 64, "detector identity"),
    ],
)
def test_every_engine_identity_binding_is_enforced(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    """Each of the eight identity bindings must discriminate on its own.

    A binding that is pinned but never compared is the "identity declared, never
    checked" defect: the artifact would claim provenance the engine run does not
    have. `engine evidence identity` is covered separately below, because drifting
    it requires leaving the artifact bound to a different payload.
    """
    engine = _engine_evidence()
    engine[field] = value
    _sign(engine)
    artifact_path = tmp_path / "artifact.json"
    _write_json(artifact_path, _bind_artifact_to_engine(_m1_concepts(), engine))

    with pytest.raises(GoldenSetValidationError, match=message):
        evaluate_adjudication(
            load_adjudication(artifact_path), engine, _corpus_evidence()
        )


@pytest.mark.unit
def test_engine_evidence_identity_binding_is_enforced(tmp_path: Path) -> None:
    engine = _engine_evidence()
    artifact_path = tmp_path / "artifact.json"
    _write_json(
        artifact_path,
        _artifact(_m1_concepts(), engine_evidence_identity="f" * 64),
    )

    with pytest.raises(
        GoldenSetValidationError, match="engine evidence identity does not match"
    ):
        evaluate_adjudication(
            load_adjudication(artifact_path), engine, _corpus_evidence()
        )


@pytest.mark.unit
def test_engine_worklist_order_must_match_adjudication_order(tmp_path: Path) -> None:
    """Per-concept scoring pairs adjudication[i] with engine[i].

    A permuted engine worklist with the same code set would score every concept
    against another concept's expectation while validation stayed green.
    """
    engine = _engine_evidence()
    concepts = cast("list[dict[str, object]]", engine["concepts"])
    concepts[0], concepts[1] = concepts[1], concepts[0]
    _sign(engine)
    artifact_path = tmp_path / "artifact.json"
    _write_json(artifact_path, _bind_artifact_to_engine(_m1_concepts(), engine))

    with pytest.raises(GoldenSetValidationError, match="adjudication order"):
        evaluate_adjudication(
            load_adjudication(artifact_path), engine, _corpus_evidence()
        )


@pytest.mark.unit
def test_deferral_counters_report_pairs_the_engine_also_emitted(
    tmp_path: Path,
) -> None:
    """`engine_matches` is the signal that a run cannot quietly bury a deferral.

    Every other assertion on `expected_pair_deferrals` uses a fixture where both
    accumulators sit at zero, so neither ever has to accumulate anything.
    """
    concepts = _m1_concepts()
    concepts[0] = _accepted(
        "C0",
        constituents=[
            _constituent(),
            _constituent("op:AssociatedRegion", "C12418", needs_review=True),
            _constituent("op:CellType", "C36903", needs_review=True),
        ],
    )
    engine = _engine_evidence()
    # The engine emits one of the two deferred pairs and not the other.
    cast("list[dict[str, object]]", engine["concepts"][0]["constituents"]).append(
        _engine_constituent("op:AssociatedRegion", "C12418")
    )
    _sign(engine)
    artifact_path = tmp_path / "artifact.json"
    _write_json(artifact_path, _bind_artifact_to_engine(concepts, engine))

    report = evaluate_adjudication(
        load_adjudication(artifact_path), engine, _corpus_evidence()
    )

    deferrals = report["expected_pair_deferrals"]
    assert deferrals["ncit_bound"] == {
        "deferred": 2,
        "engine_matches": 1,
        "expected": 22,
    }
    assert deferrals["augmented"]["deferred"] == 2
    assert deferrals["augmented"]["engine_matches"] == 1
    assert deferrals["non_defining"] == {
        "deferred": 0,
        "engine_matches": 0,
        "expected": 0,
    }


@pytest.mark.unit
def test_evaluation_rejects_tampered_or_non_decomposed_residual_evidence(
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / "artifact.json"
    _write_json(artifact_path, _artifact(_m1_concepts()))
    artifact = load_adjudication(artifact_path)
    engine = _engine_evidence()
    engine["concepts"][0]["semantic_types"] = ["Finding"]
    with pytest.raises(GoldenSetValidationError, match="identity does not match"):
        evaluate_adjudication(artifact, engine, _corpus_evidence())

    engine = _engine_evidence()
    engine["concepts"][0]["outcome"] = "atomic-no-op"
    engine["concepts"][0]["constituents"] = []
    _sign(engine)
    with pytest.raises(
        GoldenSetValidationError, match="residual codes require decomposed outcomes"
    ):
        evaluate_adjudication(artifact, engine, _corpus_evidence())


@pytest.mark.unit
def test_residual_denominator_uses_actual_decomposed_and_types_are_sets(
    tmp_path: Path,
) -> None:
    concepts = _m1_concepts()
    concepts[0] = _accepted(
        "C0",
        semantic_types=["Neoplastic Process", "Disease or Syndrome"],
    )
    engine = _engine_evidence()
    engine["concepts"][0]["semantic_types"] = [
        "Disease or Syndrome",
        "Neoplastic Process",
    ]
    engine["concepts"][1]["outcome"] = "atomic-no-op"
    engine["concepts"][1]["constituents"] = []
    engine["residual_precoordinated_codes"].remove("C1")
    _sign(engine)
    artifact_path = tmp_path / "artifact.json"
    _write_json(artifact_path, _bind_artifact_to_engine(concepts, engine))

    report = evaluate_adjudication(
        load_adjudication(artifact_path), engine, _corpus_evidence()
    )

    assert report["concepts"][0]["semantic_types_match"] is True
    assert report["residual_comparison"]["adjudication"]["denominator"] == 19


@pytest.mark.unit
def test_zero_pair_metrics_are_undefined_and_detector_drift_is_rejected(
    tmp_path: Path,
) -> None:
    concepts = _m1_concepts()
    concepts[0] = _accepted("C0", outcome="atomic-no-op", constituents=[])
    for index in range(1, len(concepts)):
        code = concepts[index]["code"]
        concepts[index] = {
            "code": code,
            "label": f"Rejected {code}",
            "adjudication": {
                "status": "rejected",
                "rationale": "Unsuitable for this oracle.",
            },
            "expected": None,
        }
    engine = _engine_evidence()
    engine["concepts"][0]["outcome"] = "atomic-no-op"
    engine["concepts"][0]["constituents"] = []
    engine["residual_precoordinated_codes"].remove("C0")
    _sign(engine)
    artifact_path = tmp_path / "artifact.json"
    _write_json(artifact_path, _bind_artifact_to_engine(concepts, engine))

    report = evaluate_adjudication(
        load_adjudication(artifact_path), engine, _corpus_evidence()
    )

    assert report["pair_micro"]["ncit_bound"]["precision"] is None
    assert report["pair_micro"]["ncit_bound"]["recall"] is None
    assert report["pair_micro"]["augmented"]["precision"] is None
    assert report["pair_micro"]["augmented"]["recall"] is None
    drifted_corpus = _corpus_evidence()
    drifted_corpus["detector_identity"] = "5" * 64
    _sign(drifted_corpus)
    with pytest.raises(GoldenSetValidationError, match="detector identity"):
        evaluate_adjudication(load_adjudication(artifact_path), engine, drifted_corpus)


@pytest.mark.unit
def test_evaluation_report_is_byte_reproducible(tmp_path: Path) -> None:
    corpus = _corpus_evidence(residual=["C1"])
    artifact_path = tmp_path / "artifact.json"
    _write_json(
        artifact_path,
        _artifact(
            _m1_concepts(),
            corpus_evidence_identity=cast("str", corpus["evidence_identity"]),
        ),
    )
    artifact = load_adjudication(artifact_path)
    report = evaluate_adjudication(artifact, _engine_evidence(), corpus)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    write_evaluation_report(report, first)
    write_evaluation_report(report, second)

    assert first.read_bytes() == second.read_bytes()


@pytest.mark.unit
def test_evidence_json_reader_rejects_duplicate_provenance_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"run_id":"first","run_id":"second"}', encoding="utf-8")

    with pytest.raises(GoldenSetValidationError, match="duplicate JSON key: run_id"):
        read_json_without_duplicates(path)


@pytest.mark.unit
def test_adjudication_cli_imports_workbook_and_evaluates_report(
    tmp_path: Path,
) -> None:
    workbook = tmp_path / "review.xlsx"
    artifact_path = tmp_path / "artifact.json"
    engine_path = tmp_path / "engine.json"
    corpus_path = tmp_path / "corpus.json"
    registry_path = tmp_path / "registry.json"
    report_path = tmp_path / "report.json"
    engine = _engine_evidence()
    corpus = _corpus_evidence(residual=["C1"])
    _create_workbook(workbook)
    review = load_workbook(workbook)
    evidence = review["Source & Run Evidence"]
    for row in range(5, evidence.max_row + 1):
        if evidence.cell(row, 1).value == "Corpus evidence identity":
            evidence.cell(row, 2, corpus["evidence_identity"])
    review.save(workbook)
    _write_json(engine_path, engine)
    registry_path.write_text(
        _EMPTY_PROPOSAL_REGISTRY.model_dump_json(),
        encoding="utf-8",
    )
    _write_json(
        corpus_path,
        corpus,
    )

    adjudication_main(
        [
            "import-workbook",
            str(workbook),
            str(registry_path),
            str(artifact_path),
        ]
    )
    adjudication_main(
        [
            "evaluate",
            str(artifact_path),
            str(engine_path),
            str(corpus_path),
            str(registry_path),
            str(report_path),
        ]
    )

    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert artifact["_meta"]["reviewer"]["name"] == "Example Reviewer"
    assert report["accepted_concepts"] == 20


_EXTRA_DECISION_ROWS = (
    # code, row type, action, expected axis, expected filler, complete
    ("C0", "ADD IF MISSING", "include", "op:PrimarySite", "C12345", "YES"),
    ("C1", "ADD IF MISSING", "exclude", None, None, "YES"),
    ("C2", "ADD IF MISSING", "not-needed", None, None, "NO"),
    ("C3", "ENGINE SUGGESTION", "revise", "op:StageValue", "C27971", "YES"),
    # An excluded row that still carries a stale expectation, as three rows of the
    # attested #57 workbook do. Its pair differs from C4's kept pair, so a reader
    # that filtered on "has an expected pair" instead of the SME action would
    # produce a triple the oracle does not contain.
    ("C4", "ENGINE SUGGESTION", "exclude", "op:StageValue", "C27972", "YES"),
)


def _append_decision_rows(path: Path) -> None:
    """Append reviewer decision rows covering every SME action and row type."""
    workbook = load_workbook(path)
    sheet = workbook["Constituent Decisions"]
    for offset, (code, row_type, action, axis, filler, complete) in enumerate(
        _EXTRA_DECISION_ROWS
    ):
        row = sheet.max_row + 1 + offset
        sheet.cell(row, 2, code)
        sheet.cell(row, 4, row_type)
        sheet.cell(row, 10, action)
        sheet.cell(row, 11, axis)
        sheet.cell(row, 12, filler)
        sheet.cell(row, 14, "FALSE")
        sheet.cell(row, 15, "ncit-26.07d")
        sheet.cell(row, 18, complete)
    workbook.save(path)


@pytest.mark.unit
def test_row_decision_export_keeps_every_reviewer_decision(tmp_path: Path) -> None:
    """Rejected and not-needed rows survive the export; the importer discards them."""
    workbook = tmp_path / "review.xlsx"
    _create_workbook(workbook)
    _append_decision_rows(workbook)

    export = export_row_decisions(workbook)

    assert len(export.rows) == 25
    assert export.meta.ncit_version == "26.07d"
    assert export.meta.source_workbook == "review.xlsx"
    assert export.meta.reviewer.name == "Example Reviewer"
    assert (
        export.meta.workbook_identity
        == hashlib.sha256(workbook.read_bytes()).hexdigest()
    )
    assert export.cross_tab() == {
        "ENGINE SUGGESTION": {
            "include": 20,
            "revise": 1,
            "exclude": 1,
            "not-needed": 0,
        },
        "ADD IF MISSING": {
            "include": 1,
            "revise": 0,
            "exclude": 1,
            "not-needed": 1,
        },
    }


@pytest.mark.unit
def test_row_decision_export_reproduces_the_imported_expected_set(
    tmp_path: Path,
) -> None:
    """`include` + `revise` rows are exactly the constituents the importer keeps.

    This is the correspondence the tracked artifacts rely on. Keying on the SME
    action matters: the excluded C4 row carries a stale expectation the importer
    drops, so a pair-presence filter would over-collect.
    """
    workbook = tmp_path / "review.xlsx"
    _create_workbook(workbook)
    _append_decision_rows(workbook)

    export = export_row_decisions(workbook)
    artifact = import_adjudication_workbook(workbook)

    assert export.expected_pairs() == {
        (concept.code, item.axis, item.filler)
        for concept in artifact.concepts
        if concept.expected is not None
        for item in concept.expected.constituents
    }
    assert ("C4", "op:StageValue", "C27972") not in export.expected_pairs()


@pytest.mark.unit
def test_row_decision_export_fails_closed_on_a_tampered_workbook(
    tmp_path: Path,
) -> None:
    """The export runs the workbook-level tamper gates the oracle import runs.

    Sheet contract, hidden rows and columns, formula cells, attestation, required
    evidence keys and the constituent row identity are shared. The gates the
    export does *not* run are pinned by
    `test_row_decision_export_accepts_kept_constituent_defects_the_import_rejects`.
    """
    workbook_path = tmp_path / "hidden.xlsx"
    _create_workbook(workbook_path)
    workbook = load_workbook(workbook_path)
    workbook["Constituent Decisions"].row_dimensions[5].hidden = True
    workbook.save(workbook_path)

    with pytest.raises(GoldenSetValidationError, match="hidden constituent rows"):
        export_row_decisions(workbook_path)


@pytest.mark.unit
def test_row_decision_export_accepts_kept_constituent_defects_the_import_rejects(
    tmp_path: Path,
) -> None:
    """The export stops at the row identity; it never builds the expectation.

    `_kept_constituent` runs only on the import path, so the `Expected Provenance
    Status` and `Expected needs_review` gates are the import's alone. A workbook
    corrupt in either column is a valid row-decision export and an invalid oracle.
    The export is therefore not a substitute for `import-workbook`, and this pins
    that boundary so the claim cannot silently widen.
    """
    for column, value, message in (
        (15, "not-a-status", "provenance_status"),
        (14, "MAYBE", "expected needs_review must be TRUE or FALSE"),
    ):
        workbook_path = tmp_path / f"kept-gate-{column}.xlsx"
        _create_workbook(workbook_path)
        workbook = load_workbook(workbook_path)
        workbook["Constituent Decisions"].cell(5, column, value)
        workbook.save(workbook_path)

        export = export_row_decisions(workbook_path)

        assert export.rows[0].code == "C0"
        assert export.rows[0].sme_action == "include"
        assert export.rows[0].expected_axis == "op:StageValue"
        assert export.rows[0].expected_filler == "C27970"
        with pytest.raises(GoldenSetValidationError, match=message):
            import_adjudication_workbook(workbook_path)


@pytest.mark.unit
def test_row_decision_reader_rejects_a_padded_axis_on_an_excluded_row(
    tmp_path: Path,
) -> None:
    """Non-kept rows are canonically validated too — a deliberate tightening.

    The pre-export reader skipped `exclude` and `not-needed` rows before reading
    `Expected Axis`/`Expected Filler`, so a padded value there was accepted. The
    export writes those cells out verbatim, so it must read them, and it holds them
    to the same canonical form as a kept row's on both paths.
    """
    workbook_path = tmp_path / "padded-excluded-axis.xlsx"
    _create_workbook(workbook_path)
    workbook = load_workbook(workbook_path)
    sheet = workbook["Constituent Decisions"]
    sheet["J5"] = "exclude"
    sheet["K5"] = " op:StageValue"
    workbook.save(workbook_path)

    message = "C0 expected axis must be non-empty without outer whitespace"
    with pytest.raises(GoldenSetValidationError, match=message):
        export_row_decisions(workbook_path)
    with pytest.raises(GoldenSetValidationError, match=message):
        import_adjudication_workbook(workbook_path)


@pytest.mark.unit
def test_row_decision_export_rejects_an_unrecognized_sme_action(
    tmp_path: Path,
) -> None:
    """An action outside the reviewed vocabulary cannot be silently recorded."""
    workbook_path = tmp_path / "invalid-action.xlsx"
    _create_workbook(workbook_path)
    workbook = load_workbook(workbook_path)
    workbook["Constituent Decisions"]["J5"] = "maybe"
    workbook.save(workbook_path)

    with pytest.raises(GoldenSetValidationError, match="invalid SME action: maybe"):
        export_row_decisions(workbook_path)


@pytest.mark.unit
def test_row_decision_loader_rejects_a_kept_row_without_an_expected_pair(
    tmp_path: Path,
) -> None:
    """A hand-edited export that drops a kept row's pair cannot be loaded."""
    workbook = tmp_path / "review.xlsx"
    export_path = tmp_path / "rows.json"
    _create_workbook(workbook)
    adjudication_main(["export-row-decisions", str(workbook), str(export_path)])
    payload = json.loads(export_path.read_text(encoding="utf-8"))
    assert load_row_decisions(export_path).rows[0].sme_action == "include"
    payload["rows"][0]["expected_filler"] = None
    _write_json(export_path, payload)

    with pytest.raises(GoldenSetValidationError, match="expected axis and filler"):
        load_row_decisions(export_path)


@pytest.mark.unit
def test_row_decision_loader_rejects_duplicate_kept_pairs(tmp_path: Path) -> None:
    """Two kept rows cannot claim the same concept, axis and filler."""
    workbook = tmp_path / "review.xlsx"
    export_path = tmp_path / "rows.json"
    _create_workbook(workbook)
    adjudication_main(["export-row-decisions", str(workbook), str(export_path)])
    payload = json.loads(export_path.read_text(encoding="utf-8"))
    payload["rows"][1]["code"] = payload["rows"][0]["code"]
    _write_json(export_path, payload)

    with pytest.raises(GoldenSetValidationError, match="kept rows must be unique"):
        load_row_decisions(export_path)
