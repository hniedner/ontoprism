"""Source-bound Uberon/CL full-text cache contracts."""

from types import SimpleNamespace
from typing import Any

import pytest

from ontolib.terminologies.uberon.search_index import (
    UberonSearchIndex,
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


class _Transaction:
    async def __aenter__(self) -> "_Transaction":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _Session:
    def __init__(self, factory: "_Factory") -> None:
        self.factory = factory

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    def begin(self) -> _Transaction:
        return _Transaction()

    async def execute(self, sql: Any, params: Any = None) -> _Result:
        statement = str(sql)
        self.factory.executed.append((statement, params))
        for needle, result in self.factory.results.items():
            if needle in statement:
                return result
        return _Result()


class _Factory:
    def __init__(self, results: dict[str, _Result]) -> None:
        self.results = results
        self.executed: list[tuple[str, Any]] = []

    def __call__(self) -> _Session:
        return _Session(self)


@pytest.mark.unit
async def test_ready_requires_matching_identity_and_complete_nonempty_rows() -> None:
    factory = _Factory({"EXISTS": _Result(scalar=True)})

    assert await UberonSearchIndex(factory).is_populated(  # type: ignore[arg-type]
        "a" * 64, "b" * 64
    )

    sql, params = factory.executed[0]
    assert "row_count > 0" in sql
    assert "row_count = (SELECT COUNT(*) FROM uberon_search)" in sql
    assert params == {"source_identity": "a" * 64, "source_hash": "b" * 64}


@pytest.mark.unit
async def test_search_filters_source_before_pagination() -> None:
    factory = _Factory(
        {
            "SELECT COUNT(*)": _Result(scalar=1),
            "FROM uberon_search": _Result(
                rows=[
                    SimpleNamespace(
                        code="CL:0000000", source="cl", label="cell", total=1
                    )
                ]
            ),
        }
    )

    page = await UberonSearchIndex(factory).search(  # type: ignore[arg-type]
        "cell", source="cl", limit=10, offset=20
    )

    assert page.hits[0].source == "cl"
    sql, params = factory.executed[1]
    assert sql.index("source = CAST(:source AS text)") < sql.index("LIMIT :limit")
    assert params == {"q": "cell", "source": "cl", "limit": 10, "offset": 20}


@pytest.mark.unit
async def test_search_preserves_total_when_offset_page_is_empty() -> None:
    factory = _Factory(
        {"SELECT COUNT(*)": _Result(scalar=2), "FROM uberon_search": _Result(rows=[])}
    )

    page = await UberonSearchIndex(factory).search(  # type: ignore[arg-type]
        "cell", offset=100
    )

    assert page.total == 2
    assert page.hits == []


async def _batches():
    yield [
        {
            "code": "UBERON:0002048",
            "source": "uberon",
            "label": "lung",
            "synonyms": "",
        },
        {"code": "CL:0000000", "source": "cl", "label": "cell", "synonyms": ""},
    ]


@pytest.mark.unit
async def test_rebuild_publishes_rows_and_manifest_in_one_transaction() -> None:
    factory = _Factory({})

    count = await UberonSearchIndex(factory).rebuild(  # type: ignore[arg-type]
        _batches(), source_identity="a" * 64, source_hash="b" * 64
    )

    assert count == 2
    statements = [statement for statement, _params in factory.executed]
    assert "DELETE FROM uberon_search_manifest" in statements[0]
    assert "DELETE FROM uberon_search" in statements[1]
    assert "INSERT INTO uberon_search" in statements[2]
    assert "INSERT INTO uberon_search_manifest" in statements[3]


@pytest.mark.unit
async def test_rebuild_validates_source_before_manifest_publication() -> None:
    factory = _Factory({})
    observed_statement_counts: list[int] = []

    async def validate_source() -> None:
        observed_statement_counts.append(len(factory.executed))

    await UberonSearchIndex(factory).rebuild(  # type: ignore[arg-type]
        _batches(),
        source_identity="a" * 64,
        source_hash="b" * 64,
        validate_source=validate_source,
    )

    assert observed_statement_counts == [3]
    assert "INSERT INTO uberon_search_manifest" in factory.executed[3][0]


@pytest.mark.unit
async def test_populate_pages_store_until_empty() -> None:
    class _Store:
        def __init__(self) -> None:
            self.calls: list[tuple[int, int]] = []

        async def search_records(
            self, *, limit: int, offset: int
        ) -> list[dict[str, str | None]]:
            self.calls.append((limit, offset))
            if offset:
                return []
            return [
                {
                    "code": "CL:0000000",
                    "source": "cl",
                    "label": "cell",
                    "synonyms": "",
                }
            ]

    store = _Store()
    factory = _Factory({})

    count = await populate_from_store(  # type: ignore[arg-type]
        store,
        UberonSearchIndex(factory),  # type: ignore[arg-type]
        source_identity="a" * 64,
        source_hash="b" * 64,
        batch_size=100,
    )

    assert count == 1
    assert store.calls == [(100, 0), (100, 100)]
