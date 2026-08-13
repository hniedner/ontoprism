from datetime import UTC, datetime

import pytest

from ontolib.repositories.icdo.models import CanonicalDataset, IcdoRecord, SourceShape
from ontolib.repositories.icdo.store import (
    CertificationExpectation,
    IcdoCertificationError,
    certify_dataset,
    dataset_manifest,
)

pytestmark = pytest.mark.unit


def _dataset() -> CanonicalDataset:
    return CanonicalDataset(
        edition="4.0",
        axis="topography",
        records=(IcdoRecord(code="C34", level="category", preferred="LUNG"),),
        source_shape=SourceShape(
            sheet_names=("Topography",),
            headers=("ICDO4",),
            merged_ranges=(),
            trailing_blank_rows=0,
        ),
        source_sha256="a" * 64,
    )


@pytest.mark.parametrize(
    "field", ["source_sha256", "edition", "axis", "row_count", "serving_sha256"]
)
def test_certification_rejects_each_forced_drift_dimension(field: str) -> None:
    dataset = _dataset()
    manifest = dataset_manifest(
        dataset, publisher_url="https://example.test", published_at=datetime.now(UTC)
    )
    expected = CertificationExpectation(
        source_sha256=manifest.source_sha256,
        edition=manifest.edition,
        axis=manifest.axis,
        row_count=manifest.row_count,
        serving_sha256=manifest.serving_sha256,
    )
    changed = expected.model_copy(
        update={
            field: "b" * 64
            if field.endswith("sha256")
            else "3.2"
            if field == "edition"
            else "morphology"
            if field == "axis"
            else 2
        }
    )
    with pytest.raises(IcdoCertificationError, match=field):
        certify_dataset(manifest, dataset, changed)


@pytest.mark.parametrize("field", ["row_count", "serving_sha256", "generation_id"])
def test_certification_rejects_corrupt_persisted_manifest(field: str) -> None:
    dataset = _dataset()
    manifest = dataset_manifest(
        dataset, publisher_url="https://example.test", published_at=datetime.now(UTC)
    )
    expected = CertificationExpectation(
        source_sha256=manifest.source_sha256,
        edition=manifest.edition,
        axis=manifest.axis,
        row_count=manifest.row_count,
        serving_sha256=manifest.serving_sha256,
    )
    corrupt = manifest.model_copy(
        update={field: 2 if field == "row_count" else "b" * 64}
    )

    with pytest.raises(IcdoCertificationError, match=field):
        certify_dataset(corrupt, dataset, expected)
