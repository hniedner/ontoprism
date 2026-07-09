#!/usr/bin/env python3
"""Research script (#44): per-level differentia-diffing extractor.

Walks the genus chain like `ontolib.decomposition.walker`, but only KEEPS roles whose
code is in a curated "defining axis" allowlist — R88 (stage), R101 (primary site), R105
(abnormal cell) — dropping everything else the chain asserts along the way (R103/R104
normal origin, R106 molecular abnormality, R108 findings, R113/R115 May_Have_*,
R135/R138/R139/R142 Excludes_*, R176 Mapped_To_Gene). This is a DIFFERENT axis-selection
strategy than `scripts/decomposition_spike.py` (which keeps everything not
May_/Mapped_/Excludes_ and relies solely on most-specific selection to clean
up afterwards — the strategy design §6.2 found insufficient). Findings and
open questions are recorded in docs/DECISIONS.md, not asserted as settled
here.

"""

from __future__ import annotations

import asyncio
import json
import sys

from ontolib.decomposition.score import score
from ontolib.decomposition.walker import Role, walk_chain
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

# The axes kept as "own differentia" candidates regardless of which chain level they
# appear at — everything else is dropped even though it IS part of some
# ancestor's formal equivalence definition (see docs/DECISIONS.md D15/D20 for
# the reasoning).
_DEFINING_AXES = {"R88", "R101", "R105"}

# A stage-VALUE filler ("Stage III") and a stage-SYSTEM filler ("AJCC v7 Stage") both
# use role R88 in the raw graph; the golden set distinguishes them by relabelling the
# system one as `op:StageSystem` (design §4.2's "else an op: axis"). Detected here by
# a simple label heuristic — a genuine axis-naming judgment call (#44 SME task), not a
# mechanical fact, so it's isolated in one place.
_STAGE_SYSTEM_MARKERS = ("AJCC", "UICC", "Stage System")


def _is_stage_system_filler(label: str | None) -> bool:
    return bool(label) and any(m in label for m in _STAGE_SYSTEM_MARKERS)


def extract_defining_axes(roles: list[Role]) -> set[tuple[str, str]]:
    """Filter+relabel a level's roles into (axis, filler_code) pairs."""
    pairs: set[tuple[str, str]] = set()
    for r in roles:
        if r.code not in _DEFINING_AXES:
            continue
        axis = (
            "op:StageSystem"
            if r.code == "R88" and _is_stage_system_filler(r.filler_label)
            else r.code
        )
        pairs.add((axis, r.filler_code))
    return pairs


async def extract(client: OxigraphHttpClient, code: str) -> set[tuple[str, str]]:
    levels = await walk_chain(client, code)
    pairs: set[tuple[str, str]] = set()
    for level in levels:
        pairs |= extract_defining_axes(level.roles)
    return pairs


def ambiguous_axes(pairs: set[tuple[str, str]]) -> dict[str, set[str]]:
    """Axes with more than one candidate filler across the whole DAG walk."""
    by_axis: dict[str, set[str]] = {}
    for axis, filler in pairs:
        by_axis.setdefault(axis, set()).add(filler)
    return {axis: fillers for axis, fillers in by_axis.items() if len(fillers) > 1}


def _load_golden(path: str) -> dict:
    with open(path) as f:
        return json.loads(f.read())["concepts"]


async def main() -> None:
    codes = sys.argv[1:] or ["C6135"]
    golden_path = "ontolib/tests/decomposition/golden/neoplasm.json"
    golden = _load_golden(golden_path)

    async with OxigraphHttpClient("http://localhost:7888") as client:
        for code in codes:
            actual = await extract(client, code)
            print(f"{code}: extracted {sorted(actual)}")
            ambiguous = ambiguous_axes(actual)
            if ambiguous:
                print(f"  needs_review (multiple fillers for one axis): {ambiguous}")
            if code in golden:
                expected = {tuple(p) for p in golden[code]["constituents"]}
                s = score(expected, actual)
                print(
                    f"  vs golden: precision={s.precision:.2f} recall={s.recall:.2f} "
                    f"missing={sorted(s.missing)} extra={sorted(s.extra)}"
                )


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
