"""Behavioral guards for the decomposition command wiring."""

from __future__ import annotations

import asyncio
import os
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import typer
from click import unstyle
from scripts import decompose

from ontolib.decomposition import vocab
from ontolib.decomposition.run import RunProgress, SourceIdentityChangedError
from ontolib.decomposition.sampling import (
    REQUIRED_SAMPLE_STRATA,
    DecompositionSampleManifest,
    SampleConcept,
)
from ontolib.repositories.xref.vocab import NCIT_UPSTREAM_XREF_GRAPH_IRI
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.ncit.sibling_store import (
    CandidateGraph,
    CandidateObservation,
)


@pytest.mark.unit
def test_progress_message_reports_resume_rate_eta_and_active_concept() -> None:
    progress = RunProgress(
        run_id="neoplasm-run",
        phase="heartbeat",
        concept_code="C219638",
        completed=7324,
        total=15633,
        session_completed=3404,
        elapsed_seconds=1702.0,
    )

    assert decompose._progress_message(progress) == (
        "run=neoplasm-run phase=heartbeat completed=7324/15633 "
        "active=C219638 elapsed=1702s rate=2.00/s eta=4154s"
    )


@pytest.mark.unit
def test_progress_message_suppresses_nonmilestone_completions() -> None:
    progress = RunProgress(
        run_id="run",
        phase="completed",
        concept_code="C1",
        completed=101,
        total=1000,
        session_completed=101,
        elapsed_seconds=10.0,
    )

    assert decompose._progress_message(progress) is None


@pytest.mark.unit
def test_residual_progress_prints_milestones(
    capsys: pytest.CaptureFixture[str],
) -> None:
    decompose._print_residual_progress(100, 450, "C123")
    decompose._print_residual_progress(101, 450, "C124")

    assert capsys.readouterr().err == (
        "phase=residual-metric completed=100/450 active=C123\n"
    )


class _RunClient:
    def __init__(
        self,
        events: list[str],
        cleanup_error: BaseException | None = None,
    ) -> None:
        self.events = events
        self.cleanup_error = cleanup_error

    async def __aenter__(self) -> _RunClient:
        self.events.append("client-enter")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: object,
    ) -> None:
        self.events.append(
            "client-exit-clean" if exc_type is None else "client-exit-error"
        )
        if self.cleanup_error is not None:
            raise self.cleanup_error


class _RunLabelStore:
    async def labels_for(self, _codes: list[str]) -> dict[str, str]:
        return {}

    async def search(self, _term: str, *, limit: int) -> SimpleNamespace:
        assert limit == 5
        return SimpleNamespace(hits=[])


def _install_run_collaborators(
    monkeypatch: pytest.MonkeyPatch,
    pipeline: Any,
    *,
    cleanup_error: BaseException | None = None,
    client_cleanup_error: BaseException | None = None,
) -> SimpleNamespace:
    events: list[str] = []
    engine = object()
    client = _RunClient(events, client_cleanup_error)
    store = _RunLabelStore()
    provenance = object()
    settings = SimpleNamespace(
        database_url="postgresql+asyncpg://unused",
        ncit_sparql_url="http://unused",
    )

    async def dispose(actual_engine: object) -> None:
        assert actual_engine is engine
        events.append("dispose")
        if cleanup_error is not None:
            raise cleanup_error

    monkeypatch.setattr(decompose, "get_settings", lambda: settings)
    monkeypatch.setattr(
        decompose,
        "make_engine",
        lambda url: engine if url == settings.database_url else None,
    )
    monkeypatch.setattr(
        decompose,
        "make_sessionmaker",
        lambda actual_engine: (
            "session-factory" if actual_engine is engine else "wrong-engine"
        ),
    )
    monkeypatch.setattr(
        decompose,
        "ProvenanceStore",
        lambda sf: provenance if sf == "session-factory" else None,
    )
    monkeypatch.setattr(
        decompose,
        "ncit_sparql_client",
        lambda url: client if url == settings.ncit_sparql_url else None,
    )
    monkeypatch.setattr(
        decompose,
        "NcitGraphStore",
        lambda actual_client: store if actual_client is client else None,
    )
    monkeypatch.setattr(decompose, "run_pipeline", pipeline)
    monkeypatch.setattr(decompose, "dispose_engine", dispose)
    return SimpleNamespace(
        events=events,
        engine=engine,
        client=client,
        store=store,
        provenance=provenance,
        settings=settings,
    )


def _sample_manifest() -> DecompositionSampleManifest:
    return DecompositionSampleManifest(
        name="ncit-26.07d-review",
        branch="neoplasm",
        scope_root="C3262",
        scope_version="stated-genus-subclass-v1",
        source_identity="a" * 64,
        ontology_version="26.07d",
        selection_method="explicit-stratified",
        seed=None,
        concepts=(
            SampleConcept(
                code="C27262",
                strata=tuple(sorted(REQUIRED_SAMPLE_STRATA)),
                rationale="Known nested-definition hard case.",
            ),
        ),
    )


@pytest.mark.unit
async def test_label_lookup_requires_normalized_exact_label() -> None:
    calls: list[tuple[str, int]] = []

    class _SearchStore:
        async def search(self, term: str, *, limit: int) -> SimpleNamespace:
            calls.append((term, limit))
            return SimpleNamespace(
                hits=[
                    SimpleNamespace(code="C0", label=None),
                    SimpleNamespace(code="C1", label="Near match"),
                    SimpleNamespace(code="C2", label="  Exact Match  "),
                ]
            )

    lookup = decompose._make_label_lookup(_SearchStore())  # type: ignore[arg-type]

    assert await lookup(" exact match ") == "C2"
    assert await lookup("missing") is None
    assert calls == [(" exact match ", 5), ("missing", 5)]


@pytest.mark.unit
async def test_load_is_coordinated_inside_pipeline_before_run_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "decomposed.ttl"
    settings = SimpleNamespace(
        database_url="postgresql+asyncpg://unused",
        ncit_sparql_url="http://unused",
    )
    monkeypatch.setattr(decompose, "get_settings", lambda: settings)
    engine = object()
    monkeypatch.setattr(decompose, "make_engine", lambda _url: engine)
    monkeypatch.setattr(decompose, "make_sessionmaker", MagicMock())
    monkeypatch.setattr(decompose, "dispose_engine", AsyncMock())
    client = MagicMock()
    client.load = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(decompose, "ncit_sparql_client", lambda _url: client)
    store = SimpleNamespace(labels_for=AsyncMock())
    monkeypatch.setattr(decompose, "NcitGraphStore", lambda _client: store)
    pipeline = AsyncMock(return_value=decompose.RunMetrics())
    monkeypatch.setattr(decompose, "run_pipeline", pipeline)

    await decompose._run(
        source_manifest=tmp_path / "candidate.json",
        branch="neoplasm",
        out=output,
        load=True,
        emit_equivalence=False,
        resume=None,
        total_limit=None,
    )

    config = pipeline.await_args.args[0]
    assert config.out == output
    assert config.load_to_store is True
    client.load.assert_not_awaited()


@pytest.mark.unit
async def test_file_only_resume_wires_exact_pipeline_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    expected = decompose.RunMetrics(total_in_scope=3, decomposed=2, residual=1)

    async def pipeline(
        config: object,
        client: object,
        provenance: object,
        **kwargs: object,
    ) -> decompose.RunMetrics:
        captured.update(
            config=config,
            client=client,
            provenance=provenance,
            kwargs=kwargs,
        )
        harness.events.append("pipeline")
        return expected

    harness = _install_run_collaborators(monkeypatch, pipeline)
    output = tmp_path / "review.ttl"

    actual = await decompose._run(
        source_manifest=tmp_path / "candidate.json",
        branch="disease",
        out=output,
        load=False,
        emit_equivalence=False,
        resume="disease-run-1",
        total_limit=7,
        walker_max_depth=3,
    )

    assert actual is expected
    config = captured["config"]
    assert isinstance(config, decompose.RunConfig)
    assert config.branch.value == "disease"
    assert config.out == output
    assert config.load_to_store is False
    assert config.resume_from == "disease-run-1"
    assert config.walker_max_depth == 3
    assert captured["client"] is harness.client
    assert captured["provenance"] is harness.provenance
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["total_limit"] == 7
    assert callable(kwargs["get_source_snapshot"])
    assert callable(kwargs["get_labels"])
    assert callable(kwargs["label_lookup"])
    assert harness.events == [
        "client-enter",
        "pipeline",
        "client-exit-clean",
        "dispose",
    ]


@pytest.mark.unit
async def test_pipeline_failure_propagates_after_client_and_engine_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = RuntimeError("pipeline unavailable")

    async def pipeline(*_args: object, **_kwargs: object) -> decompose.RunMetrics:
        harness.events.append("pipeline")
        raise primary

    harness = _install_run_collaborators(monkeypatch, pipeline)

    with pytest.raises(RuntimeError, match="pipeline unavailable") as exc_info:
        await decompose._run(
            tmp_path / "candidate.json",
            decompose.DecompositionBranch.NEOPLASM,
            None,
            False,
            False,
            None,
            None,
        )

    assert exc_info.value is primary
    assert harness.events == [
        "client-enter",
        "pipeline",
        "client-exit-error",
        "dispose",
    ]


@pytest.mark.unit
async def test_client_cleanup_failure_does_not_mask_pipeline_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = RuntimeError("pipeline unavailable")
    cleanup = RuntimeError("client cleanup failed")

    async def pipeline(*_args: object, **_kwargs: object) -> decompose.RunMetrics:
        harness.events.append("pipeline")
        raise primary

    harness = _install_run_collaborators(
        monkeypatch,
        pipeline,
        client_cleanup_error=cleanup,
    )

    with pytest.raises(RuntimeError, match="pipeline unavailable") as exc_info:
        await decompose._run(
            tmp_path / "candidate.json",
            decompose.DecompositionBranch.NEOPLASM,
            None,
            False,
            False,
            None,
            None,
        )

    assert exc_info.value is primary
    assert any("client cleanup failed" in note for note in primary.__notes__)
    assert harness.events[-1] == "dispose"


@pytest.mark.unit
async def test_cancellation_propagates_after_client_and_engine_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def pipeline(*_args: object, **_kwargs: object) -> decompose.RunMetrics:
        harness.events.append("pipeline")
        raise asyncio.CancelledError

    harness = _install_run_collaborators(monkeypatch, pipeline)

    with pytest.raises(asyncio.CancelledError):
        await decompose._run(
            tmp_path / "candidate.json",
            decompose.DecompositionBranch.NEOPLASM,
            None,
            False,
            False,
            None,
            None,
        )

    assert harness.events == [
        "client-enter",
        "pipeline",
        "client-exit-error",
        "dispose",
    ]


@pytest.mark.unit
async def test_cleanup_failure_after_success_is_not_reported_as_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def pipeline(*_args: object, **_kwargs: object) -> decompose.RunMetrics:
        harness.events.append("pipeline")
        return decompose.RunMetrics()

    cleanup = RuntimeError("engine cleanup failed")
    harness = _install_run_collaborators(
        monkeypatch,
        pipeline,
        cleanup_error=cleanup,
    )

    with pytest.raises(RuntimeError, match="engine cleanup failed") as exc_info:
        await decompose._run(
            tmp_path / "candidate.json",
            decompose.DecompositionBranch.NEOPLASM,
            None,
            False,
            False,
            None,
            None,
        )

    assert exc_info.value is cleanup
    assert harness.events[-1] == "dispose"


@pytest.mark.unit
async def test_cleanup_failure_is_metadata_on_primary_pipeline_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = ValueError("pipeline failed")
    cleanup = RuntimeError("engine cleanup failed")

    async def pipeline(*_args: object, **_kwargs: object) -> decompose.RunMetrics:
        raise primary

    _install_run_collaborators(
        monkeypatch,
        pipeline,
        cleanup_error=cleanup,
    )

    with pytest.raises(ValueError, match="pipeline failed") as exc_info:
        await decompose._run(
            tmp_path / "candidate.json",
            decompose.DecompositionBranch.NEOPLASM,
            None,
            False,
            False,
            None,
            None,
        )

    assert exc_info.value is primary
    assert any("engine cleanup failed" in note for note in primary.__notes__)


@pytest.mark.unit
async def test_cleanup_cancellation_does_not_mask_primary_pipeline_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = ValueError("pipeline failed")

    async def pipeline(*_args: object, **_kwargs: object) -> decompose.RunMetrics:
        raise primary

    _install_run_collaborators(
        monkeypatch,
        pipeline,
        cleanup_error=asyncio.CancelledError(),
    )

    with pytest.raises(ValueError, match="pipeline failed") as exc_info:
        await decompose._run(
            tmp_path / "candidate.json",
            decompose.DecompositionBranch.NEOPLASM,
            None,
            False,
            False,
            None,
            None,
        )

    assert exc_info.value is primary
    assert any("CancelledError" in note for note in primary.__notes__)


@pytest.mark.unit
async def test_equivalence_refusal_precedes_settings_and_clients(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_settings = MagicMock(side_effect=AssertionError("settings were loaded"))
    monkeypatch.setattr(decompose, "get_settings", get_settings)

    with pytest.raises(ValueError, match="not available"):
        await decompose._run(
            source_manifest=tmp_path / "unused-manifest.json",
            branch="neoplasm",
            out=Path("unused.ttl"),
            load=True,
            emit_equivalence=True,
            resume=None,
            total_limit=None,
        )

    get_settings.assert_not_called()


@pytest.mark.unit
def test_cli_rejects_equivalence_before_starting_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MagicMock(side_effect=AssertionError("event loop started"))
    monkeypatch.setattr(decompose.asyncio, "run", run)

    with pytest.raises(typer.BadParameter, match="not available"):
        decompose.main(
            source_manifest=tmp_path / "unused-manifest.json",
            branch="neoplasm",
            out=None,
            load=False,
            emit_equivalence=True,
            resume=None,
            total_limit=None,
            walker_max_depth=5,
        )

    run.assert_not_called()


@pytest.mark.unit
def test_cli_rejects_load_without_output_before_starting_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MagicMock(side_effect=AssertionError("event loop started"))
    monkeypatch.setattr(decompose.asyncio, "run", run)

    with pytest.raises(typer.BadParameter, match="requires --out"):
        decompose.main(
            source_manifest=tmp_path / "unused-manifest.json",
            branch="neoplasm",
            out=None,
            load=True,
            emit_equivalence=False,
            resume=None,
            total_limit=None,
            walker_max_depth=5,
        )

    run.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("out", "load", "total_limit", "message"),
    [
        (None, False, None, "requires --out"),
        (Path("review.ttl"), True, None, "cannot be combined with --load"),
        (Path("review.ttl"), False, 1, "mutually exclusive"),
    ],
)
def test_cli_rejects_unsafe_sample_modes_before_starting_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    out: Path | None,
    load: bool,
    total_limit: int | None,
    message: str,
) -> None:
    run = MagicMock(side_effect=AssertionError("event loop started"))
    monkeypatch.setattr(decompose.asyncio, "run", run)

    with pytest.raises(typer.BadParameter, match=message):
        decompose.main(
            source_manifest=tmp_path / "unused-manifest.json",
            branch="neoplasm",
            out=out,
            load=load,
            emit_equivalence=False,
            resume=None,
            total_limit=total_limit,
            walker_max_depth=5,
            sample_manifest=tmp_path / "sample.json",
        )

    run.assert_not_called()


@pytest.mark.unit
async def test_invalid_sample_manifest_fails_before_settings_are_loaded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    malformed = tmp_path / "sample.json"
    malformed.write_text("{not-json", encoding="utf-8")
    get_settings = MagicMock(side_effect=AssertionError("settings were loaded"))
    monkeypatch.setattr(decompose, "get_settings", get_settings)

    with pytest.raises(ValueError, match="valid sample manifest"):
        await decompose._run(
            source_manifest=tmp_path / "unused-manifest.json",
            branch="neoplasm",
            out=tmp_path / "review.ttl",
            load=False,
            emit_equivalence=False,
            resume=None,
            total_limit=None,
            sample_manifest=malformed,
        )

    get_settings.assert_not_called()


@pytest.mark.unit
async def test_sample_manifest_is_loaded_and_wired_to_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = _sample_manifest()
    sample_path = tmp_path / "sample.json"
    sample_path.write_text(sample.model_dump_json(indent=2), encoding="utf-8")
    pipeline = AsyncMock(return_value=decompose.RunMetrics(total_in_scope=1))
    _install_run_collaborators(monkeypatch, pipeline)

    metrics = await decompose._run(
        source_manifest=tmp_path / "candidate.json",
        branch="neoplasm",
        out=tmp_path / "review.ttl",
        load=False,
        emit_equivalence=False,
        resume=None,
        total_limit=None,
        sample_manifest=sample_path,
    )

    config = pipeline.await_args.args[0]
    assert metrics.total_in_scope == 1
    assert config.sample_manifest == sample
    assert config.sample_manifest.identity == sample.identity


@pytest.mark.unit
def test_main_prints_metrics_and_forwards_resume_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    async def run_command(*args: object) -> decompose.RunMetrics:
        captured["args"] = args
        return decompose.RunMetrics(
            total_in_scope=4,
            decomposed=2,
            residual=0,
            semantic_excluded=1,
            atomic_noop=1,
            minted_count=1,
            residual_precoordinated_count=1,
        )

    monkeypatch.setattr(decompose, "_run", run_command)
    output = tmp_path / "decomposed.ttl"

    decompose.main(
        source_manifest=tmp_path / "candidate.json",
        branch=decompose.DecompositionBranch.DISEASE,
        out=output,
        load=False,
        emit_equivalence=False,
        resume="disease-run-1",
        total_limit=4,
        walker_max_depth=6,
    )

    assert captured["args"] == (
        tmp_path / "candidate.json",
        decompose.DecompositionBranch.DISEASE,
        output,
        False,
        False,
        "disease-run-1",
        4,
        6,
        None,
    )
    assert capsys.readouterr().out == (
        "in_scope=4 decomposed=2 residual=0 semantic_excluded=1 atomic_noop=1 "
        "unknown=0 minted=1 coverage=50.00% "
        "residual_precoordination=50.00% (1/2)\n"
    )


@pytest.mark.unit
def test_main_propagates_pipeline_failure_without_success_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fail_command(*_args: object) -> decompose.RunMetrics:
        raise RuntimeError("pipeline unavailable")

    monkeypatch.setattr(decompose, "_run", fail_command)

    with pytest.raises(RuntimeError, match="pipeline unavailable"):
        decompose.main(
            source_manifest=tmp_path / "candidate.json",
            branch=decompose.DecompositionBranch.NEOPLASM,
            out=None,
            load=False,
            emit_equivalence=False,
            resume=None,
            total_limit=None,
            walker_max_depth=5,
        )

    assert capsys.readouterr().out == ""


@pytest.mark.unit
def test_command_rejects_equivalence_at_real_cli_boundary() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "DATABASE_URL": "invalid://must-not-be-used",
        "NCIT_SPARQL_URL": "invalid://must-not-be-used",
        "FORCE_COLOR": "0",
        "NO_COLOR": "1",
    }

    result = subprocess.run(
        [
            sys.executable,
            "scripts/decompose.py",
            "--source-manifest",
            "unused-manifest.json",
            "--emit-equivalence",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    error_text = " ".join(unstyle(result.stderr).split())
    assert "--emit-equivalence is not available" in error_text


@pytest.mark.unit
def test_command_rejects_load_without_output_at_real_cli_boundary() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "DATABASE_URL": "invalid://must-not-be-used",
        "NCIT_SPARQL_URL": "invalid://must-not-be-used",
        "FORCE_COLOR": "0",
        "NO_COLOR": "1",
    }

    result = subprocess.run(
        [
            sys.executable,
            "scripts/decompose.py",
            "--source-manifest",
            "unused-manifest.json",
            "--load",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "--load requires --out" in " ".join(unstyle(result.stderr).split())


@pytest.mark.unit
def test_command_rejects_sample_without_output_at_real_cli_boundary() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "DATABASE_URL": "invalid://must-not-be-used",
        "NCIT_SPARQL_URL": "invalid://must-not-be-used",
        "FORCE_COLOR": "0",
        "NO_COLOR": "1",
    }

    result = subprocess.run(
        [
            sys.executable,
            "scripts/decompose.py",
            "--source-manifest",
            "unused-manifest.json",
            "--sample-manifest",
            "unused-sample.json",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "--sample-manifest requires --out" in " ".join(
        unstyle(result.stderr).split()
    )


@pytest.mark.unit
def test_command_rejects_regimen_at_real_cli_boundary() -> None:
    """The unimplemented algorithm is rejected before settings are loaded."""
    repo_root = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "DATABASE_URL": "invalid://must-not-be-used",
        "NCIT_SPARQL_URL": "invalid://must-not-be-used",
        "FORCE_COLOR": "0",
        "NO_COLOR": "1",
    }

    result = subprocess.run(
        [
            sys.executable,
            "scripts/decompose.py",
            "--source-manifest",
            "unused-manifest.json",
            "--branch",
            "regimen",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    error_text = " ".join(unstyle(result.stderr).split())
    assert "Invalid value for '--branch'" in error_text
    assert "neoplasm" in error_text
    assert "disease" in error_text


@pytest.mark.unit
def test_module_entry_point_registers_main_with_typer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered: list[object] = []
    monkeypatch.setattr(typer, "run", registered.append)

    runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "scripts" / "decompose.py"),
        run_name="__main__",
    )

    assert len(registered) == 1
    assert getattr(registered[0], "__name__", None) == "main"


def _observation(*extra_graphs: str) -> CandidateObservation:
    """A production-shaped candidate observation plus any additive graphs."""
    return CandidateObservation(
        default_triples=12_500_000,
        stated_triples=10_800_000,
        named_graphs=(
            CandidateGraph(graph_iri=STATED_GRAPH_IRI, triples=10_800_000),
            *(CandidateGraph(graph_iri=iri, triples=42) for iri in extra_graphs),
        ),
        default_version="26.07d",
        stated_version="26.07d",
        restriction_count=149_694,
        has_required_restriction=True,
        default_has_stated_only_sentinel=False,
        stated_has_stated_only_sentinel=True,
    )


def _manifest(observation: CandidateObservation) -> SimpleNamespace:
    return SimpleNamespace(
        source_identity="a" * 64,
        ontology_version="26.07d",
        observation=observation,
    )


@pytest.mark.unit
async def test_source_snapshot_binds_live_candidate_to_revalidated_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation = _observation()
    monkeypatch.setattr(
        decompose,
        "validate_ncit_sibling_manifest",
        lambda _path: _manifest(observation),
    )
    observe = AsyncMock(return_value=observation)
    monkeypatch.setattr(decompose, "observe_ncit_candidate", observe)

    snapshot = await decompose._source_snapshot(
        tmp_path / "candidate/.ontoprism-ncit-candidate.json",
        "http://127.0.0.1:7888",
    )

    assert snapshot.source_identity == "a" * 64
    assert snapshot.ontology_version == "26.07d"
    observe.assert_awaited_once_with("http://127.0.0.1:7888")


@pytest.mark.unit
async def test_source_snapshot_ignores_ontoprisms_own_published_graphs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--load` publishes into the same store; that must not read as source drift.

    Without this, the first `decompose --load` would make every subsequent run of
    the same candidate manifest fail permanently, including a resume.
    """
    monkeypatch.setattr(
        decompose,
        "validate_ncit_sibling_manifest",
        lambda _path: _manifest(_observation()),
    )
    monkeypatch.setattr(
        decompose,
        "observe_ncit_candidate",
        AsyncMock(
            return_value=_observation(
                vocab.DECOMPOSED_GRAPH_IRI, NCIT_UPSTREAM_XREF_GRAPH_IRI
            )
        ),
    )

    snapshot = await decompose._source_snapshot(
        tmp_path / "candidate/.ontoprism-ncit-candidate.json",
        "http://127.0.0.1:7888",
    )

    assert snapshot.source_identity == "a" * 64


@pytest.mark.unit
async def test_source_snapshot_rejects_endpoint_observation_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        decompose,
        "validate_ncit_sibling_manifest",
        lambda _path: _manifest(_observation()),
    )
    drifted = _observation().model_copy(update={"stated_version": "26.08a"})
    monkeypatch.setattr(
        decompose,
        "observe_ncit_candidate",
        AsyncMock(return_value=drifted),
    )

    with pytest.raises(SourceIdentityChangedError, match="observation"):
        await decompose._source_snapshot(
            tmp_path / "candidate/.ontoprism-ncit-candidate.json",
            "http://127.0.0.1:7888",
        )


@pytest.mark.unit
async def test_source_snapshot_rejects_an_unexpected_extra_named_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only ontoprism's own additive graphs are ignorable, not any extra graph."""
    monkeypatch.setattr(
        decompose,
        "validate_ncit_sibling_manifest",
        lambda _path: _manifest(_observation()),
    )
    monkeypatch.setattr(
        decompose,
        "observe_ncit_candidate",
        AsyncMock(return_value=_observation("http://example.invalid/other-graph")),
    )

    with pytest.raises(SourceIdentityChangedError, match="observation"):
        await decompose._source_snapshot(
            tmp_path / "candidate/.ontoprism-ncit-candidate.json",
            "http://127.0.0.1:7888",
        )
