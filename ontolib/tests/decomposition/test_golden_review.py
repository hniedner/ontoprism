from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import TYPE_CHECKING, cast

import pytest
from openpyxl import Workbook, load_workbook
from scripts.adjudication import main as adjudication_main
from scripts.research.golden_review import (
    GoldenSetValidationError,
    evaluate_adjudication,
    import_adjudication_workbook,
    load_adjudication,
    load_scorable_golden,
    read_json_without_duplicates,
    write_evaluation_report,
)

if TYPE_CHECKING:
    from pathlib import Path

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
) -> dict[str, object]:
    return {
        "axis": axis,
        "filler": filler,
        "relationship_group": group,
        "needs_review": needs_review,
        "provenance_status": (
            provenance_status
            or ("locally-approved" if filler.startswith("MINT-") else "ncit-26.07d")
        ),
    }


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
) -> dict[str, object]:
    payload = {
        "_meta": {
            "schema_version": 2,
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
) -> dict[str, object]:
    bound_corpus = corpus or _corpus_evidence()
    return _artifact(
        concepts,
        engine_evidence_identity=cast("str", engine["evidence_identity"]),
        corpus_evidence_identity=cast("str", bound_corpus["evidence_identity"]),
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
    concepts = _m1_concepts()
    concepts[0] = _accepted(
        "C0",
        constituents=[
            _constituent("op:CellType", "C36903"),
            _constituent(
                "op:CellType",
                "LOCAL-APPROVED-1",
                provenance_status="locally-approved",
            ),
            _constituent(
                "op:CellType",
                "C999999",
                provenance_status="proposed",
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
    _write_json(artifact_path, _bind_artifact_to_engine(concepts, engine))

    report = evaluate_adjudication(
        load_adjudication(artifact_path), engine, _corpus_evidence()
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
        "expected": 2,
        "actual": 1,
        "true_positive": 1,
        "precision": 1.0,
        "recall": 0.5,
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
    assert first["augmented"]["missing"] == [["op:CellType", "LOCAL-APPROVED-1"]]


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
    assert first["actual_review_exclusions"] == [["op:StageValue", "C27970"]]
    assert first["pair_score"]["ncit_bound"]["true_positive"] == 1
    assert report["expected_pair_deferrals"]["ncit_bound"] == {
        "deferred": 0,
        "engine_matches": 0,
        "expected": 20,
    }


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
    _write_json(
        corpus_path,
        corpus,
    )

    adjudication_main(["import-workbook", str(workbook), str(artifact_path)])
    adjudication_main(
        [
            "evaluate",
            str(artifact_path),
            str(engine_path),
            str(corpus_path),
            str(report_path),
        ]
    )

    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert artifact["_meta"]["reviewer"]["name"] == "Example Reviewer"
    assert report["accepted_concepts"] == 20
