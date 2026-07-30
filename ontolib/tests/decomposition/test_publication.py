"""Behavioral contracts for decomposition artifact and graph publication."""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
import rdflib

from ontolib.decomposition import vocab
from ontolib.decomposition.legacy_writer import write_ttl
from ontolib.decomposition.models import Constituent, Decomposition
from ontolib.decomposition.provenance import RunStateError
from ontolib.decomposition.publication import (
    PublicationMarker,
    PublicationValidationError,
    _record_failure_without_masking,
    build_replacement_update,
    publish_artifact,
    read_publication_marker,
    staging_graph_iri,
    validate_artifact,
)
from ontolib.terminologies.namespaces import NCIT_NS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Collection, Mapping, Sequence
    from io import BufferedReader
    from pathlib import Path


def _decomposition(code: str = "C1") -> Decomposition:
    return Decomposition(
        code=code,
        semantic_type="Neoplastic Process",
        constituents=[
            Constituent(
                axis="op:PrimarySite",
                filler_code="C12400",
                axis_source="role",
                source_role="R101",
            )
        ],
    )


def _marker_rows(marker: PublicationMarker) -> list[dict[str, str]]:
    return [
        {"predicate": str(predicate), "value": str(value)}
        for predicate, value in (
            (rdflib.RDF.type, vocab.PUBLICATION_CLASS),
            (vocab.PUBLICATION_RUN, marker.run_id),
            (vocab.PUBLICATION_SOURCE_IDENTITY, marker.source_identity),
            (
                vocab.PUBLICATION_REPRESENTATION_IDENTITY,
                marker.representation_identity,
            ),
            (vocab.PUBLICATION_BUILT_AT, marker.built_at_lexical),
        )
    ]


class _GraphClient:
    def __init__(
        self,
        marker_rows: list[dict[str, str]] | None = None,
        *,
        update_error: BaseException | None = None,
        marker_on_update_error: PublicationMarker | None = None,
    ) -> None:
        self.marker_rows = marker_rows or []
        self.update_error = update_error
        self.marker_on_update_error = marker_on_update_error
        self.events: list[str] = []
        self.loaded_payload: bytes | None = None
        self.loaded_graph: str | None = None

    async def select_once(
        self,
        _query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> Sequence[Mapping[str, str | None]]:
        assert set(required_variables) == {"predicate", "value"}
        self.events.append("read-marker")
        return self.marker_rows

    async def load(
        self,
        data: bytes | BufferedReader,
        *,
        content_type: str,
        graph_iri: str | None = None,
        replace: bool = True,
    ) -> None:
        assert content_type == "text/turtle"
        assert replace is True
        self.events.append("stage")
        self.loaded_payload = data if isinstance(data, bytes) else data.read()
        self.loaded_graph = graph_iri

    async def update(self, _update: str) -> None:
        self.events.append("replace")
        if self.update_error is not None:
            if self.marker_on_update_error is not None:
                self.marker_rows = _marker_rows(self.marker_on_update_error)
            raise self.update_error


class _BlockingGraphClient(_GraphClient):
    def __init__(self) -> None:
        super().__init__()
        self.update_started = asyncio.Event()

    async def update(self, _update: str) -> None:
        self.events.append("replace")
        self.update_started.set()
        await asyncio.Event().wait()


class _PublicationStore:
    def __init__(
        self,
        *,
        destination: Path,
        persisted_built_at: datetime | None = None,
        summary_state: str | None = None,
        missing: bool = False,
        begin_error: BaseException | None = None,
        record_error: BaseException | None = None,
    ) -> None:
        self.destination = destination
        self.persisted_built_at = persisted_built_at
        self.summary_state = summary_state
        self.missing = missing
        self.begin_error = begin_error
        self.record_error = record_error
        self.events: list[str] = []
        self.failures: list[BaseException] = []
        self.finished_identity: str | None = None

    @asynccontextmanager
    async def publication_lock(self) -> AsyncIterator[None]:
        self.events.append("lock")
        try:
            yield
        finally:
            self.events.append("unlock")

    async def get_run(self, _run_id: str) -> Any:
        if self.missing:
            return None
        return SimpleNamespace(
            publication_state=(
                self.summary_state
                or ("failed" if self.persisted_built_at is not None else "pending")
            ),
            publication_built_at=self.persisted_built_at,
        )

    async def begin_publication(self, _run_id: str, **_kwargs: object) -> None:
        self.events.append("begin")
        if self.begin_error is not None:
            raise self.begin_error

    async def record_publication_failure(
        self,
        _run_id: str,
        error: BaseException,
    ) -> None:
        self.events.append("failure")
        self.failures.append(error)
        if self.record_error is not None:
            raise self.record_error

    async def finish_run(
        self,
        _run_id: str,
        *,
        source_identity: str,
        metrics: dict[str, object],
        representation_identity: str | None = None,
    ) -> bool:
        assert source_identity == "a" * 64
        assert metrics == {"decomposed": 1}
        assert self.destination.read_text(encoding="utf-8").endswith("\n")
        self.events.append("finish")
        self.finished_identity = representation_identity
        return True


@pytest.mark.unit
async def test_artifact_validation_binds_exact_codes_run_and_bytes(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "decomposed.ttl"
    await write_ttl(
        [_decomposition()],
        artifact,
        run_id="neoplasm-run-1",
        emitted_on=datetime(2026, 7, 30, tzinfo=UTC).date(),
    )

    identity = validate_artifact(
        artifact,
        expected_codes={"C1"},
        run_id="neoplasm-run-1",
    )

    assert identity == hashlib.sha256(artifact.read_bytes()).hexdigest()

    with pytest.raises(PublicationValidationError, match="expected concept set"):
        validate_artifact(
            artifact,
            expected_codes={"C2"},
            run_id="neoplasm-run-1",
        )
    with pytest.raises(PublicationValidationError, match="run identifier"):
        validate_artifact(
            artifact,
            expected_codes={"C1"},
            run_id="neoplasm-run-2",
        )


@pytest.mark.unit
async def test_empty_decomposition_artifact_is_valid_and_malformed_turtle_is_not(
    tmp_path: Path,
) -> None:
    empty_artifact = tmp_path / "empty.ttl"
    await write_ttl([], empty_artifact, run_id="neoplasm-empty")

    assert (
        validate_artifact(
            empty_artifact,
            expected_codes=set(),
            run_id="neoplasm-empty",
        )
        == hashlib.sha256(empty_artifact.read_bytes()).hexdigest()
    )

    malformed = tmp_path / "malformed.ttl"
    malformed.write_text("this is not Turtle {", encoding="utf-8")
    with pytest.raises(PublicationValidationError, match="valid Turtle"):
        validate_artifact(
            malformed,
            expected_codes=set(),
            run_id="neoplasm-empty",
        )


@pytest.mark.unit
def test_artifact_validation_rejects_missing_foreign_and_extra_run_subjects(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.ttl"
    with pytest.raises(PublicationValidationError, match="could not be read"):
        validate_artifact(missing, expected_codes=set(), run_id="run-1")

    foreign = tmp_path / "foreign.ttl"
    foreign.write_text(
        f"<urn:foreign> <{vocab.REPRESENTATION_STATUS}> "
        f'"{vocab.LEGACY_PRECOORDINATED}" ; '
        f'<{vocab.DECOMPOSED_BY}> "run-1" .',
        encoding="utf-8",
    )
    with pytest.raises(PublicationValidationError, match="not an NCIt concept"):
        validate_artifact(foreign, expected_codes={"C1"}, run_id="run-1")

    empty_code = tmp_path / "empty-code.ttl"
    empty_code.write_text(
        f"<{NCIT_NS}> <{vocab.REPRESENTATION_STATUS}> "
        f'"{vocab.LEGACY_PRECOORDINATED}" ; '
        f'<{vocab.DECOMPOSED_BY}> "run-1" .',
        encoding="utf-8",
    )
    with pytest.raises(PublicationValidationError, match="empty NCIt concept code"):
        validate_artifact(empty_code, expected_codes={""}, run_id="run-1")

    extra = tmp_path / "extra-run.ttl"
    extra.write_text(
        f"<{NCIT_NS}C1> <{vocab.REPRESENTATION_STATUS}> "
        f'"{vocab.LEGACY_PRECOORDINATED}" ; '
        f'<{vocab.DECOMPOSED_BY}> "run-1" . '
        f'<urn:unexpected> <{vocab.DECOMPOSED_BY}> "run-1" .',
        encoding="utf-8",
    )
    with pytest.raises(PublicationValidationError, match="unexpected subject"):
        validate_artifact(extra, expected_codes={"C1"}, run_id="run-1")


@pytest.mark.unit
def test_replacement_update_is_run_scoped_and_replaces_marker_atomically() -> None:
    marker = PublicationMarker(
        run_id="neoplasm-run-1",
        source_identity="a" * 64,
        representation_identity="b" * 64,
        built_at=datetime(2026, 7, 30, 12, 0, tzinfo=UTC),
    )
    staging = staging_graph_iri(marker.run_id)
    update = build_replacement_update(marker, staging)

    assert staging.startswith(f"{vocab.DECOMPOSED_GRAPH_IRI}/staging/")
    assert marker.run_id not in staging
    assert f"CLEAR GRAPH <{vocab.DECOMPOSED_GRAPH_IRI}>" in update
    assert f"ADD GRAPH <{staging}> TO GRAPH <{vocab.DECOMPOSED_GRAPH_IRI}>" in update
    assert f"DROP GRAPH <{staging}>" in update
    assert marker.run_id in update
    assert marker.source_identity in update
    assert marker.representation_identity in update
    assert "2026-07-30T12:00:00Z" in update

    with pytest.raises(ValueError, match="run-scoped staging graph"):
        build_replacement_update(marker, "urn:unsafe> ; CLEAR ALL; #")


@pytest.mark.unit
async def test_publish_stages_replaces_file_then_completes_the_run(
    tmp_path: Path,
) -> None:
    staging = tmp_path / ".decomposed.ttl.staging-run"
    destination = tmp_path / "decomposed.ttl"
    destination.write_text("old artifact", encoding="utf-8")
    await write_ttl([_decomposition()], staging, run_id="neoplasm-run-1")
    graph = _GraphClient()
    store = _PublicationStore(destination=destination)

    marker = await publish_artifact(
        run_id="neoplasm-run-1",
        source_identity="a" * 64,
        artifact=staging,
        destination=destination,
        expected_codes={"C1"},
        metrics={"decomposed": 1},
        load_to_store=True,
        client=graph,
        provenance=store,
    )

    assert (
        marker.representation_identity
        == hashlib.sha256(destination.read_bytes()).hexdigest()
    )
    assert store.finished_identity == marker.representation_identity
    assert graph.loaded_payload == destination.read_bytes()
    assert graph.loaded_graph == staging_graph_iri(marker.run_id)
    assert graph.events == ["read-marker", "stage", "replace"]
    assert store.events == ["lock", "begin", "finish", "unlock"]
    assert not staging.exists()


@pytest.mark.unit
async def test_file_only_publication_never_touches_the_graph(
    tmp_path: Path,
) -> None:
    staging = tmp_path / ".decomposed.ttl.staging-run"
    destination = tmp_path / "decomposed.ttl"
    await write_ttl([_decomposition()], staging, run_id="neoplasm-run-1")
    graph = _GraphClient()
    store = _PublicationStore(destination=destination)

    await publish_artifact(
        run_id="neoplasm-run-1",
        source_identity="a" * 64,
        artifact=staging,
        destination=destination,
        expected_codes={"C1"},
        metrics={"decomposed": 1},
        load_to_store=False,
        client=graph,
        provenance=store,
    )

    assert graph.events == []
    assert destination.exists()
    assert store.events == ["lock", "begin", "finish", "unlock"]


@pytest.mark.unit
async def test_marker_ahead_retry_skips_graph_replacement_and_finishes_file(
    tmp_path: Path,
) -> None:
    staging = tmp_path / ".decomposed.ttl.staging-run"
    destination = tmp_path / "decomposed.ttl"
    await write_ttl([_decomposition()], staging, run_id="neoplasm-run-1")
    identity = hashlib.sha256(staging.read_bytes()).hexdigest()
    built_at = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    expected_marker = PublicationMarker(
        run_id="neoplasm-run-1",
        source_identity="a" * 64,
        representation_identity=identity,
        built_at=built_at,
    )
    graph = _GraphClient(_marker_rows(expected_marker))
    store = _PublicationStore(
        destination=destination,
        persisted_built_at=built_at,
    )

    marker = await publish_artifact(
        run_id="neoplasm-run-1",
        source_identity="a" * 64,
        artifact=staging,
        destination=destination,
        expected_codes={"C1"},
        metrics={"decomposed": 1},
        load_to_store=True,
        client=graph,
        provenance=store,
    )

    assert marker == expected_marker
    assert graph.events == ["read-marker"]
    assert store.events == ["lock", "begin", "finish", "unlock"]
    assert destination.exists()


@pytest.mark.unit
async def test_new_run_replaces_the_prior_complete_publication(
    tmp_path: Path,
) -> None:
    staging = tmp_path / ".decomposed.ttl.staging-run"
    destination = tmp_path / "decomposed.ttl"
    await write_ttl([_decomposition()], staging, run_id="neoplasm-run-2")
    prior = PublicationMarker(
        run_id="neoplasm-run-1",
        source_identity="9" * 64,
        representation_identity="8" * 64,
        built_at=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
    )
    graph = _GraphClient(_marker_rows(prior))
    store = _PublicationStore(destination=destination)

    current = await publish_artifact(
        run_id="neoplasm-run-2",
        source_identity="a" * 64,
        artifact=staging,
        destination=destination,
        expected_codes={"C1"},
        metrics={"decomposed": 1},
        load_to_store=True,
        client=graph,
        provenance=store,
    )

    assert current.run_id == "neoplasm-run-2"
    assert graph.events == ["read-marker", "stage", "replace"]
    assert store.failures == []


@pytest.mark.unit
async def test_failed_graph_replacement_preserves_old_file_and_is_retryable(
    tmp_path: Path,
) -> None:
    staging = tmp_path / ".decomposed.ttl.staging-run"
    destination = tmp_path / "decomposed.ttl"
    destination.write_text("old artifact", encoding="utf-8")
    await write_ttl([_decomposition()], staging, run_id="neoplasm-run-1")
    graph = _GraphClient(update_error=RuntimeError("update unavailable"))
    store = _PublicationStore(destination=destination)

    with pytest.raises(RuntimeError, match="update unavailable"):
        await publish_artifact(
            run_id="neoplasm-run-1",
            source_identity="a" * 64,
            artifact=staging,
            destination=destination,
            expected_codes={"C1"},
            metrics={"decomposed": 1},
            load_to_store=True,
            client=graph,
            provenance=store,
        )

    assert destination.read_text(encoding="utf-8") == "old artifact"
    assert staging.exists()
    assert store.events == ["lock", "begin", "failure", "unlock"]
    assert len(store.failures) == 1


@pytest.mark.unit
async def test_retry_refuses_to_overwrite_a_different_newer_marker(
    tmp_path: Path,
) -> None:
    staging = tmp_path / ".decomposed.ttl.staging-run"
    destination = tmp_path / "decomposed.ttl"
    await write_ttl([_decomposition()], staging, run_id="neoplasm-run-1")
    built_at = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    newer = PublicationMarker(
        run_id="neoplasm-run-2",
        source_identity="9" * 64,
        representation_identity="8" * 64,
        built_at=datetime(2026, 7, 30, 13, 0, tzinfo=UTC),
    )
    graph = _GraphClient(_marker_rows(newer))
    store = _PublicationStore(
        destination=destination,
        persisted_built_at=built_at,
    )

    with pytest.raises(PublicationValidationError, match="does not match"):
        await publish_artifact(
            run_id="neoplasm-run-1",
            source_identity="a" * 64,
            artifact=staging,
            destination=destination,
            expected_codes={"C1"},
            metrics={"decomposed": 1},
            load_to_store=True,
            client=graph,
            provenance=store,
        )

    assert graph.events == ["read-marker"]
    assert not destination.exists()
    assert len(store.failures) == 1


@pytest.mark.unit
async def test_ambiguous_update_error_reconciles_committed_marker(
    tmp_path: Path,
) -> None:
    staging = tmp_path / ".decomposed.ttl.staging-run"
    destination = tmp_path / "decomposed.ttl"
    await write_ttl([_decomposition()], staging, run_id="neoplasm-run-1")
    identity = hashlib.sha256(staging.read_bytes()).hexdigest()
    committed = PublicationMarker(
        run_id="neoplasm-run-1",
        source_identity="a" * 64,
        representation_identity=identity,
        built_at=datetime(2026, 7, 30, 12, 0, tzinfo=UTC),
    )
    graph = _GraphClient(
        update_error=RuntimeError("connection closed after commit"),
        marker_on_update_error=committed,
    )
    store = _PublicationStore(
        destination=destination,
        persisted_built_at=committed.built_at,
    )

    marker = await publish_artifact(
        run_id=committed.run_id,
        source_identity=committed.source_identity,
        artifact=staging,
        destination=destination,
        expected_codes={"C1"},
        metrics={"decomposed": 1},
        load_to_store=True,
        client=graph,
        provenance=store,
    )

    assert marker == committed
    assert graph.events == ["read-marker", "stage", "replace", "read-marker"]
    assert store.failures == []
    assert store.finished_identity == identity


@pytest.mark.unit
async def test_cancellation_records_retryable_publication_and_preserves_file(
    tmp_path: Path,
) -> None:
    staging = tmp_path / ".decomposed.ttl.staging-run"
    destination = tmp_path / "decomposed.ttl"
    destination.write_text("old artifact", encoding="utf-8")
    await write_ttl([_decomposition()], staging, run_id="neoplasm-run-1")
    graph = _BlockingGraphClient()
    store = _PublicationStore(destination=destination)
    task = asyncio.create_task(
        publish_artifact(
            run_id="neoplasm-run-1",
            source_identity="a" * 64,
            artifact=staging,
            destination=destination,
            expected_codes={"C1"},
            metrics={"decomposed": 1},
            load_to_store=True,
            client=graph,
            provenance=store,
        )
    )
    await graph.update_started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert destination.read_text(encoding="utf-8") == "old artifact"
    assert staging.exists()
    assert len(store.failures) == 1
    assert isinstance(store.failures[0], asyncio.CancelledError)
    assert store.events == ["lock", "begin", "failure", "unlock"]


@pytest.mark.unit
async def test_partial_or_conflicting_marker_fails_closed() -> None:
    graph = _GraphClient(
        [
            {
                "predicate": vocab.PUBLICATION_RUN,
                "value": "neoplasm-run-1",
            }
        ]
    )

    with pytest.raises(PublicationValidationError, match="partial or ambiguous"):
        await read_publication_marker(graph)

    complete = PublicationMarker(
        run_id="neoplasm-run-1",
        source_identity="a" * 64,
        representation_identity="b" * 64,
        built_at=datetime(2026, 7, 30, 12, 0, tzinfo=UTC),
    )
    missing_predicate = _marker_rows(complete)
    missing_predicate[0]["predicate"] = None  # type: ignore[assignment]
    with pytest.raises(PublicationValidationError, match="partial or ambiguous"):
        await read_publication_marker(_GraphClient(missing_predicate))

    unexpected_predicate = _marker_rows(complete)
    unexpected_predicate[0]["predicate"] = "urn:unexpected-predicate"
    with pytest.raises(PublicationValidationError, match="partial or ambiguous"):
        await read_publication_marker(_GraphClient(unexpected_predicate))

    wrong_type = _marker_rows(complete)
    wrong_type[0]["value"] = "urn:not-a-publication"
    with pytest.raises(PublicationValidationError, match=r"wrong.*type"):
        await read_publication_marker(_GraphClient(wrong_type))

    unbound = _marker_rows(complete)
    unbound[-1]["value"] = None  # type: ignore[assignment]
    with pytest.raises(PublicationValidationError, match="unbound"):
        await read_publication_marker(_GraphClient(unbound))

    invalid_time = _marker_rows(complete)
    invalid_time[-1]["value"] = "not-a-date"
    with pytest.raises(PublicationValidationError, match="invalid"):
        await read_publication_marker(_GraphClient(invalid_time))


@pytest.mark.unit
async def test_cancelled_failure_journal_waits_for_recording_to_finish() -> None:
    recording_started = asyncio.Event()
    release_recording = asyncio.Event()
    recorded: list[BaseException] = []

    class _BlockingFailureJournal:
        async def record_publication_failure(
            self,
            _run_id: str,
            error: BaseException,
        ) -> None:
            recording_started.set()
            await release_recording.wait()
            recorded.append(error)

    original = RuntimeError("graph replacement failed")
    task = asyncio.create_task(
        _record_failure_without_masking(
            _BlockingFailureJournal(),  # type: ignore[arg-type]
            "neoplasm-run-1",
            original,
        )
    )
    await recording_started.wait()
    task.cancel()
    release_recording.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert recorded == [original]


@pytest.mark.unit
async def test_missing_or_corrupt_publication_journal_fails_before_side_effects(
    tmp_path: Path,
) -> None:
    staging = tmp_path / ".decomposed.ttl.staging-run"
    destination = tmp_path / "decomposed.ttl"
    await write_ttl([_decomposition()], staging, run_id="neoplasm-run-1")
    graph = _GraphClient()

    missing = _PublicationStore(destination=destination, missing=True)
    with pytest.raises(RunStateError, match="does not exist"):
        await publish_artifact(
            run_id="neoplasm-run-1",
            source_identity="a" * 64,
            artifact=staging,
            destination=destination,
            expected_codes={"C1"},
            metrics={"decomposed": 1},
            load_to_store=True,
            client=graph,
            provenance=missing,
        )
    assert missing.events == ["lock", "unlock"]

    corrupt = _PublicationStore(destination=destination, summary_state="failed")
    with pytest.raises(PublicationValidationError, match="no build timestamp"):
        await publish_artifact(
            run_id="neoplasm-run-1",
            source_identity="a" * 64,
            artifact=staging,
            destination=destination,
            expected_codes={"C1"},
            metrics={"decomposed": 1},
            load_to_store=True,
            client=graph,
            provenance=corrupt,
        )
    assert corrupt.events == ["lock", "unlock"]


@pytest.mark.unit
async def test_begin_and_failure_record_errors_do_not_corrupt_error_identity(
    tmp_path: Path,
) -> None:
    staging = tmp_path / ".decomposed.ttl.staging-run"
    destination = tmp_path / "decomposed.ttl"
    await write_ttl([_decomposition()], staging, run_id="neoplasm-run-1")
    begin_error = RuntimeError("journal unavailable")
    begin_store = _PublicationStore(
        destination=destination,
        begin_error=begin_error,
    )

    with pytest.raises(RuntimeError, match="journal unavailable") as begin_info:
        await publish_artifact(
            run_id="neoplasm-run-1",
            source_identity="a" * 64,
            artifact=staging,
            destination=destination,
            expected_codes={"C1"},
            metrics={"decomposed": 1},
            load_to_store=False,
            client=_GraphClient(),
            provenance=begin_store,
        )
    assert begin_info.value is begin_error
    assert begin_store.failures == []

    graph_error = RuntimeError("graph unavailable")
    record_store = _PublicationStore(
        destination=destination,
        record_error=RuntimeError("journal also unavailable"),
    )
    with pytest.raises(RuntimeError, match="graph unavailable") as record_info:
        await publish_artifact(
            run_id="neoplasm-run-1",
            source_identity="a" * 64,
            artifact=staging,
            destination=destination,
            expected_codes={"C1"},
            metrics={"decomposed": 1},
            load_to_store=True,
            client=_GraphClient(update_error=graph_error),
            provenance=record_store,
        )
    assert record_info.value is graph_error
    assert any(
        "Recording the publication failure also failed" in note
        for note in record_info.value.__notes__
    )
