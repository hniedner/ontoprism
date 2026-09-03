"""Validated, atomic publication of embedding corpora via build-scoped staging.

Batches are staged invisibly (PostgreSQL MVCC keeps them unreadable until commit);
the switchover itself takes an ``ACCESS EXCLUSIVE`` lock on the serving table and so
briefly blocks similarity readers while the rows and HNSW index are replaced.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)
EmbeddingRow = tuple[str, list[float], dict[str, JsonValue]]
ManifestState = Literal["building", "failed", "complete"]
EMBEDDING_VECTOR_DIMENSION = 768
logger = logging.getLogger(__name__)


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


class CorpusCoordinationError(RuntimeError):
    """A corpus advisory lock could not be acquired or safely released."""


@dataclass(frozen=True, slots=True)
class CorpusBuild:
    """Immutable provenance and completeness contract for one candidate build."""

    build_id: UUID
    corpus: Corpus
    source_identity: str
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
    _validate_contract_shape(
        build.vector_dimension, build.expected_row_count, build.required_doc_ids
    )


def _validate_contract_shape(
    vector_dimension: int,
    expected_row_count: int,
    required_doc_ids: tuple[str, ...],
) -> None:
    if vector_dimension != EMBEDDING_VECTOR_DIMENSION:
        raise ValueError("vector_dimension must match the physical vector(768)")
    if expected_row_count <= 0:
        raise ValueError("expected_row_count must be positive")
    if not required_doc_ids or any(not value for value in required_doc_ids):
        raise ValueError("required_doc_ids must contain non-empty sentinels")
    if len(required_doc_ids) != len(set(required_doc_ids)):
        raise ValueError("required_doc_ids must be unique")


def _validate_provenance(build: CorpusBuild) -> None:
    if re.fullmatch(r"[0-9a-f]{64}", build.source_identity) is None:
        raise ValueError("source_identity must be a lowercase SHA-256 digest")
    _validate_provenance_fields(
        build.source_version,
        build.source_hash,
        build.model_id,
        build.model_revision,
        build.code_commit,
    )


def _validate_provenance_fields(
    source_version: str,
    source_hash: str,
    model_id: str,
    model_revision: str,
    code_commit: str,
) -> None:
    values = (source_version, source_hash, model_id, model_revision, code_commit)
    if not all(value.strip() for value in values):
        raise ValueError("embedding build provenance fields must be non-empty")
    if re.fullmatch(r"[0-9a-f]{64}", source_hash) is None:
        raise ValueError("source_hash must be a lowercase SHA-256 digest")
    if re.fullmatch(r"[0-9a-f]{40}", model_revision) is None:
        raise ValueError("model_revision must be an immutable 40-hex revision")
    if re.fullmatch(r"[0-9a-f]{40}", code_commit) is None:
        raise ValueError("code_commit must be a 40-hex Git commit")


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


class CorpusManifest(BaseModel):
    """Persisted lifecycle and evidence for one embedding corpus build."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    build_id: UUID
    corpus: Corpus
    state: ManifestState
    is_active: bool
    source_identity: str | None
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

    @model_validator(mode="after")
    def _valid_manifest(self) -> CorpusManifest:
        self._validate_contract()
        validators = {
            "building": _validate_building_manifest,
            "failed": _validate_failed_manifest,
            "complete": _validate_complete_manifest,
        }
        validators[self.state](self)
        return self

    def _validate_contract(self) -> None:
        if self.source_identity is None:
            if self.is_active:
                raise ValueError("active manifest requires source_identity")
            _validate_provenance_fields(
                self.source_version,
                self.source_hash,
                self.model_id,
                self.model_revision,
                self.code_commit,
            )
            _validate_contract_shape(
                self.vector_dimension,
                self.expected_row_count,
                self.required_doc_ids,
            )
            return
        _validate_build(
            CorpusBuild(
                build_id=self.build_id,
                corpus=self.corpus,
                source_identity=self.source_identity,
                source_version=self.source_version,
                source_hash=self.source_hash,
                model_id=self.model_id,
                model_revision=self.model_revision,
                vector_dimension=self.vector_dimension,
                expected_row_count=self.expected_row_count,
                code_commit=self.code_commit,
                required_doc_ids=self.required_doc_ids,
            )
        )


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


def _manifest(row: RowMapping) -> CorpusManifest:
    return CorpusManifest(
        build_id=row["build_id"],
        corpus=Corpus(row["corpus"]),
        state=row["state"],
        is_active=row["is_active"],
        source_identity=row["source_identity"],
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
        self._build = build

    @property
    def build(self) -> CorpusBuild:
        return self._build

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
                self._require_same_contract(manifest)
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
                "build_id, corpus, state, source_identity, source_version, "
                "source_hash, "
                "model_id, model_revision, vector_dimension, "
                "expected_row_count, code_commit, required_doc_ids) VALUES ("
                ":build_id, :corpus, 'building', :source_identity, "
                ":source_version, :source_hash, "
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
            "source_identity": self.build.source_identity,
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
            "source_identity": manifest.source_identity,
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


async def _restore_active_source_manifest(
    connection: AsyncConnection,
    corpus: Corpus,
    build_id: UUID | None,
) -> None:
    await connection.execute(
        text(
            "UPDATE embedding_corpus_manifest SET is_active = false "
            "WHERE corpus = :corpus AND is_active"
        ),
        {"corpus": corpus.value},
    )
    if build_id is not None:
        await connection.execute(
            text(
                "UPDATE embedding_corpus_manifest SET is_active = true "
                "WHERE corpus = :corpus AND build_id = :build_id "
                "AND state = 'complete'"
            ),
            {"corpus": corpus.value, "build_id": build_id},
        )
    await connection.commit()


async def _invalidate_with_note(
    connection: AsyncConnection, original: BaseException
) -> None:
    try:
        await connection.invalidate()
    except asyncio.CancelledError:
        raise
    except Exception as invalidate_error:
        original.add_note(
            f"Failed to invalidate embedding source connection: {invalidate_error}"
        )


async def _run_failure_recovery(
    recovery: Awaitable[None], original: BaseException
) -> None:
    task = asyncio.ensure_future(recovery)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        try:
            await task
        except Exception as recovery_error:
            cancelled.add_note(f"Failure recovery also failed: {recovery_error}")
        cancelled.add_note(f"Source operation had already failed: {original}")
        raise


async def _recover_uncertain_deactivation(
    connection: AsyncConnection,
    engine: AsyncEngine,
    corpus: Corpus,
    active_build_id: UUID | None,
    original: BaseException,
) -> None:
    await _invalidate_with_note(connection, original)
    await _restore_active_source_manifest_fresh(
        engine, corpus, active_build_id, original
    )


async def _recover_failed_replacement(
    connection: AsyncConnection,
    corpus: Corpus,
    active_build_id: UUID | None,
    original: BaseException,
) -> None:
    try:
        await _restore_active_source_manifest(connection, corpus, active_build_id)
    except asyncio.CancelledError:
        raise
    except Exception as restore_error:
        original.add_note(
            "Failed to restore active embedding manifest after source replacement "
            f"failure: {restore_error}"
        )
        await _invalidate_with_note(connection, original)


async def _prepare_and_replace_source[T](
    connection: AsyncConnection,
    engine: AsyncEngine,
    corpus: Corpus,
    prepare: Callable[[], Awaitable[T]],
    replace: Callable[[T], None],
) -> T:
    candidate = await prepare()
    active_build_id = await connection.scalar(
        text(
            "SELECT build_id FROM embedding_corpus_manifest "
            "WHERE corpus = :corpus AND state = 'complete' AND is_active"
        ),
        {"corpus": corpus.value},
    )
    try:
        await connection.execute(
            text(
                "UPDATE embedding_corpus_manifest SET is_active = false "
                "WHERE corpus = :corpus AND is_active"
            ),
            {"corpus": corpus.value},
        )
        await connection.commit()
    except BaseException as original:
        await _run_failure_recovery(
            _recover_uncertain_deactivation(
                connection, engine, corpus, active_build_id, original
            ),
            original,
        )
        raise
    try:
        replace(candidate)
    except BaseException as original:
        await _run_failure_recovery(
            _recover_failed_replacement(connection, corpus, active_build_id, original),
            original,
        )
        raise
    return candidate


async def _restore_active_source_manifest_fresh(
    engine: AsyncEngine,
    corpus: Corpus,
    build_id: UUID | None,
    original: BaseException,
) -> None:
    key = f"embedding:{corpus.value}"
    try:
        recovery = await engine.connect()
    except asyncio.CancelledError:
        raise
    except Exception as recovery_error:
        original.add_note(
            "Failed to open a fresh connection to restore the active embedding "
            f"manifest: {recovery_error}"
        )
        return
    try:
        await _acquire_source_lock(recovery, key)
    except asyncio.CancelledError as cancelled:
        await _run_failure_recovery(
            _cleanup_uncertain_source_lock(recovery, cancelled), cancelled
        )
        raise
    except Exception as recovery_error:
        original.add_note(
            "Failed to acquire embedding source lock for manifest recovery: "
            f"{recovery_error}"
        )
        await _invalidate_with_note(recovery, original)
        await _cleanup_failed_source_coordination(
            recovery, key, lock_acquired=False, original=original
        )
        return
    try:
        await _restore_active_source_manifest(recovery, corpus, build_id)
    except asyncio.CancelledError as cancelled:
        await _run_failure_recovery(
            _cleanup_failed_source_coordination(
                recovery, key, lock_acquired=True, original=cancelled
            ),
            cancelled,
        )
        raise
    except Exception as recovery_error:
        original.add_note(
            "Failed to restore active embedding manifest after uncertain "
            f"deactivation commit: {recovery_error}"
        )
    await _cleanup_failed_source_coordination(
        recovery, key, lock_acquired=True, original=original
    )


async def _cleanup_failed_source_coordination(
    connection: AsyncConnection,
    key: str,
    *,
    lock_acquired: bool,
    original: BaseException,
) -> None:
    cancellation: asyncio.CancelledError | None = None
    if lock_acquired and not connection.invalidated:
        try:
            await _release_source_lock(connection, key)
        except asyncio.CancelledError as exc:
            cancellation = exc
        except Exception as unlock_error:
            original.add_note(
                f"Failed to release embedding source lock: {unlock_error}"
            )
            await _invalidate_with_note(connection, original)
    cancellation = await _close_failed_source_connection(
        connection, original, cancellation
    )
    if cancellation is not None:
        raise cancellation


async def _close_failed_source_connection(
    connection: AsyncConnection,
    original: BaseException,
    cancellation: asyncio.CancelledError | None,
) -> asyncio.CancelledError | None:
    try:
        await _close_source_connection(connection)
    except asyncio.CancelledError as exc:
        if cancellation is None:
            return exc
        cancellation.add_note(
            "Connection close was also cancelled during failure cleanup"
        )
    except Exception as close_error:
        original.add_note(f"Failed to close embedding source connection: {close_error}")
    return cancellation


async def _cleanup_uncertain_source_lock(
    connection: AsyncConnection, original: BaseException
) -> None:
    cancellation: asyncio.CancelledError | None = None
    try:
        await _invalidate_with_note(connection, original)
    except asyncio.CancelledError as exc:
        cancellation = exc
    try:
        await _close_source_connection(connection)
    except asyncio.CancelledError as exc:
        if cancellation is None:
            cancellation = exc
        else:
            cancellation.add_note(
                "Connection close was also cancelled after uncertain lock acquisition"
            )
    except Exception as close_error:
        original.add_note(f"Failed to close embedding source connection: {close_error}")
    if cancellation is not None:
        raise cancellation


async def _cleanup_committed_source_coordination(
    connection: AsyncConnection, key: str, corpus: Corpus
) -> None:
    cancellation: asyncio.CancelledError | None = None
    try:
        await _release_source_lock(connection, key)
    except asyncio.CancelledError as exc:
        cancellation = exc
    except Exception:
        logger.exception(
            "%s source replacement committed but lock cleanup failed", corpus.value
        )
        cancellation = await _invalidate_committed_source_connection(connection, corpus)
    try:
        await _close_source_connection(connection)
    except asyncio.CancelledError as exc:
        if cancellation is None:
            cancellation = exc
        else:
            cancellation.add_note(
                "Connection close was also cancelled after source replacement"
            )
    except Exception:
        logger.exception(
            "%s source replacement committed but connection cleanup failed",
            corpus.value,
        )
    if cancellation is not None:
        raise cancellation


async def _invalidate_committed_source_connection(
    connection: AsyncConnection, corpus: Corpus
) -> asyncio.CancelledError | None:
    try:
        await connection.invalidate()
    except asyncio.CancelledError as exc:
        return exc
    except Exception:
        logger.exception(
            "%s source replacement committed but invalidation failed", corpus.value
        )
    return None


async def _close_source_connection(connection: AsyncConnection) -> None:
    task = asyncio.create_task(connection.close())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def coordinate_corpus_source_replacement[T](
    session_factory: async_sessionmaker[AsyncSession],
    corpus: Corpus,
    *,
    prepare: Callable[[], Awaitable[T]],
    replace: Callable[[T], None],
) -> T:
    """Run preparation and a caller-supplied atomic replacement under one lock.

    Successful callback completion is the commit point. The synchronous callback must
    perform its irreversible replacement as its final operation.
    """
    engine = session_factory.kw.get("bind")
    if not isinstance(engine, AsyncEngine):
        raise TypeError("embedding session factory must be bound to an AsyncEngine")
    key = f"embedding:{corpus.value}"
    connection = await engine.connect()
    try:
        await _acquire_source_lock(connection, key)
    except BaseException as original:
        await _run_failure_recovery(
            _cleanup_uncertain_source_lock(connection, original), original
        )
        raise
    try:
        result = await _prepare_and_replace_source(
            connection, engine, corpus, prepare, replace
        )
    except BaseException as original:
        await _run_failure_recovery(
            _cleanup_failed_source_coordination(
                connection,
                key,
                lock_acquired=True,
                original=original,
            ),
            original,
        )
        raise
    await _cleanup_committed_source_coordination(connection, key, corpus)
    return result


@asynccontextmanager
async def replacing_corpus_source(
    session_factory: async_sessionmaker[AsyncSession], corpus: Corpus
) -> AsyncIterator[None]:
    """Acquire a per-corpus session lock, commit deactivation, then hold that lock
    across source replacement.

    Deliberately retained without a production caller: #180 removed the HTTP source
    replacement that used to call it, and #148's journaled activation must pause
    source-dependent embedding publication through exactly this coordination (D42).
    """
    engine = session_factory.kw.get("bind")
    if not isinstance(engine, AsyncEngine):
        raise TypeError("embedding session factory must be bound to an AsyncEngine")
    async with engine.connect() as connection:
        key = f"embedding:{corpus.value}"
        lock_acquired = False
        try:
            await _acquire_source_lock(connection, key)
            lock_acquired = True
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
                if lock_acquired:
                    await _release_source_lock(connection, key)
            except BaseException as unlock_error:
                original.add_note(
                    f"Failed to release embedding source lock: {unlock_error}"
                )
                await connection.invalidate()
            raise
        else:
            try:
                await _release_source_lock(connection, key)
            except BaseException:
                await connection.invalidate()
                raise


async def _acquire_source_lock(connection: AsyncConnection, key: str) -> None:
    task = asyncio.create_task(
        connection.execute(
            text("SELECT pg_advisory_lock(hashtextextended(:corpus, 0))"),
            {"corpus": key},
        )
    )
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        await task
        try:
            await _release_source_lock(connection, key)
        except BaseException as unlock_error:
            cancelled.add_note(
                f"Failed to release embedding source lock after cancellation: "
                f"{unlock_error}"
            )
            await connection.invalidate()
        raise


async def _release_source_lock(connection: AsyncConnection, key: str) -> None:
    async def release() -> None:
        unlocked = await connection.scalar(
            text("SELECT pg_advisory_unlock(hashtextextended(:corpus, 0))"),
            {"corpus": key},
        )
        await connection.commit()
        if not unlocked:
            raise CorpusCoordinationError("failed to release embedding source lock")

    task = asyncio.create_task(release())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise
