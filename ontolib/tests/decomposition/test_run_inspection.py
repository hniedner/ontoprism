from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from ontolib.decomposition import run_inspection
from ontolib.decomposition.provenance_models import (
    RUN_STAGE_SEQUENCE,
    canonical_json_identity,
)
from ontolib.decomposition.run_inspection import RunInspection, summarize_fingerprint


@pytest.mark.unit
def test_inspection_summarizes_worklist_without_dumping_codes() -> None:
    summary = summarize_fingerprint(
        {
            "schema_version": 4,
            "algorithm_version": "decomposition-v5",
            "routing_implementation_identity": "a" * 64,
            "worklist": ["C1", "C2"],
        }
    )

    assert summary["schema_version"] == 4
    assert summary["algorithm_version"] == "decomposition-v5"
    assert summary["routing_implementation_identity"] == "a" * 64
    assert summary["worklist_count"] == 2
    assert len(cast("str", summary["worklist_identity"])) == 64
    assert "worklist" not in summary


@pytest.mark.unit
def test_inspection_rejects_fingerprint_without_an_exact_worklist() -> None:
    with pytest.raises(ValueError, match="worklist is not a list"):
        summarize_fingerprint({"worklist": ("C1",)})


def _run_row(
    run_id: str,
    *,
    work_state: str | None = "complete",
    finished_at: datetime | None = None,
) -> dict[str, object]:
    fingerprint: dict[str, object] = {
        "routing_implementation_identity": "a" * 64,
        "worklist": ["C1"],
    }
    return {
        "run_id": run_id,
        "status": "complete",
        "started_at": datetime(2026, 9, 9, tzinfo=UTC),
        "finished_at": finished_at,
        "source_identity": "b" * 64,
        "fingerprint": fingerprint,
        "fingerprint_sha256": canonical_json_identity(fingerprint),
        "publication_state": "published",
        "representation_identity": "c" * 64,
        "publication_artifact_path": "artifact.ttl",
        "error_type": None,
        "error_message": None,
        "work_state": work_state,
        "item_count": 1,
    }


@pytest.mark.unit
def test_inspection_finalization_requires_complete_work_stages_and_identity() -> None:
    item = run_inspection._run_summary(
        cast("Any", _run_row("run-1", finished_at=datetime(2026, 9, 10, tzinfo=UTC))),
        "a" * 64,
    )
    item["work_item_states"] = {"complete": 1}
    item["stages"] = [
        {"stage": stage, "state": "complete"} for stage in RUN_STAGE_SEQUENCE
    ]

    run_inspection._finalize_summary(item, "a" * 64)

    assert item["all_work_items_complete"] is True
    assert item["stage_inventory_complete"] is True
    assert item["stage_state"] == "complete"
    assert item["resume_compatible"] is True
    assert item["finished_at"] == "2026-09-10T00:00:00+00:00"

    item["work_item_states"] = {"complete": 1, "failed": 1}
    item["stages"] = []
    run_inspection._finalize_summary(item, "different")
    assert item["all_work_items_complete"] is False
    assert item["stage_inventory_complete"] is False
    assert item["resume_compatible"] is False


@pytest.mark.unit
def test_a_rehearsal_is_never_reported_resume_compatible() -> None:
    """Every resume path refuses a rehearsal, so the inspection an operator reads before
    resuming must not call one resumable."""
    row = _run_row("run-1")
    fingerprint = {**cast("dict[str, object]", row["fingerprint"])}
    fingerprint["rehearsal_nonce"] = "d" * 32
    row["fingerprint"] = fingerprint
    row["fingerprint_sha256"] = canonical_json_identity(fingerprint)
    item = run_inspection._run_summary(cast("Any", row), "a" * 64)
    item["work_item_states"] = {"failed": 1}
    item["stages"] = [
        {"stage": stage, "state": "complete"} for stage in RUN_STAGE_SEQUENCE
    ]

    inspected = run_inspection._finalize_summary(item, "a" * 64)

    assert inspected.fingerprint_content_valid is True
    assert inspected.routing_state == "match"
    assert inspected.rehearsal is True
    assert inspected.resume_compatible is False


@pytest.mark.unit
def test_inspection_aggregates_work_states_and_serializes_stage_times() -> None:
    rows = (
        _run_row("run-1", work_state=None),
        _run_row("run-2", work_state="complete"),
        {**_run_row("run-2", work_state="failed"), "item_count": 2},
    )
    summaries = run_inspection._summaries_by_run(cast("Any", rows), "a" * 64)
    run_inspection._attach_stages(
        summaries,
        cast(
            "Any",
            (
                {
                    "run_id": "run-2",
                    "stage": "preflight",
                    "started_at": datetime(2026, 9, 9, tzinfo=UTC),
                },
            ),
        ),
    )

    assert summaries["run-1"]["work_item_states"] == {}
    assert summaries["run-2"]["work_item_states"] == {"complete": 1, "failed": 2}
    assert summaries["run-2"]["stages"] == [
        {"stage": "preflight", "started_at": "2026-09-09T00:00:00+00:00"}
    ]


@pytest.mark.unit
async def test_inspection_fails_closed_when_any_requested_run_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_inspection, "_read_run_rows", AsyncMock(return_value=((), ()))
    )

    with pytest.raises(ValueError, match="decomposition runs not found: run-1"):
        await run_inspection.inspect_decomposition_runs(
            cast("Any", object()), ("run-1",)
        )


@pytest.mark.unit
def test_inspection_exposes_closed_routing_and_stage_states() -> None:
    item = run_inspection._run_summary(
        cast("Any", _run_row("run-1", finished_at=datetime(2026, 9, 10, tzinfo=UTC))),
        "different",
    )
    item["work_item_states"] = {"complete": 1}
    item["stages"] = []

    inspected = run_inspection._finalize_summary(item, "different")

    assert isinstance(inspected, RunInspection)
    assert inspected.routing_state == "differs"
    assert inspected.stage_state == "missing"
    assert inspected.resume_compatible is False

    item["persisted_routing_implementation_identity"] = None
    item["stages"] = [
        {"stage": stage, "state": "failed" if stage == "publication" else "complete"}
        for stage in RUN_STAGE_SEQUENCE
    ]
    inspected = run_inspection._finalize_summary(item, "different")

    assert inspected.routing_state == "not-recorded"
    assert inspected.stage_state == "incomplete"
    assert inspected.resume_compatible is False
