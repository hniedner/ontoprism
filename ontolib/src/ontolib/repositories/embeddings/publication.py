"""Validated, atomic publication of embedding corpora through PostgreSQL MVCC."""

from __future__ import annotations

import json
import math
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)
EmbeddingRow = tuple[str, list[float], dict[str, JsonValue]]
ManifestState = Literal["building", "failed", "complete"]
EMBEDDING_VECTOR_DIMENSION = 768


class Corpus(StrEnum):
    """Independently published embedding corpora."""

    NCIT = "ncit"
    CADSR = "cadsr"


_SERVING_TABLE = {
    Corpus.NCIT: "ncit_concepts",
    Corpus.CADSR: "cde_repository",
}
_SERVING_INDEX = {
    Corpus.NCIT: "idx_ncit_concepts_hnsw",
    Corpus.CADSR: "idx_cde_repository_hnsw",
}


class CorpusValidationError(ValueError):
    """A staged corpus is not complete enough to publish."""


class CorpusBuildStateError(RuntimeError):
    """A build operation is invalid for the manifest's current state."""


class CorpusUnavailableError(RuntimeError):
    """The corpus is uncertified or unusable for the requested similarity read."""


@dataclass(frozen=True, slots=True)
class CorpusBuild:
    """Immutable provenance and completeness contract for one candidate build."""

    build_id: UUID
    corpus: Corpus
    source_version: str
    source_hash: str
    model_id: str
    model_revision: str
    vector_dimension: int
    expected_row_count: int
    code_commit: str
    required_doc_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_build(self)


def _validate_build(build: CorpusBuild) -> None:
    _validate_provenance(build)
    if build.vector_dimension != EMBEDDING_VECTOR_DIMENSION:
        raise ValueError("vector_dimension must match the physical vector(768)")
    if build.expected_row_count <= 0:
        raise ValueError("expected_row_count must be positive")
    if not build.required_doc_ids or any(not value for value in build.required_doc_ids):
        raise ValueError("required_doc_ids must contain non-empty sentinels")
    if len(build.required_doc_ids) != len(set(build.required_doc_ids)):
        raise ValueError("required_doc_ids must be unique")


def _validate_provenance(build: CorpusBuild) -> None:
    values = (
        build.source_version,
        build.source_hash,
        build.model_id,
        build.model_revision,
        build.code_commit,
    )
    if not all(value.strip() for value in values):
        raise ValueError("embedding build provenance fields must be non-empty")


def _validate_batch(build: CorpusBuild, rows: Sequence[EmbeddingRow]) -> None:
    if len({doc_id for doc_id, _, _ in rows}) != len(rows):
        raise CorpusValidationError("embedding batch contains duplicate doc_ids")
    for doc_id, vector, _ in rows:
        if not doc_id:
            raise CorpusValidationError("embedding doc_id must be non-empty")
        _validate_vector(doc_id, vector, build.vector_dimension)


def _validate_vector(doc_id: str, vector: list[float], dimension: int) -> None:
    if len(vector) != dimension:
        raise CorpusValidationError(
            f"{doc_id} has dimension {len(vector)}, expected {dimension}"
        )
    if not all(math.isfinite(value) for value in vector):
        raise CorpusValidationError(f"{doc_id} contains non-finite vector values")
    if not any(value != 0.0 for value in vector):
        raise CorpusValidationError(f"{doc_id} has a zero-norm vector")


@dataclass(frozen=True, slots=True)
class CorpusManifest:
    """Persisted lifecycle and evidence for one embedding corpus build."""

    build_id: UUID
    corpus: Corpus
    state: ManifestState
    is_active: bool
    source_version: str
    source_hash: str
    model_id: str
    model_revision: str
    vector_dimension: int
    expected_row_count: int
    actual_row_count: int | None
    code_commit: str
    required_doc_ids: tuple[str, ...]
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None

    def __post_init__(self) -> None:
        validators = {
            "building": _validate_building_manifest,
            "failed": _validate_failed_manifest,
            "complete": _validate_complete_manifest,
        }
        validators[self.state](self)


def _validate_building_manifest(manifest: CorpusManifest) -> None:
    terminal = (
        manifest.is_active,
        manifest.actual_row_count,
        manifest.completed_at,
        manifest.error_message,
    )
    if any(value is not None and value is not False for value in terminal):
        raise ValueError("building manifest contains terminal evidence")


def _validate_failed_manifest(manifest: CorpusManifest) -> None:
    if manifest.is_active or manifest.actual_row_count is not None:
        raise ValueError("failed manifest has invalid lifecycle evidence")
    if manifest.completed_at is not None or not (manifest.error_message or "").strip():
        raise ValueError("failed manifest has invalid lifecycle evidence")


def _validate_complete_manifest(manifest: CorpusManifest) -> None:
    if manifest.actual_row_count != manifest.expected_row_count:
        raise ValueError("complete manifest has invalid lifecycle evidence")
    if manifest.completed_at is None or manifest.error_message is not None:
        raise ValueError("complete manifest has invalid lifecycle evidence")


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


def _manifest(row: Any) -> CorpusManifest:
    return CorpusManifest(
        build_id=row["build_id"],
        corpus=Corpus(row["corpus"]),
        state=row["state"],
        is_active=row["is_active"],
        source_version=row["source_version"],
        source_hash=row["source_hash"],
        model_id=row["model_id"],
        model_revision=row["model_revision"],
        vector_dimension=row["vector_dimension"],
        expected_row_count=row["expected_row_count"],
        actual_row_count=row["actual_row_count"],
        code_commit=row["code_commit"],
        required_doc_ids=tuple(row["required_doc_ids"]),
        error_message=row["error_message"],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )


class EmbeddingCorpusPublisher:
    """Stage batches invisibly, validate them, then activate one corpus atomically."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        build: CorpusBuild,
    ) -> None:
        self._sf = session_factory
        self.build = build

    async def start(self, *, restart: bool = False) -> CorpusManifest:
        """Create the build manifest, or explicitly restart its failed attempt."""
        async with self._sf() as session, session.begin():
            existing = await self._select_manifest(session, for_update=True)
            if existing is None:
                await self._insert_manifest(session)
            elif existing.state == "complete":
                self._require_same_contract(existing)
                if not existing.is_active:
                    raise CorpusBuildStateError(
                        f"completed build {self.build.build_id} is no longer active"
                    )
                return existing
            else:
                await self._restart_manifest(session, existing, restart=restart)
            started = await self._select_manifest(session)
            if started is None:
                raise CorpusBuildStateError(
                    f"build {self.build.build_id} disappeared during start"
                )
        return started

    async def stage(self, rows: Sequence[EmbeddingRow]) -> None:
        """Commit one batch to build-scoped staging; it remains reader-invisible."""
        if not rows:
            return
        _validate_batch(self.build, rows)
        params = [
            {
                "build_id": self.build.build_id,
                "doc_id": doc_id,
                "embedding": _vector_literal(vector),
                "metadata": json.dumps(metadata),
            }
            for doc_id, vector, metadata in rows
        ]
        async with self._sf() as session, session.begin():
            manifest = await self._select_manifest(session, for_update=True)
            if manifest is None or manifest.state != "building":
                state = "missing" if manifest is None else manifest.state
                raise CorpusBuildStateError(
                    f"cannot stage build {self.build.build_id} in state {state}"
                )
            await session.execute(
                text(
                    "INSERT INTO embedding_corpus_staging "
                    "(build_id, doc_id, embedding, metadata) VALUES "
                    "(:build_id, :doc_id, (:embedding)::vector, (:metadata)::jsonb)"
                ),
                params,
            )

    async def publish(
        self, source_validator: Callable[[], Awaitable[None]] | None = None
    ) -> CorpusManifest:
        """Validate and atomically replace serving rows plus the active manifest."""
        table = _SERVING_TABLE[self.build.corpus]
        index = _SERVING_INDEX[self.build.corpus]
        async with self._sf() as session, session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:corpus, 0))"),
                {"corpus": f"embedding:{self.build.corpus.value}"},
            )
            manifest = await self._select_manifest(session, for_update=True)
            if manifest is None:
                raise CorpusBuildStateError(
                    f"build {self.build.build_id} does not exist"
                )
            if manifest.state == "complete" and manifest.is_active:
                return manifest
            if manifest.state != "building":
                raise CorpusBuildStateError(
                    f"cannot publish build {self.build.build_id} in state "
                    f"{manifest.state}"
                )
            self._require_same_contract(manifest)
            count = await self._validate_candidate(session)
            if source_validator is not None:
                await source_validator()
            # The stable table's HNSW graph is mutated in place. Block similarity
            # readers until replacement and a clean index rebuild commit together;
            # otherwise concurrent scans can traverse dead/uncommitted graph nodes.
            await session.execute(text(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE"))
            await session.execute(
                text(
                    "UPDATE embedding_corpus_manifest SET is_active = false "
                    "WHERE corpus = :corpus AND is_active"
                ),
                {"corpus": self.build.corpus.value},
            )
            await session.execute(text(f"DELETE FROM {table}"))  # noqa: S608
            await session.execute(
                text(
                    f"INSERT INTO {table} (doc_id, embedding, metadata) "  # noqa: S608
                    "SELECT doc_id, embedding, metadata "
                    "FROM embedding_corpus_staging WHERE build_id = :build_id"
                ),
                {"build_id": self.build.build_id},
            )
            await session.execute(text(f"REINDEX INDEX {index}"))
            await session.execute(
                text(
                    "UPDATE embedding_corpus_manifest SET state = 'complete', "
                    "is_active = true, actual_row_count = :count, "
                    "completed_at = now(), error_message = NULL "
                    "WHERE build_id = :build_id"
                ),
                {"count": count, "build_id": self.build.build_id},
            )
            await session.execute(
                text("DELETE FROM embedding_corpus_staging WHERE build_id = :build_id"),
                {"build_id": self.build.build_id},
            )
            published = await self._select_manifest(session)
            if published is None:
                raise CorpusBuildStateError(
                    f"build {self.build.build_id} disappeared during publication"
                )
        return published

    async def _insert_manifest(self, session: AsyncSession) -> None:
        await session.execute(
            text(
                "INSERT INTO embedding_corpus_manifest ("
                "build_id, corpus, state, source_version, source_hash, "
                "model_id, model_revision, vector_dimension, "
                "expected_row_count, code_commit, required_doc_ids) VALUES ("
                ":build_id, :corpus, 'building', :source_version, :source_hash, "
                ":model_id, :model_revision, :vector_dimension, "
                ":expected_row_count, :code_commit, :required_doc_ids)"
            ),
            self._build_params(),
        )

    async def _restart_manifest(
        self, session: AsyncSession, existing: CorpusManifest, *, restart: bool
    ) -> None:
        self._require_same_contract(existing)
        if not restart:
            raise CorpusBuildStateError(
                f"build {self.build.build_id} already exists in state {existing.state}"
            )
        if existing.state != "failed":
            raise CorpusBuildStateError(
                f"only failed builds can restart; build {self.build.build_id} "
                f"is {existing.state}"
            )
        await session.execute(
            text("DELETE FROM embedding_corpus_staging WHERE build_id = :build_id"),
            {"build_id": self.build.build_id},
        )
        await session.execute(
            text(
                "UPDATE embedding_corpus_manifest SET state = 'building', "
                "is_active = false, actual_row_count = NULL, error_message = NULL, "
                "completed_at = NULL WHERE build_id = :build_id"
            ),
            {"build_id": self.build.build_id},
        )

    async def _validate_candidate(self, session: AsyncSession) -> int:
        count = int(
            await session.scalar(
                text(
                    "SELECT count(*) FROM embedding_corpus_staging "
                    "WHERE build_id = :build_id"
                ),
                {"build_id": self.build.build_id},
            )
            or 0
        )
        if count != self.build.expected_row_count:
            raise CorpusValidationError(
                f"expected {self.build.expected_row_count} rows, found {count}"
            )
        missing = list(
            (
                await session.execute(
                    text(
                        "SELECT required.doc_id FROM "
                        "unnest(CAST(:required AS text[])) required(doc_id) "
                        "WHERE NOT EXISTS ("
                        "SELECT 1 FROM embedding_corpus_staging staged "
                        "WHERE staged.build_id = :build_id "
                        "AND staged.doc_id = required.doc_id) ORDER BY 1"
                    ),
                    {
                        "required": list(self.build.required_doc_ids),
                        "build_id": self.build.build_id,
                    },
                )
            ).scalars()
        )
        if missing:
            raise CorpusValidationError(
                f"missing required doc_ids: {', '.join(missing)}"
            )
        return count

    async def fail(self, error_message: str) -> CorpusManifest:
        """Record a failed candidate without changing any active corpus."""
        if not error_message.strip():
            raise ValueError("error_message must be non-empty")
        async with self._sf() as session, session.begin():
            existing = await self._select_manifest(session, for_update=True)
            if existing is None:
                raise CorpusBuildStateError(
                    f"build {self.build.build_id} does not exist"
                )
            if existing.state == "complete":
                raise CorpusBuildStateError(
                    f"cannot fail completed build {self.build.build_id}"
                )
            await session.execute(
                text("DELETE FROM embedding_corpus_staging WHERE build_id = :build_id"),
                {"build_id": self.build.build_id},
            )
            await session.execute(
                text(
                    "UPDATE embedding_corpus_manifest SET state = 'failed', "
                    "is_active = false, error_message = :error_message "
                    "WHERE build_id = :build_id AND state <> 'complete'"
                ),
                {
                    "build_id": self.build.build_id,
                    "error_message": error_message,
                },
            )
            failed = await self._select_manifest(session)
            if failed is None:
                raise CorpusBuildStateError(
                    f"build {self.build.build_id} does not exist"
                )
        return failed

    async def manifest(self) -> CorpusManifest:
        """Return this build's current persisted manifest."""
        async with self._sf() as session:
            manifest = await self._select_manifest(session)
        if manifest is None:
            raise CorpusBuildStateError(f"build {self.build.build_id} does not exist")
        return manifest

    async def _select_manifest(
        self, session: AsyncSession, *, for_update: bool = False
    ) -> CorpusManifest | None:
        statement = (
            "SELECT * FROM embedding_corpus_manifest "
            "WHERE build_id = :build_id FOR UPDATE"
            if for_update
            else "SELECT * FROM embedding_corpus_manifest WHERE build_id = :build_id"
        )
        result = await session.execute(
            text(statement),
            {"build_id": self.build.build_id},
        )
        row = result.mappings().one_or_none()
        return _manifest(row) if row is not None else None

    def _build_params(self) -> dict[str, object]:
        return {
            "build_id": self.build.build_id,
            "corpus": self.build.corpus.value,
            "source_version": self.build.source_version,
            "source_hash": self.build.source_hash,
            "model_id": self.build.model_id,
            "model_revision": self.build.model_revision,
            "vector_dimension": self.build.vector_dimension,
            "expected_row_count": self.build.expected_row_count,
            "code_commit": self.build.code_commit,
            "required_doc_ids": list(self.build.required_doc_ids),
        }

    def _require_same_contract(self, manifest: CorpusManifest) -> None:
        expected = self._build_params()
        actual: dict[str, object] = {
            "build_id": manifest.build_id,
            "corpus": manifest.corpus.value,
            "source_version": manifest.source_version,
            "source_hash": manifest.source_hash,
            "model_id": manifest.model_id,
            "model_revision": manifest.model_revision,
            "vector_dimension": manifest.vector_dimension,
            "expected_row_count": manifest.expected_row_count,
            "code_commit": manifest.code_commit,
            "required_doc_ids": list(manifest.required_doc_ids),
        }
        if actual != expected:
            raise CorpusBuildStateError(
                f"build {self.build.build_id} exists with different provenance"
            )


async def active_manifests(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[CorpusManifest, ...]:
    """Return completed active manifests for operator inspection."""
    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT * FROM embedding_corpus_manifest "
                    "WHERE state = 'complete' AND is_active ORDER BY corpus"
                )
            )
        ).mappings()
        return tuple(_manifest(row) for row in rows)


async def corpus_manifests(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[CorpusManifest, ...]:
    """Return every build attempt for operator validation and recovery."""
    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT * FROM embedding_corpus_manifest "
                    "ORDER BY created_at DESC, corpus, build_id"
                )
            )
        ).mappings()
        return tuple(_manifest(row) for row in rows)


async def deactivate_corpus(
    session_factory: async_sessionmaker[AsyncSession], corpus: Corpus
) -> None:
    """Invalidate active embeddings before the official source is replaced."""
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE embedding_corpus_manifest SET is_active = false "
                "WHERE corpus = :corpus AND is_active"
            ),
            {"corpus": corpus.value},
        )


@asynccontextmanager
async def replacing_corpus_source(
    session_factory: async_sessionmaker[AsyncSession], corpus: Corpus
) -> AsyncIterator[None]:
    """Commit deactivation, then hold a session lock across source replacement."""
    engine = session_factory.kw.get("bind")
    if not isinstance(engine, AsyncEngine):
        raise TypeError("embedding session factory must be bound to an AsyncEngine")
    async with engine.connect() as connection:
        await connection.execute(
            text("SELECT pg_advisory_lock(hashtextextended(:corpus, 0))"),
            {"corpus": f"embedding:{corpus.value}"},
        )
        await connection.commit()
        try:
            await connection.execute(
                text(
                    "UPDATE embedding_corpus_manifest SET is_active = false "
                    "WHERE corpus = :corpus AND is_active"
                ),
                {"corpus": corpus.value},
            )
            await connection.commit()
            yield
        except BaseException as original:
            try:
                unlocked = await connection.scalar(
                    text("SELECT pg_advisory_unlock(hashtextextended(:corpus, 0))"),
                    {"corpus": f"embedding:{corpus.value}"},
                )
                await connection.commit()
                if not unlocked:
                    original.add_note("Failed to release embedding source lock")
            except Exception as unlock_error:
                original.add_note(
                    f"Failed to release embedding source lock: {unlock_error}"
                )
            raise
        else:
            unlocked = await connection.scalar(
                text("SELECT pg_advisory_unlock(hashtextextended(:corpus, 0))"),
                {"corpus": f"embedding:{corpus.value}"},
            )
            await connection.commit()
            if not unlocked:
                raise RuntimeError("failed to release embedding source lock")
