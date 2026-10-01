"""Atomic source-bound publication shared by terminology search indexes."""

import re
from collections.abc import AsyncIterable, Awaitable, Callable, Mapping, Sequence
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_SHA256 = re.compile(r"[0-9a-f]{64}")


def require_digest(name: str, value: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


class SearchPublicationError(RuntimeError): ...


class SearchRecordStore(Protocol):
    async def search_records(
        self, *, limit: int, offset: int
    ) -> Sequence[Mapping[str, object]]: ...


class SearchIndexPublication:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        table: str,
        subject: str,
        upsert_sql: str,
        bind_source_hash: bool = False,
        record_key: str | None = None,
    ) -> None:
        if re.fullmatch(r"[a-z_]+", table) is None:
            raise ValueError("search publication table must be a SQL identifier")
        self._sf = session_factory
        self._table = table
        self._subject = subject
        self._upsert = upsert_sql
        self._bind_hash = bind_source_hash
        self._record_key = record_key

    async def is_populated(
        self, source_identity: str, source_hash: str | None = None
    ) -> bool:
        require_digest("source_identity", source_identity)
        params = {"source_identity": source_identity}
        hash_clause = ""
        if source_hash is not None and not self._bind_hash:
            raise ValueError(f"{self._subject} publication does not bind a source hash")
        if self._bind_hash:
            if source_hash is None:
                raise ValueError("source_hash is required")
            require_digest("source_hash", source_hash)
            params["source_hash"] = source_hash
            hash_clause = "AND manifest.source_hash = :source_hash"
        sql = f"""SELECT EXISTS(SELECT 1 FROM {self._table}_manifest manifest
            WHERE manifest.singleton = true
              AND manifest.source_identity = :source_identity {hash_clause}
              AND manifest.row_count > 0
              AND manifest.row_count = (SELECT COUNT(*) FROM {self._table}))"""  # noqa: S608
        async with self._sf() as session:
            return bool((await session.execute(text(sql), params)).scalar_one())

    async def rebuild[Record: Mapping[str, object]](
        self,
        batches: AsyncIterable[Sequence[Record]],
        *,
        source_identity: str,
        source_hash: str,
        validate_source: Callable[[], Awaitable[None]] | None = None,
        expected_row_count: int | None = None,
    ) -> int:
        require_digest("source_identity", source_identity)
        require_digest("source_hash", source_hash)
        total, seen = 0, set()
        async with self._sf() as session, session.begin():
            await session.execute(text(f"DELETE FROM {self._table}_manifest"))  # noqa: S608
            await session.execute(text(f"DELETE FROM {self._table}"))  # noqa: S608
            async for records in batches:
                if not records:
                    continue
                self._validate_record_keys(records, seen)
                await session.execute(text(self._upsert), list(records))
                total += len(records)
            await self._validate_counts(session, total, expected_row_count)
            if validate_source:
                await validate_source()
            manifest = text(
                f"""INSERT INTO {self._table}_manifest
                (singleton, source_identity, source_hash, row_count, built_at)
                VALUES (true, :source_identity, :source_hash, :row_count, now())"""  # noqa: S608
            )
            params = {
                "source_identity": source_identity,
                "source_hash": source_hash,
                "row_count": total,
            }
            await session.execute(manifest, params)
        return total

    def _validate_record_keys(
        self, records: Sequence[Mapping[str, object]], seen: set[object]
    ) -> None:
        if self._record_key is None:
            return
        keys = [record.get(self._record_key) for record in records]
        if None in keys or len(set(keys)) != len(keys) or not seen.isdisjoint(keys):
            raise SearchPublicationError(
                f"{self._subject} search source contains a missing or "
                f"duplicate {self._record_key}"
            )
        seen.update(keys)

    async def _validate_counts(
        self,
        session: AsyncSession,
        total: int,
        expected_row_count: int | None,
    ) -> None:
        if total <= 0:
            raise SearchPublicationError(
                f"{self._subject} search source produced no records"
            )
        if expected_row_count is not None and total != expected_row_count:
            raise SearchPublicationError(
                f"{self._subject} search row count differs from certified class count"
            )
        if self._record_key is None:
            return
        count_sql = text(f"SELECT COUNT(*) FROM {self._table}")  # noqa: S608
        stored = int((await session.execute(count_sql)).scalar_one())
        if stored != total:
            raise SearchPublicationError(
                f"{self._subject} stored search row count differs from source rows"
            )


async def populate_search_index(
    store: SearchRecordStore,
    index: SearchIndexPublication,
    *,
    source_identity: str,
    source_hash: str,
    batch_size: int = 5000,
    validate_source: Callable[[], Awaitable[None]] | None = None,
    expected_row_count: int | None = None,
) -> int:
    async def pages() -> AsyncIterable[Sequence[Mapping[str, object]]]:
        offset = 0
        while records := await store.search_records(limit=batch_size, offset=offset):
            yield records
            offset += batch_size

    return await index.rebuild(
        pages(),
        source_identity=source_identity,
        source_hash=source_hash,
        validate_source=validate_source,
        expected_row_count=expected_row_count,
    )
