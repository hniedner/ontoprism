"""Read-only P334 import prediction against the configured reference cohort."""

import pytest
from scripts.import_morphology_evidence import run

pytestmark = [pytest.mark.integration, pytest.mark.full_store]


async def test_reference_morphology_prediction_preserves_nonmatching_rows():
    report = await run("neoplasm-dc534534-08e0-4c24-86bc-f5a9ae3841e0")
    assert report["dry_run"] is True
    assert report["assertions"] == 40818
    assert report["unique_nonmatching"] == {
        "incomparable": 11550,
        "filler-ancestor-of-anchor": 6800,
    }
    assert report["by_status"] == {
        "evidence-supported": 17248,
        "provisional": 18363,
        "needs-SME": 5207,
    }
    assert report["by_reason"] == {"ambiguous": 3672, "no-evidence": 1535}
