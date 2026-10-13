"""Read-only ROBOT/ELK comparison of a stored run's SubClassOf projection."""

import asyncio
import json
from pathlib import Path
from time import perf_counter
from typing import Annotated

import typer
from scripts.oracle_metrics import read_oracle
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.core.robot import RobotSettings, run_robot
from ontolib.decomposition.axis_diagnostics import read_axis_diagnostic_source
from ontolib.decomposition.reasoner_check import check_projection, disjoint_fillers
from ontolib.decomposition.reasoner_evidence import preview_reasoner_evidence
from ontolib.repositories.xref.validation import ReasonerUnavailableError
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.client import ncit_sparql_client


async def _inputs(sessions, run_id):
    async with sessions() as session:
        result = await session.execute(
            text(
                "SELECT status,ncit_version,source_identity "
                "FROM decomp_run WHERE id=:run"
            ),
            {"run": run_id},
        )
        metadata = result.mappings().one()
        if metadata["status"] != "complete":
            raise ValueError("reasoner check requires a completed stored run")
        result = await session.execute(
            text(
                "SELECT concept_code,axis,filler_code,source_roles "
                "FROM decomp_constituent "
                "WHERE run_id=:run ORDER BY concept_code,axis,filler_code"
            ),
            {"run": run_id},
        )
        return metadata, [dict(row) for row in result.mappings()]


def _oracle_module(stated, assertions, workspace, config):
    terms = workspace / "terms.txt"
    seeds = {
        row[key]
        for row in assertions
        for key in ("concept_code", "filler_code")
        if row[key].startswith("C")
    }
    terms.write_text("\n".join(NCIT_NS + c for c in sorted(seeds)))
    module = workspace / "module.owl"
    result = run_robot(
        [
            "extract",
            "--method",
            "BOT",
            "--input",
            str(stated),
            "--term-file",
            str(terms),
            "--output",
            str(module),
        ],
        config,
    )
    if result.returncode:
        raise ReasonerUnavailableError(result.stdout + result.stderr)
    return module


async def run(run_id, workspace, oracle=False):
    start = perf_counter()
    settings, config = get_settings(), RobotSettings().robot_config()
    engine = make_engine(settings.database_url)
    try:
        sessions = make_sessionmaker(engine)
        metadata, assertions = await _inputs(sessions, run_id)
        if oracle:
            codes = {
                c.code
                for c in read_oracle(
                    Path("ontolib/tests/decomposition/golden/neoplasm-adjudicated.json")
                )
                if c.adjudication.status == "accepted"
            }
            assertions = [r for r in assertions if r["concept_code"] in codes]
        async with ncit_sparql_client(settings.ncit_sparql_url) as client:
            if await client.version() != metadata["ncit_version"]:
                raise ValueError("configured NCIt differs from stored run")
            hierarchy = (
                await read_axis_diagnostic_source(client, metadata["source_identity"])
            ).snapshot
        workspace.mkdir(parents=True, exist_ok=False)
        stated = Path(settings.ncit_owl_dir) / "Thesaurus-stated.owl"
        if oracle:
            stated = _oracle_module(stated, assertions, workspace, config)
        ready = perf_counter()
        report = check_projection(stated, assertions, workspace / "comparison", config)
        report.update(
            run_id=run_id,
            scope="oracle" if oracle else "corpus",
            assertions=len(assertions),
            disjoint_fillers_inspection_only=disjoint_fillers(assertions, hierarchy),
        )
        report["ledger_preview"] = await preview_reasoner_evidence(
            sessions, report, metadata["ncit_version"]
        )
        report["phase_seconds"]["input_reads_and_module"] = ready - start
        report["total_seconds"] = perf_counter() - start
        return report
    finally:
        await dispose_engine(engine)


def main(
    run_id: Annotated[str, typer.Option("--run")],
    workspace: Annotated[Path, typer.Option()],
    oracle: bool = False,
):
    typer.echo(json.dumps(asyncio.run(run(run_id, workspace, oracle)), indent=2))


if __name__ == "__main__":
    typer.run(main)
