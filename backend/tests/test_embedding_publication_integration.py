"""Real-Postgres contracts for atomic embedding-corpus publication (#174)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.repositories.embeddings import publication
from ontolib.repositories.embeddings.publication import (
    Corpus,
    CorpusBuild,
    CorpusBuildStateError,
    CorpusUnavailableError,
    CorpusValidationError,
    EmbeddingCorpusPublisher,
    EmbeddingRow,
    active_manifests,
    coordinate_corpus_source_replacement,
    replacing_corpus_source,
)
from ontolib.repositories.embeddings.store import EmbeddingStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings"),
]

_OLD_BUILD = UUID("00000000-0000-0000-0000-000000000001")
_NEW_BUILD = UUID("00000000-0000-0000-0000-000000000002")
_CADSR_BUILD = UUID("00000000-0000-0000-0000-000000000003")
_VECTOR_DIMENSION = 768


@pytest.fixture
async def session_factory(
    isolated_postgres_settings: None,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    del isolated_postgres_settings
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    try:
        yield sf
    finally:
        await dispose_engine(engine)


@pytest.fixture(autouse=True)
async def clean_embedding_publication(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    async def clean() -> None:
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "DROP TRIGGER IF EXISTS reject_new_embedding_activation "
                    "ON embedding_corpus_manifest"
                )
            )
            await session.execute(
                text("DROP FUNCTION IF EXISTS reject_new_embedding_activation()")
            )
            await session.execute(text("DELETE FROM embedding_corpus_staging"))
            await session.execute(text("DELETE FROM embedding_corpus_manifest"))
            await session.execute(text("DELETE FROM ncit_concepts"))
            await session.execute(text("DELETE FROM cde_repository"))

    await clean()
    yield
    await clean()


def _build(
    build_id: UUID,
    *,
    corpus: Corpus = Corpus.NCIT,
    expected: int = 2,
    required: tuple[str, ...] = ("C3262",),
) -> CorpusBuild:
    return CorpusBuild(
        build_id=build_id,
        corpus=corpus,
        source_version="26.02d" if corpus is Corpus.NCIT else "sha256:test-cadsr",
        source_hash="a" * 64,
        model_id="sentence-transformers/all-mpnet-base-v2",
        model_revision="e8c3b32edf5434bc2275fc9bab85f82640a19130",
        vector_dimension=_VECTOR_DIMENSION,
        expected_row_count=expected,
        code_commit="b" * 40,
        required_doc_ids=required,
    )


def _row(doc_id: str, value: float) -> EmbeddingRow:
    return doc_id, [value] * _VECTOR_DIMENSION, {"label": doc_id}


async def _active_rows(
    sf: async_sessionmaker[AsyncSession],
) -> list[str]:
    async with sf() as session:
        result = await session.execute(
            text("SELECT doc_id FROM ncit_concepts ORDER BY 1")
        )
        return list(result.scalars())


async def _publish_old(
    sf: async_sessionmaker[AsyncSession], corpus: Corpus = Corpus.NCIT
) -> None:
    build_id = _OLD_BUILD if corpus is Corpus.NCIT else _CADSR_BUILD
    sentinel = "C3262" if corpus is Corpus.NCIT else "2517527:4"
    publisher = EmbeddingCorpusPublisher(
        sf, _build(build_id, corpus=corpus, required=(sentinel,))
    )
    await publisher.start()
    await publisher.stage([_row(sentinel, 0.5), _row("OLD", 0.25)])
    manifest = await publisher.publish()
    assert manifest.is_active


async def test_complete_candidate_activates_rows_and_manifest_together(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    publisher = EmbeddingCorpusPublisher(session_factory, _build(_NEW_BUILD))
    await publisher.start()
    await publisher.stage([_row("C3262", 1.0)])
    await publisher.stage([_row("C9305", 0.9)])

    with pytest.raises(CorpusUnavailableError):
        await EmbeddingStore(session_factory).similar_ncit("C3262")
    manifest = await publisher.publish()
    after = await EmbeddingStore(session_factory).similar_ncit("C3262")

    assert manifest.state == "complete"
    assert manifest.is_active
    assert manifest.actual_row_count == 2
    assert await _active_rows(session_factory) == ["C3262", "C9305"]
    assert after
    assert after[0][0] == "C9305"
    async with session_factory() as session:
        staged = await session.scalar(
            text("SELECT count(*) FROM embedding_corpus_staging")
        )
    assert staged == 0


async def test_similar_results_are_ordered_by_descending_similarity(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    def vec(primary: float, secondary: float) -> list[float]:
        values = [0.0] * _VECTOR_DIMENSION
        values[0], values[1] = primary, secondary
        return values

    publisher = EmbeddingCorpusPublisher(
        session_factory, _build(_NEW_BUILD, expected=5)
    )
    await publisher.start()
    # Staged out of similarity order with one more neighbor (ANTI, cosine -1) than the
    # limit, so LIMIT must drop the least-similar row: this exercises the top-N nearest
    # selection (inner ORDER BY), not just the final sort. Cosine to query direction
    # (1,0): NEAR ~0.89 > MID ~0.45 > FAR 0 > ANTI -1.
    await publisher.stage(
        [
            ("C3262", vec(1.0, 0.0), {"label": "query"}),
            ("FAR", vec(0.0, 1.0), {"label": "far"}),
            ("ANTI", vec(-1.0, 0.0), {"label": "anti"}),
            ("NEAR", vec(1.0, 0.5), {"label": "near"}),
            ("MID", vec(1.0, 2.0), {"label": "mid"}),
        ]
    )
    await publisher.publish()

    hits = await EmbeddingStore(session_factory).similar_ncit("C3262", limit=3)

    assert [doc_id for doc_id, _ in hits] == ["NEAR", "MID", "FAR"]
    assert "ANTI" not in {doc_id for doc_id, _ in hits}
    scores = [score for _, score in hits]
    assert scores == sorted(scores, reverse=True)


async def test_plausible_partial_candidate_is_rejected_and_old_corpus_survives(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _publish_old(session_factory)
    publisher = EmbeddingCorpusPublisher(
        session_factory,
        _build(_NEW_BUILD, expected=3, required=("C3262",)),
    )
    await publisher.start()
    # Non-empty, unique, and dimensionally valid: the old weak preflight accepted this
    # shape. It is still incomplete and lacks the canonical NCIt sentinel.
    await publisher.stage([_row("C1001", 0.1), _row("C1002", 0.2)])

    with pytest.raises(CorpusValidationError, match="expected 3 rows, found 2"):
        await publisher.publish()

    await publisher.fail("candidate validation failed")
    assert await _active_rows(session_factory) == ["C3262", "OLD"]
    async with session_factory() as session:
        active = await session.scalar(
            text(
                "SELECT build_id FROM embedding_corpus_manifest "
                "WHERE corpus = 'ncit' AND is_active"
            )
        )
        failed = await session.scalar(
            text(
                "SELECT state FROM embedding_corpus_manifest WHERE build_id = :build_id"
            ),
            {"build_id": _NEW_BUILD},
        )
    assert active == _OLD_BUILD
    assert failed == "failed"
    async with session_factory() as session:
        staged = await session.scalar(
            text(
                "SELECT count(*) FROM embedding_corpus_staging "
                "WHERE build_id = :build_id"
            ),
            {"build_id": _NEW_BUILD},
        )
    assert staged == 0


async def test_activation_error_rolls_back_rows_and_manifest(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _publish_old(session_factory)
    publisher = EmbeddingCorpusPublisher(session_factory, _build(_NEW_BUILD))
    await publisher.start()
    await publisher.stage([_row("C3262", 1.0), _row("NEW", 0.75)])
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "CREATE FUNCTION reject_new_embedding_activation() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN "
                f"IF NEW.build_id = '{_NEW_BUILD}'::uuid "
                "AND NEW.state = 'complete' THEN "
                "RAISE EXCEPTION 'injected activation failure'; END IF; RETURN NEW; "
                "END $$"
            )
        )
        await session.execute(
            text(
                "CREATE TRIGGER reject_new_embedding_activation "
                "BEFORE UPDATE ON embedding_corpus_manifest "
                "FOR EACH ROW EXECUTE FUNCTION reject_new_embedding_activation()"
            )
        )

    with pytest.raises(Exception, match="injected activation failure"):
        await publisher.publish()

    assert await _active_rows(session_factory) == ["C3262", "OLD"]
    async with session_factory() as session:
        active = await session.scalar(
            text(
                "SELECT build_id FROM embedding_corpus_manifest "
                "WHERE corpus = 'ncit' AND is_active"
            )
        )
    assert active == _OLD_BUILD


async def test_failed_build_can_restart_without_duplicate_publication(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    publisher = EmbeddingCorpusPublisher(session_factory, _build(_NEW_BUILD))
    await publisher.start()
    await publisher.stage([_row("C3262", 0.1)])
    await publisher.fail("injected first attempt")

    await publisher.start(restart=True)
    await publisher.stage([_row("C3262", 1.0), _row("C9305", 0.9)])
    first = await publisher.publish()
    second = await publisher.publish()

    assert first == second
    assert await _active_rows(session_factory) == ["C3262", "C9305"]
    async with session_factory() as session:
        manifests = await session.scalar(
            text(
                "SELECT count(*) FROM embedding_corpus_manifest "
                "WHERE corpus = 'ncit' AND is_active"
            )
        )
    assert manifests == 1


async def test_cadsr_failure_does_not_change_active_ncit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _publish_old(session_factory)
    cadsr = EmbeddingCorpusPublisher(
        session_factory,
        _build(
            _NEW_BUILD,
            corpus=Corpus.CADSR,
            expected=2,
            required=("2517527:1.0",),
        ),
    )
    await cadsr.start()
    await cadsr.stage([_row("999:1.0", 0.2)])

    with pytest.raises(CorpusValidationError):
        await cadsr.publish()

    assert await _active_rows(session_factory) == ["C3262", "OLD"]


async def test_ncit_failure_does_not_change_active_cadsr(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    cadsr_id = UUID("00000000-0000-0000-0000-000000000003")
    cadsr = EmbeddingCorpusPublisher(
        session_factory,
        _build(
            cadsr_id,
            corpus=Corpus.CADSR,
            expected=2,
            required=("2517527:1.0",),
        ),
    )
    await cadsr.start()
    await cadsr.stage([_row("2517527:1.0", 0.4), _row("200:1.0", 0.3)])
    await cadsr.publish()
    ncit = EmbeddingCorpusPublisher(
        session_factory,
        _build(_NEW_BUILD, expected=2, required=("C3262",)),
    )
    await ncit.start()
    await ncit.stage([_row("C1001", 0.2)])

    with pytest.raises(CorpusValidationError):
        await ncit.publish()

    async with session_factory() as session:
        cadsr_rows = await session.execute(
            text("SELECT doc_id FROM cde_repository ORDER BY 1")
        )
        active = await session.scalar(
            text(
                "SELECT build_id FROM embedding_corpus_manifest "
                "WHERE corpus = 'cadsr' AND is_active"
            )
        )
    assert list(cadsr_rows.scalars()) == ["200:1.0", "2517527:1.0"]
    assert active == cadsr_id


async def test_exact_count_without_required_sentinel_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    publisher = EmbeddingCorpusPublisher(session_factory, _build(_NEW_BUILD))
    await publisher.start()
    await publisher.stage([_row("C1001", 0.1), _row("C1002", 0.2)])

    with pytest.raises(CorpusValidationError, match="missing required doc_ids: C3262"):
        await publisher.publish()

    assert await _active_rows(session_factory) == []


async def test_cross_batch_duplicate_identifier_is_rejected_by_real_postgres(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    publisher = EmbeddingCorpusPublisher(session_factory, _build(_NEW_BUILD))
    await publisher.start()
    await publisher.stage([_row("C3262", 0.1)])

    with pytest.raises(IntegrityError):
        await publisher.stage([_row("C3262", 0.2)])

    async with session_factory() as session:
        staged = await session.scalar(
            text(
                "SELECT count(*) FROM embedding_corpus_staging "
                "WHERE build_id = :build_id"
            ),
            {"build_id": _NEW_BUILD},
        )
    assert staged == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_version", "changed"),
        ("source_hash", "d" * 64),
        ("model_id", "changed-model"),
        ("model_revision", "d" * 40),
        ("expected_row_count", 3),
        ("code_commit", "e" * 40),
        ("required_doc_ids", ("C3262", "C9305")),
    ],
)
async def test_restart_refuses_changed_provenance(
    session_factory: async_sessionmaker[AsyncSession], field: str, value: object
) -> None:
    original = EmbeddingCorpusPublisher(session_factory, _build(_NEW_BUILD))
    await original.start()
    await original.fail("first attempt")
    changed = EmbeddingCorpusPublisher(
        session_factory,
        replace(_build(_NEW_BUILD), **{field: value}),
    )

    with pytest.raises(CorpusBuildStateError, match="different provenance"):
        await changed.start(restart=True)


async def test_completed_build_id_refuses_changed_provenance(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _publish_old(session_factory)
    changed = EmbeddingCorpusPublisher(
        session_factory,
        replace(_build(_OLD_BUILD), model_revision="d" * 40),
    )

    with pytest.raises(CorpusBuildStateError, match="different provenance"):
        await changed.start()


async def test_publish_of_active_build_refuses_changed_provenance(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _publish_old(session_factory)
    changed = EmbeddingCorpusPublisher(
        session_factory, replace(_build(_OLD_BUILD), model_revision="d" * 40)
    )

    with pytest.raises(CorpusBuildStateError, match="different provenance"):
        await changed.publish()


async def test_inactive_completed_build_cannot_be_reused_or_failed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _publish_old(session_factory)
    replacement = EmbeddingCorpusPublisher(session_factory, _build(_NEW_BUILD))
    await replacement.start()
    await replacement.stage([_row("C3262", 1.0), _row("NEW", 0.9)])
    await replacement.publish()
    old = EmbeddingCorpusPublisher(session_factory, _build(_OLD_BUILD))

    with pytest.raises(CorpusBuildStateError, match="no longer active"):
        await old.start()
    with pytest.raises(CorpusBuildStateError, match="cannot fail completed"):
        await old.fail("must not rewrite history")


async def test_stage_rejects_invalid_batches_before_database_write(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    publisher = EmbeddingCorpusPublisher(session_factory, _build(_NEW_BUILD))
    await publisher.start()

    await publisher.stage([])
    with pytest.raises(CorpusValidationError, match="duplicate doc_ids"):
        await publisher.stage([_row("C3262", 0.1), _row("C3262", 0.2)])
    with pytest.raises(CorpusValidationError, match="doc_id must be non-empty"):
        await publisher.stage([_row("", 0.1)])
    with pytest.raises(CorpusValidationError, match="dimension 1, expected 768"):
        await publisher.stage([("C3262", [0.1], {})])
    with pytest.raises(CorpusValidationError, match="non-finite"):
        await publisher.stage([("C3262", [float("nan")] * _VECTOR_DIMENSION, {})])
    with pytest.raises(CorpusValidationError, match="zero-norm"):
        await publisher.stage([("C3262", [0.0] * _VECTOR_DIMENSION, {})])

    async with session_factory() as session:
        staged = await session.scalar(
            text("SELECT count(*) FROM embedding_corpus_staging")
        )
    assert staged == 0


async def test_missing_and_failed_builds_reject_illegal_operations(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    publisher = EmbeddingCorpusPublisher(session_factory, _build(_NEW_BUILD))

    with pytest.raises(CorpusBuildStateError, match="does not exist"):
        await publisher.manifest()
    with pytest.raises(CorpusBuildStateError, match="state missing"):
        await publisher.stage([_row("C3262", 0.1)])
    with pytest.raises(CorpusBuildStateError, match="does not exist"):
        await publisher.publish()

    await publisher.start()
    with pytest.raises(CorpusBuildStateError, match="already exists in state building"):
        await publisher.start()
    with pytest.raises(CorpusBuildStateError, match="only failed builds can restart"):
        await publisher.start(restart=True)
    await publisher.fail("injected")
    with pytest.raises(CorpusBuildStateError, match="state failed"):
        await publisher.stage([_row("C3262", 0.1)])
    with pytest.raises(CorpusBuildStateError, match="state failed"):
        await publisher.publish()
    with pytest.raises(ValueError, match="non-empty"):
        await publisher.fail(" ")


async def test_active_manifest_inspection_returns_only_completed_active_builds(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _publish_old(session_factory)
    failed = EmbeddingCorpusPublisher(
        session_factory,
        _build(
            _NEW_BUILD,
            corpus=Corpus.CADSR,
            required=("2517527:1.0",),
        ),
    )
    await failed.start()
    await failed.fail("not published")

    manifests = await active_manifests(session_factory)

    assert len(manifests) == 1
    assert manifests[0].build_id == _OLD_BUILD
    assert manifests[0].corpus is Corpus.NCIT


async def test_concurrent_publishers_serialize_to_one_consistent_active_corpus(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first_id = UUID("00000000-0000-0000-0000-000000000010")
    second_id = UUID("00000000-0000-0000-0000-000000000020")
    first = EmbeddingCorpusPublisher(session_factory, _build(first_id))
    second = EmbeddingCorpusPublisher(session_factory, _build(second_id))
    await first.start()
    await second.start()
    await first.stage([_row("C3262", 0.1), _row("FIRST", 0.2)])
    await second.stage([_row("C3262", 0.8), _row("SECOND", 0.9)])

    await asyncio.gather(first.publish(), second.publish())

    async with session_factory() as session:
        active = await session.execute(
            text(
                "SELECT build_id FROM embedding_corpus_manifest "
                "WHERE corpus = 'ncit' AND is_active"
            )
        )
        active_id = active.scalar_one()
    expected_rows = ["C3262", "FIRST"] if active_id == first_id else ["C3262", "SECOND"]
    assert active_id in {first_id, second_id}
    assert await _active_rows(session_factory) == expected_rows


async def test_reader_blocks_during_activation_then_sees_complete_new_corpus(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _publish_old(session_factory)
    publisher = EmbeddingCorpusPublisher(session_factory, _build(_NEW_BUILD))
    await publisher.start()
    await publisher.stage([_row("C3262", 1.0), _row("C9305", 0.9)])
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "CREATE FUNCTION pause_embedding_activation() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(0.5); RETURN NEW; END $$"
            )
        )
        await session.execute(
            text(
                "CREATE TRIGGER pause_embedding_activation BEFORE UPDATE "
                "ON embedding_corpus_manifest FOR EACH ROW "
                "WHEN (NEW.build_id = '00000000-0000-0000-0000-000000000002'::uuid "
                "AND NEW.state = 'complete') EXECUTE FUNCTION "
                "pause_embedding_activation()"
            )
        )

    publish_task = asyncio.create_task(publisher.publish())
    await asyncio.sleep(0.1)
    read_task = asyncio.create_task(
        EmbeddingStore(session_factory).similar_ncit("C3262")
    )
    await asyncio.sleep(0.1)

    assert not publish_task.done()
    assert not read_task.done()
    hits = await read_task
    await publish_task
    assert hits
    assert hits[0][0] == "C9305"


async def test_published_corpus_has_usable_rebuilt_hnsw_index(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        before_node = await session.scalar(
            text(
                "SELECT relfilenode FROM pg_class "
                "WHERE relname = 'idx_ncit_concepts_hnsw'"
            )
        )
    publisher = EmbeddingCorpusPublisher(session_factory, _build(_NEW_BUILD))
    await publisher.start()
    await publisher.stage([_row("C3262", 1.0), _row("C9305", 0.9)])
    await publisher.publish()

    async with session_factory() as session, session.begin():
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(
            (
                await session.execute(
                    text(
                        "EXPLAIN (FORMAT TEXT) SELECT doc_id FROM ncit_concepts "
                        "ORDER BY embedding <=> CAST(:query AS vector) LIMIT 1"
                    ),
                    {"query": "[" + ",".join(["1.0"] * _VECTOR_DIMENSION) + "]"},
                )
            ).scalars()
        )
        after_node = await session.scalar(
            text(
                "SELECT relfilenode FROM pg_class "
                "WHERE relname = 'idx_ncit_concepts_hnsw'"
            )
        )

    assert before_node != after_node
    assert "idx_ncit_concepts_hnsw" in plan


async def test_failed_source_replacement_keeps_manifest_inactive(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _publish_old(session_factory)

    async def replace_then_fail() -> None:
        async with replacing_corpus_source(session_factory, Corpus.NCIT):
            async with session_factory() as observer:
                active = await observer.scalar(
                    text(
                        "SELECT count(*) FROM embedding_corpus_manifest "
                        "WHERE corpus = 'ncit' AND is_active"
                    )
                )
            assert active == 0
            raise RuntimeError("source replacement failed")

    with pytest.raises(RuntimeError, match="source replacement failed"):
        await replace_then_fail()

    async with session_factory() as session:
        active_after = await session.scalar(
            text(
                "SELECT count(*) FROM embedding_corpus_manifest "
                "WHERE corpus = 'ncit' AND is_active"
            )
        )
    assert active_after == 0
    async with replacing_corpus_source(session_factory, Corpus.NCIT):
        pass  # reacquiring proves the exceptional path released its session lock


async def test_coordinated_source_replacement_locks_before_preparation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = session_factory.kw["bind"]
    key = "embedding:cadsr"
    prepared = asyncio.Event()
    replaced: list[str] = []

    async def prepare() -> str:
        prepared.set()
        return "candidate"

    async def replace_source(candidate: str) -> None:
        replaced.append(candidate)

    async with engine.connect() as blocker:
        await blocker.execute(
            text("SELECT pg_advisory_lock(hashtextextended(:corpus, 0))"),
            {"corpus": key},
        )
        await blocker.commit()
        task = asyncio.create_task(
            coordinate_corpus_source_replacement(
                session_factory,
                Corpus.CADSR,
                prepare=prepare,
                replace=replace_source,
            )
        )
        await asyncio.sleep(0.1)
        assert not prepared.is_set()
        await blocker.execute(
            text("SELECT pg_advisory_unlock(hashtextextended(:corpus, 0))"),
            {"corpus": key},
        )
        await blocker.commit()

    assert await task == "candidate"
    assert replaced == ["candidate"]


async def test_coordinated_source_replacement_holds_lock_through_replacement(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = session_factory.kw["bind"]
    key = "embedding:cadsr"
    replacement_entered = asyncio.Event()
    finish_replacement = asyncio.Event()

    async def prepare() -> str:
        return "candidate"

    async def replace_source(_candidate: str) -> None:
        replacement_entered.set()
        await finish_replacement.wait()

    task = asyncio.create_task(
        coordinate_corpus_source_replacement(
            session_factory,
            Corpus.CADSR,
            prepare=prepare,
            replace=replace_source,
        )
    )
    await asyncio.sleep(0.1)
    assert replacement_entered.is_set()
    assert not task.done()

    async with engine.connect() as probe:
        acquired = bool(
            await probe.scalar(
                text("SELECT pg_try_advisory_lock(hashtextextended(:corpus, 0))"),
                {"corpus": key},
            )
        )
        if acquired:
            await probe.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:corpus, 0))"),
                {"corpus": key},
            )
            await probe.commit()
    assert not acquired

    finish_replacement.set()
    assert await task == "candidate"


async def test_coordinated_source_failure_restores_active_manifest(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _publish_old(session_factory)

    async def prepare() -> str:
        return "candidate"

    async def fail_replace(_candidate: str) -> None:
        raise OSError("rename failed")

    with pytest.raises(OSError, match="rename failed"):
        await coordinate_corpus_source_replacement(
            session_factory,
            Corpus.NCIT,
            prepare=prepare,
            replace=fail_replace,
        )

    manifests = await active_manifests(session_factory)
    assert [manifest.build_id for manifest in manifests] == [_OLD_BUILD]


async def test_coordinated_cadsr_failure_restores_only_cadsr_manifest(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _publish_old(session_factory, Corpus.NCIT)
    await _publish_old(session_factory, Corpus.CADSR)

    async def prepare() -> str:
        return "candidate"

    async def fail_replace(_candidate: str) -> None:
        raise OSError("rename failed")

    with pytest.raises(OSError, match="rename failed"):
        await coordinate_corpus_source_replacement(
            session_factory,
            Corpus.CADSR,
            prepare=prepare,
            replace=fail_replace,
        )

    manifests = await active_manifests(session_factory)
    assert {manifest.build_id for manifest in manifests} == {
        _OLD_BUILD,
        _CADSR_BUILD,
    }


async def test_deactivation_commit_error_restores_manifest_using_fresh_connection(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _publish_old(session_factory, Corpus.CADSR)
    original_commit = AsyncConnection.commit
    injected = False
    replaced: list[str] = []

    async def commit_then_raise(connection: AsyncConnection) -> None:
        nonlocal injected
        await original_commit(connection)
        if not injected:
            injected = True
            raise RuntimeError("commit result lost")

    async def prepare() -> str:
        return "candidate"

    async def replace_source(candidate: str) -> None:
        replaced.append(candidate)

    monkeypatch.setattr(AsyncConnection, "commit", commit_then_raise)

    with pytest.raises(RuntimeError, match="commit result lost"):
        await coordinate_corpus_source_replacement(
            session_factory,
            Corpus.CADSR,
            prepare=prepare,
            replace=replace_source,
        )

    manifests = await active_manifests(session_factory)
    assert [manifest.build_id for manifest in manifests] == [_CADSR_BUILD]
    assert replaced == []


async def test_uncertain_lock_acquisition_invalidates_connection(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalidated: list[AsyncConnection] = []
    original_acquire = publication._acquire_source_lock
    original_invalidate = AsyncConnection.invalidate

    async def acquire_then_raise(connection: AsyncConnection, key: str) -> None:
        await original_acquire(connection, key)
        raise RuntimeError("lock result lost")

    async def record_invalidate(connection: AsyncConnection) -> None:
        invalidated.append(connection)
        await original_invalidate(connection)

    async def prepare() -> str:
        pytest.fail("preparation ran after uncertain lock acquisition")

    async def replace_source(_candidate: str) -> None:
        pytest.fail("replacement ran after uncertain lock acquisition")

    monkeypatch.setattr(publication, "_acquire_source_lock", acquire_then_raise)
    monkeypatch.setattr(AsyncConnection, "invalidate", record_invalidate)

    with pytest.raises(RuntimeError, match="lock result lost"):
        await coordinate_corpus_source_replacement(
            session_factory,
            Corpus.CADSR,
            prepare=prepare,
            replace=replace_source,
        )

    assert len(invalidated) == 1


async def test_coordinated_preparation_failure_keeps_active_manifest(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _publish_old(session_factory)

    async def fail_prepare() -> str:
        raise RuntimeError("candidate validation failed")

    async def replace_source(_candidate: str) -> None:
        pytest.fail("invalid candidate reached replacement")

    with pytest.raises(RuntimeError, match="candidate validation failed"):
        await coordinate_corpus_source_replacement(
            session_factory,
            Corpus.NCIT,
            prepare=fail_prepare,
            replace=replace_source,
        )

    manifests = await active_manifests(session_factory)
    assert [manifest.build_id for manifest in manifests] == [_OLD_BUILD]


async def test_coordinated_post_commit_unlock_failure_reports_success(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _publish_old(session_factory)
    replaced: list[str] = []

    async def prepare() -> str:
        return "candidate"

    async def replace_source(candidate: str) -> None:
        replaced.append(candidate)

    async def fail_unlock(_connection: object, _key: str) -> None:
        raise RuntimeError("unlock failed after rename")

    monkeypatch.setattr(publication, "_release_source_lock", fail_unlock)
    result = await coordinate_corpus_source_replacement(
        session_factory,
        Corpus.NCIT,
        prepare=prepare,
        replace=replace_source,
    )

    assert result == "candidate"
    assert replaced == ["candidate"]


async def test_source_replacement_blocks_publication_until_lock_release(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _publish_old(session_factory)
    candidate = EmbeddingCorpusPublisher(session_factory, _build(_NEW_BUILD))
    await candidate.start()
    await candidate.stage([_row("C3262", 1.0), _row("NEW", 0.9)])

    async with replacing_corpus_source(session_factory, Corpus.NCIT):
        publish_task = asyncio.create_task(candidate.publish())
        await asyncio.sleep(0.1)
        assert not publish_task.done()
        async with session_factory() as observer:
            active = await observer.scalar(
                text(
                    "SELECT count(*) FROM embedding_corpus_manifest "
                    "WHERE corpus = 'ncit' AND is_active"
                )
            )
        assert active == 0

    manifest = await publish_task
    assert manifest.is_active
    assert await _active_rows(session_factory) == ["C3262", "NEW"]


async def test_source_validator_runs_only_after_replacement_lock_releases(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    candidate = EmbeddingCorpusPublisher(session_factory, _build(_NEW_BUILD))
    await candidate.start()
    await candidate.stage([_row("C3262", 1.0), _row("NEW", 0.9)])
    validated: list[str] = []

    async def validate() -> None:
        validated.append("validated")

    async with replacing_corpus_source(session_factory, Corpus.NCIT):
        publish_task = asyncio.create_task(candidate.publish(validate))
        await asyncio.sleep(0.1)
        assert not publish_task.done()
        assert validated == []

    manifest = await publish_task
    assert validated == ["validated"]
    assert manifest.is_active


async def test_unlock_failure_invalidates_physical_connection(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalidated: list[AsyncConnection] = []
    original_invalidate = AsyncConnection.invalidate

    async def record_invalidate(connection: AsyncConnection) -> None:
        invalidated.append(connection)
        await original_invalidate(connection)

    async def fail_unlock(_connection: object, _key: str) -> None:
        raise RuntimeError("injected unlock failure")

    monkeypatch.setattr(AsyncConnection, "invalidate", record_invalidate)
    monkeypatch.setattr(publication, "_release_source_lock", fail_unlock)

    with pytest.raises(RuntimeError, match="injected unlock failure"):
        async with replacing_corpus_source(session_factory, Corpus.NCIT):
            pass

    assert len(invalidated) == 1


async def test_body_error_remains_primary_when_unlock_also_fails(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalidated: list[AsyncConnection] = []
    original_invalidate = AsyncConnection.invalidate

    async def record_invalidate(connection: AsyncConnection) -> None:
        invalidated.append(connection)
        await original_invalidate(connection)

    async def fail_unlock(_connection: object, _key: str) -> None:
        raise RuntimeError("unlock also failed")

    monkeypatch.setattr(AsyncConnection, "invalidate", record_invalidate)
    monkeypatch.setattr(publication, "_release_source_lock", fail_unlock)

    with pytest.raises(ValueError, match="source replacement failed") as captured:
        async with replacing_corpus_source(session_factory, Corpus.NCIT):
            raise ValueError("source replacement failed")

    assert any("unlock also failed" in note for note in captured.value.__notes__)
    assert len(invalidated) == 1


async def test_cancelled_lock_wait_releases_after_eventual_acquisition(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = session_factory.kw["bind"]
    key = "embedding:ncit"
    async with engine.connect() as blocker:
        await blocker.execute(
            text("SELECT pg_advisory_lock(hashtextextended(:corpus, 0))"),
            {"corpus": key},
        )
        await blocker.commit()

        async def replace() -> None:
            async with replacing_corpus_source(session_factory, Corpus.NCIT):
                pass

        task = asyncio.create_task(replace())
        await asyncio.sleep(0.1)
        task.cancel()
        await asyncio.sleep(0.1)
        assert not task.done()
        await blocker.execute(
            text("SELECT pg_advisory_unlock(hashtextextended(:corpus, 0))"),
            {"corpus": key},
        )
        await blocker.commit()

    with pytest.raises(asyncio.CancelledError):
        await task
    async with engine.connect() as probe:
        acquired = await probe.scalar(
            text("SELECT pg_try_advisory_lock(hashtextextended(:corpus, 0))"),
            {"corpus": key},
        )
        assert acquired
        await probe.execute(
            text("SELECT pg_advisory_unlock(hashtextextended(:corpus, 0))"),
            {"corpus": key},
        )


@pytest.mark.parametrize(
    ("state", "is_active", "expected", "actual", "sentinels", "error", "completed"),
    [
        ("building", True, 1, None, ["C3262"], None, None),
        ("failed", False, 1, None, ["C3262"], " ", None),
        ("complete", True, 2, 1, ["C3262"], None, "now"),
        ("complete", True, 1, 1, ["C3262", "C3262"], None, "now"),
    ],
)
async def test_manifest_constraints_reject_invalid_lifecycle_evidence(
    session_factory: async_sessionmaker[AsyncSession],
    state: str,
    is_active: bool,
    expected: int,
    actual: int | None,
    sentinels: list[str],
    error: str | None,
    completed: str | None,
) -> None:
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO embedding_corpus_manifest (build_id,corpus,state,"
                    "is_active,source_version,source_hash,model_id,model_revision,"
                    "vector_dimension,expected_row_count,actual_row_count,code_commit,"
                    "required_doc_ids,error_message,completed_at) VALUES ("
                    ":build_id,'ncit',:state,:is_active,'v',:hash,'m',:revision,768,"
                    ":expected,:actual,:commit,:sentinels,:error,"
                    "CASE WHEN CAST(:completed AS text) IS NULL "
                    "THEN NULL ELSE now() END)"
                ),
                {
                    "build_id": UUID("00000000-0000-0000-0000-000000000099"),
                    "state": state,
                    "is_active": is_active,
                    "hash": "a" * 64,
                    "revision": "b" * 40,
                    "expected": expected,
                    "actual": actual,
                    "commit": "c" * 40,
                    "sentinels": sentinels,
                    "error": error,
                    "completed": completed,
                },
            )


async def test_manifest_provenance_cannot_be_rewritten(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _publish_old(session_factory)

    with pytest.raises(DBAPIError, match="provenance is immutable"):
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE embedding_corpus_manifest SET source_hash = :changed "
                    "WHERE build_id = :build_id"
                ),
                {"changed": "f" * 64, "build_id": _OLD_BUILD},
            )


@pytest.mark.parametrize(
    ("source_hash", "model_revision", "code_commit"),
    [
        ("A" * 64, "b" * 40, "c" * 40),
        ("a" * 63, "b" * 40, "c" * 40),
        ("a" * 64, "z" * 40, "c" * 40),
        ("a" * 64, "b" * 40, "c" * 39),
    ],
)
async def test_database_rejects_malformed_provenance(
    session_factory: async_sessionmaker[AsyncSession],
    source_hash: str,
    model_revision: str,
    code_commit: str,
) -> None:
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO embedding_corpus_manifest (build_id,corpus,state,"
                    "source_version,source_hash,model_id,model_revision,"
                    "vector_dimension,expected_row_count,code_commit,required_doc_ids) "
                    "VALUES (:build_id,'ncit','building','v',:source_hash,'m',"
                    ":model_revision,768,1,:code_commit,ARRAY['C3262'])"
                ),
                {
                    "build_id": uuid4(),
                    "source_hash": source_hash,
                    "model_revision": model_revision,
                    "code_commit": code_commit,
                },
            )


async def test_manifest_build_id_cannot_be_rewritten(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _publish_old(session_factory)
    with pytest.raises(DBAPIError, match="provenance is immutable"):
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE embedding_corpus_manifest SET build_id = :changed "
                    "WHERE build_id = :build_id"
                ),
                {"changed": _NEW_BUILD, "build_id": _OLD_BUILD},
            )


async def test_cancellation_during_unlock_waits_for_release(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    original_scalar = AsyncConnection.scalar

    async def delayed_scalar(connection, statement, parameters=None, **kwargs):  # type: ignore[no-untyped-def]
        if "pg_advisory_unlock" in str(statement):
            entered.set()
            await release.wait()
        return await original_scalar(connection, statement, parameters, **kwargs)

    monkeypatch.setattr(AsyncConnection, "scalar", delayed_scalar)

    async def replace() -> None:
        async with replacing_corpus_source(session_factory, Corpus.NCIT):
            pass

    task = asyncio.create_task(replace())
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0.1)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
