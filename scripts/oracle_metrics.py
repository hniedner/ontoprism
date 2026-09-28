#!/usr/bin/env python3
"""Score a complete stored run, or rehearse first, without review-tooling imports."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, Literal

import typer
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.config import get_settings
from ontolib.decomposition.evaluation import (
    compare_common_pair_partition,
    compare_full_partition,
)
from ontolib.decomposition.normalized_group_policy import UNRESOLVED_ABSTENTION_BLOCKS
from ontolib.decomposition.r101_run_conservation import R101ConservationCounts

_ROOT = Path(__file__).resolve().parents[1]
_GOLDEN = _ROOT / "ontolib/tests/decomposition/golden"


class ExpectedPair(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    axis: str
    filler: str
    needs_review: bool
    provenance_status: str
    relationship_group: str | None


class ExpectedResult(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    constituents: list[ExpectedPair]


class Adjudication(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    status: Literal["accepted", "deferred", "rejected"]


class ExpectedConcept(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    code: str
    adjudication: Adjudication
    expected: ExpectedResult | None


def read_oracle(path: Path) -> list[ExpectedConcept]:
    """Read scoring fields, excluding historical review metadata."""
    concepts = json.loads(path.read_text())["concepts"]
    return [
        ExpectedConcept(
            code=c["code"],
            adjudication=Adjudication(status=c["adjudication"]["status"]),
            expected=ExpectedResult(
                constituents=[
                    ExpectedPair.model_validate(
                        {key: row[key] for key in ExpectedPair.model_fields}
                    )
                    for row in c["expected"]["constituents"]
                ]
            )
            if c["expected"] is not None
            else None,
        )
        for c in concepts
    ]


class StoredPair(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    concept_code: str
    axis: str
    filler_code: str
    needs_review: bool
    normalized_group_id: str | None


def _conservation_counts(counts: dict[str, int]) -> R101ConservationCounts:
    return R101ConservationCounts(
        total=sum(counts.values()),
        projected=counts.get("projected", 0),
        unchanged_unprojected=counts.get("unchanged-unprojected", 0),
        one_step_r82=counts.get("one-step-r82", 0),
        closure_only_r82=counts.get("closure-only-r82", 0),
        unresolved=counts.get("unresolved", 0),
    )


def _rate(name: str, numerator: int, denominator: int) -> str:
    value = f"{numerator / denominator:.6f}" if denominator else "not-computed"
    return f"{name}={numerator}/{denominator} ({value})"


def _partitions(oracle: list[ExpectedConcept], actual: list[StoredPair]) -> list[str]:
    full = eligible = agreements = 0
    abstentions = []
    for concept in oracle:
        if concept.expected is None:
            raise ValueError(
                f"accepted oracle concept {concept.code} lacks expectations"
            )
        expected = tuple(
            ((r.axis, r.filler), r.relationship_group)
            for r in concept.expected.constituents
            if not r.needs_review and r.provenance_status == "ncit-26.07d"
        )
        emitted = tuple(
            ((r.axis, r.filler_code), r.normalized_group_id)
            for r in actual
            if r.concept_code == concept.code
            and not r.needs_review
            and r.filler_code.startswith("C")
        )
        full += compare_full_partition(expected, emitted).agrees is True
        common = compare_common_pair_partition(expected, emitted)
        if not common.eligible:
            continue
        eligible += 1
        shared = {pair for block in common.actual_partition for pair in block}
        unresolved = UNRESOLVED_ABSTENTION_BLOCKS.get(concept.code, frozenset())
        if any(
            pair in unresolved & shared and group is None for pair, group in emitted
        ):
            abstentions.append(concept.code)
        else:
            agreements += common.agrees is True
    decided = eligible - len(abstentions)
    return [
        _rate("full_partition_agreement", full, len(oracle))
        + f" [full-cohort; includes {len(abstentions)} common-pair abstentions]",
        _rate("common_pair_partition_agreement_decided_only", agreements, decided)
        + f" [decided-only; agrees={agreements}; disagrees={decided - agreements}; "
        + f"abstains={len(abstentions)}; ineligible={len(oracle) - eligible}]",
        _rate("common_pair_decision_coverage", decided, eligible),
        "partition_abstentions=" + (",".join(sorted(abstentions)) or "none"),
    ]


def score_pairs(oracle: list[ExpectedConcept], actual: list[StoredPair]) -> str:
    accepted = [c for c in oracle if c.adjudication.status == "accepted"]
    if not accepted or any(c.expected is None for c in accepted):
        raise ValueError("accepted oracle cohort is empty or lacks expectations")
    codes = {c.code for c in accepted}
    expected = {
        (c.code, r.axis, r.filler)
        for c in accepted
        if c.expected is not None
        for r in c.expected.constituents
    }
    official_expected = {
        (c.code, r.axis, r.filler)
        for c in accepted
        if c.expected is not None
        for r in c.expected.constituents
        if not r.needs_review and r.provenance_status == "ncit-26.07d"
    }
    emitted = {
        (r.concept_code, r.axis, r.filler_code)
        for r in actual
        if r.concept_code in codes
    }
    official_emitted = {
        (r.concept_code, r.axis, r.filler_code)
        for r in actual
        if r.concept_code in codes
        and not r.needs_review
        and r.filler_code.startswith("C")
    }
    lines = []
    for label, prefix, wanted, got in [
        (
            "official (flagged unscoreable, NCIt-bound)",
            "",
            official_expected,
            official_emitted,
        ),
        ("plain exact pairs", "plain_", expected, emitted),
    ]:
        lines.extend(
            [
                label,
                _rate(prefix + "exact_pair_precision", len(wanted & got), len(got)),
                _rate(prefix + "exact_pair_recall", len(wanted & got), len(wanted)),
            ]
        )
    return "\n".join([*lines, *_partitions(accepted, actual)])


async def _stored_report(run_id: str) -> str:
    oracle = read_oracle(_GOLDEN / "neoplasm-adjudicated.json")
    codes = {c.code for c in oracle if c.adjudication.status == "accepted"}
    engine = create_async_engine(
        get_settings().database_url,
        connect_args={"server_settings": {"default_transaction_read_only": "on"}},
    )
    try:
        async with engine.connect() as conn:
            run = (
                await conn.execute(
                    text(
                        "SELECT status,fingerprint->'worklist' AS worklist "
                        "FROM decomp_run WHERE id=:run"
                    ),
                    {"run": run_id},
                )
            ).first()
            items = (
                await conn.execute(
                    text(
                        "SELECT concept_code,state FROM decomp_work_item "
                        "WHERE run_id=:run AND concept_code=ANY(CAST(:codes AS text[]))"
                    ),
                    {"run": run_id, "codes": sorted(codes)},
                )
            ).all()
            worklist = set(run[1]) if run is not None else set()
            missing = codes - (worklist & {r[0] for r in items})
            incomplete = {r[0] for r in items if r[1] != "complete"}
            if run is None or run[0] != "complete" or missing or incomplete:
                raise ValueError(
                    f"run {run_id} is missing or incomplete; missing oracle codes: "
                    f"{','.join(sorted(missing)) or 'none'}; incomplete oracle codes: "
                    f"{','.join(sorted(incomplete)) or 'none'}"
                )
            result = await conn.execute(
                text(
                    "SELECT concept_code,axis,filler_code,needs_review,"
                    "normalized_group_id FROM decomp_constituent WHERE run_id=:run "
                    "AND concept_code=ANY(CAST(:codes AS text[]))"
                ),
                {"run": run_id, "codes": sorted(codes)},
            )
            actual = [StoredPair.model_validate(dict(row)) for row in result.mappings()]
            counts = {
                str(row[0]): int(row[1])
                for row in await conn.execute(
                    text(
                        "SELECT category,count(*) FROM decomp_r101_conservation "
                        "WHERE run_id=:run GROUP BY category"
                    ),
                    {"run": run_id},
                )
            }
        conservation = _conservation_counts(counts)
        historical = json.loads((_GOLDEN / "neoplasm-row-decisions.json").read_text())[
            "rows"
        ]
        suggestions = [r for r in historical if r["row_type"] == "ENGINE SUGGESTION"]
        headline = _rate(
            "historical_sme_include_rate",
            sum(r["sme_action"] == "include" for r in suggestions),
            len(suggestions),
        )
        return (
            f"run_id={run_id}\n{headline} [historical]\n"
            + score_pairs(oracle, actual)
            + "\nr101_conservation="
            + conservation.model_dump_json()
            + "\nr101_conservation_scope=recorded occurrences only"
        )
    finally:
        await engine.dispose()


async def _execute(run_id: str | None = None) -> str:
    if run_id is None:
        from scripts.decompose import _run  # noqa: PLC0415

        from ontolib.decomposition.branches import DecompositionBranch  # noqa: PLC0415

        def capture(progress: object) -> None:
            nonlocal run_id
            observed = getattr(progress, "run_id", None)
            if isinstance(observed, str):
                run_id = observed

        await _run(
            source_manifest=_ROOT / "data/qlever-ncit/.ontoprism-ncit-candidate.json",
            branch=DecompositionBranch.NEOPLASM,
            out=None,
            load=False,
            emit_equivalence=False,
            resume=None,
            total_limit=None,
            walker_max_depth=7,
            sample_manifest=_ROOT / "samples/ncit-26.07d-m1-current-replay.json",
            rehearsal=True,
            progress=capture,
        )
    if run_id is None:
        raise RuntimeError("rehearsal did not report its run id")
    return await _stored_report(run_id)


def main(run_id: Annotated[str | None, typer.Option("--run")] = None) -> None:
    """Score a stored complete cohort; without --run, rehearse it first."""
    typer.echo(asyncio.run(_execute(run_id)))


if __name__ == "__main__":
    typer.run(main)
