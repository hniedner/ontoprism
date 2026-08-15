from datetime import UTC, datetime

import pytest

from ontolib.repositories.icdo.models import (
    CanonicalDataset,
    IcdoRecord,
    SourceShape,
    decode_icdo_record,
)
from ontolib.repositories.icdo.store import canonical_sha256, dataset_manifest

pytestmark = pytest.mark.unit


def _dataset() -> CanonicalDataset:
    return CanonicalDataset(
        edition="3.2",
        axis="morphology",
        records=(
            IcdoRecord(
                code="8503/0",
                level="morphology",
                base_morphology="8503",
                behaviour="0",
                preferred="Intraductal papilloma",
                synonyms=("Papilloma",),
                related=("Duct papilloma",),
            ),
        ),
        source_shape=SourceShape(
            sheet_names=("ICD-O-3.2 Morphology",),
            headers=("ICDO3.2", "Level", "Term"),
            merged_ranges=("A1:H1",),
            trailing_blank_rows=0,
        ),
        source_sha256="a" * 64,
    )


def test_manifest_identity_covers_exact_served_values_not_time() -> None:
    dataset = _dataset()
    fingerprint = canonical_sha256(dataset)
    first = dataset_manifest(
        dataset, publisher_url="https://example.test", published_at=datetime.now(UTC)
    )
    second = dataset_manifest(
        dataset, publisher_url="https://example.test", published_at=datetime.now(UTC)
    )

    assert first.serving_sha256 == fingerprint
    assert first.generation_id == second.generation_id
    assert first.row_count == 1
    assert first.term_counts == {"preferred": 1, "synonym": 1, "related": 1}


def test_manifest_changes_when_one_served_term_changes() -> None:
    original = _dataset()
    changed = original.model_copy(
        update={
            "records": (
                original.records[0].model_copy(update={"preferred": "Changed"}),
            )
        }
    )

    assert canonical_sha256(original) != canonical_sha256(changed)


def test_persisted_json_arrays_decode_without_weakening_strict_fields() -> None:
    record = decode_icdo_record(
        {
            "code": "C00.0",
            "level": "leaf",
            "parent_code": "C00",
            "synonyms": [],
            "related": ["Upper lip, NOS"],
            "notes": [],
            "code_references": [],
            "see_also": [],
            "see_notes": [],
            "includes": [],
            "excludes": [],
            "other_text": ["excludes skin of upper lip C44.0"],
        }
    )

    assert record.related == ("Upper lip, NOS",)
    assert record.other_text == ("excludes skin of upper lip C44.0",)


def test_persisted_record_decoder_rejects_non_array_collection_fields() -> None:
    with pytest.raises(ValueError, match="valid tuple"):
        decode_icdo_record(
            {
                "code": "C00.0",
                "level": "leaf",
                "parent_code": "C00",
                "related": "Upper lip, NOS",
            }
        )
