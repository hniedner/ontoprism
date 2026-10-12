"""Corroborate stored morphology against P334 carriers; preview unless --apply."""

import argparse
import asyncio
import json

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition.axis_diagnostics import read_axis_diagnostic_source
from ontolib.decomposition.histology_anchor import HistologyAnchors, read_p334_values
from ontolib.decomposition.morphology_evidence import (
    import_morphology_evidence,
    morphology_inputs,
)
from ontolib.terminologies.ncit.client import ncit_sparql_client


async def run(run_id: str, apply: bool = False) -> dict:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        sessions = make_sessionmaker(engine)
        metadata, _ = await morphology_inputs(sessions, run_id)
        async with ncit_sparql_client(settings.ncit_sparql_url) as client:
            if await client.version() != metadata["ncit_version"]:
                raise ValueError("configured NCIt version differs from stored run")
            source = await read_axis_diagnostic_source(
                client, metadata["source_identity"]
            )
            anchors = HistologyAnchors(source.snapshot, await read_p334_values(client))
            if await client.version() != metadata["ncit_version"]:
                raise ValueError("NCIt version changed during source reads")
        return await import_morphology_evidence(
            sessions, run_id, anchors, dry_run=not apply
        )
    finally:
        await dispose_engine(engine)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.run_id, args.apply)), indent=2))
