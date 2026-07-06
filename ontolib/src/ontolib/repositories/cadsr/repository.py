"""caDSR CDE read model over the SQLite repository DB (read-only).

The DB is the one built by fairdata's caDSR pipeline: a ``cdes`` table (with the full
``cde_json``) and a ``cde_concepts`` table linking each CDE to NCIt concept codes —
the shared identity that joins caDSR to the NCIt graph.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

from ontolib.repositories.cadsr.models import (
    CdeDetail,
    CdeSearchPage,
    CdeSummary,
    ConceptLink,
    PermissibleValue,
)

_SUMMARY_COLS = "public_id, version, short_name, long_name, context, datatype"
# Same columns qualified with the table name, for the FTS join (both cdes and cdes_fts
# expose short_name/long_name/definition, so unqualified names are ambiguous there).
_SUMMARY_COLS_Q = ", ".join(f"cdes.{c}" for c in _SUMMARY_COLS.split(", "))
# FTS5 special characters we strip from user tokens before quoting them (quoting each
# token as a phrase both AND-combines them and neutralizes operator syntax).
_FTS_STRIP = str.maketrans(dict.fromkeys('"*():^-', " "))


def _fts_match_query(query: str) -> str:
    """Turn a user query into a safe FTS5 MATCH string (quoted AND-ed prefix tokens)."""
    tokens = query.translate(_FTS_STRIP).split()
    # Prefix-match each token so "tumo" finds "tumor" (mirrors the old substring feel).
    return " ".join(f'"{t}"*' for t in tokens)


def _has_cdes_fts(conn: sqlite3.Connection) -> bool:
    """True if the DB has the ``cdes_fts`` FTS5 index (fairdata-built DBs do)."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cdes_fts'"
    ).fetchone()
    return row is not None


class CdeRepository:
    """Read-only caDSR CDE repository backed by SQLite."""

    def __init__(self, db_path: str | Path) -> None:
        """Wrap the caDSR SQLite DB at *db_path* (opened read-only per query)."""
        self._path = Path(db_path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # Read-only URI connection; a fresh handle per call keeps this thread-safe
        # under FastAPI's threadpool. Opening is cheap (no full-file read). Closed on
        # exit — sqlite3's own context manager commits but does not close.
        conn = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def get_cde(self, public_id: str, version: str | None = None) -> CdeDetail | None:
        """Return a CDE by id (latest version when *version* is omitted)."""
        with self._connect() as conn:
            if version is not None:
                row = conn.execute(
                    "SELECT cde_json FROM cdes WHERE public_id = ? AND version = ?",
                    (public_id, version),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT cde_json FROM cdes WHERE public_id = ? "
                    "ORDER BY CAST(version AS REAL) DESC LIMIT 1",
                    (public_id,),
                ).fetchone()
            if row is None:
                return None
            data = json.loads(row["cde_json"])
            concepts = self._concepts_for(conn, public_id, data["version"])
        return _to_detail(data, concepts)

    def search(self, query: str, *, limit: int = 25, offset: int = 0) -> CdeSearchPage:
        """Search CDE short/long name and definition.

        Uses the ``cdes_fts`` FTS5 index (single windowed query, no leading-wildcard
        scan) when present; falls back to a ``LIKE`` scan for DBs without the index
        (e.g. minimal test fixtures).
        """
        with self._connect() as conn:
            if _has_cdes_fts(conn):
                return self._search_fts(conn, query, limit=limit, offset=offset)
            return self._search_like(conn, query, limit=limit, offset=offset)

    def _search_fts(
        self, conn: sqlite3.Connection, query: str, *, limit: int, offset: int
    ) -> CdeSearchPage:
        match = _fts_match_query(query)
        if not match:  # query was all punctuation/empty → no matches
            return CdeSearchPage(query=query, total=0, limit=limit, offset=offset)
        # COUNT(*) OVER () yields the full match total in every row — one query, and the
        # match uses the FTS index rather than a full table scan.
        # Order by name (deterministic): bm25() relevance ranking can't be combined
        # with the COUNT(*) OVER () window in one statement.
        rows = conn.execute(
            f"SELECT {_SUMMARY_COLS_Q}, COUNT(*) OVER () AS _total "  # noqa: S608
            "FROM cdes JOIN cdes_fts ON cdes_fts.rowid = cdes.rowid "
            "WHERE cdes_fts MATCH ? ORDER BY cdes.long_name LIMIT ? OFFSET ?",
            (match, limit, offset),
        ).fetchall()
        total = rows[0]["_total"] if rows else 0
        return CdeSearchPage(
            query=query,
            total=total,
            limit=limit,
            offset=offset,
            hits=[_to_summary(r) for r in rows],
        )

    def _search_like(
        self, conn: sqlite3.Connection, query: str, *, limit: int, offset: int
    ) -> CdeSearchPage:
        like = f"%{query}%"
        where = "long_name LIKE ? OR short_name LIKE ? OR definition LIKE ?"
        params = (like, like, like)
        # S608 noqa: the interpolated parts (`where`, `_SUMMARY_COLS`) are module
        # constants; all user values are bound parameters.
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM cdes WHERE {where}",  # noqa: S608
            params,
        ).fetchone()["n"]
        rows = conn.execute(
            f"SELECT {_SUMMARY_COLS} FROM cdes WHERE {where} "  # noqa: S608
            "ORDER BY long_name LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return CdeSearchPage(
            query=query,
            total=total,
            limit=limit,
            offset=offset,
            hits=[_to_summary(r) for r in rows],
        )

    def find_cdes_by_concept(
        self, concept_code: str, *, limit: int = 50
    ) -> list[CdeSummary]:
        """Return CDEs linked to an NCIt *concept_code* (the caDSR↔NCIt join)."""
        cols = ", ".join(f"c.{c}" for c in _SUMMARY_COLS.split(", "))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT {cols}
                FROM cde_concepts cc
                JOIN cdes c ON cc.public_id = c.public_id AND cc.version = c.version
                WHERE cc.concept_code = ?
                ORDER BY c.long_name
                LIMIT ?
                """,  # noqa: S608 — `cols` is derived from a module constant
                (concept_code, limit),
            ).fetchall()
        return [_to_summary(r) for r in rows]

    def count(self) -> int:
        """Total number of CDE rows (used by the refresh/status report)."""
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM cdes").fetchone()["n"]

    def list_cdes(self, *, limit: int = 25, offset: int = 0) -> CdeSearchPage:
        """List all CDEs in natural (public_id) order — the no-search browse mode."""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) AS n FROM cdes").fetchone()["n"]
            rows = conn.execute(
                f"SELECT {_SUMMARY_COLS} FROM cdes "  # noqa: S608 — module constant
                "ORDER BY CAST(public_id AS INTEGER), version LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return CdeSearchPage(
            query="",
            total=total,
            limit=limit,
            offset=offset,
            hits=[_to_summary(r) for r in rows],
        )

    def summaries_for(self, doc_ids: list[str]) -> dict[str, CdeSummary]:
        """Map ``{public_id}:{version}`` doc_ids to CDE summaries (one query)."""
        if not doc_ids:
            return {}
        placeholders = ", ".join("?" for _ in doc_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_SUMMARY_COLS} FROM cdes "  # noqa: S608 — placeholders only
                f"WHERE public_id || ':' || version IN ({placeholders})",
                doc_ids,
            ).fetchall()
        return {f"{r['public_id']}:{r['version']}": _to_summary(r) for r in rows}

    def _concepts_for(
        self, conn: sqlite3.Connection, public_id: str, version: str
    ) -> list[ConceptLink]:
        rows = conn.execute(
            "SELECT concept_code, concept_name, concept_type, is_primary "
            "FROM cde_concepts WHERE public_id = ? AND version = ?",
            (public_id, version),
        ).fetchall()
        return [
            ConceptLink(
                concept_code=r["concept_code"],
                concept_name=r["concept_name"],
                concept_type=r["concept_type"],
                is_primary=bool(r["is_primary"]),
            )
            for r in rows
        ]


def _to_summary(row: sqlite3.Row) -> CdeSummary:
    return CdeSummary(
        public_id=row["public_id"],
        version=row["version"],
        short_name=row["short_name"],
        long_name=row["long_name"],
        context=row["context"],
        datatype=row["datatype"],
    )


def _to_detail(data: dict[str, Any], concepts: list[ConceptLink]) -> CdeDetail:
    pvs = [
        PermissibleValue(
            value=pv.get("value", ""),
            meaning=pv.get("meaning"),
            meaning_code=pv.get("meaning_code"),
        )
        for pv in data.get("permissible_values", [])
    ]
    return CdeDetail(
        public_id=data["public_id"],
        version=data["version"],
        short_name=data.get("short_name", ""),
        long_name=data.get("long_name", ""),
        context=data.get("context"),
        datatype=data.get("datatype"),
        definition=data.get("definition"),
        workflow_status=data.get("workflow_status"),
        registration_status=data.get("registration_status"),
        value_domain_type=data.get("value_domain_type"),
        permissible_values=pvs,
        concepts=concepts,
    )
