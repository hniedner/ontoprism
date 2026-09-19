"""Bounded read-only inspection of persisted decomposition run state."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy import bindparam, text
from sqlalchemy.engine import RowMapping

from ontolib.decomposition.provenance_models import (
    RUN_STAGE_SEQUENCE,
    canonical_json_identity,
)
from ontolib.decomposition.semantic_identity import routing_implementation_identity

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncEngine

_RUN_SQL = text(
    "SELECT r.id AS run_id,r.status,r.started_at,r.finished_at,r.source_identity,"
    "r.fingerprint,r.fingerprint_sha256,r.publication_state,"
    "r.representation_identity,r.publication_artifact_path,r.error_type,"
    "r.error_message,w.state AS work_state,w.item_count FROM decomp_run r LEFT JOIN "
    "(SELECT run_id,state,count(*) AS item_count FROM decomp_work_item "
    "GROUP BY run_id,state) w ON w.run_id=r.id WHERE r.id IN :run_ids "
    "ORDER BY r.started_at DESC,w.state"
).bindparams(bindparam("run_ids", expanding=True))
_STAGE_SQL = text(
    "SELECT run_id,stage,ordinal,state,attempt_count,input_identity,output_identity,"
    "output_payload,started_at,finished_at,failed_at,error_type,error_message "
    "FROM decomp_run_stage WHERE run_id IN :run_ids ORDER BY run_id,ordinal"
).bindparams(bindparam("run_ids", expanding=True))


class RunStageInspection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    stage: str
    ordinal: int | None = None
    state: str | None = None
    attempt_count: int | None = None
    input_identity: str | None = None
    output_identity: str | None = None
    output_payload: dict[str, object] | None = None
    started_at: str | None = None
    finished_at: str | None = None
    failed_at: str | None = None
    error_type: str | None = None
    error_message: str | None = None


class RunInspection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    run_id: str
    status: str
    started_at: str
    finished_at: str | None
    source_identity: str
    fingerprint: dict[str, object]
    fingerprint_sha256: str
    fingerprint_content_valid: bool
    rehearsal: bool
    persisted_routing_implementation_identity: str | None
    current_routing_implementation_identity: str
    routing_state: Literal["match", "differs", "not-recorded"]
    publication_state: str
    representation_identity: str | None
    publication_artifact_path: str | None
    error_type: str | None
    error_message: str | None
    work_item_states: dict[str, int]
    stages: tuple[RunStageInspection, ...]
    all_work_items_complete: bool
    stage_inventory_complete: bool
    stage_state: Literal["missing", "incomplete", "complete"]
    resume_compatible: bool


def summarize_fingerprint(fingerprint: dict[str, object]) -> dict[str, object]:
    """Retain exact dimensions while replacing a large worklist with its identity."""
    worklist = fingerprint.get("worklist")
    if not isinstance(worklist, list):
        raise ValueError("persisted fingerprint worklist is not a list")
    return {
        **{key: value for key, value in fingerprint.items() if key != "worklist"},
        "worklist_count": len(worklist),
        "worklist_identity": canonical_json_identity(worklist),
    }


async def _read_run_rows(
    engine: AsyncEngine, run_ids: tuple[str, ...]
) -> tuple[Sequence[RowMapping], Sequence[RowMapping]]:
    connection = await engine.connect()
    connection = await connection.execution_options(
        isolation_level="REPEATABLE READ", postgresql_readonly=True
    )
    try:
        async with connection.begin():
            stage_table = bool(
                (
                    await connection.execute(
                        text(
                            "SELECT to_regclass('public.decomp_run_stage') IS NOT NULL"
                        )
                    )
                ).scalar_one()
            )
            rows = (
                (await connection.execute(_RUN_SQL, {"run_ids": run_ids}))
                .mappings()
                .all()
            )
            if not stage_table:
                return rows, []
            stage_rows = (
                (await connection.execute(_STAGE_SQL, {"run_ids": run_ids}))
                .mappings()
                .all()
            )
            return rows, stage_rows
    finally:
        await connection.close()


def _run_summary(row: RowMapping, current_routing_identity: str) -> dict[str, object]:
    values = cast("dict[str, object]", dict(row))
    fingerprint = cast("dict[str, object]", values["fingerprint"])
    finished_at = values["finished_at"]
    return {
        "run_id": values["run_id"],
        "status": values["status"],
        "started_at": values["started_at"].isoformat(),  # type: ignore[union-attr]
        "finished_at": (
            finished_at.isoformat() if finished_at is not None else None  # type: ignore[union-attr]
        ),
        "source_identity": values["source_identity"],
        "fingerprint": summarize_fingerprint(fingerprint),
        "fingerprint_sha256": values["fingerprint_sha256"],
        "fingerprint_content_valid": (
            canonical_json_identity(fingerprint) == values["fingerprint_sha256"]
        ),
        "rehearsal": fingerprint.get("rehearsal_nonce") is not None,
        "persisted_routing_implementation_identity": fingerprint.get(
            "routing_implementation_identity"
        ),
        "current_routing_implementation_identity": current_routing_identity,
        "publication_state": values["publication_state"],
        "representation_identity": values["representation_identity"],
        "publication_artifact_path": values["publication_artifact_path"],
        "error_type": values["error_type"],
        "error_message": values["error_message"],
        "work_item_states": {},
        "stages": [],
    }


def _finalize_summary(
    item: dict[str, object], current_routing_identity: str
) -> RunInspection:
    states = cast("dict[str, int]", item["work_item_states"])
    stages = cast("list[dict[str, object]]", item["stages"])
    item["all_work_items_complete"] = bool(states) and set(states) == {"complete"}
    stage_inventory_complete = _stage_inventory_complete(stages)
    item["stage_inventory_complete"] = stage_inventory_complete
    item["routing_state"] = _routing_state(
        item["persisted_routing_implementation_identity"], current_routing_identity
    )
    item["stage_state"] = _stage_state(stages, stage_inventory_complete)
    # A rehearsal is a throwaway run: every resume path refuses it.
    item["resume_compatible"] = bool(
        item["fingerprint_content_valid"]
        and stage_inventory_complete
        and item["routing_state"] == "match"
        and not item["rehearsal"]
    )
    item["stages"] = tuple(stages)
    return RunInspection.model_validate(item)


def _stage_inventory_complete(stages: list[dict[str, object]]) -> bool:
    return tuple(row["stage"] for row in stages) == RUN_STAGE_SEQUENCE


def _routing_state(
    persisted_routing: object, current_routing_identity: str
) -> Literal["match", "differs", "not-recorded"]:
    if persisted_routing is None:
        return "not-recorded"
    if persisted_routing == current_routing_identity:
        return "match"
    return "differs"


def _stage_state(
    stages: list[dict[str, object]], inventory_complete: bool
) -> Literal["missing", "incomplete", "complete"]:
    if not stages:
        return "missing"
    if inventory_complete and all(row.get("state") == "complete" for row in stages):
        return "complete"
    return "incomplete"


def _summaries_by_run(
    rows: Sequence[RowMapping], current_routing_identity: str
) -> dict[str, dict[str, object]]:
    by_run: dict[str, dict[str, object]] = {}
    for row in rows:
        run_id = cast("str", row["run_id"])
        item = by_run.setdefault(run_id, _run_summary(row, current_routing_identity))
        if row["work_state"] is not None:
            cast("dict[str, int]", item["work_item_states"])[row["work_state"]] = row[
                "item_count"
            ]
    return by_run


def _attach_stages(
    by_run: dict[str, dict[str, object]], stage_rows: Sequence[RowMapping]
) -> None:
    for row in stage_rows:
        item = by_run[cast("str", row["run_id"])]
        cast("list[dict[str, object]]", item["stages"]).append(
            {
                key: (value.isoformat() if hasattr(value, "isoformat") else value)
                for key, value in dict(row).items()
                if key != "run_id"
            }
        )


async def inspect_decomposition_runs(
    engine: AsyncEngine, run_ids: tuple[str, ...]
) -> list[RunInspection]:
    """Inspect exact requested runs in one read-only repeatable-read snapshot."""
    current_routing_identity = routing_implementation_identity()
    rows, stage_rows = await _read_run_rows(engine, run_ids)
    by_run = _summaries_by_run(rows, current_routing_identity)
    _attach_stages(by_run, stage_rows)
    inspected = {
        run_id: _finalize_summary(item, current_routing_identity)
        for run_id, item in by_run.items()
    }
    missing = set(run_ids) - by_run.keys()
    if missing:
        raise ValueError("decomposition runs not found: " + ", ".join(sorted(missing)))
    return list(map(inspected.__getitem__, run_ids))
