from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ontolib.repositories.xref.evidence import SME_CURATION, Evidence
from ontolib.repositories.xref.models import (
    SSSOMRecord,
    UberonPromotionGenerationMetadata,
)
from ontolib.repositories.xref.publication import (
    XrefPublicationError,
    _active_pointer,
    _write_pointer,
    active_graph_iri,
    fail_run_on_error,
    generation_graph_iri,
    generation_identity,
    publish_generation,
    rdf_active_generation,
)
from ontolib.repositories.xref.vocab import EXACT_MATCH


def _record(subject: str) -> SSSOMRecord:
    return SSSOMRecord(
        subject_id=subject,
        subject_system="ncit",
        predicate_id=EXACT_MATCH,
        object_id="UBERON:0002048",
        object_system="uberon-cl",
        mapping_justification="https://ontoprism.org/vocab#SemanticSimilarityThresholdMatching",
        confidence=1.0,
        subject_source_version="26.07d",
        object_source_version="2026-06-19",
    )


@pytest.mark.unit
def test_generation_graph_is_source_specific_and_injection_safe() -> None:
    uberon = generation_graph_iri("uberon publisher", "a" * 64)
    icdo = generation_graph_iri("icdo/p334", "a" * 64)

    assert uberon != icdo
    assert "uberon-publisher" in uberon
    assert "icdo-p334" in icdo
    assert " " not in uberon
    assert "/p334" not in icdo


@pytest.mark.unit
@pytest.mark.parametrize("generation_id", ["", "abc", "g" * 64])
def test_generation_graph_refuses_invalid_generation_identity(
    generation_id: str,
) -> None:
    with pytest.raises(ValueError, match="generation"):
        generation_graph_iri("uberon", generation_id)


@pytest.mark.unit
def test_generation_graph_refuses_source_without_alphanumeric_component() -> None:
    with pytest.raises(ValueError, match="source"):
        generation_graph_iri("///", "a" * 64)


@pytest.mark.unit
def test_active_graph_refuses_source_without_alphanumeric_component() -> None:
    with pytest.raises(ValueError, match="source"):
        active_graph_iri("///")


@pytest.mark.unit
@pytest.mark.parametrize(
    "rows",
    [
        [{"source": "https://example.test/wrong", "predicate": "p", "g": "g"}],
        [{"source": "s", "predicate": "https://example.test/wrong", "g": "g"}],
        [{"source": "s", "predicate": "p", "g": "g", "extra": "value"}],
        [
            {"source": "s", "predicate": "p", "g": "g"},
            {"source": "s", "predicate": "p", "g": "g"},
        ],
    ],
)
async def test_rdf_active_generation_rejects_every_non_exact_pointer_row(
    rows: list[dict[str, str]],
) -> None:
    source = "strict-pointer"
    subject = active_graph_iri(source)
    predicate = (
        "http://ncicb.nci.nih.gov/xml/owl/EVS/"
        "Thesaurus-upstream-xref.owl/activeGeneration"
    )
    graph = generation_graph_iri(source, "a" * 64)
    replacements = {"s": subject, "p": predicate, "g": graph}
    observed = [
        {key: replacements.get(value, value) for key, value in row.items()}
        for row in rows
    ]

    class Client:
        async def select(self, query: str) -> list[dict[str, str]]:
            assert "SELECT ?source ?predicate ?g" in query
            return observed

    with pytest.raises(XrefPublicationError, match="pointer"):
        await rdf_active_generation(Client(), source)  # type: ignore[arg-type]


@pytest.mark.unit
async def test_rdf_active_generation_accepts_only_the_exact_pointer_statement() -> None:
    source = "strict-pointer"
    subject = active_graph_iri(source)
    predicate = (
        "http://ncicb.nci.nih.gov/xml/owl/EVS/"
        "Thesaurus-upstream-xref.owl/activeGeneration"
    )
    generation_id = "a" * 64

    class Client:
        async def select(self, _query: str) -> list[dict[str, str]]:
            return [
                {
                    "source": subject,
                    "predicate": predicate,
                    "g": generation_graph_iri(source, generation_id),
                }
            ]

    assert await rdf_active_generation(Client(), source) == generation_id  # type: ignore[arg-type]


@pytest.mark.unit
async def test_rdf_active_generation_reports_an_empty_pointer_graph() -> None:
    class Client:
        async def select(self, _query: str) -> list[dict[str, str]]:
            return []

    assert await rdf_active_generation(Client(), "strict-pointer") is None  # type: ignore[arg-type]


@pytest.mark.unit
async def test_rdf_active_generation_rejects_malformed_id_in_exact_graph_prefix() -> (
    None
):
    source = "strict-pointer"

    class Client:
        async def select(self, _query: str) -> list[dict[str, str]]:
            return [
                {
                    "source": active_graph_iri(source),
                    "predicate": (
                        "http://ncicb.nci.nih.gov/xml/owl/EVS/"
                        "Thesaurus-upstream-xref.owl/activeGeneration"
                    ),
                    "g": generation_graph_iri(source, "a" * 64).rsplit("/", 1)[0]
                    + "/"
                    + "A" * 64,
                }
            ]

    with pytest.raises(XrefPublicationError, match="pointer"):
        await rdf_active_generation(Client(), source)  # type: ignore[arg-type]


@pytest.mark.unit
def test_active_pointer_clear_is_an_empty_replacement_payload() -> None:
    assert _active_pointer("uberon-cl", None) == b""


@pytest.mark.unit
def test_generation_identity_includes_ordered_record_provenance() -> None:
    records = [_record("C1"), _record("C2")]
    metadata = UberonPromotionGenerationMetadata(
        ncit_source_identity="a" * 64,
        uberon_source_identity="b" * 64,
        uberon_serving_identity="c" * 64,
    )

    first, first_content = generation_identity(
        "uberon-cl-promotion", records, metadata, ["run-a", "run-b"]
    )
    second, second_content = generation_identity(
        "uberon-cl-promotion", records, metadata, ["run-b", "run-a"]
    )

    assert first != second
    assert first_content == second_content


@pytest.mark.unit
def test_generation_identity_canonicalizes_record_run_pairs_together() -> None:
    records = [_record("C1"), _record("C2")]
    metadata = UberonPromotionGenerationMetadata(
        ncit_source_identity="a" * 64,
        uberon_source_identity="b" * 64,
        uberon_serving_identity="c" * 64,
    )

    first = generation_identity(
        "uberon-cl-promotion", records, metadata, ["run-a", "run-b"]
    )
    reordered = generation_identity(
        "uberon-cl-promotion", records[::-1], metadata, ["run-b", "run-a"]
    )

    assert first == reordered


@pytest.mark.unit
def test_generation_identity_serializes_evidence_despite_dataclass_comparison() -> None:
    record = _record("C1")
    with_evidence = replace(
        record,
        evidence=(Evidence(kind=SME_CURATION, source="golden/mappings.json"),),
    )
    metadata = UberonPromotionGenerationMetadata(
        ncit_source_identity="a" * 64,
        uberon_source_identity="b" * 64,
        uberon_serving_identity="c" * 64,
    )

    assert record == with_evidence
    assert generation_identity(
        "uberon-cl-promotion", [record], metadata, ["run-a"]
    ) != generation_identity(
        "uberon-cl-promotion", [with_evidence], metadata, ["run-a"]
    )


@pytest.mark.unit
def test_generation_identity_rejects_misaligned_record_provenance() -> None:
    metadata = UberonPromotionGenerationMetadata(
        ncit_source_identity="a" * 64,
        uberon_source_identity="b" * 64,
        uberon_serving_identity="c" * 64,
    )

    with pytest.raises(ValueError, match="record_run_ids must match records"):
        generation_identity(
            "uberon-cl-promotion", [_record("C1")], metadata, ["run-a", "run-b"]
        )


@pytest.mark.unit
async def test_publish_generation_rejects_explicit_empty_record_provenance() -> None:
    metadata = UberonPromotionGenerationMetadata(
        ncit_source_identity="a" * 64,
        uberon_source_identity="b" * 64,
        uberon_serving_identity="c" * 64,
    )

    with pytest.raises(ValueError, match="record_run_ids must match records"):
        await publish_generation(  # type: ignore[arg-type]
            object(),
            object(),
            source="uberon-cl-promotion",
            run_id="run-a",
            records=[_record("C1")],
            source_metadata=metadata,
            record_run_ids=[],
        )


@pytest.mark.unit
async def test_run_failure_cleanup_preserves_original_error_with_cleanup_note() -> None:
    class Store:
        async def update_run_metrics(self, *_args: object, **_kwargs: object) -> None:
            raise OSError("failed-status write failed")

    original = RuntimeError("RDF load failed")
    with pytest.raises(RuntimeError, match="RDF load failed") as captured:
        async with fail_run_on_error(Store(), "run-1"):  # type: ignore[arg-type]
            raise original

    assert captured.value is original
    assert any("failed-status write failed" in note for note in original.__notes__)


@pytest.mark.unit
async def test_successful_run_does_not_write_failure_metrics() -> None:
    class Store:
        async def update_run_metrics(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("successful run must not be terminalized as failed")

    async with fail_run_on_error(Store(), "run-1"):  # type: ignore[arg-type]
        pass


@pytest.mark.unit
async def test_run_failure_is_persisted_before_original_error_is_reraised() -> None:
    class Store:
        metrics: dict[str, object] | None = None
        status: str | None = None

        async def update_run_metrics(
            self, _run_id: str, metrics: dict[str, object], *, status: str
        ) -> None:
            self.metrics = metrics
            self.status = status

    store = Store()
    original = RuntimeError("RDF load failed")
    with pytest.raises(RuntimeError, match="RDF load failed") as captured:
        async with fail_run_on_error(store, "run-1"):  # type: ignore[arg-type]
            raise original

    assert captured.value is original
    assert store.status == "failed"
    assert store.metrics == {
        "failure": {"type": "RuntimeError", "message": "RDF load failed"}
    }


@pytest.mark.unit
async def test_run_cancellation_waits_for_failed_status_cleanup() -> None:
    cleanup_started = asyncio.Event()
    allow_cleanup = asyncio.Event()

    class Store:
        metrics: dict[str, object] | None = None
        status: str | None = None

        async def update_run_metrics(
            self, _run_id: str, metrics: dict[str, object], *, status: str
        ) -> None:
            cleanup_started.set()
            await allow_cleanup.wait()
            self.metrics = metrics
            self.status = status

    store = Store()

    async def work() -> None:
        async with fail_run_on_error(store, "run-1"):  # type: ignore[arg-type]
            await asyncio.sleep(60)

    task = asyncio.create_task(work())
    await asyncio.sleep(0)
    task.cancel()
    await cleanup_started.wait()
    assert not task.done()
    allow_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert store.status == "failed"
    assert store.metrics == {
        "failure": {"type": "CancelledError", "message": "run cancelled"}
    }


@pytest.mark.unit
async def test_pointer_cancellation_preserves_reconciliation_failure_as_note() -> None:
    class Store:
        async def active_generation(self, _source: str) -> None:
            return None

    class Client:
        async def load(self, *_args: object, **_kwargs: object) -> None:
            raise asyncio.CancelledError

        async def select(self, _query: str) -> list[dict[str, str]]:
            raise RuntimeError("pointer observation failed")

    with pytest.raises(asyncio.CancelledError) as captured:
        await _write_pointer(  # type: ignore[arg-type]
            Store(), Client(), "uberon-cl", "a" * 64
        )

    assert any(
        "pointer observation failed" in note for note in captured.value.__notes__
    )


@pytest.mark.unit
async def test_pointer_error_remains_primary_when_reconciliation_fails() -> None:
    class Store:
        async def active_generation(self, _source: str) -> None:
            return None

    class Client:
        async def load(self, *_args: object, **_kwargs: object) -> None:
            raise OSError("pointer write failed")

        async def select(self, _query: str) -> list[dict[str, str]]:
            raise RuntimeError("pointer observation failed")

    with pytest.raises(OSError, match="pointer write failed") as captured:
        await _write_pointer(  # type: ignore[arg-type]
            Store(), Client(), "uberon-cl", "a" * 64
        )

    assert isinstance(captured.value.__cause__, RuntimeError)


@pytest.mark.unit
async def test_pointer_error_is_reconciled_when_remote_commit_succeeded() -> None:
    source = "uberon-cl"
    generation_id = "a" * 64

    class Store:
        observed: str | None = None

        async def active_generation(self, _source: str) -> None:
            return None

        async def set_active_generation(
            self, _source: str, observed: str, **_kwargs: object
        ) -> None:
            self.observed = observed

    class Client:
        async def load(self, *_args: object, **_kwargs: object) -> None:
            raise OSError("response lost after commit")

        async def select(self, _query: str) -> list[dict[str, str]]:
            return [
                {
                    "source": active_graph_iri(source),
                    "predicate": (
                        "http://ncicb.nci.nih.gov/xml/owl/EVS/"
                        "Thesaurus-upstream-xref.owl/activeGeneration"
                    ),
                    "g": generation_graph_iri(source, generation_id),
                }
            ]

    store = Store()
    await _write_pointer(  # type: ignore[arg-type]
        store, Client(), source, generation_id
    )
    assert store.observed == generation_id
