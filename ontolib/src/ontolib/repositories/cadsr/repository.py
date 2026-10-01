"""caDSR CDE read model over the generated SQLite repository DB (read-only)."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

from ontolib.common.grid import sqlite_grid_filters
from ontolib.repositories.cadsr.archive import CadsrSource
from ontolib.repositories.cadsr.models import (
    CdeDetail,
    CdeRepositorySort,
    CdeSearchPage,
    CdeSummary,
    ConceptLink,
    PermissibleValue,
)
from ontolib.repositories.embeddings.generate import cadsr_source_fingerprint


def _cde_order(sort: CdeRepositorySort, *, table: str = "") -> str:
    prefix = f"{table}." if table else ""
    return {
        "source": f"CAST({prefix}public_id AS INTEGER), {prefix}version",
        "public_id:asc": f"CAST({prefix}public_id AS INTEGER), {prefix}version",
        "public_id:desc": (
            f"CAST({prefix}public_id AS INTEGER) DESC, {prefix}version DESC"
        ),
        "name:asc": (
            f"{prefix}long_name IS NULL, {prefix}long_name COLLATE NOCASE, "
            f"CAST({prefix}public_id AS INTEGER), {prefix}version"
        ),
        "name:desc": (
            f"{prefix}long_name IS NULL, {prefix}long_name COLLATE NOCASE DESC, "
            f"CAST({prefix}public_id AS INTEGER), {prefix}version"
        ),
    }[sort]


_SUMMARY_COLS = (
    "public_id, version, short_name, long_name, context, datatype, "
    "workflow_status, registration_status, value_domain_type"
)
# Same columns qualified with the table name, for the FTS join (both cdes and cdes_fts
# expose short_name/long_name/definition, so unqualified names are ambiguous there).
_SUMMARY_COLS_Q = ", ".join(f"cdes.{c}" for c in _SUMMARY_COLS.split(", "))
# FTS5 special characters we strip from user tokens before quoting them (quoting each
# token as a phrase both AND-combines them and neutralizes operator syntax).
_FTS_STRIP = str.maketrans(dict.fromkeys('"*():^-', " "))
_CATEGORICAL_COLUMNS = {
    name: f"cdes.{name}"
    for name in (
        "value_domain_type",
        "workflow_status",
        "registration_status",
        "context",
        "datatype",
    )
}
_TEXT_EXPRESSIONS = {
    "public_id": "cdes.public_id || ' v' || cdes.version",
    "name": "COALESCE(cdes.long_name, '') || ' ' || COALESCE(cdes.short_name, '')",
    **_CATEGORICAL_COLUMNS,
}
_grid_filters = partial(
    sqlite_grid_filters,
    categorical_expressions=_CATEGORICAL_COLUMNS,
    text_expressions=_TEXT_EXPRESSIONS,
)


def _fts_match_query(query: str) -> str:
    """Turn a user query into a safe FTS5 MATCH string (quoted AND-ed prefix tokens)."""
    tokens = query.translate(_FTS_STRIP).split()
    # Prefix-match each token so "tumo" finds "tumor" (mirrors the old substring feel).
    return " ".join(f'"{t}"*' for t in tokens)


def _has_cdes_fts(conn: sqlite3.Connection) -> bool:
    """Return whether the DB exposes the ``cdes_fts`` FTS5 index."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cdes_fts'"
    ).fetchone()
    return row is not None


def _search_page(
    query: str,
    total: int,
    limit: int,
    offset: int,
    sort: CdeRepositorySort,
    filters: Mapping[str, Sequence[str]],
    column_text: Mapping[str, str],
    rows: Sequence[sqlite3.Row] = (),
) -> CdeSearchPage:
    return CdeSearchPage(
        query=query,
        total=total,
        limit=limit,
        offset=offset,
        sort=sort,
        filters={key: list(value) for key, value in filters.items()},
        column_text=dict(column_text),
        hits=[_to_summary(row) for row in rows],
    )


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

    def search(
        self,
        query: str,
        *,
        limit: int = 25,
        offset: int = 0,
        sort: CdeRepositorySort = "source",
        filters: Mapping[str, Sequence[str]] | None = None,
        column_text: Mapping[str, str] | None = None,
    ) -> CdeSearchPage:
        """Search CDE short/long name and definition.

        Uses the ``cdes_fts`` FTS5 index when present and otherwise uses the table's
        bounded ``LIKE`` search path.
        """
        applied_filters = filters or {}
        applied_text = column_text or {}
        with self._connect() as conn:
            search = self._search_fts if _has_cdes_fts(conn) else self._search_like
            return search(
                conn,
                query,
                limit=limit,
                offset=offset,
                sort=sort,
                filters=applied_filters,
                column_text=applied_text,
            )

    def _search_fts(
        self,
        conn: sqlite3.Connection,
        query: str,
        *,
        limit: int,
        offset: int,
        sort: CdeRepositorySort,
        filters: Mapping[str, Sequence[str]],
        column_text: Mapping[str, str],
    ) -> CdeSearchPage:
        match = _fts_match_query(query)
        if not match:  # query was all punctuation/empty → no matches
            return _search_page(query, 0, limit, offset, sort, filters, column_text)
        # Count separately so pages beyond the final hit retain the authoritative total.
        # Both bounded statements use the FTS index; the result order is deterministic.
        grid_where, grid_params = _grid_filters(filters, column_text)
        count_source = (
            "cdes JOIN cdes_fts ON cdes_fts.rowid = cdes.rowid"
            if grid_where
            else "cdes_fts"
        )
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM {count_source} "  # noqa: S608
            f"WHERE cdes_fts MATCH ?{grid_where}",
            (match, *grid_params),
        ).fetchone()["n"]
        # S608: `_cde_order` selects a fixed SQL fragment from the closed
        # CdeRepositorySort domain; the source query and page values remain bound.
        rows = conn.execute(
            f"SELECT {_SUMMARY_COLS_Q} "  # noqa: S608
            "FROM cdes JOIN cdes_fts ON cdes_fts.rowid = cdes.rowid "
            f"WHERE cdes_fts MATCH ?{grid_where} "
            f"ORDER BY {_cde_order(sort, table='cdes')} "
            "LIMIT ? OFFSET ?",
            (match, *grid_params, limit, offset),
        ).fetchall()
        return _search_page(
            query, total, limit, offset, sort, filters, column_text, rows
        )

    def _search_like(
        self,
        conn: sqlite3.Connection,
        query: str,
        *,
        limit: int,
        offset: int,
        sort: CdeRepositorySort,
        filters: Mapping[str, Sequence[str]],
        column_text: Mapping[str, str],
    ) -> CdeSearchPage:
        like = f"%{query}%"
        grid_where, grid_params = _grid_filters(filters, column_text)
        where = (
            "(cdes.long_name LIKE ? OR cdes.short_name LIKE ? "
            "OR cdes.definition LIKE ?)"
            f"{grid_where}"
        )
        params = (like, like, like, *grid_params)
        # S608 noqa: the interpolated parts (`where`, `_SUMMARY_COLS`) are module
        # constants; all user values are bound parameters.
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM cdes WHERE {where}",  # noqa: S608
            params,
        ).fetchone()["n"]
        # S608: `_cde_order` selects a fixed SQL fragment from the closed
        # CdeRepositorySort domain; the source query and page values remain bound.
        rows = conn.execute(
            f"SELECT {_SUMMARY_COLS_Q} FROM cdes WHERE {where} "  # noqa: S608
            f"ORDER BY {_cde_order(sort)} LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return _search_page(
            query, total, limit, offset, sort, filters, column_text, rows
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

    def find_cde_ids_by_concept(
        self, concept_code: str, *, limit: int = 50
    ) -> list[str]:
        """Return bounded CDE document IDs linked to an NCIt concept code."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT public_id, version FROM cde_concepts "
                "WHERE concept_code = ? ORDER BY public_id, version LIMIT ?",
                (concept_code, limit),
            ).fetchall()
        return [f"{row['public_id']}:{row['version']}" for row in rows]

    def count(self) -> int:
        """Total number of CDE rows (used by the refresh/status report)."""
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM cdes").fetchone()["n"]

    def source_provenance(self) -> CadsrSource:
        """Return the single authoritative archive record persisted by the builder."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT url, downloaded_at, etag, last_modified, archive_size, "
                "archive_sha256, member_count, member_names_sha256, "
                "first_member_timestamp, last_member_timestamp FROM cadsr_source"
            ).fetchall()
        if len(rows) != 1:
            raise sqlite3.OperationalError(
                "caDSR repository must contain exactly one source provenance row"
            )
        return CadsrSource(**dict(rows[0]))

    def certification(self) -> tuple[CadsrSource, int, str]:
        """Return persisted archive provenance and the exact serving-row identity."""
        source = self.source_provenance()
        item_count, fingerprint = cadsr_source_fingerprint(str(self._path))
        return source, item_count, fingerprint

    def certification_inputs(self) -> tuple[int, int, int, int]:
        """Return cheap file-generation inputs for worker-local certification reuse."""
        stat = self._path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns

    def list_cdes(
        self,
        *,
        limit: int = 25,
        offset: int = 0,
        sort: CdeRepositorySort = "source",
        filters: Mapping[str, Sequence[str]] | None = None,
        column_text: Mapping[str, str] | None = None,
    ) -> CdeSearchPage:
        """List all CDEs in the requested deterministic browse order."""
        applied_filters = filters or {}
        applied_text = column_text or {}
        with self._connect() as conn:
            grid_where, grid_params = _grid_filters(applied_filters, applied_text)
            total = conn.execute(
                f"SELECT COUNT(*) AS n FROM cdes WHERE 1=1{grid_where}",  # noqa: S608
                grid_params,
            ).fetchone()["n"]
            # S608: `_cde_order` selects a fixed SQL fragment from the closed
            # CdeRepositorySort domain; page values remain bound parameters.
            rows = conn.execute(
                f"SELECT {_SUMMARY_COLS} FROM cdes WHERE 1=1{grid_where} "  # noqa: S608
                f"ORDER BY {_cde_order(sort)} LIMIT ? OFFSET ?",
                (*grid_params, limit, offset),
            ).fetchall()
        return _search_page(
            "", total, limit, offset, sort, applied_filters, applied_text, rows
        )

    def filter_values(self, field: str) -> list[str]:
        """Return the active source spellings for one declared categorical field."""
        column = _CATEGORICAL_COLUMNS[field]
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT {column} AS value FROM cdes "  # noqa: S608
                f"WHERE {column} IS NOT NULL AND {column} != '' "
                "ORDER BY value COLLATE NOCASE, value"
            ).fetchall()
        return [row["value"] for row in rows]

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
        workflow_status=row["workflow_status"],
        registration_status=row["registration_status"],
        value_domain_type=row["value_domain_type"],
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
