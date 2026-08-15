from __future__ import annotations

import asyncio
import json
import secrets
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.repositories.xref.models import (
    GenerationSourceMetadata,
    IcdoReadIdentity,
    P334GenerationMetadata,
    SSSOMRecord,
    StaleXrefGenerationError,
    UberonCandidateGenerationMetadata,
    UberonPromotionGenerationMetadata,
    UberonPublisherGenerationMetadata,
    UberonReadIdentity,
    UnavailableXrefGenerationError,
    XrefReadPolicy,
)
from ontolib.repositories.xref.publication import (
    XrefPublicationError,
    active_graph_iri,
    generation_graph_iri,
    rollback_generation,
)
from ontolib.repositories.xref.publication import (
    generation_identity as _generation_identity,
)
from ontolib.repositories.xref.publication import (
    publish_generation as _publish_generation,
)
from ontolib.repositories.xref.store import XrefStore
from ontolib.repositories.xref.vocab import CLOSE_MATCH
from ontolib.terminologies.sparql_http_client import SparqlHttpClient

pytestmark = [
    pytest.mark.integration,
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings", "isolated_qlever_settings"),
]

_ACTIVE_PREDICATE = (
    "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-upstream-xref.owl/activeGeneration"
)
_SOURCE_METADATA = UberonCandidateGenerationMetadata(
    ncit_source_identity="a" * 64,
    uberon_source_identity="b" * 64,
    uberon_serving_identity="c" * 64,
)
_READ_POLICY = XrefReadPolicy(
    uberon=UberonReadIdentity(
        ncit_source_identity="a" * 64,
        uberon_source_identity="b" * 64,
        uberon_serving_identity="c" * 64,
    )
)


def _metadata_for(source: str) -> GenerationSourceMetadata:
    if source == "uberon-cl-promotion":
        return UberonPromotionGenerationMetadata(
            ncit_source_identity="a" * 64,
            uberon_source_identity="b" * 64,
            uberon_serving_identity="c" * 64,
        )
    return _SOURCE_METADATA


def generation_identity(
    source: str, records: list[SSSOMRecord], record_run_ids: list[str] | None = None
) -> tuple[str, str]:
    return _generation_identity(source, records, _metadata_for(source), record_run_ids)


async def publish_generation(*args: object, **kwargs: object) -> object:
    kwargs.setdefault("source_metadata", _metadata_for(str(kwargs["source"])))
    return await _publish_generation(*args, **kwargs)  # type: ignore[arg-type]


def _pointer_row(source: str, graph: str | None) -> list[dict[str, str]]:
    if graph is None:
        return []
    return [
        {
            "source": active_graph_iri(source),
            "predicate": _ACTIVE_PREDICATE,
            "g": graph,
        }
    ]


def _record(subject: str, obj: str, version: str) -> SSSOMRecord:
    return SSSOMRecord(
        subject_id=subject,
        subject_system="ncit",
        predicate_id=CLOSE_MATCH,
        object_id=obj,
        object_system="uberon-cl",
        mapping_justification="https://ontoprism.org/vocab#PublisherDatabaseCrossReference",
        confidence=0.9,
        subject_source_version="26.07d",
        object_source_version=version,
    )


async def _run(store: XrefStore, source: str, version: str) -> str:
    run_id = uuid.uuid4().hex
    await store.upsert_run(run_id, source, "26.07d", version)
    return run_id


async def _clear_active_generations(engine: object) -> None:
    async with engine.begin() as connection:  # type: ignore[attr-defined]
        await connection.execute(text("DELETE FROM xref_active_generation"))


@pytest.fixture(autouse=True)
async def _isolate_xref_tables(
    isolated_postgres_settings: None, isolated_qlever_url: str
) -> None:
    del isolated_postgres_settings
    engine = make_engine(get_settings().database_url)
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE xref_generation, xref_run CASCADE"))
    await dispose_engine(engine)
    async with SparqlHttpClient.for_qlever(
        isolated_qlever_url, named_graphs=()
    ) as client:
        for source in (
            "uberon-cl",
            "uberon-cl-promotion",
            "uberon-publisher-xref",
            "ncit-p334-icdo32",
        ):
            await client.load(
                b"", graph_iri=active_graph_iri(source), content_type="text/turtle"
            )


async def test_active_reads_are_typed_many_to_many_and_rollback_is_source_local(
    isolated_qlever_url: str,
) -> None:
    engine = make_engine(get_settings().database_url)
    store = XrefStore(make_sessionmaker(engine))
    async with SparqlHttpClient.for_qlever(
        isolated_qlever_url, named_graphs=()
    ) as client:
        run_a = await _run(store, "uberon-cl", "u1")
        first = await publish_generation(
            store,
            client,
            source="uberon-cl",
            run_id=run_a,
            records=[_record("C1", "UBERON:1", "u1")],
        )
        run_other = await _run(store, "uberon-cl-promotion", "u1")
        other = await publish_generation(
            store,
            client,
            source="uberon-cl-promotion",
            run_id=run_other,
            records=[_record("C9", "UBERON:9", "u1")],
        )
        run_b = await _run(store, "uberon-cl", "u2")
        second = await publish_generation(
            store,
            client,
            source="uberon-cl",
            run_id=run_b,
            records=[
                _record("C1", "UBERON:2", "u2"),
                _record("C2", "UBERON:2", "u2"),
                _record("C2", "UBERON:3", "u2"),
            ],
        )

        forward = await store.mappings_by_subjects({"C1", "C2"}, expected=_READ_POLICY)
        reverse = await store.mappings_by_objects({"UBERON:2"}, expected=_READ_POLICY)
        assert {row.object.identifier for row in forward["C2"]} == {
            "UBERON:2",
            "UBERON:3",
        }
        assert {row.subject.identifier for row in reverse["UBERON:2"]} == {"C1", "C2"}
        assert all(
            row.subject.system == "ncit" for rows in forward.values() for row in rows
        )
        assert all(
            row.object.version == "u2" for rows in forward.values() for row in rows
        )
        assert await client.ask(f"ASK {{ GRAPH <{second.graph_iri}> {{ ?s ?p ?o }} }}")
        assert await client.ask(f"ASK {{ GRAPH <{other.graph_iri}> {{ ?s ?p ?o }} }}")

        assert (
            await rollback_generation(store, client, "uberon-cl") == first.generation_id
        )
        rolled_back = await store.mappings_by_subjects(
            {"C1", "C2", "C9"}, expected=_READ_POLICY
        )
        assert {row.object.identifier for row in rolled_back["C1"]} == {"UBERON:1"}
        assert "C2" not in rolled_back
        assert {row.object.identifier for row in rolled_back["C9"]} == {"UBERON:9"}
        assert other.generation_id != first.generation_id
        pointer = await client.select(
            f"SELECT ?g WHERE {{ GRAPH <{active_graph_iri('uberon-cl')}> "
            "{ ?source ?predicate ?g } }"
        )
        assert pointer == [{"g": first.graph_iri}]
    await dispose_engine(engine)


async def test_crash_reconciliation_is_idempotent_without_pointer_churn(
    isolated_qlever_url: str,
) -> None:
    engine = make_engine(get_settings().database_url)
    store = XrefStore(make_sessionmaker(engine))
    source = "uberon-cl"
    run_id = await _run(store, source, "u1")
    records = [_record("C7", "UBERON:7", "u1")]
    async with SparqlHttpClient.for_qlever(
        isolated_qlever_url, named_graphs=()
    ) as client:
        for failpoint in ("after_postgres", "after_rdf", "before_pointer"):
            with pytest.raises(XrefPublicationError, match="injected"):
                await publish_generation(
                    store,
                    client,
                    source=source,
                    run_id=run_id,
                    records=records,
                    failpoint=failpoint,
                )
            with pytest.raises(UnavailableXrefGenerationError):
                await store.mappings_by_subjects({"C7"}, expected=_READ_POLICY)

        result = await publish_generation(
            store, client, source=source, run_id=run_id, records=records
        )
        async with engine.connect() as conn:
            before = (
                await conn.execute(
                    text(
                        "SELECT activated_at FROM xref_active_generation "
                        "WHERE source = :source"
                    ),
                    {"source": source},
                )
            ).scalar_one()
        retry = await publish_generation(
            store, client, source=source, run_id=run_id, records=records
        )
        async with engine.connect() as conn:
            after = (
                await conn.execute(
                    text(
                        "SELECT activated_at FROM xref_active_generation "
                        "WHERE source = :source"
                    ),
                    {"source": source},
                )
            ).scalar_one()
            counts = (
                await conn.execute(
                    text(
                        "SELECT (SELECT count(*) FROM xref_generation "
                        "WHERE source = :s), "
                        "(SELECT count(*) FROM concept_xref WHERE generation_id = :g)"
                    ),
                    {"s": source, "g": result.generation_id},
                )
            ).one()
        assert retry == type(retry)(
            generation_id=result.generation_id,
            graph_iri=result.graph_iri,
            changed=False,
        )
        assert before == after
        assert counts == (1, 1)
    await dispose_engine(engine)


async def test_different_originating_run_creates_a_distinct_generation(
    isolated_qlever_url: str,
) -> None:
    engine = make_engine(get_settings().database_url)
    store = XrefStore(make_sessionmaker(engine))
    source = "uberon-cl"
    records = [_record("C-RUN", "UBERON:RUN", "u1")]
    first_run = await _run(store, source, "u1")
    second_run = await _run(store, source, "u1")
    async with SparqlHttpClient.for_qlever(
        isolated_qlever_url, named_graphs=()
    ) as client:
        first = await publish_generation(
            store, client, source=source, run_id=first_run, records=records
        )
        second = await publish_generation(
            store, client, source=source, run_id=second_run, records=records
        )
        assert first.generation_id != second.generation_id
    await dispose_engine(engine)


async def test_prepare_generation_rejects_explicit_empty_record_provenance() -> None:
    engine = make_engine(get_settings().database_url)
    store = XrefStore(make_sessionmaker(engine))
    source = "uberon-cl"
    run_id = await _run(store, source, "u1")
    records = [_record("C-RUN", "UBERON:RUN", "u1")]
    generation_id, content_sha256 = generation_identity(source, records)

    with pytest.raises(ValueError, match="record_run_ids must match records"):
        await store.prepare_generation(
            source=source,
            generation_id=generation_id,
            content_sha256=content_sha256,
            source_metadata=_SOURCE_METADATA,
            graph_iri=generation_graph_iri(source, generation_id),
            run_id=run_id,
            records=records,
            record_run_ids=[],
        )
    await dispose_engine(engine)


@pytest.mark.parametrize(
    "corruption",
    ["run_id", "evidence", "lifecycle_state", "confidence", "missing", "extra"],
)
async def test_exact_generation_retry_rejects_corrupted_persisted_rows(
    corruption: str,
) -> None:
    engine = make_engine(get_settings().database_url)
    store = XrefStore(make_sessionmaker(engine))
    source = "uberon-cl"
    run_id = await _run(store, source, "u1")
    other_run_id = await _run(store, source, "u1")
    records = [_record("C-RETRY", "UBERON:RETRY", "u1")]
    generation_id, content_sha256 = generation_identity(source, records, [run_id])
    kwargs = {
        "source": source,
        "generation_id": generation_id,
        "content_sha256": content_sha256,
        "source_metadata": _metadata_for(source),
        "graph_iri": generation_graph_iri(source, generation_id),
        "run_id": run_id,
        "records": records,
        "record_run_ids": [run_id],
    }
    assert await store.prepare_generation(**kwargs)  # type: ignore[arg-type]

    statements = {
        "run_id": "UPDATE concept_xref SET run_id = :other WHERE generation_id = :g",
        "evidence": (
            "UPDATE concept_xref SET evidence = "
            '\'[ {"kind": "sme_curation", "source": "corrupt", '
            '"detail": ""} ]\'::jsonb WHERE generation_id = :g'
        ),
        "lifecycle_state": (
            "UPDATE concept_xref SET lifecycle_state = 'quarantined' "
            "WHERE generation_id = :g"
        ),
        "confidence": (
            "UPDATE concept_xref SET confidence = 0.1 WHERE generation_id = :g"
        ),
        "missing": "DELETE FROM concept_xref WHERE generation_id = :g",
        "extra": (
            "INSERT INTO concept_xref "
            "(generation_id, generation_source, run_id, subject_system, "
            "subject_version, subject_id, predicate_id, object_system, object_version, "
            "object_id, mapping_justification, confidence, lifecycle_state, "
            "review_status, author, evidence) "
            "SELECT generation_id, generation_source, run_id, subject_system, "
            "subject_version, 'C-EXTRA', predicate_id, object_system, object_version, "
            "object_id, mapping_justification, confidence, lifecycle_state, "
            "review_status, author, evidence FROM concept_xref WHERE generation_id = :g"
        ),
    }
    async with engine.begin() as connection:
        await connection.execute(
            text(statements[corruption]),
            {"g": generation_id, "other": other_run_id},
        )

    with pytest.raises(ValueError, match="persisted rows"):
        await store.prepare_generation(**kwargs)  # type: ignore[arg-type]
    await dispose_engine(engine)


async def test_rdf_pointer_failure_restores_previous_postgres_generation() -> None:
    engine = make_engine(get_settings().database_url)
    store = XrefStore(make_sessionmaker(engine))
    source = "uberon-cl"

    class FailingPointerClient:
        calls = 0

        async def load(self, *_args: object, **_kwargs: object) -> None:
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("pointer write failed")

        async def select(self, _query: str) -> list[dict[str, str]]:
            return []

    client = FailingPointerClient()
    run_id = await _run(store, source, "u1")
    with pytest.raises(RuntimeError, match="pointer write failed"):
        await publish_generation(
            store,
            client,  # type: ignore[arg-type]
            source=source,
            run_id=run_id,
            records=[_record("FAIL", "UBERON:FAIL", "u1")],
        )
    assert await store.active_generation(source) is None
    with pytest.raises(UnavailableXrefGenerationError):
        await store.mappings_by_subjects({"FAIL"}, expected=_READ_POLICY)
    await dispose_engine(engine)


async def test_rollback_pointer_failure_restores_newer_postgres_generation() -> None:
    engine = make_engine(get_settings().database_url)
    store = XrefStore(make_sessionmaker(engine))
    source = "uberon-cl"

    class Client:
        fail = False
        pointer: str | None = None

        async def load(self, data: bytes, *, graph_iri: str, **_kwargs: object) -> None:
            if self.fail:
                raise RuntimeError("rollback pointer write failed")
            if graph_iri == active_graph_iri(source):
                text_data = data.decode()
                self.pointer = (
                    text_data.rsplit("<", 1)[1].split(">", 1)[0] if text_data else None
                )

        async def select(self, _query: str) -> list[dict[str, str]]:
            return _pointer_row(source, self.pointer)

    client = Client()
    first = await publish_generation(
        store,
        client,  # type: ignore[arg-type]
        source=source,
        run_id=await _run(store, source, "u1"),
        records=[_record("RB1", "UBERON:RB1", "u1")],
    )
    second = await publish_generation(
        store,
        client,  # type: ignore[arg-type]
        source=source,
        run_id=await _run(store, source, "u2"),
        records=[_record("RB2", "UBERON:RB2", "u2")],
    )
    client.fail = True
    with pytest.raises(RuntimeError, match="rollback pointer write failed"):
        await rollback_generation(store, client, source)  # type: ignore[arg-type]
    assert await store.active_generation(source) == second.generation_id
    assert first.generation_id != second.generation_id
    assert set(
        await store.mappings_by_subjects({"RB1", "RB2"}, expected=_READ_POLICY)
    ) == {"RB2"}
    await dispose_engine(engine)


async def test_pointer_commit_then_raise_is_reconciled_as_success() -> None:
    engine = make_engine(get_settings().database_url)
    store = XrefStore(make_sessionmaker(engine))
    source = "uberon-cl"

    class Client:
        pointer: str | None = None
        calls = 0

        async def load(self, data: bytes, *, graph_iri: str, **_kwargs: object) -> None:
            self.calls += 1
            if graph_iri == active_graph_iri(source):
                text_data = data.decode()
                self.pointer = (
                    text_data.rsplit("<", 1)[1].split(">", 1)[0] if text_data else None
                )
                raise RuntimeError("response lost after commit")

        async def select(self, _query: str) -> list[dict[str, str]]:
            return _pointer_row(source, self.pointer)

    client = Client()
    result = await publish_generation(
        store,
        client,  # type: ignore[arg-type]
        source=source,
        run_id=await _run(store, source, "u1"),
        records=[_record("COMMIT", "UBERON:COMMIT", "u1")],
    )
    assert await store.active_generation(source) == result.generation_id
    await dispose_engine(engine)


@pytest.mark.parametrize("committed", [False, True])
async def test_pointer_cancellation_reconciles_before_propagating(
    committed: bool,
) -> None:
    engine = make_engine(get_settings().database_url)
    store = XrefStore(make_sessionmaker(engine))
    source = "uberon-cl"

    class Client:
        pointer: str | None = None
        select_calls = 0

        async def load(self, data: bytes, *, graph_iri: str, **_kwargs: object) -> None:
            if graph_iri == active_graph_iri(source):
                if committed:
                    self.pointer = data.decode().rsplit("<", 1)[1].split(">", 1)[0]
                raise asyncio.CancelledError

        async def select(self, _query: str) -> list[dict[str, str]]:
            self.select_calls += 1
            return _pointer_row(source, self.pointer)

    client = Client()
    records = [_record("CANCEL", "UBERON:CANCEL", "u1")]
    run_id = await _run(store, source, "u1")
    result_id, _ = generation_identity(source, records, [run_id])
    with pytest.raises(asyncio.CancelledError):
        await publish_generation(
            store,
            client,  # type: ignore[arg-type]
            source=source,
            run_id=run_id,
            records=records,
        )
    assert client.select_calls == 2
    expected = result_id if committed else None
    assert await store.active_generation(source) == expected
    await dispose_engine(engine)


@pytest.mark.parametrize("committed", [False, True])
async def test_rollback_pointer_cancellation_reconciles_before_propagating(
    committed: bool,
) -> None:
    engine = make_engine(get_settings().database_url)
    store = XrefStore(make_sessionmaker(engine))
    source = "uberon-cl"

    class Client:
        pointer: str | None = None
        cancel = False

        async def load(self, data: bytes, *, graph_iri: str, **_kwargs: object) -> None:
            if graph_iri != active_graph_iri(source):
                return
            next_pointer = data.decode().rsplit("<", 1)[1].split(">", 1)[0]
            if not self.cancel or committed:
                self.pointer = next_pointer
            if self.cancel:
                raise asyncio.CancelledError

        async def select(self, _query: str) -> list[dict[str, str]]:
            return _pointer_row(source, self.pointer)

    client = Client()
    first = await publish_generation(
        store,
        client,  # type: ignore[arg-type]
        source=source,
        run_id=await _run(store, source, "u1"),
        records=[_record("RC1", "UBERON:RC1", "u1")],
    )
    second = await publish_generation(
        store,
        client,  # type: ignore[arg-type]
        source=source,
        run_id=await _run(store, source, "u2"),
        records=[_record("RC2", "UBERON:RC2", "u2")],
    )
    client.cancel = True

    with pytest.raises(asyncio.CancelledError):
        await rollback_generation(store, client, source)  # type: ignore[arg-type]

    expected = first.generation_id if committed else second.generation_id
    assert await store.active_generation(source) == expected
    await dispose_engine(engine)


async def test_pointer_reconciliation_failure_preserves_original_network_error() -> (
    None
):
    engine = make_engine(get_settings().database_url)
    store = XrefStore(make_sessionmaker(engine))
    source = "uberon-cl"

    class Client:
        select_calls = 0

        async def load(
            self, _data: bytes, *, graph_iri: str, **_kwargs: object
        ) -> None:
            if graph_iri == active_graph_iri(source):
                raise RuntimeError("original pointer error")

        async def select(self, _query: str) -> list[dict[str, str]]:
            self.select_calls += 1
            if self.select_calls > 1:
                raise ValueError("reconciliation failed")
            return []

    with pytest.raises(RuntimeError, match="original pointer error") as captured:
        await publish_generation(
            store,
            Client(),  # type: ignore[arg-type]
            source=source,
            run_id=await _run(store, source, "u1"),
            records=[_record("ERROR", "UBERON:ERROR", "u1")],
        )
    assert isinstance(captured.value.__cause__, ValueError)
    await dispose_engine(engine)


async def test_reactivation_rollback_uses_activation_history_not_creation_parent() -> (
    None
):
    engine = make_engine(get_settings().database_url)
    store = XrefStore(make_sessionmaker(engine))
    source = "uberon-cl"

    class Client:
        pointer: str | None = None

        async def load(self, data: bytes, *, graph_iri: str, **_kwargs: object) -> None:
            if graph_iri == active_graph_iri(source):
                text_data = data.decode()
                self.pointer = (
                    text_data.rsplit("<", 1)[1].split(">", 1)[0] if text_data else None
                )

        async def select(self, _query: str) -> list[dict[str, str]]:
            return _pointer_row(source, self.pointer)

    client = Client()
    generations = []
    runs = {code: await _run(store, source, code) for code in ("A", "B", "C")}
    for code in ("A", "B", "C", "B"):
        generations.append(
            await publish_generation(
                store,
                client,  # type: ignore[arg-type]
                source=source,
                run_id=runs[code],
                records=[_record(code, f"UBERON:{code}", code)],
            )
        )
    assert (
        await rollback_generation(store, client, source) == generations[2].generation_id
    )  # type: ignore[arg-type]
    assert (
        await rollback_generation(store, client, source) == generations[1].generation_id
    )
    await dispose_engine(engine)


async def test_repeated_rollback_traverses_forward_activation_events() -> None:
    engine = make_engine(get_settings().database_url)
    store = XrefStore(make_sessionmaker(engine))
    source = "uberon-cl"

    class Client:
        pointer: str | None = None

        async def load(self, data: bytes, *, graph_iri: str, **_kwargs: object) -> None:
            if graph_iri == active_graph_iri(source):
                self.pointer = data.decode().rsplit("<", 1)[1].split(">", 1)[0]

        async def select(self, _query: str) -> list[dict[str, str]]:
            if self.pointer is None:
                return []
            return [
                {
                    "source": active_graph_iri(source),
                    "predicate": _ACTIVE_PREDICATE,
                    "g": self.pointer,
                }
            ]

    client = Client()
    generations = []
    for code in ("A", "B", "C"):
        generations.append(
            await publish_generation(
                store,
                client,  # type: ignore[arg-type]
                source=source,
                run_id=await _run(store, source, code),
                records=[_record(code, f"UBERON:{code}", code)],
            )
        )
    assert (
        await rollback_generation(store, client, source) == generations[1].generation_id
    )  # type: ignore[arg-type]
    assert (
        await rollback_generation(store, client, source) == generations[0].generation_id
    )  # type: ignore[arg-type]
    await dispose_engine(engine)


async def test_publication_preflight_repairs_hard_crash_split_brain() -> None:
    engine = make_engine(get_settings().database_url)
    store = XrefStore(make_sessionmaker(engine))
    source = "uberon-cl"

    class Client:
        pointer: str | None = None

        async def load(self, data: bytes, *, graph_iri: str, **_kwargs: object) -> None:
            if graph_iri == active_graph_iri(source):
                text_data = data.decode()
                self.pointer = (
                    text_data.rsplit("<", 1)[1].split(">", 1)[0] if text_data else None
                )

        async def select(self, _query: str) -> list[dict[str, str]]:
            return _pointer_row(source, self.pointer)

    client = Client()
    first_run = await _run(store, source, "a")
    first = await publish_generation(
        store,
        client,
        source=source,
        run_id=first_run,  # type: ignore[arg-type]
        records=[_record("SPLIT1", "UBERON:SPLIT1", "a")],
    )
    second_records = [_record("SPLIT2", "UBERON:SPLIT2", "b")]
    second_id, content = generation_identity(source, second_records)
    await store.prepare_generation(
        source=source,
        generation_id=second_id,
        content_sha256=content,
        source_metadata=_SOURCE_METADATA,
        graph_iri=generation_graph_iri(source, second_id),
        run_id=await _run(store, source, "b"),
        records=second_records,
    )
    await store.activate_generation(source, second_id)
    assert await store.active_generation(source) == second_id

    await publish_generation(
        store,
        client,
        source=source,
        run_id=first_run,  # type: ignore[arg-type]
        records=[_record("SPLIT1", "UBERON:SPLIT1", "a")],
    )
    assert await store.active_generation(source) == first.generation_id
    await dispose_engine(engine)


async def test_forward_and_reverse_queries_use_dedicated_indexes() -> None:
    engine = make_engine(get_settings().database_url)
    store = XrefStore(make_sessionmaker(engine))
    source = "uberon-cl"
    run_id = await _run(store, source, "u1")
    records = [_record(f"EX{i}", "UBERON:fanout", "u1") for i in range(300)]
    generation_id, content = generation_identity(source, records)
    await store.prepare_generation(
        source=source,
        generation_id=generation_id,
        content_sha256=content,
        source_metadata=_SOURCE_METADATA,
        graph_iri=generation_graph_iri(source, generation_id),
        run_id=run_id,
        records=records,
    )
    await store.activate_generation(source, generation_id)
    async with engine.connect() as conn:
        await conn.execute(text("SET enable_seqscan = off"))
        forward = " ".join(
            row[0]
            for row in (
                await conn.execute(
                    text(
                        "EXPLAIN SELECT * FROM concept_xref WHERE generation_id = :g "
                        "AND subject_system = 'ncit' AND subject_version = '26.07d' "
                        "AND subject_id = 'EX1'"
                    ),
                    {"g": generation_id},
                )
            )
        )
        reverse = " ".join(
            row[0]
            for row in (
                await conn.execute(
                    text(
                        "EXPLAIN SELECT * FROM concept_xref WHERE generation_id = :g "
                        "AND object_system = 'uberon' AND object_version = 'u1' "
                        "AND object_id = 'UBERON:fanout'"
                    ),
                    {"g": generation_id},
                )
            )
        )
    assert "idx_concept_xref_forward" in forward
    assert "idx_concept_xref_reverse" in reverse
    reverse_rows = (
        await store.mappings_by_objects({"UBERON:fanout"}, expected=_READ_POLICY)
    )["UBERON:fanout"]
    assert len(reverse_rows) == 300
    await dispose_engine(engine)


async def test_concurrent_publishers_and_reader_observe_complete_generations(
    isolated_qlever_url: str,
) -> None:
    engine = make_engine(get_settings().database_url)
    store = XrefStore(make_sessionmaker(engine))
    source = "uberon-cl"
    old_run = await _run(store, source, "old")
    async with SparqlHttpClient.for_qlever(
        isolated_qlever_url, named_graphs=()
    ) as client:
        await publish_generation(
            store,
            client,
            source=source,
            run_id=old_run,
            records=[_record("CON0", "UBERON:CON0", "old")],
        )
        observations: list[frozenset[str]] = []
        stop = asyncio.Event()

        async def reader() -> None:
            while not stop.is_set():
                rows = await store.mappings_by_subjects(
                    {"CON0", "CON1", "CON2"}, expected=_READ_POLICY
                )
                observations.append(frozenset(rows))
                await asyncio.sleep(0)

        async def publisher(code: str) -> None:
            run_id = await _run(store, source, code)
            await publish_generation(
                store,
                client,
                source=source,
                run_id=run_id,
                records=[_record(code, f"UBERON:{code[1:]}", code)],
            )

        task = asyncio.create_task(reader())
        try:
            await asyncio.gather(publisher("CON1"), publisher("CON2"))
        finally:
            stop.set()
            await task
        assert observations
        assert set(observations) <= {
            frozenset({"CON0"}),
            frozenset({"CON1"}),
            frozenset({"CON2"}),
        }
    await dispose_engine(engine)


async def test_source_lock_spans_postgres_rdf_and_activation() -> None:
    engine = make_engine(get_settings().database_url)
    store = XrefStore(make_sessionmaker(engine))
    source = "uberon-cl"
    first_run = await _run(store, source, "first")
    second_run = await _run(store, source, "second")
    first_in_rdf = asyncio.Event()
    release_first = asyncio.Event()

    class PausingClient:
        calls = 0
        pointer: str | None = None

        async def load(self, data: bytes, *, graph_iri: str, **_kwargs: object) -> None:
            self.calls += 1
            if self.calls == 1:
                first_in_rdf.set()
                await release_first.wait()
            if graph_iri == active_graph_iri(source):
                text_data = data.decode()
                self.pointer = (
                    text_data.rsplit("<", 1)[1].split(">", 1)[0] if text_data else None
                )

        async def select(self, _query: str) -> list[dict[str, str]]:
            return _pointer_row(source, self.pointer)

    client = PausingClient()
    first = asyncio.create_task(
        publish_generation(
            store,
            client,  # type: ignore[arg-type]
            source=source,
            run_id=first_run,
            records=[_record("LOCK1", "UBERON:LOCK1", "first")],
        )
    )
    await first_in_rdf.wait()
    second = asyncio.create_task(
        publish_generation(
            store,
            client,  # type: ignore[arg-type]
            source=source,
            run_id=second_run,
            records=[_record("LOCK2", "UBERON:LOCK2", "second")],
        )
    )
    await asyncio.sleep(0.05)
    async with engine.connect() as conn:
        prepared = (
            await conn.execute(
                text("SELECT count(*) FROM xref_generation WHERE source = :source"),
                {"source": source},
            )
        ).scalar_one()
    assert prepared == 1
    assert client.calls == 1

    release_first.set()
    await asyncio.gather(first, second)
    assert client.calls == 4
    await dispose_engine(engine)


async def test_mixed_active_sources_validate_only_their_certified_inputs() -> None:
    engine = make_engine(get_settings().database_url)
    await _clear_active_generations(engine)
    store = XrefStore(make_sessionmaker(engine))
    expected = XrefReadPolicy(
        uberon=UberonReadIdentity(
            ncit_source_identity="1" * 64,
            uberon_source_identity="2" * 64,
            uberon_serving_identity="3" * 64,
        ),
        icdo=IcdoReadIdentity(
            ncit_source_identity="1" * 64,
            icdo_generation_identity="4" * 64,
            icdo_serving_identity="5" * 64,
        ),
    )
    generations = (
        (
            "uberon-cl",
            UberonCandidateGenerationMetadata(
                ncit_source_identity="1" * 64,
                uberon_source_identity="2" * 64,
                uberon_serving_identity="3" * 64,
            ),
            _record("MIX-CANDIDATE", "UBERON:1", "u1"),
        ),
        (
            "uberon-publisher-xref",
            UberonPublisherGenerationMetadata(
                ncit_source_identity="1" * 64,
                uberon_source_identity="2" * 64,
                uberon_serving_identity="3" * 64,
                uberon_assertion_identity="6" * 64,
                ncit_target_identity="7" * 64,
            ),
            SSSOMRecord(
                subject_id="UBERON:2",
                subject_system="uberon-cl",
                predicate_id=CLOSE_MATCH,
                object_id="MIX-PUBLISHER",
                object_system="ncit",
                mapping_justification="semapv:ManualMappingCuration",
                confidence=0.9,
                subject_source_version="u1",
                object_source_version="26.07d",
            ),
        ),
        (
            "uberon-cl-promotion",
            UberonPromotionGenerationMetadata(
                ncit_source_identity="1" * 64,
                uberon_source_identity="2" * 64,
                uberon_serving_identity="3" * 64,
            ),
            _record("MIX-PROMOTION", "UBERON:3", "u1"),
        ),
        (
            "ncit-p334-icdo32",
            P334GenerationMetadata(
                ncit_source_identity="1" * 64,
                icdo_generation_identity="4" * 64,
                icdo_serving_identity="5" * 64,
                ncit_p334_identity="8" * 64,
            ),
            SSSOMRecord(
                subject_id="MIX-P334",
                predicate_id=CLOSE_MATCH,
                object_id="8140/3",
                object_system="icdo",
                mapping_justification="semapv:ManualMappingCuration",
                confidence=1.0,
                subject_source_version="26.07d",
                object_source_version="3.2",
            ),
        ),
    )
    for source, metadata, record in generations:
        run_id = await _run(store, source, "mixed")
        generation_id, content = _generation_identity(source, [record], metadata)
        await store.prepare_generation(
            source=source,
            generation_id=generation_id,
            content_sha256=content,
            source_metadata=metadata,
            graph_iri=generation_graph_iri(source, generation_id),
            run_id=run_id,
            records=[record],
        )
        await store.activate_generation(source, generation_id)

    rows = await store.mappings_for_identifiers(
        {"MIX-CANDIDATE", "MIX-PUBLISHER", "MIX-PROMOTION", "MIX-P334"},
        expected=expected,
    )
    assert set(rows) == {
        "MIX-CANDIDATE",
        "MIX-PUBLISHER",
        "MIX-PROMOTION",
        "MIX-P334",
    }
    await dispose_engine(engine)


@pytest.mark.parametrize(
    ("read_name", "lookup", "expected_key"),
    [
        ("mappings_by_subjects", {"CAPTURED"}, "CAPTURED"),
        ("mappings_by_objects", {"UBERON:CAPTURED"}, "UBERON:CAPTURED"),
        ("mappings_for_identifiers", {"CAPTURED"}, "CAPTURED"),
    ],
)
async def test_general_mapping_reads_pin_the_generation_validated_before_pointer_switch(
    read_name: str, lookup: set[str], expected_key: str
) -> None:
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    normal_store = XrefStore(sf)
    source = "uberon-cl"
    generations: list[str] = []
    for suffix in ("CAPTURED", "REPLACEMENT"):
        record = _record(suffix, f"UBERON:{suffix}", "u1")
        generation_id, content = _generation_identity(
            source, [record], _SOURCE_METADATA
        )
        generations.append(generation_id)
        await normal_store.prepare_generation(
            source=source,
            generation_id=generation_id,
            content_sha256=content,
            source_metadata=_SOURCE_METADATA,
            graph_iri=generation_graph_iri(source, generation_id),
            run_id=await _run(normal_store, source, suffix),
            records=[record],
        )
    await normal_store.activate_generation(source, generations[0])

    execution_count = 0

    class SwitchingSession:
        def __init__(self, session: object) -> None:
            self._session = session
            self._switched = False

        def __getattr__(self, name: str) -> object:
            return getattr(self._session, name)

        async def execute(self, statement: object, params: object = None) -> object:
            nonlocal execution_count
            execution_count += 1
            result = await self._session.execute(statement, params)  # type: ignore[union-attr]
            if not self._switched and "source_metadata" in str(statement):
                self._switched = True
                await normal_store.set_active_generation(source, generations[1])
            return result

    class SwitchingContext:
        def __init__(self) -> None:
            self._context = sf()

        async def __aenter__(self) -> SwitchingSession:
            return SwitchingSession(await self._context.__aenter__())

        async def __aexit__(self, *args: object) -> object:
            return await self._context.__aexit__(*args)

    store = XrefStore(SwitchingContext)  # type: ignore[arg-type]
    rows = await getattr(store, read_name)(lookup, expected=_READ_POLICY)

    assert set(rows) == {expected_key}
    assert execution_count == 2
    assert await normal_store.active_generation(source) == generations[1]
    await dispose_engine(engine)


async def test_candidates_pin_validated_generation_across_pointer_switch() -> None:
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    normal_store = XrefStore(sf)
    source = "uberon-cl"
    generations: list[str] = []
    for suffix in ("CAPTURED-CANDIDATE", "REPLACEMENT-CANDIDATE"):
        record = _record(suffix, f"UBERON:{suffix}", "u1")
        generation_id, content = _generation_identity(
            source, [record], _SOURCE_METADATA
        )
        generations.append(generation_id)
        await normal_store.prepare_generation(
            source=source,
            generation_id=generation_id,
            content_sha256=content,
            source_metadata=_SOURCE_METADATA,
            graph_iri=generation_graph_iri(source, generation_id),
            run_id=await _run(normal_store, source, suffix),
            records=[record],
        )
    await normal_store.activate_generation(source, generations[0])

    execution_count = 0

    class SwitchingSession:
        def __init__(self, session: object) -> None:
            self._session = session
            self._switched = False

        def __getattr__(self, name: str) -> object:
            return getattr(self._session, name)

        async def execute(self, statement: object, params: object = None) -> object:
            nonlocal execution_count
            execution_count += 1
            result = await self._session.execute(statement, params)  # type: ignore[union-attr]
            if not self._switched and "source_metadata" in str(statement):
                self._switched = True
                await normal_store.set_active_generation(source, generations[1])
            return result

    class SwitchingContext:
        def __init__(self) -> None:
            self._context = sf()

        async def __aenter__(self) -> SwitchingSession:
            return SwitchingSession(await self._context.__aenter__())

        async def __aexit__(self, *args: object) -> object:
            return await self._context.__aexit__(*args)

    store = XrefStore(SwitchingContext)  # type: ignore[arg-type]
    rows = await store.proposed_candidates(
        expected=UberonReadIdentity(
            ncit_source_identity="a" * 64,
            uberon_source_identity="b" * 64,
            uberon_serving_identity="c" * 64,
        )
    )

    assert [row.subject_id for row in rows] == ["CAPTURED-CANDIDATE"]
    assert execution_count == 2
    assert await normal_store.active_generation(source) == generations[1]
    await dispose_engine(engine)


@pytest.mark.parametrize("matching", [False, True])
async def test_stale_active_generation_refuses_even_without_a_matching_row(
    matching: bool,
) -> None:
    engine = make_engine(get_settings().database_url)
    await _clear_active_generations(engine)
    store = XrefStore(make_sessionmaker(engine))
    source = "uberon-cl"
    metadata = UberonCandidateGenerationMetadata(
        ncit_source_identity="a" * 64,
        uberon_source_identity="b" * 64,
        uberon_serving_identity="d" * 64,
    )
    code = "STALE" if matching else "OTHER"
    record = _record(code, "UBERON:STALE", "u1")
    run_id = await _run(store, source, "stale")
    generation_id, content = _generation_identity(source, [record], metadata)
    await store.prepare_generation(
        source=source,
        generation_id=generation_id,
        content_sha256=content,
        source_metadata=metadata,
        graph_iri=generation_graph_iri(source, generation_id),
        run_id=run_id,
        records=[record],
    )
    await store.activate_generation(source, generation_id)

    with pytest.raises(StaleXrefGenerationError, match="uberon_source_identity"):
        await store.mappings_by_subjects(
            {"STALE"},
            expected=XrefReadPolicy(
                uberon=UberonReadIdentity(
                    ncit_source_identity="a" * 64,
                    uberon_source_identity="c" * 64,
                    uberon_serving_identity="d" * 64,
                )
            ),
        )
    await dispose_engine(engine)


async def test_read_validates_only_sources_relevant_to_expected_contract() -> None:
    engine = make_engine(get_settings().database_url)
    await _clear_active_generations(engine)
    store = XrefStore(make_sessionmaker(engine))
    generations = (
        (
            "uberon-cl",
            UberonCandidateGenerationMetadata(
                ncit_source_identity="1" * 64,
                uberon_source_identity="2" * 64,
                uberon_serving_identity="3" * 64,
            ),
            _record("RELEVANT", "UBERON:RELEVANT", "u1"),
        ),
        (
            "ncit-p334-icdo32",
            P334GenerationMetadata(
                ncit_source_identity="9" * 64,
                icdo_generation_identity="4" * 64,
                icdo_serving_identity="5" * 64,
                ncit_p334_identity="8" * 64,
            ),
            SSSOMRecord(
                subject_id="IRRELEVANT",
                predicate_id=CLOSE_MATCH,
                object_id="8140/3",
                object_system="icdo",
                mapping_justification="semapv:ManualMappingCuration",
                confidence=1.0,
                subject_source_version="26.07d",
                object_source_version="3.2",
            ),
        ),
    )
    for source, metadata, record in generations:
        generation_id, content = _generation_identity(source, [record], metadata)
        await store.prepare_generation(
            source=source,
            generation_id=generation_id,
            content_sha256=content,
            source_metadata=metadata,
            graph_iri=generation_graph_iri(source, generation_id),
            run_id=await _run(store, source, "relevant"),
            records=[record],
        )
        await store.activate_generation(source, generation_id)

    rows = await store.mappings_by_subjects(
        {"RELEVANT", "IRRELEVANT"},
        expected=XrefReadPolicy(
            uberon=UberonReadIdentity(
                ncit_source_identity="1" * 64,
                uberon_source_identity="2" * 64,
                uberon_serving_identity="3" * 64,
            )
        ),
    )
    assert set(rows) == {"RELEVANT"}
    await dispose_engine(engine)


async def test_promotion_requires_all_read_identities() -> None:
    engine = make_engine(get_settings().database_url)
    await _clear_active_generations(engine)
    store = XrefStore(make_sessionmaker(engine))
    source = "uberon-cl-promotion"
    metadata = UberonPromotionGenerationMetadata(
        ncit_source_identity="1" * 64,
        uberon_source_identity="2" * 64,
        uberon_serving_identity="3" * 64,
    )
    record = _record("PROMOTION-METADATA", "UBERON:PROMOTION", "u1")
    generation_id, content = _generation_identity(source, [record], metadata)
    await store.prepare_generation(
        source=source,
        generation_id=generation_id,
        content_sha256=content,
        source_metadata=metadata,
        graph_iri=generation_graph_iri(source, generation_id),
        run_id=await _run(store, source, "promotion"),
        records=[record],
    )
    await store.activate_generation(source, generation_id)

    with pytest.raises(StaleXrefGenerationError, match="uberon_serving_identity"):
        await store.mappings_by_subjects(
            {"PROMOTION-METADATA"},
            expected=XrefReadPolicy(
                uberon=UberonReadIdentity(
                    ncit_source_identity="1" * 64,
                    uberon_source_identity="2" * 64,
                    uberon_serving_identity="4" * 64,
                )
            ),
        )
    await dispose_engine(engine)


async def test_nonempty_lookup_without_requested_active_family_fails_closed() -> None:
    engine = make_engine(get_settings().database_url)
    await _clear_active_generations(engine)
    store = XrefStore(make_sessionmaker(engine))

    with pytest.raises(UnavailableXrefGenerationError, match="Uberon"):
        await store.mappings_by_subjects({"ABSENT"}, expected=_READ_POLICY)
    await dispose_engine(engine)


async def test_active_certified_family_with_no_matching_mapping_returns_empty() -> None:
    engine = make_engine(get_settings().database_url)
    await _clear_active_generations(engine)
    store = XrefStore(make_sessionmaker(engine))
    source = "uberon-cl"
    record = _record("OTHER", "UBERON:OTHER", "u1")
    generation_id, content = _generation_identity(source, [record], _SOURCE_METADATA)
    await store.prepare_generation(
        source=source,
        generation_id=generation_id,
        content_sha256=content,
        source_metadata=_SOURCE_METADATA,
        graph_iri=generation_graph_iri(source, generation_id),
        run_id=await _run(store, source, "active-empty"),
        records=[record],
    )
    await store.activate_generation(source, generation_id)

    assert await store.mappings_by_subjects({"ABSENT"}, expected=_READ_POLICY) == {}
    await dispose_engine(engine)


async def test_prepare_rejects_metadata_for_another_source() -> None:
    engine = make_engine(get_settings().database_url)
    store = XrefStore(make_sessionmaker(engine))
    metadata = UberonCandidateGenerationMetadata(
        source="uberon-cl",
        ncit_source_identity="1" * 64,
        uberon_source_identity="2" * 64,
        uberon_serving_identity="3" * 64,
    )
    record = _record("SOURCE-MISMATCH", "UBERON:1", "u1")
    generation_id, content = _generation_identity(
        "uberon-cl-promotion", [record], metadata
    )
    with pytest.raises(ValueError, match="metadata source"):
        await store.prepare_generation(
            source="uberon-cl-promotion",
            generation_id=generation_id,
            content_sha256=content,
            source_metadata=metadata,
            graph_iri=generation_graph_iri("uberon-cl-promotion", generation_id),
            run_id=await _run(store, "uberon-cl-promotion", "mismatch"),
            records=[record],
        )
    await dispose_engine(engine)


async def test_database_rejects_invalid_source_metadata_and_endpoint_systems() -> None:
    engine = make_engine(get_settings().database_url)
    for source, metadata, constraint in (
        (
            "unknown",
            {"source": "unknown", "ncit_source_identity": "1" * 64},
            "xref_generation_check",
        ),
        (
            "uberon-cl",
            {"source": "uberon-cl", "ncit_source_identity": "1" * 64},
            "xref_generation_check",
        ),
        (
            "uberon-cl",
            {
                "source": "uberon-cl-promotion",
                "ncit_source_identity": "1" * 64,
                "uberon_source_identity": "2" * 64,
                "uberon_serving_identity": "3" * 64,
            },
            "xref_generation_check",
        ),
    ):
        run_id = f"invalid-metadata-{secrets.token_hex(8)}"
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO xref_run "
                    "(id,source,status,ncit_version,source_version,started_at) "
                    "VALUES (:run,:source,'running','n','u',now())"
                ),
                {"run": run_id, "source": source},
            )
            with pytest.raises(IntegrityError, match=constraint):
                await connection.execute(
                    text(
                        "INSERT INTO xref_generation "
                        "(id,source,content_sha256,source_metadata,graph_iri,"
                        "run_id,state) "
                        "VALUES (:id,:source,:id,CAST(:metadata AS jsonb),"
                        ":graph,:run,'prepared')"
                    ),
                    {
                        "id": secrets.token_hex(32),
                        "source": source,
                        "metadata": json.dumps(metadata),
                        "graph": f"https://example.test/{secrets.token_hex(8)}",
                        "run": run_id,
                    },
                )

    store = XrefStore(make_sessionmaker(engine))
    metadata = UberonCandidateGenerationMetadata(
        ncit_source_identity="1" * 64,
        uberon_source_identity="2" * 64,
        uberon_serving_identity="3" * 64,
    )
    cross_system = SSSOMRecord(
        subject_id="CROSS-SYSTEM",
        predicate_id=CLOSE_MATCH,
        object_id="8140/3",
        object_system="icdo",
        mapping_justification="semapv:ManualMappingCuration",
        confidence=1.0,
        subject_source_version="26.07d",
        object_source_version="3.2",
    )
    generation_id, content = _generation_identity("uberon-cl", [cross_system], metadata)
    with pytest.raises(IntegrityError, match="concept_xref_check"):
        await store.prepare_generation(
            source="uberon-cl",
            generation_id=generation_id,
            content_sha256=content,
            source_metadata=metadata,
            graph_iri=generation_graph_iri("uberon-cl", generation_id),
            run_id=await _run(store, "uberon-cl", "cross-system"),
            records=[cross_system],
        )
    await dispose_engine(engine)


async def test_identical_content_with_changed_metadata_is_distinct() -> None:
    engine = make_engine(get_settings().database_url)
    store = XrefStore(make_sessionmaker(engine))
    source = "uberon-cl"
    record = _record("METADATA", "UBERON:METADATA", "u1")
    generation_ids: list[str] = []
    for identity in ("a" * 64, "b" * 64):
        metadata = UberonCandidateGenerationMetadata(
            ncit_source_identity="1" * 64,
            uberon_source_identity=identity,
            uberon_serving_identity="3" * 64,
        )
        generation_id, content = _generation_identity(source, [record], metadata)
        generation_ids.append(generation_id)
        assert await store.prepare_generation(
            source=source,
            generation_id=generation_id,
            content_sha256=content,
            source_metadata=metadata,
            graph_iri=generation_graph_iri(source, generation_id),
            run_id=await _run(store, source, identity[:1]),
            records=[record],
        )
        assert await store.activate_generation(source, generation_id)

    assert generation_ids[0] != generation_ids[1]
    assert await store.active_generation(source) == generation_ids[1]
    async with engine.connect() as connection:
        assert (
            await connection.execute(
                text(
                    "SELECT count(*) FROM xref_generation "
                    "WHERE source=:source AND id=ANY(:ids)"
                ),
                {"source": source, "ids": generation_ids},
            )
        ).scalar_one() == 2
    await dispose_engine(engine)
