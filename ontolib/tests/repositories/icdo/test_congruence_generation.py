import pytest

from ontolib.repositories.icdo.congruence import build_congruence_report
from ontolib.repositories.icdo.models import CanonicalDataset, IcdoRecord, SourceShape

pytestmark = pytest.mark.unit


def _topography() -> CanonicalDataset:
    return CanonicalDataset(
        edition="4.0",
        axis="topography",
        records=(
            IcdoRecord(code="C34", level="category", preferred="BRONCHUS AND LUNG"),
            IcdoRecord(
                code="C34.9", level="leaf", parent_code="C34", preferred="Lung, NOS"
            ),
            IcdoRecord(
                code="C80.9",
                level="leaf",
                parent_code="C80",
                preferred="Primary site unspecified",
            ),
            IcdoRecord(
                code="C00.0",
                level="leaf",
                parent_code="C00",
                preferred="Duplicated label",
            ),
            IcdoRecord(code="C99", level="category", preferred=None),
        ),
        source_shape=SourceShape(
            sheet_names=("Topography",),
            headers=("ICDO4",),
            merged_ranges=(),
            trailing_blank_rows=0,
        ),
        source_sha256="a" * 64,
    )


def test_generation_classifies_all_variants_without_mapping_predicates() -> None:
    report = build_congruence_report(
        _topography(),
        icdo_serving_identity="c" * 64,
        uberon_serving_identity="b" * 64,
        uberon_records=(
            {"code": "UBERON:0002048", "label": "lung", "synonyms": "pulmo"},
            {"code": "UBERON:1", "label": "duplicated label", "synonyms": ""},
            {"code": "UBERON:2", "label": "duplicated label", "synonyms": ""},
        ),
    )
    by_code = {row.code: row for row in report.rows}
    assert report.total == 5
    assert by_code["C34"].classification == "broader-narrower-mismatch"
    assert by_code["C34.9"].classification == "one-supported-candidate"
    assert by_code["C80.9"].classification == "intentionally-unresolved"
    assert by_code["C00.0"].classification == "multiple-candidates"
    assert by_code["C99"].classification == "source-data-anomaly"
    assert all(not hasattr(row, "predicate") for row in report.rows)
