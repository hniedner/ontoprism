"""Corroborate stored ToldGenus assertions with the pinned Mondo release."""

import argparse
import asyncio
import json

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition.mondo_evidence import import_mondo_evidence
from ontolib.terminologies.mondo import published_mondo


async def run(run_id: str, dry_run: bool) -> None:
    source = await asyncio.to_thread(published_mondo)
    engine = make_engine(get_settings().database_url)
    try:
        report = await import_mondo_evidence(
            make_sessionmaker(engine), run_id, source, dry_run=dry_run
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
