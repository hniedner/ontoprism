#!/usr/bin/env python3
"""Print line-oriented corpus distributions from a complete stored run."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Mapping
from typing import Annotated, Any

import typer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from backend.config import get_settings
from ontolib.decomposition.enhancement_delta import DeltaOccurrence, delta_sql
from ontolib.decomposition.normalized_group_policy import (
    load_packaged_normalized_group_policy,
)
from ontolib.decomposition.provenance import _missing_group_policy_pairs

_WHOLE_RUN_DELTA_SQL = delta_sql(whole_run=True)
_FLAG_SPLIT = (
    "flagged-constituents-by-retained-value-count-on-axis.co-occurrence-not-cause"
)


async def _grouped(
    conn: AsyncConnection, sql: str, run_id: str
) -> list[tuple[Any, ...]]:
    return [tuple(row) for row in await conn.execute(text(sql), {"run_id": run_id})]


async def _ordinary_counts(conn: AsyncConnection, run_id: str) -> Counter[str]:
    counts: Counter[str] = Counter(
        {
            "constituents.total": 0,
            "review-flags.constituent.total": 0,
            "review-flags.other.mint-filler.total": 0,
            "review-flags.other.unresolved-r101-loss.total": 0,
            "review-flags.other.group-policy-pair-not-emitted.total": 0,
        }
    )
    for outcome, count in await _grouped(
        conn,
        "SELECT outcome,count(*) FROM decomp_work_item WHERE run_id=:run_id "
        "GROUP BY outcome",
        run_id,
    ):
        counts[f"outcomes.{outcome}"] = int(count)
    axis_rows = await _grouped(
        conn,
        "SELECT axis,count(*) FROM decomp_constituent WHERE run_id=:run_id "
        "GROUP BY axis",
        run_id,
    )
    for axis, count in axis_rows:
        counts[f"constituents.axis.{axis}"] = int(count)
        counts["constituents.total"] += int(count)
    for axis, count in await _grouped(
        conn,
        "SELECT axis,count(*) FROM (SELECT concept_code,axis FROM decomp_constituent "
        "WHERE run_id=:run_id GROUP BY concept_code,axis HAVING count(*)>1) grouped "
        "GROUP BY axis",
        run_id,
    ):
        counts[f"concepts-with-multiple-values.axis.{axis}"] = int(count)
    await _flag_counts(conn, run_id, counts)
    row = (
        await conn.execute(
            text(
                "SELECT count(DISTINCT concept_code) FROM (SELECT concept_code FROM "
                "decomp_constituent WHERE run_id=:run_id AND axis='op:PrimarySite' "
                "GROUP BY concept_code HAVING count(*)>1) grouped"
            ),
            {"run_id": run_id},
        )
    ).one()
    counts["primary-site-more-than-one"] = int(row[0])
    residual = (
        await conn.execute(
            text(
                "SELECT count(DISTINCT c.concept_code),count(DISTINCT c.concept_code) "
                "FILTER(WHERE c.axis!='op:Morphology') FROM decomp_constituent c JOIN "
                "decomp_residual_filler f ON f.run_id=c.run_id AND "
                "f.filler_code=c.filler_code WHERE c.run_id=:run_id AND "
                "f.state='complete' AND f.classification='precoordinated'"
            ),
            {"run_id": run_id},
        )
    ).one()
    counts["residual-precoordinated.including-morphology"] = int(residual[0])
    counts["residual-precoordinated.excluding-morphology"] = int(residual[1])
    return counts


async def _flag_counts(
    conn: AsyncConnection, run_id: str, counts: Counter[str]
) -> None:
    reasons = await _grouped(
        conn,
        "SELECT axis,filler_code,count(*) FROM decomp_constituent WHERE "
        "run_id=:run_id AND needs_review GROUP BY axis,filler_code",
        run_id,
    )
    for axis, filler, count in reasons:
        value = int(count)
        reason = f"constituent {axis} / {filler} needs review"
        counts[f"review-flags.constituent.axis.{axis}.reason.{reason}"] = value
        counts[f"review-flags.constituent.axis.{axis}"] += value
        counts["review-flags.constituent.total"] += value
    split = await _grouped(
        conn,
        "WITH value_counts AS (SELECT concept_code,axis,count(*) AS retained FROM "
        "decomp_constituent WHERE run_id=:run_id GROUP BY concept_code,axis) "
        "SELECT flagged.axis,CASE WHEN retained=1 THEN 'exactly-one' ELSE "
        "'more-than-one' END,count(*) FROM decomp_constituent flagged JOIN "
        "value_counts USING(concept_code,axis) WHERE flagged.run_id=:run_id AND "
        "flagged.needs_review GROUP BY flagged.axis,2",
        run_id,
    )
    for axis, bucket, count in split:
        counts[f"{_FLAG_SPLIT}.axis.{axis}.{bucket}"] = int(count)
    flagged_axes = {str(axis) for axis, _filler, _count in reasons}
    for axis in flagged_axes:
        counts.setdefault(f"{_FLAG_SPLIT}.axis.{axis}.exactly-one", 0)
        counts.setdefault(f"{_FLAG_SPLIT}.axis.{axis}.more-than-one", 0)
    for reason, count in await _grouped(
        conn,
        "SELECT reason,count(*) FROM decomp_r101_conservation WHERE run_id=:run_id "
        "AND category='unresolved' GROUP BY reason",
        run_id,
    ):
        counts[f"review-flags.other.unresolved-r101-loss.reason.{reason}"] = int(count)
        counts["review-flags.other.unresolved-r101-loss.total"] += int(count)
    for axis, proposal, count in await _grouped(
        conn,
        "SELECT axis,proposal_id,count(*) FROM decomp_minted_proposal WHERE "
        "run_id=:run_id GROUP BY axis,proposal_id",
        run_id,
    ):
        reason = f"constituent {axis} uses proposed filler {proposal}"
        counts[f"review-flags.other.mint-filler.axis.{axis}.reason.{reason}"] = int(
            count
        )
        counts["review-flags.other.mint-filler.total"] += int(count)
    policy = load_packaged_normalized_group_policy()
    policy_codes = [row.concept_code for row in policy.rows]
    decomposed = await conn.execute(
        text(
            "SELECT concept_code FROM decomp_work_item WHERE run_id=:run_id "
            "AND outcome='decomposed' AND concept_code=ANY(CAST(:codes AS text[]))"
        ),
        {"run_id": run_id, "codes": policy_codes},
    )
    emitted = await conn.execute(
        text(
            "SELECT concept_code,axis,filler_code FROM decomp_constituent "
            "WHERE run_id=:run_id AND concept_code=ANY(CAST(:codes AS text[]))"
        ),
        {"run_id": run_id, "codes": policy_codes},
    )
    missing = _missing_group_policy_pairs(
        {row[0] for row in decomposed.all()}, emitted.mappings().all(), policy
    )
    counts["review-flags.other.group-policy-pair-not-emitted.total"] = len(missing)
    for row in missing:
        reason = f"group-policy pair {row['axis']} / {row['filler_code']} not emitted"
        counts["review-flags.other.group-policy-pair-not-emitted.reason." + reason] += 1


async def corpus_shape_counts(
    run_id: str, *, database_url: str | None = None
) -> Counter[str]:
    """Read all report distributions for one complete stored run."""
    engine = create_async_engine(
        database_url or get_settings().database_url,
        connect_args={"server_settings": {"default_transaction_read_only": "on"}},
    )
    try:
        async with engine.connect() as conn:
            status = await conn.scalar(
                text("SELECT status FROM decomp_run WHERE id=:run_id"),
                {"run_id": run_id},
            )
            if status != "complete":
                raise ValueError(f"run {run_id} does not exist or is not complete")
            counts = await _ordinary_counts(conn, run_id)
            async for row in (
                await conn.stream(text(_WHOLE_RUN_DELTA_SQL), {"run_id": run_id})
            ).mappings():
                occurrence = DeltaOccurrence.model_validate(dict(row))
                counts["stated-occurrences.total"] += 1
                counts[f"stated-occurrences.category.{occurrence.category}"] += 1
                counts[
                    f"stated-occurrences.category.{occurrence.category}.reason."
                    f"{occurrence.reason}"
                ] += 1
            counts.setdefault("stated-occurrences.total", 0)
            return counts
    finally:
        await engine.dispose()


def render_report(
    run_id: str,
    counts: Mapping[str, int],
    *,
    against: tuple[str, Mapping[str, int]] | None = None,
) -> str:
    lines = [f"run_id={run_id}", *(f"{key}={counts[key]}" for key in sorted(counts))]
    if against is not None:
        against_id, baseline = against
        lines.append(f"against_run_id={against_id}")
        for key in sorted(set(counts) | set(baseline)):
            difference = counts.get(key, 0) - baseline.get(key, 0)
            lines.append(f"difference.selected-minus-against.{key}={difference:+d}")
    return "\n".join(lines)


async def _execute(run_id: str, against_id: str | None) -> str:
    selected = await corpus_shape_counts(run_id)
    against = (
        (against_id, await corpus_shape_counts(against_id))
        if against_id is not None
        else None
    )
    return render_report(run_id, selected, against=against)


def main(
    run_id: Annotated[str, typer.Option("--run")],
    against: Annotated[str | None, typer.Option("--against")] = None,
) -> None:
    """Print corpus distributions and optional selected-minus-baseline differences."""
    typer.echo(asyncio.run(_execute(run_id, against)))


if __name__ == "__main__":
    typer.run(main)
