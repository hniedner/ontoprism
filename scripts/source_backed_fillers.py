"""Report source-backed filler share and corroboration needs without writing data."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import get_settings
from ontolib.decomposition.provenance import ProvenanceStore

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ontolib.decomposition.source_support import ConstituentEvidence


def summarize_fillers(
    records: Iterable[ConstituentEvidence], counts: Counter[tuple[str, ...]]
) -> None:
    """Count constituents, not the number of source citations attached to them."""
    for item in records:
        counts[(item.support,)] += 1
        counts[(item.axis, item.axis_source, item.support)] += 1


def headline(counts: Counter[tuple[str, ...]]) -> str:
    kinds = ("restriction-backed", "genus-backed", "not-source-backed")
    total = sum(counts[(kind,)] for kind in kinds)
    parts = []
    for kind in kinds:
        count = counts[(kind,)]
        share = f"{count / total:.6%}" if total else "not-computed"
        parts.append(f"{kind}: {count}/{total} ({share})")
    return "source-backed filler share: " + " / ".join(parts)


async def report(run_id: str, *, details: bool) -> None:
    engine = create_async_engine(
        get_settings().database_url,
        connect_args={"server_settings": {"default_transaction_read_only": "on"}},
    )
    store = ProvenanceStore(async_sessionmaker(engine))
    counts: Counter[tuple[str, ...]] = Counter()
    try:
        run = await store.get_run(run_id)
        if (
            run is None
            or run.status != "complete"
            or run.publication_state != "published"
        ):
            raise ValueError("source support report requires a completed published run")
        print(f"run={run_id} NCIt={run.ncit_version}")
        print("Source support is not acceptance of normalized semantics.")
        for outcome in await store.work_item_outcomes(run_id):
            records = await store.constituent_evidence(run_id, outcome.concept_code)
            summarize_fillers(records, counts)
            if details:
                for item in records:
                    key = f"{item.concept_code} {item.axis} {item.filler_code}"
                    if item.policy_choices:
                        print(
                            f"policy-choices: {key}: {', '.join(item.policy_choices)}"
                        )
                    if item.inferred_assertions:
                        print(
                            f"inferred-assertions: {key}: "
                            + "; ".join(item.inferred_assertions)
                        )
        print(headline(counts))
        for key, count in sorted(counts.items()):
            if len(key) > 1:
                print("axis/derivation/support:", *key, count)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument(
        "--details",
        action="store_true",
        help="List policy and inferred assertions separately",
    )
    args = parser.parse_args()
    asyncio.run(report(args.run_id, details=args.details))


if __name__ == "__main__":
    main()
