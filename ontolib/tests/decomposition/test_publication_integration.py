"""Real external-system contracts for decomposition graph publication."""

from __future__ import annotations

import asyncio
import datetime
from typing import TYPE_CHECKING

import asyncpg
import httpx
import pytest

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition import vocab
from ontolib.decomposition.legacy_writer import write_ttl
from ontolib.decomposition.models import Decomposition
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.decomposition.provenance_models import RunFingerprint
from ontolib.decomposition.publication import (
    publish_artifact,
    read_publication_marker,
    staging_graph_iri,
)
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [
    pytest.mark.integration,
    pytest.mark.mutating_integration,
]

_PUBLIC = vocab.DECOMPOSED_GRAPH_IRI
_STAGING = f"{_PUBLIC}/staging/preflight"
_OLD = '<urn:old> <urn:value> "old" .'
_NEW = '<urn:new> <urn:value> "new" .'
_MARKER = '<urn:publication> <urn:run> "preflight" .'
_RUN_ID = "test-decomposition-publication-integration"
_CONCURRENT_RUN_IDS = (
    "test-decomposition-publication-concurrent-1",
    "test-decomposition-publication-concurrent-2",
)


async def _put_graph(url: str, graph: str, turtle: str) -> None:
    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"{url}/store",
            params={"graph": graph},
            content=turtle.encode(),
            headers={"Content-Type": "text/turtle"},
        )
    response.raise_for_status()


async def _update(url: str, statement: str) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.post(
            f"{url}/update",
            content=statement.encode(),
            headers={"Content-Type": "application/sparql-update"},
        )


async def _ask(url: str, statement: str) -> bool:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{url}/query",
            content=statement.encode(),
            headers={
                "Content-Type": "application/sparql-query",
                "Accept": "application/sparql-results+json",
            },
        )
    response.raise_for_status()
    return bool(response.json()["boolean"])


async def _write_concurrent_artifacts(
    tmp_path: Path,
    decompositions: tuple[Decomposition, ...],
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    artifacts = tuple(tmp_path / f".{run_id}.ttl" for run_id in _CONCURRENT_RUN_IDS)
    destinations = tuple(tmp_path / f"{run_id}.ttl" for run_id in _CONCURRENT_RUN_IDS)
    for artifact, run_id, decomposition in zip(
        artifacts,
        _CONCURRENT_RUN_IDS,
        decompositions,
        strict=True,
    ):
        await write_ttl([decomposition], artifact, run_id=run_id)
    return artifacts, destinations


async def _assert_runs_published(store: ProvenanceStore) -> None:
    for run_id in _CONCURRENT_RUN_IDS:
        summary = await store.get_run(run_id)
        assert summary is not None
        assert summary.status == "complete"
        assert summary.publication_state == "published"


@pytest.mark.usefixtures("isolated_oxigraph_settings")
async def test_oxigraph_update_is_transactional_and_empty_replacement_is_clean(
    isolated_oxigraph_url: str,
) -> None:
    await _put_graph(isolated_oxigraph_url, _PUBLIC, _OLD)
    await _put_graph(isolated_oxigraph_url, _STAGING, _NEW)

    failed = await _update(
        isolated_oxigraph_url,
        f"""
        CLEAR GRAPH <{_PUBLIC}>;
        ADD GRAPH <{_STAGING}> TO GRAPH <{_PUBLIC}>;
        THIS IS NOT VALID SPARQL
        """,
    )
    assert failed.is_error
    assert await _ask(
        isolated_oxigraph_url,
        f'ASK {{ GRAPH <{_PUBLIC}> {{ <urn:old> <urn:value> "old" }} }}',
    )
    assert await _ask(
        isolated_oxigraph_url,
        f'ASK {{ GRAPH <{_STAGING}> {{ <urn:new> <urn:value> "new" }} }}',
    )

    replaced = await _update(
        isolated_oxigraph_url,
        f"""
        CLEAR GRAPH <{_PUBLIC}>;
        ADD GRAPH <{_STAGING}> TO GRAPH <{_PUBLIC}>;
        DROP GRAPH <{_STAGING}>;
        INSERT DATA {{ GRAPH <{_PUBLIC}> {{ {_MARKER} }} }}
        """,
    )
    replaced.raise_for_status()
    assert not await _ask(
        isolated_oxigraph_url,
        f"ASK {{ GRAPH <{_PUBLIC}> {{ <urn:old> ?p ?o }} }}",
    )
    assert await _ask(
        isolated_oxigraph_url,
        f'ASK {{ GRAPH <{_PUBLIC}> {{ <urn:new> <urn:value> "new" }} }}',
    )
    assert not await _ask(
        isolated_oxigraph_url,
        f"ASK {{ GRAPH <{_STAGING}> {{ ?s ?p ?o }} }}",
    )

    emptied = await _update(
        isolated_oxigraph_url,
        f"""
        CLEAR GRAPH <{_PUBLIC}>;
        ADD SILENT GRAPH <urn:missing-empty-staging> TO GRAPH <{_PUBLIC}>;
        INSERT DATA {{ GRAPH <{_PUBLIC}> {{ {_MARKER} }} }}
        """,
    )
    emptied.raise_for_status()
    assert not await _ask(
        isolated_oxigraph_url,
        f"ASK {{ GRAPH <{_PUBLIC}> {{ <urn:new> ?p ?o }} }}",
    )
    assert await _ask(
        isolated_oxigraph_url,
        f'ASK {{ GRAPH <{_PUBLIC}> {{ <urn:publication> <urn:run> "preflight" }} }}',
    )


@pytest.mark.usefixtures("isolated_postgres_settings")
async def test_postgres_advisory_lock_excludes_and_then_admits_a_publisher(
    isolated_postgres_url: str,
) -> None:
    dsn = isolated_postgres_url.replace("+asyncpg", "")
    first = await asyncpg.connect(dsn)
    second = await asyncpg.connect(dsn)
    key = "decomposition:publication"
    try:
        await first.execute("SELECT pg_advisory_lock(hashtextextended($1, 0))", key)
        assert not await second.fetchval(
            "SELECT pg_try_advisory_lock(hashtextextended($1, 0))", key
        )
        assert await first.fetchval(
            "SELECT pg_advisory_unlock(hashtextextended($1, 0))", key
        )
        assert await second.fetchval(
            "SELECT pg_try_advisory_lock(hashtextextended($1, 0))", key
        )
        assert await second.fetchval(
            "SELECT pg_advisory_unlock(hashtextextended($1, 0))", key
        )
    finally:
        await first.close()
        await second.close()


@pytest.mark.usefixtures("isolated_postgres_settings", "isolated_oxigraph_settings")
async def test_production_publication_reconciles_marker_ahead_and_clears_stale_graph(
    isolated_oxigraph_url: str,
    tmp_path: Path,
) -> None:
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    dsn = get_settings().database_url.replace("+asyncpg", "")
    artifact = tmp_path / ".decomposed.ttl.staging"
    destination = tmp_path / "decomposed.ttl"
    destination.mkdir()
    fingerprint = RunFingerprint(
        source_identity="a" * 64,
        branch="neoplasm",
        semantic_types=("Neoplastic Process",),
        worklist=(),
        algorithm_version="decomposition-v2",
        config_version="complete-definition-v1",
        walker_max_depth=5,
        output_mode="file",
        load_mode="named-graph",
        emitted_at=datetime.datetime(2026, 7, 30, 12, 0, tzinfo=datetime.UTC),
    )
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("DELETE FROM decomp_run WHERE id = $1", _RUN_ID)
        await store.create_run(_RUN_ID, "26.07d", fingerprint)
        await _put_graph(isolated_oxigraph_url, _PUBLIC, _OLD)
        await write_ttl([], artifact, run_id=_RUN_ID)

        async with OxigraphHttpClient(isolated_oxigraph_url) as client:
            with pytest.raises(OSError, match="directory"):
                await publish_artifact(
                    run_id=_RUN_ID,
                    source_identity="a" * 64,
                    artifact=artifact,
                    destination=destination,
                    expected_codes=set(),
                    metrics={"decomposed": 0, "total_in_scope": 0},
                    load_to_store=True,
                    client=client,
                    provenance=store,
                )

            failed = await store.get_run(_RUN_ID)
            assert failed is not None
            assert failed.status == "running"
            assert failed.publication_state == "failed"
            first_marker = await read_publication_marker(client)
            assert first_marker is not None
            assert first_marker.run_id == _RUN_ID

            destination.rmdir()
            await write_ttl([], artifact, run_id=_RUN_ID)
            second_marker = await publish_artifact(
                run_id=_RUN_ID,
                source_identity="a" * 64,
                artifact=artifact,
                destination=destination,
                expected_codes=set(),
                metrics={"decomposed": 0, "total_in_scope": 0},
                load_to_store=True,
                client=client,
                provenance=store,
            )

            assert second_marker == first_marker
            assert not await client.ask(
                f"ASK {{ GRAPH <{_PUBLIC}> {{ <urn:old> ?p ?o }} }}"
            )
            assert not await client.ask(
                f"ASK {{ GRAPH <{staging_graph_iri(_RUN_ID)}> {{ ?s ?p ?o }} }}"
            )

        complete = await store.get_run(_RUN_ID)
        assert complete is not None
        assert complete.status == "complete"
        assert complete.publication_state == "published"
        assert destination.exists()
        assert not artifact.exists()
    finally:
        await conn.execute("DELETE FROM decomp_run WHERE id = $1", _RUN_ID)
        await conn.close()
        await dispose_engine(engine)


@pytest.mark.usefixtures("isolated_postgres_settings", "isolated_oxigraph_settings")
async def test_concurrent_publishers_are_serialized_and_readers_see_complete_graphs(
    isolated_oxigraph_url: str,
    tmp_path: Path,
) -> None:
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    dsn = get_settings().database_url.replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    codes = ("C1", "C2")
    decompositions = tuple(
        Decomposition(code=code, semantic_type="Neoplastic Process") for code in codes
    )
    try:
        await conn.execute(
            "DELETE FROM decomp_run WHERE id = ANY($1)",
            list(_CONCURRENT_RUN_IDS),
        )
        for run_id, code, decomposition in zip(
            _CONCURRENT_RUN_IDS,
            codes,
            decompositions,
            strict=True,
        ):
            fingerprint = RunFingerprint(
                source_identity="a" * 64,
                branch="neoplasm",
                semantic_types=("Neoplastic Process",),
                worklist=(code,),
                algorithm_version="decomposition-v2",
                config_version="complete-definition-v1",
                walker_max_depth=5,
                output_mode="file",
                load_mode="named-graph",
                emitted_at=datetime.datetime(2026, 7, 30, 12, 0, tzinfo=datetime.UTC),
            )
            await store.create_run(run_id, "26.07d", fingerprint)
            claim = await store.claim_work_item(run_id, code)
            assert claim is not None
            await store.complete_work_item(
                run_id,
                code,
                claim,
                decomposition=decomposition,
                minted=(),
            )

        old_graph = f"""
        @prefix op: <{vocab.ONTOPRISM_NS}> .
        <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C0>
          op:representationStatus "legacy-precoordinated" ;
          op:decomposedBy "old-run" .
        """
        await _put_graph(isolated_oxigraph_url, _PUBLIC, old_graph)
        artifacts, destinations = await _write_concurrent_artifacts(
            tmp_path,
            decompositions,
        )

        observations: list[frozenset[str]] = []
        stop_reading = asyncio.Event()
        async with OxigraphHttpClient(isolated_oxigraph_url) as client:

            async def observe() -> None:
                while not stop_reading.is_set():
                    rows = await client.select_once(
                        f"SELECT ?run WHERE {{ GRAPH <{_PUBLIC}> "
                        f"{{ ?s <{vocab.DECOMPOSED_BY}> ?run }} }}",
                        required_variables={"run"},
                    )
                    observations.append(
                        frozenset(
                            str(row["run"])
                            for row in rows
                            if row.get("run") is not None
                        )
                    )
                    await asyncio.sleep(0)

            async def publish(index: int) -> None:
                await publish_artifact(
                    run_id=_CONCURRENT_RUN_IDS[index],
                    source_identity="a" * 64,
                    artifact=artifacts[index],
                    destination=destinations[index],
                    expected_codes={codes[index]},
                    metrics={"decomposed": 1, "total_in_scope": 1},
                    load_to_store=True,
                    client=client,
                    provenance=store,
                )

            reader = asyncio.create_task(observe())
            try:
                await asyncio.gather(publish(0), publish(1))
            finally:
                stop_reading.set()
                await reader

            final_marker = await read_publication_marker(client)
            assert final_marker is not None
            assert final_marker.run_id in _CONCURRENT_RUN_IDS
            final_rows = await client.select_once(
                f"SELECT ?run WHERE {{ GRAPH <{_PUBLIC}> "
                f"{{ ?s <{vocab.DECOMPOSED_BY}> ?run }} }}",
                required_variables={"run"},
            )

        allowed = {
            frozenset({"old-run"}),
            *(frozenset({run_id}) for run_id in _CONCURRENT_RUN_IDS),
        }
        assert observations
        assert set(observations) <= allowed
        assert {str(row["run"]) for row in final_rows} == {final_marker.run_id}
        assert all(destination.exists() for destination in destinations)
        await _assert_runs_published(store)
    finally:
        await conn.execute(
            "DELETE FROM decomp_run WHERE id = ANY($1)",
            list(_CONCURRENT_RUN_IDS),
        )
        await conn.close()
        await dispose_engine(engine)
