from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import TYPE_CHECKING, cast

import pytest
from openpyxl import Workbook, load_workbook
from scripts.research.stage_bundle_pilot import (
    EVIDENCE_REGISTRY,
    STAGE_BUNDLE_CANDIDATES,
    ConstituentCorrection,
    _engine_pairs,
    _payload_identity,
    apply_constituent_corrections,
    build_provenance_ledger,
    build_stage_bundle_report,
    build_verification_manifest,
    import_review_decisions,
    validate_source_audit,
    write_review_workbook,
)

from ontolib.decomposition.semantic_bundles import (
    BundleAxis,
    MemberRole,
    ProjectedConstituentEvidence,
    canonical_restriction_fact_id,
    validate_candidate_evidence,
)

if TYPE_CHECKING:
    from pathlib import Path

    from openpyxl.worksheet.worksheet import Worksheet

_SOURCE_IDENTITY = "f54dd2910a31245a30cea094dc72ce6a5c8d7b5a9c4e484007a35a1c343624c8"


def _candidate_artifact() -> dict[str, object]:
    report = build_stage_bundle_report({})
    report["artifact_identity"] = _payload_identity(report)
    return report


def _engine_evidence_for_candidates() -> dict[
    str, tuple[ProjectedConstituentEvidence, ...]
]:
    by_code: defaultdict[
        str, dict[tuple[BundleAxis, str], ProjectedConstituentEvidence]
    ] = defaultdict(dict)
    for candidate in STAGE_BUNDLE_CANDIDATES:
        for member in candidate.members:
            by_code[candidate.subject_code][member.pair] = ProjectedConstituentEvidence(
                axis=member.axis,
                filler_code=member.filler_code,
                needs_review=False,
                relationship_group="stage",
                source_role="R88" if member.source_occurrences else None,
                axis_source="role" if member.source_occurrences else None,
                source_fact_ids=tuple(
                    occurrence.fact_id for occurrence in member.source_occurrences
                ),
            )
    return {code: tuple(items.values()) for code, items in by_code.items()}


@pytest.mark.unit
def test_registry_contains_typed_review_candidates_not_accepted_rules() -> None:
    counts = Counter(candidate.subject_code for candidate in STAGE_BUNDLE_CANDIDATES)

    assert len(STAGE_BUNDLE_CANDIDATES) == 15
    assert counts["C115057"] == 2
    assert counts["C27787"] == 2
    assert counts["C35756"] == 2
    for candidate in STAGE_BUNDLE_CANDIDATES:
        validate_candidate_evidence(candidate, EVIDENCE_REGISTRY)
        assert len(candidate.members) == 2


@pytest.mark.unit
def test_valg_method_claim_does_not_fabricate_an_ncit_occurrence() -> None:
    valg = next(
        candidate
        for candidate in STAGE_BUNDLE_CANDIDATES
        if candidate.candidate_id == "stage-c35756-valg-extensive"
    )
    method = next(
        member for member in valg.members if member.role is MemberRole.STAGING_METHOD
    )

    assert method.filler_code == "C141685"
    assert method.source_occurrences == ()
    assert method.evidence_claim_ids == ("mcode-4.0.0-valg-method",)


@pytest.mark.unit
def test_source_occurrences_are_canonical_and_must_exist_in_audit() -> None:
    occurrences = {
        occurrence
        for candidate in STAGE_BUNDLE_CANDIDATES
        for member in candidate.members
        for occurrence in member.source_occurrences
    }
    facts = [
        {
            "root_code": item.root_code,
            "role_code": item.source_role,
            "filler_code": item.filler_code,
            "anchor_code": item.anchor_code,
            "depth": item.depth,
            "group_id": item.source_group_id,
        }
        for item in occurrences
    ]
    facts.append(
        {
            "root_code": "C198031",
            "role_code": "R88",
            "filler_code": "C198023",
            "anchor_code": "C198031",
            "depth": 0,
            "group_id": (
                "0d414f8ad31ecc05baa4617d99f8aa622c9c1a684f55f49120ffe79e78b594cf"
            ),
        }
    )
    audit = {"ncit_release": "26.07d", "facts": facts}

    validate_source_audit(audit)
    occurrence = next(iter(occurrences))
    assert occurrence.fact_id == canonical_restriction_fact_id(
        occurrence.anchor_code,
        occurrence.source_group_id,
        occurrence.source_role,
        occurrence.filler_code,
    )
    facts.pop()
    with pytest.raises(ValueError, match="missing semantic-bundle source fact"):
        validate_source_audit(audit)


@pytest.mark.unit
def test_report_separates_available_deferred_missing_and_observed_scores() -> None:
    evidence = _engine_evidence_for_candidates()
    incomplete_code = "C132677"
    deferred_code = "C181564"
    evidence[incomplete_code] = tuple(
        item for item in evidence[incomplete_code] if item.filler_code != "C132248"
    )
    evidence[deferred_code] = tuple(
        ProjectedConstituentEvidence(
            axis=item.axis,
            filler_code=item.filler_code,
            needs_review=item.filler_code == "C27966",
            relationship_group=item.relationship_group,
            source_role=item.source_role,
            axis_source=item.axis_source,
            source_fact_ids=item.source_fact_ids,
        )
        for item in evidence[deferred_code]
    )

    report = build_stage_bundle_report(evidence)
    availability = cast("dict[str, object]", report["engine_pair_availability"])

    assert report["status"] == "FINAL-REVIEW-PENDING"
    assert availability["candidate_counts"] == {
        "expected": 15,
        "available": 13,
        "deferred": 1,
        "incomplete": 1,
    }
    semantic_scores = cast("dict[str, dict[str, str]]", availability["semantic_scores"])
    assert semantic_scores["exact_bundle"]["status"] == "not-evaluable"
    assert semantic_scores["association"]["status"] == "not-evaluable"


@pytest.mark.unit
def test_engine_parser_preserves_review_and_source_metadata() -> None:
    raw = {
        "schema_version": 1,
        "ncit_version": "26.07d",
        "concepts": [
            {
                "code": "C1",
                "outcome": "decomposed",
                "constituents": [
                    {
                        "axis": "op:StageValue",
                        "filler": "C2",
                        "needs_review": True,
                        "relationship_group": "stage",
                        "source_role": "R88",
                        "axis_source": "role",
                        "source_fact_ids": ["a" * 64],
                    },
                    {
                        "axis": "op:Morphology",
                        "filler": "C3",
                        "needs_review": False,
                        "relationship_group": None,
                    },
                ],
            }
        ],
    }

    parsed = _engine_pairs(raw)["C1"]

    assert len(parsed) == 1
    assert parsed[0].needs_review is True
    assert parsed[0].relationship_group == "stage"
    assert parsed[0].source_role == "R88"
    assert parsed[0].axis_source == "role"
    assert parsed[0].source_fact_ids == ("a" * 64,)


@pytest.mark.unit
def test_provenance_ledger_dispositions_exactly_304_occurrences() -> None:
    facts = [
        {
            "root_code": f"C{index + 1000}",
            "role_code": "R101",
            "filler_code": f"C{index + 2000}",
            "anchor_code": f"C{index + 3000}",
            "depth": 0,
            "group_id": f"group-{index}",
        }
        for index in range(303)
    ]
    contracted_fact = {
        "root_code": "C9000",
        "role_code": "R105",
        "filler_code": "C9001",
        "anchor_code": "C9002",
        "depth": 1,
        "group_id": "group-contracted",
    }
    facts.append(contracted_fact)
    audit = {"fact_count": 304, "facts": facts}
    disposition = {
        "source_identity": _SOURCE_IDENTITY,
        "ontology_version": "26.07d",
        "rows": [
            {
                "root_code": "C9000",
                "role_code": "R105",
                "filler_code": "C9001",
                "disposition": "expected-current",
                "anchors": [["C9002", 1]],
            }
        ],
    }
    engine = {
        "concepts": [
            {"code": fact["root_code"], "outcome": "decomposed"} for fact in facts
        ]
    }

    ledger = build_provenance_ledger(audit, disposition, engine)

    assert ledger["occurrence_count"] == 304
    assert ledger["counts"] == {
        "constituent-workbook-review": 303,
        "contracted-role-disposition": 1,
    }
    rows = cast("list[dict[str, object]]", ledger["occurrences"])
    assert all(len(cast("str", row["fact_id"])) == 64 for row in rows)
    with pytest.raises(ValueError, match="fact count does not match"):
        build_provenance_ledger(audit | {"facts": facts[:-1]}, disposition, engine)


def _blank_workbook(path: Path) -> None:
    workbook = Workbook()
    cast("Worksheet", workbook.active).title = "START HERE"
    workbook.create_sheet("Reviewer & Attestation")
    workbook.create_sheet("Validation Summary")
    workbook.save(path)


@pytest.mark.unit
def test_constituent_corrections_remove_and_add_exact_pairs() -> None:
    workbook = Workbook()
    sheet = workbook.create_sheet("Constituent Decisions")
    headers = (
        "Concept Order",
        "Concept Code",
        "Source Label",
        "Row Type",
        "SME Action",
        "Expected Axis",
        "Expected Filler",
        "Expected Group",
        "Expected needs_review",
        "Expected Provenance Status",
        "Expected Role Modality",
        "SME Notes",
        "Row Complete?",
    )
    for column, header in enumerate(headers, start=1):
        sheet.cell(4, column, header)
    values = (
        1,
        "C1",
        "Source concept",
        "ADD IF MISSING",
        "include",
        "op:AssociatedRegion",
        "C2",
        None,
        "FALSE",
        "ncit-26.07d",
        "asserted",
        "Original decision.",
        "YES",
    )
    for column, value in enumerate(values, start=1):
        sheet.cell(5, column, value)

    apply_constituent_corrections(
        workbook,
        (
            ConstituentCorrection(
                action="remove",
                concept_code="C1",
                axis="op:AssociatedRegion",
                filler_code="C2",
                rationale="The source-backed correction removes this broader pair.",
            ),
            ConstituentCorrection(
                action="add",
                concept_code="C1",
                axis="op:AssociatedRegion",
                filler_code="C3",
                rationale="The complete source audit supports this missing pair.",
            ),
        ),
    )

    assert sheet["E5"].value == "exclude"
    assert sheet["E6"].value == "include"
    assert sheet["F6"].value == "op:AssociatedRegion"
    assert sheet["G6"].value == "C3"
    assert sheet["M6"].value == "YES"


@pytest.mark.unit
def test_canonical_rules_can_only_be_imported_from_attested_decisions(
    tmp_path: Path,
) -> None:
    base = tmp_path / "base.xlsx"
    review = tmp_path / "review.xlsx"
    _blank_workbook(base)
    artifact = _candidate_artifact()
    write_review_workbook(base, artifact, review)

    with pytest.raises(ValueError, match="not ATTESTED"):
        import_review_decisions(review, artifact)

    workbook = load_workbook(review)
    sheet = workbook["Semantic Bundle Decisions"]
    sheet["B5"] = "ATTESTED"
    for row in range(9, sheet.max_row + 1):
        sheet.cell(row, 8, "ACCEPT" if row == 9 else "REJECT")
        sheet.cell(row, 9, "Reviewer assessed the exact proposed association.")
        sheet.cell(row, 10, "Domain Reviewer")
        sheet.cell(row, 11, "2026-08-04")
    workbook.save(review)

    canonical = import_review_decisions(review, artifact)

    assert canonical["status"] == "ATTESTED"
    rules = cast("list[dict[str, object]]", canonical["semantic_bundle_rules"])
    assert [item["candidate_id"] for item in rules] == [
        STAGE_BUNDLE_CANDIDATES[0].candidate_id
    ]
    workbook = load_workbook(review)
    workbook["Semantic Bundle Decisions"]["B9"] = "0" * 64
    workbook.save(review)
    with pytest.raises(ValueError, match="candidate fields changed"):
        import_review_decisions(review, artifact)


@pytest.mark.unit
def test_external_manifest_hash_binds_pending_candidate_and_workbook(
    tmp_path: Path,
) -> None:
    base = tmp_path / "base.xlsx"
    review = tmp_path / "review.xlsx"
    candidate_path = tmp_path / "candidate.json"
    _blank_workbook(base)
    artifact = _candidate_artifact()
    candidate_path.write_text(
        json.dumps(artifact, sort_keys=True),
        encoding="utf-8",
    )
    write_review_workbook(base, artifact, review)

    manifest = build_verification_manifest(candidate_path, review)

    assert manifest["status"] == "FINAL-REVIEW-PENDING"
    files = cast("dict[str, dict[str, str]]", manifest["files"])
    assert len(files["candidate_artifact"]["sha256"]) == 64
    assert len(files["review_workbook"]["sha256"]) == 64
