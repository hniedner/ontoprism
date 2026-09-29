"""The real smoke refuses a broken search manifest on disposable services."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager

import pytest
from scripts.smoke_real import main
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine

pytestmark = [
    pytest.mark.integration,
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings", "isolated_qlever_settings"),
]


@pytest.mark.integration
async def test_real_smoke_fails_on_disposable_broken_search_manifest(
    isolated_qlever_url: str,
    integration_connection_scope: Callable[[str], AbstractContextManager[None]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("UBERON_SPARQL_URL", isolated_qlever_url)
    get_settings.cache_clear()
    engine = make_engine(get_settings().database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM ncit_search_manifest"))
            await connection.execute(
                text(
                    "INSERT INTO ncit_search_manifest "
                    "(singleton,source_identity,source_hash,row_count) "
                    "VALUES (true,:identity,:source_hash,1)"
                ),
                {"identity": "a" * 64, "source_hash": "b" * 64},
            )
        assert main(connection_scope=integration_connection_scope) != 0
        output = capsys.readouterr().out
        assert "GET /api/v1/ncit/list" in output
        assert "503" in output
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM ncit_search_manifest"))
        await dispose_engine(engine)
        get_settings.cache_clear()
