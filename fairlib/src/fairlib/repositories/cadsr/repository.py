"""caDSR CDE read model over the SQLite repository DB (read-only).

The DB is the one built by fairdata's caDSR pipeline: a ``cdes`` table (with the full
``cde_json``) and a ``cde_concepts`` table linking each CDE to NCIt concept codes —
the shared identity that joins caDSR to the NCIt graph.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from fairlib.repositories.cadsr.models import (
    CdeDetail,
    CdeSearchPage,
    CdeSummary,
    ConceptLink,
    PermissibleValue,
)

_SUMMARY_COLS = "public_id, version, short_name, long_name, context, datatype"


class CdeRepository:
    """Read-only caDSR CDE repository backed by SQLite."""

    def __init__(self, db_path: str | Path) -> None:
        """Wrap the caDSR SQLite DB at *db_path* (opened read-only per query)."""
        self._path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        # Read-only URI connection; a fresh handle per call keeps this thread-safe
        # under FastAPI's threadpool. Opening is cheap (no full-file read).
        conn = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

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
        """Substring search over CDE short/long name and definition."""
        like = f"%{query}%"
        where = "long_name LIKE ? OR short_name LIKE ? OR definition LIKE ?"
        params = (like, like, like)
        # S608 noqa: the interpolated parts (`where`, `_SUMMARY_COLS`) are module
        # constants; all user values are bound parameters.
        with self._connect() as conn:
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
