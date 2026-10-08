"""Read-only endpoint evidence report alongside unchanged historical D37."""

from collections import Counter
from pathlib import Path

from scripts.decompose import _source_snapshot
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from backend.config import get_settings
from ontolib.decomposition.axis_diagnostics import read_axis_diagnostic_source
from ontolib.decomposition.endpoint_assessment import Endpoint, assessment_counts
from ontolib.decomposition.histology_anchor import HistologyAnchors, read_p334_values
from ontolib.terminologies.ncit.client import ncit_sparql_client


async def read_assessment_inputs(
    conn: AsyncConnection,
    run_id: str,
    source_identity: str,
) -> tuple[dict, list[Endpoint], set[str], Counter[str]]:
    row = (
        (
            await conn.execute(
                text(
                    "SELECT status,source_identity,ncit_version,fingerprint,metrics "
                    "FROM decomp_run WHERE id=:run"
                ),
                {"run": run_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None or row["status"] != "complete":
        raise ValueError("endpoint assessment requires a complete stored run")
    if row["source_identity"] != source_identity:
        raise ValueError("endpoint assessment source differs from the run")
    outcomes = (
        (
            await conn.execute(
                text(
                    "SELECT concept_code,state,outcome FROM decomp_work_item "
                    "WHERE run_id=:run"
                ),
                {"run": run_id},
            )
        )
        .mappings()
        .all()
    )
    if not outcomes or any(
        r["state"] != "complete" or r["outcome"] is None for r in outcomes
    ):
        raise ValueError("endpoint assessment requires complete typed outcomes")
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT c.concept_code,c.axis,c.filler_code,c.axis_source,"
                    "c.needs_review,c.axis_ambiguous,f.classification "
                    "FROM decomp_constituent c LEFT JOIN decomp_residual_filler f "
                    "ON f.run_id=c.run_id AND f.filler_code=c.filler_code "
                    "AND f.state='complete' WHERE c.run_id=:run "
                    "ORDER BY c.concept_code,c.axis,c.filler_code"
                ),
                {"run": run_id},
            )
        )
        .mappings()
        .all()
    )
    return (
        dict(row),
        [Endpoint.model_validate(dict(r)) for r in rows],
        {r["concept_code"] for r in outcomes if r["outcome"] == "decomposed"},
        Counter(r["outcome"] for r in outcomes),
    )


def render_assessment(run_id: str, metadata: dict, counts: Counter[str]) -> str:
    metrics = metadata["metrics"]
    fingerprint = metadata["fingerprint"]
    lines = [
        "axis-endpoint-assessment: evidence categories, not an atomicity score",
        "No category establishes terminality, completeness or expert acceptance.",
        "Anchor source availability is recomputed; "
        "emitted absence is not retroactive failure.",
        f"run_id={run_id}",
        f"ncit_version={metadata['ncit_version']}",
        f"stored_detector={fingerprint['routing_implementation_identity']}",
        "D37.historical-detector-relative: unchanged stored values",
    ]
    for key in (
        "decomposed",
        "residual_precoordinated_count",
        "residual_precoordination_unknown_count",
        "residual_precoordination",
    ):
        lines.append(f"D37.{key}={metrics.get(key)}")
    lines.extend(f"assessment.{key}={counts[key]}" for key in sorted(counts))
    return "\n".join(lines)


async def endpoint_report(run_id: str, manifest_path: Path) -> str:
    settings = get_settings()
    snapshot = await _source_snapshot(manifest_path, settings.ncit_sparql_url)
    engine = create_async_engine(
        settings.database_url,
        connect_args={"server_settings": {"default_transaction_read_only": "on"}},
    )
    try:
        async with engine.connect() as conn:
            metadata, rows, decomposed, outcomes = await read_assessment_inputs(
                conn,
                run_id,
                snapshot.source_identity,
            )
        async with ncit_sparql_client(settings.ncit_sparql_url) as client:
            if await client.version() != metadata["ncit_version"]:
                raise ValueError("configured NCIt version differs from the stored run")
            source = await read_axis_diagnostic_source(client, snapshot.source_identity)
            anchors = HistologyAnchors(source.snapshot, await read_p334_values(client))
            counts = assessment_counts(rows, decomposed, source, anchors)
            if await client.version() != metadata["ncit_version"]:
                raise ValueError("configured NCIt version changed during assessment")
        if await _source_snapshot(manifest_path, settings.ncit_sparql_url) != snapshot:
            raise ValueError("configured NCIt source changed during assessment")
        for outcome, count in outcomes.items():
            counts[f"outcomes.{outcome}"] = count
        return render_assessment(run_id, metadata, counts)
    finally:
        await engine.dispose()
