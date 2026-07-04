"""Fixtures for caDSR repository tests: a real, tiny SQLite DB built in a temp dir.

Mirrors the fairdata-built schema (``cdes`` + ``cde_concepts``) with a couple of CDEs
so the read model runs against a genuine SQLite DB — no mocks, fully hermetic.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

_SCHEMA = """
CREATE TABLE cdes (
    public_id TEXT NOT NULL, version TEXT NOT NULL, short_name TEXT NOT NULL,
    long_name TEXT NOT NULL, definition TEXT NOT NULL, context TEXT,
    workflow_status TEXT, registration_status TEXT, datatype TEXT,
    value_domain_type TEXT, search_text TEXT, cde_json TEXT NOT NULL,
    PRIMARY KEY (public_id, version)
);
CREATE TABLE cde_concepts (
    concept_code TEXT NOT NULL, concept_name TEXT NOT NULL, public_id TEXT NOT NULL,
    version TEXT NOT NULL, concept_type TEXT, is_primary INTEGER,
    hierarchy_depth INTEGER, is_leaf INTEGER
);
CREATE INDEX idx_concept_code ON cde_concepts(concept_code);
"""


def _cde_row(
    pid: str, ver: str, short: str, long: str, ctx: str, dt: str, pvs: list[dict]
) -> tuple:
    payload = {
        "public_id": pid,
        "version": ver,
        "short_name": short,
        "long_name": long,
        "definition": f"Definition of {long}.",
        "context": ctx,
        "workflow_status": "RELEASED",
        "registration_status": "Standard",
        "datatype": dt,
        "value_domain_type": "Enumerated" if pvs else "NonEnumerated",
        "permissible_values": pvs,
    }
    return (
        pid,
        ver,
        short,
        long,
        payload["definition"],
        ctx,
        "RELEASED",
        "Standard",
        dt,
        payload["value_domain_type"],
        f"{short} {long}",
        json.dumps(payload),
    )


def build_cadsr_db(path: Path) -> None:
    """Create a small but real caDSR SQLite DB at *path*."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.executemany(
            "INSERT INTO cdes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                _cde_row("2003771", "1.0", "AGE", "Patient Age", "CTCAE", "NUMBER", []),
                _cde_row(
                    "100",
                    "2.0",
                    "NEOPLASM_HIST",
                    "Neoplasm Histology",
                    "caDSR",
                    "CHARACTER",
                    [
                        {
                            "value": "Carcinoma",
                            "meaning": "Carcinoma",
                            "meaning_code": "C2916",
                        }
                    ],
                ),
            ],
        )
        conn.executemany(
            "INSERT INTO cde_concepts (concept_code, concept_name, public_id, "
            "version, concept_type, is_primary) VALUES (?,?,?,?,?,?)",
            [
                ("C25150", "Age", "2003771", "1.0", "property", 1),
                ("C3262", "Neoplasm", "100", "2.0", "object_class", 1),
                ("C16358", "Histology", "100", "2.0", "property", 0),
            ],
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def cadsr_db_path(tmp_path: Path) -> Path:
    """Path to a freshly-built caDSR SQLite DB."""
    db = tmp_path / "cde_repository.db"
    build_cadsr_db(db)
    return db
