#!/usr/bin/env python3
"""Generate the source-qualified highest-fanout decomposition baseline."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from ontolib.decomposition.fanout_baseline import (
    FanoutBaseline,
    generate_fanout_baseline,
    write_fanout_baseline,
)
from ontolib.terminologies.ncit.client import ncit_sparql_client
from ontolib.terminologies.ncit.sibling_store import validate_ncit_sibling_manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--expected-source-identity", required=True)
    parser.add_argument("--out", required=True, type=Path)
    return parser


async def _run(args: argparse.Namespace) -> None:
    manifest = validate_ncit_sibling_manifest(args.source_manifest)
    if manifest.source_identity != args.expected_source_identity:
        raise ValueError("candidate manifest source identity does not match expected")

    def progress(index: int, total: int, code: str) -> None:
        if index == 1 or index % 1000 == 0 or index == total:
            print(f"observed {index}/{total} ({code})", file=sys.stderr)

    async with ncit_sparql_client(args.endpoint, query_timeout=180.0) as client:
        version = await client.version()
        if version != manifest.ontology_version:
            raise ValueError("endpoint release does not match candidate manifest")
        baseline = await generate_fanout_baseline(
            client,
            source_identity=manifest.source_identity,
            ontology_release=manifest.ontology_version,
            progress=progress,
        )
    write_fanout_baseline(args.out, baseline)
    print(json.dumps(as_output(baseline), sort_keys=True))


def as_output(baseline: FanoutBaseline) -> dict[str, object]:
    return baseline.model_dump(mode="json")


def main() -> None:
    asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    main()
