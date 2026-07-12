"""caDSR anchor-code enumeration from the caDSR SQLite repository.

An "anchor" is every distinct NCIt concept_code appearing in the
``cde_concepts`` table of the caDSR CDE repo — these codes are the seam
between caDSR and the NCIt OWL ontology.

Exports:
    ``AnchorEnumeration`` — typed result of the enumeration query.
    ``enumerate_anchors`` — open caDSR SQLite and run the anchor query.
    ``overlap_with_roles`` — set arithmetic between anchors and role codes.
    ``filter_in_scope`` — keep only codes in the NCIt neoplasm branch.
    ``check_liveness`` — per-code ASK against the NCIt store for liveness.

Design (PR-A2 / issue #74):
    A2.1 — The enumeration module (this file).
    A2.2 — Scope gate (neoplasm-branch filter).
    A2.3 — Liveness check (retired/merged detection).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

from ontolib.terminologies.namespaces import NCIT_NS

# ---- Constants ---------------------------------------------------------------

# The NCIt neoplasm root concept — scope gate keeps only its descendants.
_NEOPLASM_ROOT = "C3262"

# Maximum codes per VALUES clause to avoid query-bloat issues.
_BATCH_SIZE = 500

# ---- Query strings -----------------------------------------------------------

_ANCHOR_SQL = """
SELECT concept_type, COUNT(DISTINCT concept_code) AS n_codes
FROM cde_concepts
GROUP BY concept_type
"""

_ALL_CODES_SQL = """
SELECT DISTINCT concept_code
FROM cde_concepts
"""

# ---- Dataclass ---------------------------------------------------------------


@dataclass(frozen=True)
class AnchorEnumeration:
    """Typed result of enumerating anchor codes from caDSR."""

    by_type: dict[str, int]
    total_distinct: int
    all_codes: frozenset[str]


# ---- A2.1 — Enumeration ------------------------------------------------------


def enumerate_anchors(db_path: str | Path) -> AnchorEnumeration:
    """Open *db_path* read-only and count anchors by concept_type.

    Args:
        db_path: Path to the caDSR SQLite DB (or a ``file:`` URI for
            in-memory test fixtures).

    Returns:
        An ``AnchorEnumeration`` with per-type counts, total, and the
        full set of distinct codes.
    """
    uri = str(db_path)
    if not uri.startswith("file:"):
        uri = f"file:{uri}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        # By-type counts
        by_type: dict[str, int] = {}
        for row in conn.execute(_ANCHOR_SQL):
            ct: str | None = row[0]
            n: int = row[1]
            by_type[ct] = n  # type: ignore[arg-type] — SQLite guarantees TEXT

        # All distinct codes
        all_codes: set[str] = set()
        for row in conn.execute(_ALL_CODES_SQL):
            code: str | None = row[0]
            if code is not None:
                all_codes.add(code)

    finally:
        conn.close()

    return AnchorEnumeration(
        by_type=by_type,
        total_distinct=len(all_codes),
        all_codes=frozenset(all_codes),
    )


# ---- A2.1 — Overlap ----------------------------------------------------------


def overlap_with_roles(
    anchor_codes: frozenset[str],
    role_codes: frozenset[str],
) -> tuple[int, int, int]:
    """Compute overlap between anchor codes and role codes.

    Returns:
        ``(in_both, cadsr_only, roles_only)``.
    """
    in_both = len(anchor_codes & role_codes)
    cadsr_only = len(anchor_codes - role_codes)
    roles_only = len(role_codes - anchor_codes)
    return in_both, cadsr_only, roles_only


# ---- A2.2 — Scope gate -------------------------------------------------------


async def filter_in_scope(
    codes: frozenset[str],
    client: OxigraphHttpClient,
) -> frozenset[str]:
    """Keep only codes that are subclasses of ``C3262`` (Neoplasm).

    Uses a batched SPARQL SELECT with ``VALUES`` and the ``rdfs:subClassOf*``
    property path.

    Args:
        codes: Candidate anchor codes.
        client: An ``OxigraphHttpClient`` connected to the NCIt store.

    Returns:
        Subset of *codes* that are in the neoplasm branch.
    """
    if not codes:
        return frozenset()

    code_list = sorted(codes)
    in_scope: set[str] = set()

    for i in range(0, len(code_list), _BATCH_SIZE):
        batch = code_list[i : i + _BATCH_SIZE]
        values = " ".join(f"ncit:{c}" for c in batch)
        query = (
            "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> "
            "PREFIX ncit: <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#> "
            f"SELECT ?c WHERE {{ VALUES ?c {{ {values} }} "
            f"?c rdfs:subClassOf* ncit:{_NEOPLASM_ROOT} }}"
        )
        rows = await client.select(query)
        for row in rows:
            iri = row.get("c")
            if iri and iri.startswith(NCIT_NS):
                code = iri[len(NCIT_NS) :]
                in_scope.add(code)

    return frozenset(in_scope)


# ---- A2.3 — Liveness ---------------------------------------------------------


async def check_liveness(
    codes: frozenset[str],
    client: OxigraphHttpClient,
) -> dict[str, str]:
    """Check which codes are live (exist as ``owl:Class``) in the NCIt store.

    A code is **live** if ``ASK {{ ncit:{{code}} a owl:Class }}`` returns
    ``True``; otherwise it is **unresolved** (retired, merged, or bogus).

    Every input code appears in the result dict — unresolved codes are
    reported, not dropped.

    Args:
        codes: Codes to check.
        client: An ``OxigraphHttpClient`` connected to the NCIt store.

    Returns:
        Dict mapping each code to ``"live"`` or ``"unresolved"``.
    """
    if not codes:
        return {}

    result: dict[str, str] = {}
    for code in codes:
        query = (
            "PREFIX owl: <http://www.w3.org/2002/07/owl#> "
            "PREFIX ncit: <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#> "
            f"ASK {{ ncit:{code} a owl:Class }}"
        )
        is_live = await client.ask(query)
        result[code] = "live" if is_live else "unresolved"

    return result
