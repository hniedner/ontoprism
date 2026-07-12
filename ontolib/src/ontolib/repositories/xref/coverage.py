from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from ontolib.repositories.xref.store import XrefStore
    from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

from ontolib.repositories.xref.cadsr_anchors import check_liveness, filter_in_scope
from ontolib.repositories.xref.vocab import EXACT_MATCH

_IDENTITY_LIFECYCLES = frozenset({"validated", "active"})


@dataclass(frozen=True)
class CdeAnchor:
    concept_code: str
    concept_type: str | None
    is_primary: bool


@dataclass(frozen=True)
class CdeAnchors:
    public_id: str
    version: str
    anchors: tuple[CdeAnchor, ...]

    @property
    def codes(self) -> frozenset[str]:
        return frozenset(a.concept_code for a in self.anchors)

    @property
    def is_post_coordinated(self) -> bool:
        by_type: dict[str | None, set[str]] = defaultdict(set)
        for a in self.anchors:
            by_type[a.concept_type].add(a.concept_code)
        return any(len(v) > 1 for v in by_type.values())


@dataclass(frozen=True)
class CoverageReport:
    n_cdes: int
    single_code_cdes: int
    post_coordinated_cdes: int
    distinct_anchors: int
    live: int
    unresolved: int
    anchors_in_roles: int
    anchors_new: int
    anchors_identity_mapped: int
    anchors_close_only: int
    anchors_unmapped: int
    cde_coverage: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "n_cdes": self.n_cdes,
            "single_code_cdes": self.single_code_cdes,
            "post_coordinated_cdes": self.post_coordinated_cdes,
            "distinct_anchors": self.distinct_anchors,
            "live": self.live,
            "unresolved": self.unresolved,
            "anchors_in_roles": self.anchors_in_roles,
            "anchors_new": self.anchors_new,
            "anchors_identity_mapped": self.anchors_identity_mapped,
            "anchors_close_only": self.anchors_close_only,
            "anchors_unmapped": self.anchors_unmapped,
            "cde_coverage": self.cde_coverage,
        }


_CDE_ANCHORS_SQL = (
    "SELECT public_id, version, concept_code, concept_type, is_primary "
    "FROM cde_concepts WHERE concept_code IS NOT NULL"
)


def cde_anchor_map(db_path: str | Path) -> dict[tuple[str, str], CdeAnchors]:
    uri = str(db_path)
    if not uri.startswith("file:"):
        uri = f"file:{uri}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        grouped: dict[tuple[str, str], list[CdeAnchor]] = defaultdict(list)
        for pub, ver, code, ctype, prim in conn.execute(_CDE_ANCHORS_SQL):
            grouped[(pub, ver)].append(CdeAnchor(code, ctype, bool(prim)))
    finally:
        conn.close()
    return {k: CdeAnchors(k[0], k[1], tuple(v)) for k, v in grouped.items()}


def _is_identity(strengths: set[tuple[str, str]]) -> bool:
    return any(p == EXACT_MATCH and lc in _IDENTITY_LIFECYCLES for p, lc in strengths)


def _is_close(strengths: set[tuple[str, str]]) -> bool:
    return bool(strengths) and not _is_identity(strengths)


def _cde_is_covered(
    cde: CdeAnchors,
    live_status: dict[str, str],
    strength_by_subject: dict[str, set[tuple[str, str]]],
) -> bool:
    return (
        bool(cde.codes)
        and all(live_status.get(c) == "live" for c in cde.codes)
        and all(_is_identity(strength_by_subject.get(c, set())) for c in cde.codes)
    )


def _walk_cdes(
    anchor_map: dict[tuple[str, str], CdeAnchors],
    live_status: dict[str, str],
    strength_by_subject: dict[str, set[tuple[str, str]]],
) -> tuple[set[str], int, int, int]:
    codes: set[str] = set()
    single = post = covered = 0
    for cde in anchor_map.values():
        codes |= cde.codes
        if cde.is_post_coordinated:
            post += 1
        else:
            single += 1
        if _cde_is_covered(cde, live_status, strength_by_subject):
            covered += 1
    return codes, single, post, covered


def _strength_buckets(
    codes: set[str],
    strength_by_subject: dict[str, set[tuple[str, str]]],
) -> tuple[int, int, int]:
    identity = close = 0
    for c in codes:
        s = strength_by_subject.get(c, set())
        if _is_identity(s):
            identity += 1
        elif _is_close(s):
            close += 1
    return identity, close, len(codes) - identity - close


def build_coverage_report(
    anchor_map: dict[tuple[str, str], CdeAnchors],
    *,
    live_status: dict[str, str],
    strength_by_subject: dict[str, set[tuple[str, str]]],
    role_codes: frozenset[str],
) -> CoverageReport:
    all_codes, n_single, n_post, covered_cdes = _walk_cdes(
        anchor_map, live_status, strength_by_subject
    )
    n = len(anchor_map)
    live = sum(1 for c in all_codes if live_status.get(c) == "live")
    identity, close, unmapped = _strength_buckets(all_codes, strength_by_subject)
    return CoverageReport(
        n_cdes=n,
        single_code_cdes=n_single,
        post_coordinated_cdes=n_post,
        distinct_anchors=len(all_codes),
        live=live,
        unresolved=len(all_codes) - live,
        anchors_in_roles=len(all_codes & role_codes),
        anchors_new=len(all_codes - role_codes),
        anchors_identity_mapped=identity,
        anchors_close_only=close,
        anchors_unmapped=unmapped,
        cde_coverage=round(covered_cdes / n, 4) if n else 0.0,
    )


async def _filter_scope(
    anchor_map: dict[tuple[str, str], CdeAnchors],
    client: OxigraphHttpClient,
) -> tuple[dict[tuple[str, str], CdeAnchors], frozenset[str]]:
    all_codes = frozenset(c for cde in anchor_map.values() for c in cde.codes)
    keep = await filter_in_scope(all_codes, client)
    filtered = {k: v for k, v in anchor_map.items() if v.codes <= keep}
    scoped_codes = frozenset(c for cde in filtered.values() for c in cde.codes)
    return filtered, scoped_codes


async def generate_coverage_report(
    db_path: str | Path,
    store: XrefStore,
    client: OxigraphHttpClient,
    *,
    role_codes: frozenset[str],
    in_scope_only: bool = True,
) -> CoverageReport:
    anchor_map = cde_anchor_map(db_path)
    if in_scope_only:
        anchor_map, _ = await _filter_scope(anchor_map, client)
    all_codes = frozenset(c for cde in anchor_map.values() for c in cde.codes)
    live_status = await check_liveness(all_codes, client)
    strength = await store.mapping_strength_by_subject()
    return build_coverage_report(
        anchor_map,
        live_status=live_status,
        strength_by_subject=strength,
        role_codes=role_codes,
    )
