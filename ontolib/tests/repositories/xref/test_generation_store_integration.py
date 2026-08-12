from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.repositories.xref.models import SSSOMRecord
from ontolib.repositories.xref.publication import (
    XrefPublicationError,
    generation_graph_iri,
    generation_identity,
    publish_generation,
)
from ontolib.repositories.xref.store import XrefStore
from ontolib.repositories.xref.vocab import CLOSE_MATCH
from ontolib.terminologies.sparql_http_client import SparqlHttpClient

pytestmark = [
    pytest.mark.integration,
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings", "isolated_qlever_settings"),
]


def _record(subject: str, obj: str, version: str) -> SSSOMRecord:
    return SSSOMRecord(
        subject_id=subject,
        subject_system="ncit",
        predicate_id=CLOSE_MATCH,
        object_id=obj,
        object_system="uberon",
        mapping_justification="semapv:DatabaseCrossReference",
        confidence=0.9,
        subject_source_version="26.07d",
        object_source_version=version,
    )


async def _run(store: XrefStore, source: str, version: str) -> str:
    run_id = uuid.uuid4().hex
    await store.upsert_run(run_id, source, "26.07d", version)
    return run_id


async def test_active_reads_are_typed_many_to_many_and_rollback_is_source_local(
    isolated_qlever_url: str,
) -> None:
    engine = make_engine(get_settings().database_url)
    store = XrefStore(make_sessionmaker(engine))
    async with SparqlHttpClient.for_qlever(
        isolated_qlever_url, named_graphs=()
    ) as client:
        run_a = await _run(store, "issue291-uberon", "u1")
        first = await publish_generation(
            store,
            client,
            source="issue291-uberon",
            run_id=run_a,
            records=[_record("C1", "UBERON:1", "u1")],
        )
        run_other = await _run(store, "issue291-other", "u1")
        other = await publish_generation(
            store,
            client,
            source="issue291-other",
            run_id=run_other,
            records=[_record("C9", "UBERON:9", "u1")],
        )
        run_b = await _run(store, "issue291-uberon", "u2")
        second = await publish_generation(
            store,
            client,
            source="issue291-uberon",
            run_id=run_b,
            records=[
                _record("C1", "UBERON:2", "u2"),
                _record("C2", "UBERON:2", "u2"),
                _record("C2", "UBERON:3", "u2"),
            ],
        )

        forward = await store.mappings_by_subjects({"C1", "C2"})
        reverse = await store.mappings_by_objects({"UBERON:2"})
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

        assert await store.rollback("issue291-uberon") == first.generation_id
        rolled_back = await store.mappings_by_subjects({"C1", "C2", "C9"})
        assert {row.object.identifier for row in rolled_back["C1"]} == {"UBERON:1"}
        assert "C2" not in rolled_back
        assert {row.object.identifier for row in rolled_back["C9"]} == {"UBERON:9"}
        assert other.generation_id != first.generation_id
    await dispose_engine(engine)


async def test_crash_reconciliation_is_idempotent_without_pointer_churn(
    isolated_qlever_url: str,
) -> None:
    engine = make_engine(get_settings().database_url)
    store = XrefStore(make_sessionmaker(engine))
    source = "issue291-reconcile"
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
            assert await store.mappings_by_subjects({"C7"}) == {}

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


async def test_forward_and_reverse_queries_use_dedicated_indexes() -> None:
    engine = make_engine(get_settings().database_url)
    store = XrefStore(make_sessionmaker(engine))
    source = "issue291-explain"
    run_id = await _run(store, source, "u1")
    records = [_record(f"EX{i}", "UBERON:fanout", "u1") for i in range(300)]
    generation_id, content = generation_identity(source, records)
    await store.prepare_generation(
        source=source,
        generation_id=generation_id,
        content_sha256=content,
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
    reverse_rows = (await store.mappings_by_objects({"UBERON:fanout"}))["UBERON:fanout"]
    assert len(reverse_rows) == 300
    await dispose_engine(engine)


async def test_concurrent_publishers_and_reader_observe_complete_generations(
    isolated_qlever_url: str,
) -> None:
    engine = make_engine(get_settings().database_url)
    store = XrefStore(make_sessionmaker(engine))
    source = "issue291-concurrent"
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
                rows = await store.mappings_by_subjects({"CON0", "CON1", "CON2"})
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
    source = "issue291-lock-boundary"
    first_run = await _run(store, source, "first")
    second_run = await _run(store, source, "second")
    first_in_rdf = asyncio.Event()
    release_first = asyncio.Event()

    class PausingClient:
        calls = 0

        async def load(self, *_args: object, **_kwargs: object) -> None:
            self.calls += 1
            if self.calls == 1:
                first_in_rdf.set()
                await release_first.wait()

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
    assert client.calls == 2
    await dispose_engine(engine)
