"""Integration test: migration up+down for 0004_xref (issue #71).

Uses subprocess to run alembic in a separate process, avoiding event-loop conflicts
with the async test harness (alembic's sync connection tries to start its own loop)."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine


@pytest.mark.integration
async def test_migration_up_and_down_roundtrip() -> None:
    engine = make_engine(get_settings().database_url)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' "
                    "AND table_name IN ('xref_run', 'concept_xref')"
                )
            )
            assert {row[0] for row in rows} == {"xref_run", "concept_xref"}

        env = {**os.environ, "PYTHONPATH": "."}
        alembic = shutil.which("alembic") or "alembic"

        subprocess.run(  # noqa: ASYNC221, S603
            [alembic, "downgrade", "0003_decomposition"],
            capture_output=True,
            text=True,
            check=True,
            env=env,
            cwd=os.getcwd(),
        )

        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' "
                    "AND table_name IN ('xref_run', 'concept_xref')"
                )
            )
            tables = {row[0] for row in rows}
        assert "xref_run" not in tables
        assert "concept_xref" not in tables

        # note: check=True is fine; alembic path is from shutil.which
        subprocess.run(  # noqa: ASYNC221, S603
            [alembic, "upgrade", "head"],
            capture_output=True,
            text=True,
            check=True,
            env=env,
            cwd=os.getcwd(),
        )

        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' "
                    "AND table_name IN ('xref_run', 'concept_xref')"
                )
            )
            tables = {row[0] for row in rows}
        assert "xref_run" in tables
        assert "concept_xref" in tables
    finally:
        await dispose_engine(engine)
