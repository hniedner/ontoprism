#!/usr/bin/env python
"""Seed the CI integration services with a minimal deterministic fixture.

- Loads the hand-authored NCIt Turtle fixture into the Oxigraph default graph via the
  SPARQL Graph Store Protocol.
- Creates a tiny caDSR SQLite DB at CADSR_DB_PATH so the app starts (the CDE-join
  integration test builds its own temp DB via the conftest fixture).

Postgres schema is provisioned separately by `pdm run migrate` in the CI job. Reads
NCIT_SPARQL_URL and CADSR_DB_PATH from the environment (same names the app uses).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import httpx

_FIXTURE = Path(__file__).parent / "fixtures" / "ncit-fixture.ttl"


def load_oxigraph(url: str, ttl_path: Path) -> None:
    """PUT the Turtle fixture into the store's default graph (replacing it)."""
    data = ttl_path.read_bytes()
    resp = httpx.put(
        f"{url.rstrip('/')}/store?default",
        content=data,
        headers={"Content-Type": "text/turtle"},
        timeout=60.0,
    )
    resp.raise_for_status()
    # Confirm the load: the fixture has 11 owl:Class concepts.
    owl_class = "<http://www.w3.org/2002/07/owl#Class>"
    count = httpx.post(
        f"{url.rstrip('/')}/query",
        content=f"SELECT (COUNT(*) AS ?n) WHERE {{ ?c a {owl_class} }}".encode(),
        headers={
            "Content-Type": "application/sparql-query",
            "Accept": "application/sparql-results+json",
        },
        timeout=30.0,
    )
    count.raise_for_status()
    n = count.json()["results"]["bindings"][0]["n"]["value"]
    print(f"Oxigraph seeded: {n} owl:Class concepts loaded from {ttl_path.name}")


def build_cadsr_db(db_path: Path) -> None:
    """Create the (empty) caDSR schema so CdeRepository/app startup succeeds."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            "CREATE TABLE cdes (public_id TEXT, version TEXT, short_name TEXT, "
            "long_name TEXT, definition TEXT, context TEXT, workflow_status TEXT, "
            "registration_status TEXT, datatype TEXT, value_domain_type TEXT, "
            "search_text TEXT, cde_json TEXT, PRIMARY KEY (public_id, version));"
            "CREATE TABLE cde_concepts (concept_code TEXT, concept_name TEXT, "
            "public_id TEXT, version TEXT, concept_type TEXT, is_primary INTEGER, "
            "hierarchy_depth INTEGER, is_leaf INTEGER);"
            "CREATE INDEX idx_concept_code ON cde_concepts(concept_code);"
        )
        conn.commit()
    finally:
        conn.close()
    print(f"caDSR schema created at {db_path}")


def main() -> int:
    ncit_url = os.environ.get("NCIT_SPARQL_URL", "http://localhost:7888")
    cadsr_db = Path(os.environ.get("CADSR_DB_PATH", "data/cadsr/cde_repository.db"))
    load_oxigraph(ncit_url, _FIXTURE)
    build_cadsr_db(cadsr_db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
