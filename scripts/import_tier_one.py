"""Attach Uberon corroboration to a completed run's existing assertion ledger."""

import argparse
import asyncio
import json

from scripts.oracle_metrics import _GOLDEN, read_oracle

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition.tier_one import import_tier_one
from ontolib.terminologies.sparql_http_client import SparqlHttpClient


async def run(run_id: str, dry_run: bool) -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        oracle = read_oracle(_GOLDEN / "neoplasm-adjudicated.json")
        async with SparqlHttpClient.for_qlever(settings.uberon_sparql_url) as client:
            report = await import_tier_one(
                make_sessionmaker(engine),
                client,
                run_id,
                oracle_codes=tuple(
                    c.code for c in oracle if c.adjudication.status == "accepted"
                ),
                dry_run=dry_run,
            )
            print(json.dumps(report, indent=2))
    finally:
        await dispose_engine(engine)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.run_id, args.dry_run))
