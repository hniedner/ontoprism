"""Tests for the caDSR FTS5 search path (the production DB carries a cdes_fts index)."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from ontolib.repositories.cadsr.repository import CdeRepository

if TYPE_CHECKING:
    from pathlib import Path

_CDE_JSON = '{{"public_id": "{pid}", "version": "1.0", "short_name": "{sn}", '
_ROWS = [
    ("100", "1.0", "NEOPLASM_HIST", "Neoplasm Histology", "Histology of a neoplasm."),
    ("200", "1.0", "PT_AGE", "Patient Age", "Age of the patient at diagnosis."),
    ("300", "1.0", "TUMOR_STAGE", "Tumor Stage", "The stage of a tumor."),
]


@pytest.fixture
def fts_db(tmp_path: Path) -> Path:
    """A caDSR DB carrying the external-content cdes_fts FTS5 index (fairdata shape)."""
    path = tmp_path / "cde_fts.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            "CREATE TABLE cdes (public_id TEXT, version TEXT, short_name TEXT, "
            "long_name TEXT, definition TEXT, context TEXT, workflow_status TEXT, "
            "registration_status TEXT, datatype TEXT, value_domain_type TEXT, "
            "search_text TEXT, cde_json TEXT, PRIMARY KEY (public_id, version));"
            "CREATE VIRTUAL TABLE cdes_fts USING fts5(short_name, long_name, "
            "definition, search_text, content='cdes', content_rowid='rowid');"
        )
        conn.executemany(
            "INSERT INTO cdes (public_id, version, short_name, long_name, definition, "
            "search_text, cde_json) VALUES (?,?,?,?,?,?,?)",
            [(p, v, sn, ln, d, f"{sn} {ln} {d}", "{}") for p, v, sn, ln, d in _ROWS],
        )
        conn.execute("INSERT INTO cdes_fts(cdes_fts) VALUES ('rebuild')")
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.mark.unit
def test_fts_search_finds_by_token(fts_db: Path) -> None:
    page = CdeRepository(fts_db).search("neoplasm")
    assert page.total == 1
    assert page.hits[0].public_id == "100"


@pytest.mark.unit
def test_fts_search_is_prefix_matched(fts_db: Path) -> None:
    # "tumo" prefix-matches "Tumor".
    page = CdeRepository(fts_db).search("tumo")
    assert [h.public_id for h in page.hits] == ["300"]


@pytest.mark.unit
def test_fts_total_is_the_full_match_count_with_pagination(fts_db: Path) -> None:
    # "of" appears in all three definitions — a broad match — so a page of 2 still
    # reports the full total of 3 (via COUNT(*) OVER (), a single query).
    page = CdeRepository(fts_db).search("of", limit=2, offset=0)
    assert page.total == len(_ROWS)
    assert len(page.hits) == 2


@pytest.mark.unit
def test_fts_punctuation_only_query_returns_no_matches(fts_db: Path) -> None:
    page = CdeRepository(fts_db).search('""()*')
    assert page.total == 0
    assert page.hits == []
