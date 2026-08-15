from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from openpyxl import load_workbook

from ontolib.repositories.icdo.ingest import (
    ICDO4_ARCHIVE_SHA256,
    ICDO4_MORPHOLOGY_ANNEX_SHA256,
    ICDO4_SOURCE_SHA256,
    ICDO4_TOPOGRAPHY_ANNEX_SHA256,
    ICDO32_SHA256,
    SourceFormatError,
    canonical_bytes,
    ingest_icdo4,
    ingest_icdo32_morphology,
)

pytestmark = [pytest.mark.integration, pytest.mark.full_store]
ROOT = Path(__file__).parents[4]
SOURCE = ROOT / "tmp/data/icdo"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_operator_sources_have_certified_identities() -> None:
    assert _sha256(SOURCE / "ICD-O-3.2_final_update09102020.xls") == ICDO32_SHA256
    assert _sha256(SOURCE / "ICD-O-4.zip") == ICDO4_ARCHIVE_SHA256
    assert _sha256(SOURCE / "ICD-O-4.xlsx") == ICDO4_SOURCE_SHA256
    assert _sha256(SOURCE / "Morphology_annexes.xlsx") == ICDO4_MORPHOLOGY_ANNEX_SHA256
    assert _sha256(SOURCE / "Topography_annexes.xlsx") == ICDO4_TOPOGRAPHY_ANNEX_SHA256


def test_real_sources_are_deterministic_complete_and_preserve_optional_preferred() -> (
    None
):
    old = ingest_icdo32_morphology(SOURCE / "ICD-O-3.2_final_update09102020.xls")
    first = ingest_icdo4(
        SOURCE / "ICD-O-4.zip",
        morphology_annex_path=SOURCE / "Morphology_annexes.xlsx",
        topography_annex_path=SOURCE / "Topography_annexes.xlsx",
    )
    second = ingest_icdo4(
        SOURCE / "ICD-O-4.zip",
        morphology_annex_path=SOURCE / "Morphology_annexes.xlsx",
        topography_annex_path=SOURCE / "Topography_annexes.xlsx",
    )

    assert len(old.records) == 1_143
    assert old.term_counts == {
        "preferred": 1_143,
        "synonym": 1_194,
        "related": 558,
    }
    assert len(first.morphology.records) == 2_390
    assert first.morphology.term_counts == {
        "preferred": 2_389,
        "synonym": 2_109,
        "related": 222,
    }
    assert len(first.topography.records) == 406
    assert first.topography.level_counts == {"category": 69, "leaf": 337}
    assert first.topography.term_counts == {
        "preferred": 337,
        "category": 69,
        "synonym": 153,
        "related": 629,
    }
    exceptional = next(row for row in first.morphology.records if row.code == "85032/0")
    assert exceptional.preferred is None
    assert exceptional.synonyms or exceptional.related
    category = next(row for row in first.topography.records if row.code == "C08")
    assert category.notes == (
        "Neoplasms of minor salivary glands should be classified according to their "
        "anatomical site; if location is not specified, classify to C06.3",
    )
    leaf = next(row for row in first.topography.records if row.code == "C10.4")
    assert leaf.other_text == ("site of neoplasm",)
    assert canonical_bytes(first.morphology) == canonical_bytes(second.morphology)
    assert canonical_bytes(first.topography) == canonical_bytes(second.topography)


def test_real_external_readers_report_source_shapes() -> None:
    old = ingest_icdo32_morphology(SOURCE / "ICD-O-3.2_final_update09102020.xls")
    new = ingest_icdo4(
        SOURCE / "ICD-O-4.zip",
        morphology_annex_path=SOURCE / "Morphology_annexes.xlsx",
        topography_annex_path=SOURCE / "Topography_annexes.xlsx",
    )

    assert old.source_shape.sheet_names == ("ICD-O-3.2 Morphology",)
    assert old.source_shape.headers == (
        "ICDO3.2",
        "Level",
        "Term",
        "Code reference",
        "obs",
        "See also",
        "See note",
        "Includes",
        "Excludes",
        "Other text",
    )
    assert old.source_shape.merged_ranges == ("A1:H1",)
    assert old.source_shape.trailing_blank_rows == 0
    assert new.morphology.source_shape.sheet_names == ("Morphology", "Topography")
    assert new.morphology.source_shape.merged_ranges == ("A1:H1",)
    assert new.topography.source_shape.merged_ranges == ("A1:I1",)


def test_malformed_required_source_cell_fails_closed(tmp_path: Path) -> None:
    source = SOURCE / "ICD-O-4.xlsx"
    workbook = load_workbook(source)
    workbook["Morphology"]["C5"] = None
    malformed = tmp_path / "malformed.xlsx"
    workbook.save(malformed)

    with pytest.raises(SourceFormatError, match="required term"):
        ingest_icdo4(malformed, verify_identity=False)
