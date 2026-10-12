"""Populate assertion evidence from a completed stored run, without rerunning it."""

import argparse
import asyncio
import json

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition.tier_zero import import_tier_zero


async def run(run_id: str, dry_run: bool) -> None:
    engine = make_engine(get_settings().database_url)
    try:
        report = await import_tier_zero(
            make_sessionmaker(engine), run_id, dry_run=dry_run
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
