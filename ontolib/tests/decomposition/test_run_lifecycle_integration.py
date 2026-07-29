"""Real-Postgres contracts for exact, source-bound decomposition runs."""

from __future__ import annotations

import asyncio
import datetime
from typing import TYPE_CHECKING

import asyncpg
import pytest
from sqlalchemy.exc import IntegrityError

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition import run as run_module
from ontolib.decomposition.minting import MintedConcept
from ontolib.decomposition.models import Constituent, Decomposition
from ontolib.decomposition.provenance import (
    ProvenanceStore,
    RunIdentityMismatchError,
    RunStateError,
)
from ontolib.decomposition.provenance_models import (
    NcitSourceSnapshot,
    RunFingerprint,
    RunResumeIdentity,
)
from ontolib.decomposition.run import RunConfig, _new_run_id, run_pipeline

if TYPE_CHECKING:
    from collections.abc import Collection
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings"),
]


def _dsn() -> str:
    return get_settings().database_url.replace("+asyncpg", "")


def _fingerprint(*, source: str = "a" * 64) -> RunFingerprint:
    return RunFingerprint(
        source_identity=source,
        branch="neoplasm",
        semantic_types=("Disease or Syndrome", "Neoplastic Process"),
        worklist=("C0", "C1"),
        total_limit=2,
        algorithm_version="decomposition-v1",
        config_version="axes-v1",
        walker_max_depth=5,
        output_mode="file",
        load_mode="none",
        emitted_at=datetime.datetime(2026, 7, 29, 12, 0, tzinfo=datetime.UTC),
    )


async def _cleanup(run_ids: list[str]) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "DELETE FROM decomp_constituent WHERE run_id = ANY($1)", run_ids
        )
        await conn.execute("DELETE FROM minted_concept WHERE run_id = ANY($1)", run_ids)
        await conn.execute("DELETE FROM decomp_run WHERE id = ANY($1)", run_ids)
    finally:
        await conn.close()


class _LifecycleClient:
    async def version(self) -> str:
        return "26.07d"

    async def select(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str]]:
        del required_variables
        raise AssertionError(f"unexpected query: {query}")

    async def select_once(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str]]:
        return await self.select(query, required_variables=required_variables)


class _RecordingStore(ProvenanceStore):
    def __init__(self, sf: async_sessionmaker[AsyncSession]) -> None:
        super().__init__(sf)
        self.created: list[str] = []

    async def create_run(
        self,
        run_id: str,
        ncit_version: str,
        fingerprint: RunFingerprint,
    ) -> None:
        self.created.append(run_id)
        await super().create_run(run_id, ncit_version, fingerprint)


class _InterruptedDecomposer:
    def __init__(self) -> None:
        self._c2_failed = False

    async def __call__(
        self,
        code: str,
        *_args: object,
        **_kwargs: object,
    ) -> object:
        if code == "C2" and not self._c2_failed:
            self._c2_failed = True
            raise RuntimeError("injected interruption")
        return run_module._CandidateResult(
            decomposition=Decomposition(
                code=code,
                semantic_type="Neoplastic Process",
                constituents=[
                    Constituent(
                        axis="R88",
                        filler_code=f"F-{code}",
                        axis_source="role",
                    )
                ],
            )
        )


async def _two_codes(*_args: object, **_kwargs: object) -> list[str]:
    return ["C1", "C2"]


async def _no_residuals(*_args: object, **_kwargs: object) -> set[str]:
    return set()


async def _source() -> NcitSourceSnapshot:
    return NcitSourceSnapshot(
        source_identity="a" * 64,
        ontology_version="26.07d",
    )


@pytest.mark.parametrize("count", [2])
async def test_concurrent_starts_materialize_distinct_exact_worklists(
    count: int,
) -> None:
    run_ids = [_new_run_id("neoplasm") for _ in range(count)]
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    try:
        await asyncio.gather(
            *(store.create_run(run_id, "26.07d", _fingerprint()) for run_id in run_ids)
        )

        assert len(set(run_ids)) == count
        assert await store.pending_codes(run_ids[0]) == ["C0", "C1"]
        assert await store.pending_codes(run_ids[1]) == ["C0", "C1"]
    finally:
        await _cleanup(run_ids)
        await dispose_engine(engine)


async def test_zero_output_and_decomposition_complete_as_exact_work_items() -> None:
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    try:
        await store.create_run(run_id, "26.07d", _fingerprint())

        zero_claim = await store.claim_work_item(run_id, "C0")
        assert zero_claim is not None
        await store.complete_work_item(
            run_id,
            "C0",
            zero_claim,
            decomposition=None,
            minted=(),
        )
        assert await store.pending_codes(run_id) == ["C1"]

        decomposition = Decomposition(
            code="C1",
            semantic_type="Neoplastic Process",
            constituents=[
                Constituent(
                    axis="R101",
                    filler_code="C12400",
                    axis_source="role",
                    most_specific=True,
                    needs_review=True,
                    group="anatomy-1",
                )
            ],
        )
        mint = MintedConcept(axis="op:Laterality", label="Left")
        claim = await store.claim_work_item(run_id, "C1")
        assert claim is not None
        await store.complete_work_item(
            run_id,
            "C1",
            claim,
            decomposition=decomposition,
            minted=(mint,),
        )

        assert await store.pending_codes(run_id) == []
        assert await store.decompositions_for_run(run_id) == [decomposition]
        counts = await store.outcome_counts(run_id)
        assert counts.total_in_scope == 2
        assert counts.decomposed == 1
        assert counts.residual == 0
        assert counts.minted_count == 1
        assert await store.finish_run(
            run_id,
            source_identity="a" * 64,
            metrics={"total_in_scope": 2, "decomposed": 1},
        )

        with pytest.raises(RunStateError, match="complete"):
            await store.resume_run(
                run_id,
                RunResumeIdentity.from_fingerprint(_fingerprint()),
            )
    finally:
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_failed_atomic_replace_rolls_back_then_retries_without_stale_rows() -> (
    None
):
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    duplicate = Constituent(axis="R88", filler_code="C27970", axis_source="role")
    try:
        await store.create_run(run_id, "26.07d", _fingerprint())
        claim = await store.claim_work_item(run_id, "C1")
        assert claim is not None
        with pytest.raises(IntegrityError):
            await store.complete_work_item(
                run_id,
                "C1",
                claim,
                decomposition=Decomposition(
                    code="C1",
                    semantic_type="Neoplastic Process",
                    constituents=[duplicate, duplicate],
                ),
                minted=(),
            )
        await store.fail_work_item(
            run_id,
            "C1",
            claim,
            RuntimeError("x" * 2000),
        )

        conn = await asyncpg.connect(_dsn())
        try:
            row = await conn.fetchrow(
                "SELECT state, error_type, error_message FROM decomp_work_item "
                "WHERE run_id = $1 AND concept_code = 'C1'",
                run_id,
            )
            constituent_count = await conn.fetchval(
                "SELECT count(*) FROM decomp_constituent "
                "WHERE run_id = $1 AND concept_code = 'C1'",
                run_id,
            )
        finally:
            await conn.close()
        assert dict(row) == {
            "state": "failed",
            "error_type": "RuntimeError",
            "error_message": "x" * 1000,
        }
        assert constituent_count == 0

        resumed = await store.resume_run(
            run_id, RunResumeIdentity.from_fingerprint(_fingerprint())
        )
        assert resumed == _fingerprint()
        retry_claim = await store.claim_work_item(run_id, "C1")
        assert retry_claim is not None
        assert retry_claim != claim
        replacement = Decomposition(
            code="C1",
            semantic_type="Neoplastic Process",
            constituents=[duplicate],
        )
        await store.complete_work_item(
            run_id,
            "C1",
            retry_claim,
            decomposition=replacement,
            minted=(),
        )
        assert await store.decompositions_for_run(run_id) == [replacement]

        with pytest.raises(RunIdentityMismatchError, match="source"):
            await store.resume_run(
                run_id,
                RunResumeIdentity.from_fingerprint(_fingerprint(source="b" * 64)),
            )
    finally:
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_database_rejects_invalid_states_and_identity_mutation() -> None:
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    try:
        await store.create_run(run_id, "26.07d", _fingerprint())
        conn = await asyncpg.connect(_dsn())
        try:
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "UPDATE decomp_run SET status = 'looks-complete' WHERE id = $1",
                    run_id,
                )
            with pytest.raises(asyncpg.RaiseError, match="identity is immutable"):
                await conn.execute(
                    "UPDATE decomp_run SET source_identity = $2 WHERE id = $1",
                    run_id,
                    "b" * 64,
                )
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "UPDATE decomp_work_item SET state = 'done' "
                    "WHERE run_id = $1 AND concept_code = 'C0'",
                    run_id,
                )
        finally:
            await conn.close()
    finally:
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_resume_rejects_materialized_worklist_tampering() -> None:
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    try:
        await store.create_run(run_id, "26.07d", _fingerprint())
        conn = await asyncpg.connect(_dsn())
        try:
            await conn.execute(
                "DELETE FROM decomp_work_item "
                "WHERE run_id = $1 AND concept_code = 'C0'",
                run_id,
            )
        finally:
            await conn.close()

        with pytest.raises(RunIdentityMismatchError, match="worklist"):
            await store.resume_run(
                run_id,
                RunResumeIdentity.from_fingerprint(_fingerprint()),
            )
    finally:
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_invalid_completion_inputs_and_stale_claims_fail_closed() -> None:
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    claim = None
    try:
        await store.create_run(run_id, "26.07d", _fingerprint())
        claim = await store.claim_work_item(run_id, "C0")
        assert claim is not None
        wrong_code = Decomposition(
            code="C1",
            semantic_type="Neoplastic Process",
        )
        with pytest.raises(ValueError, match="code"):
            await store.complete_work_item(
                run_id,
                "C0",
                claim,
                decomposition=wrong_code,
                minted=(),
            )
        with pytest.raises(ValueError, match="require a decomposition"):
            await store.complete_work_item(
                run_id,
                "C0",
                claim,
                decomposition=None,
                minted=(MintedConcept(axis="op:Laterality", label="Left"),),
            )
        stale_claim = claim.__class__(int=claim.int + 1)
        with pytest.raises(RunStateError, match="not owned"):
            await store.complete_work_item(
                run_id,
                "C0",
                stale_claim,
                decomposition=None,
                minted=(),
            )
        with pytest.raises(RunStateError, match="claim changed"):
            await store.fail_work_item(
                run_id,
                "C0",
                stale_claim,
                RuntimeError("stale worker"),
            )
    finally:
        if claim is not None:
            await store.fail_work_item(
                run_id,
                "C0",
                claim,
                RuntimeError("test cleanup"),
            )
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_finish_and_resume_reject_invalid_run_identity_or_state() -> None:
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    try:
        await store.create_run(run_id, "26.07d", _fingerprint())
        with pytest.raises(RunIdentityMismatchError, match="completion source"):
            await store.finish_run(
                run_id,
                source_identity="b" * 64,
                metrics={},
            )
        with pytest.raises(RunStateError, match="unfinished"):
            await store.finish_run(
                run_id,
                source_identity="a" * 64,
                metrics={},
            )
        assert await store.fail_run(run_id, RuntimeError("stop")) is True
        assert await store.invalidate_run(run_id, RuntimeError("too late")) is False
        with pytest.raises(RunStateError, match="does not exist"):
            await store.resume_run(
                "missing-run",
                RunResumeIdentity.from_fingerprint(_fingerprint()),
            )
    finally:
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_source_swap_invalidation_removes_every_partial_snapshot() -> None:
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    try:
        await store.create_run(run_id, "26.07d", _fingerprint())
        for code in ("C0", "C1"):
            claim = await store.claim_work_item(run_id, code)
            assert claim is not None
            await store.complete_work_item(
                run_id,
                code,
                claim,
                decomposition=Decomposition(
                    code=code,
                    semantic_type="Neoplastic Process",
                    constituents=[
                        Constituent(
                            axis="R88",
                            filler_code=f"F-{code}",
                            axis_source="role",
                        )
                    ],
                ),
                minted=(),
            )

        invalidated = await store.invalidate_run(
            run_id,
            RuntimeError("source identity changed"),
        )

        assert invalidated is True
        assert await store.decompositions_for_run(run_id) == []
        assert await store.pending_codes(run_id) == ["C0", "C1"]
        await store.resume_run(
            run_id,
            RunResumeIdentity.from_fingerprint(_fingerprint()),
        )
        assert await store.pending_codes(run_id) == ["C0", "C1"]
    finally:
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_failed_then_resumed_run_matches_fresh_metrics_and_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_module, "_decompose_one", _InterruptedDecomposer())
    monkeypatch.setattr(run_module, "enumerate_in_scope_codes", _two_codes)
    monkeypatch.setattr(run_module, "_precoordinated_fillers", _no_residuals)

    engine = make_engine(get_settings().database_url)
    store = _RecordingStore(make_sessionmaker(engine))
    resumed_out = tmp_path / "resumed.ttl"
    fresh_out = tmp_path / "fresh.ttl"
    run_ids: list[str] = []
    try:
        with pytest.raises(RuntimeError, match="interruption"):
            await run_pipeline(
                RunConfig(branch="neoplasm", out=resumed_out),
                _LifecycleClient(),
                store,
                get_source_snapshot=_source,
            )
        interrupted_run = store.created[-1]
        run_ids.append(interrupted_run)

        resumed = await run_pipeline(
            RunConfig(
                branch="neoplasm",
                out=resumed_out,
                resume_from=interrupted_run,
            ),
            _LifecycleClient(),
            store,
            get_source_snapshot=_source,
        )
        fresh = await run_pipeline(
            RunConfig(branch="neoplasm", out=fresh_out),
            _LifecycleClient(),
            store,
            get_source_snapshot=_source,
        )
        fresh_run = store.created[-1]
        run_ids.append(fresh_run)

        assert resumed == fresh
        assert await store.decompositions_for_run(
            interrupted_run
        ) == await store.decompositions_for_run(fresh_run)
        normalized_resumed = resumed_out.read_text().replace(
            interrupted_run, "<RUN_ID>"
        )
        normalized_fresh = fresh_out.read_text().replace(fresh_run, "<RUN_ID>")
        assert normalized_resumed == normalized_fresh
    finally:
        await _cleanup(run_ids)
        await dispose_engine(engine)
