from pathlib import Path

import pytest
from openpyxl import Workbook

from ontolib.repositories.icdo.annex import reconcile_morphology_annex
from ontolib.repositories.icdo.ingest import (
    ICDO4_MORPHOLOGY_ANNEX_SHA256,
    ingest_icdo4,
    ingest_icdo32_morphology,
)

pytestmark = [pytest.mark.integration, pytest.mark.full_store]
ROOT = Path(__file__).parents[4] / "tmp/data/icdo"


def test_real_morphology_annex_reconciles_every_change_against_both_editions() -> None:
    old = ingest_icdo32_morphology(ROOT / "ICD-O-3.2_final_update09102020.xls")
    new = ingest_icdo4(
        ROOT / "ICD-O-4.zip",
        morphology_annex_path=ROOT / "Morphology_annexes.xlsx",
        topography_annex_path=ROOT / "Topography_annexes.xlsx",
    ).morphology

    report = reconcile_morphology_annex(
        ROOT / "Morphology_annexes.xlsx", old=old, new=new
    )

    assert set(report.sheet_counts) == {
        "New morphology codes (4 digits)",
        "New morphology codes (5 digits)",
        "Morphology code changes",
        "Deleted morphology codes",
        "Behaviour code changes",
        "New morphology terms",
        "Morphology term changes",
        "Deleted morphology terms",
    }
    assert report.checked_rows == sum(report.sheet_counts.values())
    assert report.annex_sha256 == ICDO4_MORPHOLOGY_ANNEX_SHA256
    assert len(report.old_serving_sha256) == len(report.new_serving_sha256) == 64
    assert report.unresolved == ()


def test_annex_reference_to_absent_code_fails_reconciliation(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "New morphology codes (4 digits)"
    sheet.append(["title"])
    sheet.append(["ICDO4", "Level", "Term"])
    sheet.append(["99999/3", "Preferred", "Absent"])
    path = tmp_path / "annex.xlsx"
    workbook.save(path)
    old = ingest_icdo32_morphology(ROOT / "ICD-O-3.2_final_update09102020.xls")
    new = ingest_icdo4(ROOT / "ICD-O-4.xlsx").morphology

    report = reconcile_morphology_annex(path, old=old, new=new)
    assert report.unresolved[0].code == "99999/3"
