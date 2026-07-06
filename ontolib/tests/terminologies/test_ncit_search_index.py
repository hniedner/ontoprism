"""Hermetic tests for the NCIt FTS cache (fake async session, no real Postgres).

The live-Postgres variants are in ``backend`` integration tests; here we pin the
SQL contract and behaviour: counts/probes coerce correctly, search binds q/limit/
offset and maps rows to hits, and rebuild is a single DELETE+insert transaction that
skips empty batches. ``populate_from_store`` is checked to page the store and feed
rebuild.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from ontolib.terminologies.ncit.search_index import (
    NcitSearchIndex,
    populate_from_store,
)


class _Result:
    def __init__(self, *, scalar: Any = None, rows: list[Any] | None = None) -> None:
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one(self) -> Any:
        return self._scalar

    def all(self) -> list[Any]:
        return self._rows


class _Begin:
    async def __aenter__(self) -> "_Begin":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _Session:
    def __init__(self, factory: "_SessionFactory") -> None:
        self._factory = factory

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    def begin(self) -> _Begin:
        return _Begin()

    async def execute(self, sql: Any, params: Any = None) -> _Result:
        self._factory.executed.append((str(sql).strip(), params))
        return self._factory.result_for(str(sql))


class _SessionFactory:
    """Serves canned results keyed by a substring of the SQL, and records executes."""

    def __init__(self, results: dict[str, _Result]) -> None:
        self._results = results
        self.executed: list[tuple[str, Any]] = []

    def result_for(self, sql: str) -> _Result:
        for needle, res in self._results.items():
            if needle in sql:
                return res
        return _Result()

    def __call__(self) -> _Session:
        return _Session(self)


@pytest.mark.unit
async def test_count_coerces_scalar_to_int() -> None:
    sf = _SessionFactory({"COUNT(*)": _Result(scalar=42)})
    assert await NcitSearchIndex(sf).count() == 42  # type: ignore[arg-type]


@pytest.mark.unit
async def test_is_populated_reflects_existence_probe() -> None:
    assert await NcitSearchIndex(  # type: ignore[arg-type]
        _SessionFactory({"EXISTS": _Result(scalar=True)})
    ).is_populated()
    assert not await NcitSearchIndex(  # type: ignore[arg-type]
        _SessionFactory({"EXISTS": _Result(scalar=False)})
    ).is_populated()


@pytest.mark.unit
async def test_search_maps_rows_and_binds_params() -> None:
    rows = [
        SimpleNamespace(code="C3262", label="Neoplasm", semantic_type="Neo", total=2),
        SimpleNamespace(code="C9305", label="Malignant", semantic_type=None, total=2),
    ]
    sf = _SessionFactory({"ncit_search": _Result(rows=rows)})
    page = await NcitSearchIndex(sf).search("tumor", limit=10, offset=5)  # type: ignore[arg-type]

    assert page.total == 2
    assert [h.code for h in page.hits] == ["C3262", "C9305"]
    assert page.limit == 10
    _sql, params = sf.executed[0]
    assert params == {"q": "tumor", "limit": 10, "offset": 5}


@pytest.mark.unit
async def test_search_empty_result_is_zero_total() -> None:
    sf = _SessionFactory({"ncit_search": _Result(rows=[])})
    page = await NcitSearchIndex(sf).search("nothing")  # type: ignore[arg-type]
    assert page.total == 0
    assert page.hits == []


async def _batches(
    *chunks: list[dict[str, str | None]],
) -> Any:
    for chunk in chunks:
        yield chunk


@pytest.mark.unit
async def test_rebuild_deletes_then_inserts_nonempty_batches() -> None:
    sf = _SessionFactory({})
    index = NcitSearchIndex(sf)  # type: ignore[arg-type]

    total = await index.rebuild(
        _batches(
            [{"code": "C1", "label": "a", "semantic_type": None, "synonyms": None}],
            [],  # empty batch is skipped, not inserted
            [
                {"code": "C2", "label": "b", "semantic_type": None, "synonyms": None},
                {"code": "C3", "label": "c", "semantic_type": None, "synonyms": None},
            ],
        )
    )

    assert total == 3
    statements = [sql for sql, _ in sf.executed]
    # First statement clears the cache; only the two non-empty batches insert.
    assert "DELETE FROM ncit_search" in statements[0]
    inserts = [p for sql, p in sf.executed if "INSERT INTO ncit_search" in sql]
    assert [len(p) for p in inserts] == [1, 2]


class _FakeStore:
    def __init__(self, records: list[dict[str, str | None]]) -> None:
        self._records = records
        self.pages: list[tuple[int, int]] = []

    async def search_records(
        self, *, limit: int, offset: int
    ) -> list[dict[str, str | None]]:
        self.pages.append((limit, offset))
        return self._records[offset : offset + limit]


@pytest.mark.unit
async def test_populate_from_store_pages_and_feeds_rebuild() -> None:
    records = [
        {"code": f"C{i}", "label": f"n{i}", "semantic_type": None, "synonyms": None}
        for i in range(3)
    ]
    store = _FakeStore(records)
    sf = _SessionFactory({})
    index = NcitSearchIndex(sf)  # type: ignore[arg-type]

    total = await populate_from_store(store, index, batch_size=2)  # type: ignore[arg-type]

    assert total == 3
    # Paged 0, 2, then 4 (empty) -> stop.
    assert store.pages == [(2, 0), (2, 2), (2, 4)]
    inserts = [p for sql, p in sf.executed if "INSERT INTO ncit_search" in sql]
    assert [len(p) for p in inserts] == [2, 1]
