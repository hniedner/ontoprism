"""Behavioral guards for the decomposition command wiring."""

import os
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
from ontolib.decomposition.run import SourceIdentityChangedError
from ontolib.repositories.xref.vocab import NCIT_UPSTREAM_XREF_GRAPH_IRI
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.ncit.sibling_store import (
    CandidateGraph,
    CandidateObservation,
)


@pytest.mark.unit
async def test_decomposition_output_load_streams_from_disk(tmp_path: Path) -> None:
    output = tmp_path / "decomposed.ttl"
    output.write_bytes(b"<urn:s> <urn:p> <urn:o> ." * 100_000)
    received: bytes | None = None

    class _Client:
        async def load(self, data: Any, **_kwargs: Any) -> None:
            nonlocal received
            assert not isinstance(data, bytes)
            received = data.read()

    await decompose._load_output(_Client(), output)  # type: ignore[arg-type]

    assert received == output.read_bytes()


@pytest.mark.unit
async def test_equivalence_refusal_precedes_settings_and_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_settings = MagicMock(side_effect=AssertionError("settings were loaded"))
    monkeypatch.setattr(decompose, "get_settings", get_settings)

    with pytest.raises(ValueError, match="not available"):
        await decompose._run(
            source_manifest=Path("unused-manifest.json"),
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MagicMock(side_effect=AssertionError("event loop started"))
    monkeypatch.setattr(decompose.asyncio, "run", run)

    with pytest.raises(typer.BadParameter, match="not available"):
        decompose.main(
            source_manifest=Path("unused-manifest.json"),
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
        Path("candidate/.ontoprism-ncit-candidate.json"),
        "http://127.0.0.1:7888",
    )

    assert snapshot.source_identity == "a" * 64
    assert snapshot.ontology_version == "26.07d"
    observe.assert_awaited_once_with("http://127.0.0.1:7888")


@pytest.mark.unit
async def test_source_snapshot_ignores_ontoprisms_own_published_graphs(
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
        Path("candidate/.ontoprism-ncit-candidate.json"),
        "http://127.0.0.1:7888",
    )

    assert snapshot.source_identity == "a" * 64


@pytest.mark.unit
async def test_source_snapshot_rejects_endpoint_observation_mismatch(
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
            Path("candidate/.ontoprism-ncit-candidate.json"),
            "http://127.0.0.1:7888",
        )


@pytest.mark.unit
async def test_source_snapshot_rejects_an_unexpected_extra_named_graph(
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
            Path("candidate/.ontoprism-ncit-candidate.json"),
            "http://127.0.0.1:7888",
        )
