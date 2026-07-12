"""Tests for caDSR anchor enumeration (PR-A2 / issue #74).

TDD: these tests are written BEFORE the production code. Every test must fail
for the right reason when run against a non-existent implementation.
"""

from __future__ import annotations

import os
import sqlite3
from typing import TYPE_CHECKING

import httpx
import pytest

from ontolib.repositories.xref.cadsr_anchors import (
    check_liveness,
    enumerate_anchors,
    filter_in_scope,
    overlap_with_roles,
)
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers — ncxt fixture replacement (follows pattern from
# test_additivity_integration.py, which lives in the same testdir and cannot
# use the ``ncit_url`` fixture from ``tests/terminologies/conftest.py``).
# ---------------------------------------------------------------------------

_DEFAULT_NCIT_URL = "http://localhost:7888"


def _ncit_url() -> str:
    url = os.environ.get("NCIT_SPARQL_URL", _DEFAULT_NCIT_URL)
    try:
        resp = httpx.post(
            f"{url.rstrip('/')}/query",
            content=b"ASK {}",
            headers={
                "Content-Type": "application/sparql-query",
                "Accept": "application/sparql-results+json",
            },
            timeout=2.0,
        )
    except httpx.HTTPError:
        pytest.skip(f"NCIt Oxigraph not reachable at {url}")
        raise
    if resp.status_code != 200:
        pytest.skip(f"NCIt Oxigraph not reachable at {url}")
    return url


# ---------------------------------------------------------------------------
# Fixtures — real-file SQLite DBs for unit tests (``:memory:`` is unusable
# with URI-mode ``?mode=ro`` because the opener gets a *private* in-memory DB).
# ---------------------------------------------------------------------------


_SCHEMA = (
    "CREATE TABLE cde_concepts ("
    "  concept_code TEXT NOT NULL, concept_name TEXT NOT NULL,"
    "  public_id TEXT NOT NULL, version TEXT NOT NULL,"
    "  concept_type TEXT, is_primary INTEGER"
    ");"
)


def _build_db(tmp_path: Path, rows: list[tuple]) -> str:
    """Create a real SQLite DB at a temp path, fill it, return the path string."""
    path = tmp_path / "cadsr_anchors.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.executemany(
            "INSERT INTO cde_concepts (concept_code, concept_name, public_id, "
            "version, concept_type, is_primary) VALUES (?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    return str(path)


@pytest.fixture
def db_all_types(tmp_path: Path) -> str:
    """SQLite DB with rows spanning all four concept_type values."""
    return _build_db(
        tmp_path,
        [
            ("C3262", "Neoplasm", "100", "1.0", "object_class", 1),
            ("C2991", "Disease or Disorder", "101", "1.0", "object_class", 1),
            ("C16358", "Histology", "100", "1.0", "property", 0),
            ("C25365", "Grade", "102", "1.0", "property", 1),
            ("C25629", "Neoplasm Grade", "103", "1.0", "representation", 1),
            ("C25776", "Liver Morphology", "104", "1.0", "representation", 1),
            ("C2916", "Carcinoma", "100", "1.0", "value_meaning", 0),
            ("C36122", "Adenocarcinoma", "105", "1.0", "value_meaning", 0),
        ],
    )


@pytest.fixture
def db_post_coordinated(tmp_path: Path) -> str:
    """SQLite DB where one CDE (2003771) has 2 concept rows."""
    return _build_db(
        tmp_path,
        [
            ("C25150", "Age", "2003771", "1.0", "property", 1),
            ("C25230", "Age Range", "2003771", "1.0", "property", 0),
            ("C3262", "Neoplasm", "100", "2.0", "object_class", 1),
        ],
    )


@pytest.fixture
def db_tiny(tmp_path: Path) -> str:
    """Minimal SQLite DB for the overlap test."""
    return _build_db(
        tmp_path,
        [
            ("C3262", "Neoplasm", "100", "1.0", "object_class", 1),
            ("C16358", "Histology", "100", "1.0", "property", 0),
            ("C2916", "Carcinoma", "100", "1.0", "value_meaning", 0),
            ("C25150", "Age", "2003771", "1.0", "property", 1),
        ],
    )


# ---------------------------------------------------------------------------
# Unit tests  (A2.1 — enumeration)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_enumeration_counts_are_deterministic(db_all_types: str) -> None:
    """All four concept_type values are counted correctly."""
    result = enumerate_anchors(db_all_types)

    assert result.by_type == {
        "object_class": 2,
        "property": 2,
        "representation": 2,
        "value_meaning": 2,
    }
    assert result.total_distinct == 8
    assert result.all_codes == frozenset(
        {"C3262", "C2991", "C16358", "C25365", "C25629", "C25776", "C2916", "C36122"}
    )


@pytest.mark.unit
def test_post_coordinated_cde_counted_whole(db_post_coordinated: str) -> None:
    """A multi-concept CDE contributes both codes to the distinct count."""
    result = enumerate_anchors(db_post_coordinated)

    assert result.total_distinct == 3
    assert result.by_type == {"property": 2, "object_class": 1}
    assert "C25150" in result.all_codes
    assert "C25230" in result.all_codes


@pytest.mark.unit
def test_overlap_with_roles(db_tiny: str) -> None:
    """Overlap between anchor codes and role codes is computed correctly."""
    result = enumerate_anchors(db_tiny)
    role_codes = frozenset({"C3262", "C12345"})

    in_both, cadsr_only, roles_only = overlap_with_roles(result.all_codes, role_codes)

    assert in_both == 1  # C3262
    assert cadsr_only == 3  # C16358, C2916, C25150
    assert roles_only == 1  # C12345


# ---------------------------------------------------------------------------
# Edge-case unit tests (empty inputs, null codes)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_filter_in_scope_empty_returns_empty() -> None:
    """Empty input yields empty output without any store query."""
    result = await filter_in_scope(frozenset(), None)  # type: ignore[arg-type]
    assert result == frozenset()


@pytest.mark.unit
async def test_check_liveness_empty_returns_empty() -> None:
    """Empty input yields empty dict without any store query."""
    result = await check_liveness(frozenset(), None)  # type: ignore[arg-type]
    assert result == {}


@pytest.mark.unit
def test_enumerate_accepts_file_uri_directly(tmp_path: Path) -> None:
    """A ``file:`` URI path (already prefixed) is accepted without double-wrapping."""
    path = tmp_path / "uri_test.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE cde_concepts ("
        "  concept_code TEXT, concept_name TEXT,"
        "  public_id TEXT, version TEXT,"
        "  concept_type TEXT, is_primary INTEGER"
        ");"
    )
    conn.execute(
        "INSERT INTO cde_concepts (concept_code, concept_name, public_id, "
        "version, concept_type, is_primary) VALUES ('C001','Test','1','1','property',1)"
    )
    conn.commit()
    conn.close()

    file_uri = f"file:{path}"
    result = enumerate_anchors(file_uri)
    assert result.total_distinct == 1


@pytest.mark.unit
def test_null_concept_code_ignored(tmp_path: Path) -> None:
    """A row with NULL concept_code is silently skipped."""
    path = tmp_path / "null_code.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE cde_concepts ("
        "  concept_code TEXT, concept_name TEXT,"
        "  public_id TEXT, version TEXT,"
        "  concept_type TEXT, is_primary INTEGER"
        ");"
    )
    conn.execute(
        "INSERT INTO cde_concepts (concept_code, concept_name, public_id, "
        "version, concept_type, is_primary) "
        "VALUES (NULL,'Null','1','1','object_class',1)"
    )
    conn.execute(
        "INSERT INTO cde_concepts (concept_code, concept_name, public_id, "
        "version, concept_type, is_primary) VALUES ('C001','Real','2','1','property',1)"
    )
    conn.commit()
    conn.close()

    result = enumerate_anchors(str(path))
    assert "C001" in result.all_codes
    assert result.total_distinct == 1


# ---------------------------------------------------------------------------
# Integration tests  (A2.2 — scope gate, A2.3 — liveness)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_in_scope_filter() -> None:
    """Only neoplasm descendant codes survive the scope gate.

    C3262 (Neoplasm) is the root — kept.
    C12345 is not a descendant of C3262 — dropped.
    """
    url = _ncit_url()
    codes = frozenset({"C3262", "C12345"})
    async with OxigraphHttpClient(url) as client:
        in_scope = await filter_in_scope(codes, client)

    assert "C3262" in in_scope
    assert "C12345" not in in_scope


@pytest.mark.integration
async def test_unresolved_code_reported_not_dropped() -> None:
    """A bogus code is flagged 'unresolved' but not dropped from the output."""
    url = _ncit_url()
    codes = frozenset({"C3262", "FOOBAR"})
    async with OxigraphHttpClient(url) as client:
        statuses = await check_liveness(codes, client)

    assert statuses["C3262"] == "live"
    assert statuses["FOOBAR"] == "unresolved"
    # All input codes must be present in the output
    assert set(statuses) == {"C3262", "FOOBAR"}
