"""Behavioral guards for embedding publication source preflight/stability."""

import pytest
from scripts.data_build import (
    _require_ncit_source,
    _require_stable_cadsr_source,
    _require_stable_ncit_source,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("version", "count", "message"),
    [
        (None, 204_373, "no owl:versionInfo"),
        ("wrong", 204_373, "version does not match"),
        ("26.02d", 4_752, "count does not match"),
    ],
)
def test_ncit_source_preflight_rejects_unpublishable_release(
    version: str | None, count: int, message: str
) -> None:
    with pytest.raises(RuntimeError, match=message):
        _require_ncit_source(
            version,
            count,
            expected_version="26.02d",
            expected_count=204_373,
        )


@pytest.mark.unit
def test_ncit_source_stability_detects_same_count_content_drift() -> None:
    with pytest.raises(RuntimeError, match="source changed"):
        _require_stable_ncit_source(
            ("26.02d", 204_373, "before"),
            ("26.02d", 204_373, "after"),
        )


@pytest.mark.unit
def test_cadsr_source_stability_detects_file_drift() -> None:
    with pytest.raises(RuntimeError, match="source changed"):
        _require_stable_cadsr_source(("before", 79_827), ("after", 79_827))
