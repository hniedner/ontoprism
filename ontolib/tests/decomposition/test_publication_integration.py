"""Real external-system contracts for decomposition graph publication."""

from __future__ import annotations

import asyncio
import datetime
from typing import TYPE_CHECKING

import asyncpg
import httpx
import pytest
from rdflib import Graph, Literal, URIRef

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition import vocab
from ontolib.decomposition.corpus_acceptance import (
    CertifiedSourceBinding,
    EvidenceAmbiguityReport,
    PersistedAssertionAssessment,
    PersistedAssertionEvidence,
    PersistedEvidenceEvaluation,
    PublicationPlaneBinding,
    build_assertion_evidence_closure,
    build_machine_acceptance_metadata_artifact,
    build_machine_evidence_acceptance,
    dry_run_corpus_publication,
    issue_machine_publication_token,
)
from ontolib.decomposition.legacy_writer import write_ttl
from ontolib.decomposition.models import Decomposition
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.decomposition.provenance_models import (
    RUN_STAGE_SEQUENCE_IDENTITY,
    RunFingerprint,
)
from ontolib.decomposition.publication import (
    AcceptancePublicationIntent,
    AcceptancePublicationReceipt,
    PublicationMarker,
    publish_artifact,
    publish_machine_accepted_artifact,
    read_publication_marker,
    staging_graph_iri,
)
from ontolib.decomposition.read import decomposition_from_rows
from ontolib.decomposition.read_models import (
    ProjectedEffectiveProjection,
)
from ontolib.decomposition.read_queries import build_decomposition_query
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.client import ncit_sparql_client

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


@pytest.mark.usefixtures("isolated_postgres_settings")
async def test_acceptance_publication_intent_and_receipt_roundtrip_postgres(
    tmp_path: Path,
) -> None:
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    marker = PublicationMarker(
        run_id=_RUN_ID,
        source_identity="a" * 64,
        representation_identity="b" * 64,
        built_at=datetime.datetime(2026, 9, 17, tzinfo=datetime.UTC),
    )
    intent = AcceptancePublicationIntent(
        acceptance_identity="c" * 64,
        candidate_identity="d" * 64,
        exclusions_identity="e" * 64,
        artifact_identity="b" * 64,
        destination=str(tmp_path / "accepted-c3262.ttl"),
        marker=marker,
        predecessor=None,
    )
    receipt = AcceptancePublicationReceipt.build(
        intent=intent,
        readback_marker=marker,
    )
    dsn = get_settings().database_url.replace("+asyncpg", "")
    connection = await asyncpg.connect(dsn)
    try:
        first = await store.begin_acceptance_publication(intent)
        retry = await store.begin_acceptance_publication(intent)
        assert first == retry == intent
        await store.finish_acceptance_publication(receipt)
        assert (
            await store.acceptance_publication_receipt(intent.acceptance_identity)
            == receipt
        )
    finally:
        await connection.execute(
            "DELETE FROM decomp_acceptance_publication WHERE acceptance_identity = $1",
            intent.acceptance_identity,
        )
        await connection.close()
        await dispose_engine(engine)


@pytest.mark.usefixtures(
    "isolated_postgres_settings",
    "isolated_qlever_settings",
    "preserved_decomposed_graph",
)
async def test_machine_accepted_publication_recovers_marker_ahead_of_receipt(
    isolated_qlever_url: str,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.ttl"
    raw_artifact = tmp_path / "closure.ttl"
    artifact = tmp_path / "accepted.ttl"
    destination = tmp_path / "published.ttl"
    fact_id = "1" * 64
    source.write_text(
        f"<{NCIT_NS}C1> "
        f"<{vocab.HAS_CONSTITUENT}> [<{vocab.AXIS}> "
        f"<{vocab.ONTOPRISM_NS}PrimarySite> ; "
        f'<{vocab.FILLER}> <{NCIT_NS}C2> ; <{vocab.AXIS_SOURCE}> "role" ; '
        f"<{vocab.SOURCE_ROLE}> <{NCIT_NS}R101> ; "
        f"<{vocab.SOURCE_DEFINITION_FACT}> "
        f"<{vocab.DEFINITION_FACT_NS}C1/{fact_id}> ; "
        f"<{vocab.MOST_SPECIFIC}> true] .\n"
        f"<{NCIT_NS}C1> "
        f'<{vocab.REPRESENTATION_STATUS}> "{vocab.LEGACY_PRECOORDINATED}" ; '
        f'<{vocab.DECOMPOSED_BY}> "{_RUN_ID}" .\n'
    )
    source_binding = CertifiedSourceBinding(
        release="26.07d",
        source_manifest_identity="2" * 64,
        source_identity="a" * 64,
        stated_artifact_identity="3" * 64,
        certification="expert-curated-ncit-release",
    )
    closure = build_assertion_evidence_closure(
        source_artifact=source,
        effective_destination=raw_artifact,
        source=source_binding,
        persisted_evidence=PersistedEvidenceEvaluation.build(
            policy_identity="5" * 64,
            assessments=(
                PersistedAssertionAssessment(
                    concept_code="C1",
                    axis="op:PrimarySite",
                    filler_code="C2",
                    axis_source="role",
                    source_fact_ids=(fact_id,),
                    source_occurrence_ids=("4" * 64,),
                    occurrence_availability="available",
                    complete_definition_identity=None,
                    source_roles=("R101",),
                    transformation_rule="axis-contract:PrimarySite",
                    transformation_policy_identity="5" * 64,
                    applicability_identity="6" * 64,
                    gap_reasons=(),
                    evidence=PersistedAssertionEvidence(
                        concept_code="C1",
                        axis="op:PrimarySite",
                        filler_code="C2",
                        axis_source="role",
                        source_fact_ids=(fact_id,),
                        source_occurrence_ids=("4" * 64,),
                        occurrence_availability="available",
                        complete_definition_identity=None,
                        transformation_rule="axis-contract:PrimarySite",
                        transformation_policy_identity="5" * 64,
                        applicability_identity="6" * 64,
                    ),
                ),
            ),
        ),
        concept_exclusions=(),
    )
    semantic_identity = closure.closure_identity
    artifact_identity = build_machine_acceptance_metadata_artifact(
        closure=closure,
        source_artifact=raw_artifact,
        destination=artifact,
        run_id=_RUN_ID,
        representation_identity=semantic_identity,
        publication_identity=semantic_identity,
    )
    accepted_graph = Graph().parse(artifact, format="turtle")
    assert set(
        accepted_graph.objects(URIRef(f"{NCIT_NS}C1"), URIRef(vocab.ACCEPTANCE_STATUS))
    ) == {Literal("projected")}
    closure = closure.model_copy(
        update={"effective_artifact_identity": artifact_identity}
    )
    ambiguity = EvidenceAmbiguityReport.from_closure(closure)
    acceptance = build_machine_evidence_acceptance(
        candidate_identity="7" * 64,
        closure=closure,
        exclusions_identity="8" * 64,
        evidence_ledger_identity=closure.evidence_ledger_identity,
        policy_identity="5" * 64,
        dry_run_identity="9" * 64,
        ambiguity=ambiguity,
    )
    token = issue_machine_publication_token(acceptance)
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    dsn = get_settings().database_url.replace("+asyncpg", "")
    connection = await asyncpg.connect(dsn)
    destination.mkdir()
    try:
        async with ncit_sparql_client(isolated_qlever_url) as client:
            with pytest.raises(OSError, match=r"[Dd]irectory"):
                await publish_machine_accepted_artifact(
                    token=token,
                    acceptance=acceptance,
                    closure=closure,
                    artifact=artifact,
                    destination=destination,
                    expected_codes=("C1",),
                    run_id=_RUN_ID,
                    source_identity="a" * 64,
                    client=client,
                    provenance=store,
                )
            marker_ahead = await read_publication_marker(client)
            assert marker_ahead is not None
            destination.rmdir()
            receipt = await publish_machine_accepted_artifact(
                token=token,
                acceptance=acceptance,
                closure=closure,
                artifact=artifact,
                destination=destination,
                expected_codes=("C1",),
                run_id=_RUN_ID,
                source_identity="a" * 64,
                client=client,
                provenance=store,
            )
            assert receipt.marker == marker_ahead
            assert await read_publication_marker(client) == marker_ahead
            rows = await client.select_once(
                build_decomposition_query("C1"), required_variables={"status"}
            )
            model = decomposition_from_rows("C1", rows)
            assert isinstance(model.acceptance, ProjectedEffectiveProjection)
            assert model.acceptance.publication_identity == semantic_identity
        assert (
            await store.acceptance_publication_receipt(acceptance.acceptance_identity)
            == receipt
        )
        assert destination.read_bytes() == artifact.read_bytes()
    finally:
        await connection.execute(
            "DELETE FROM decomp_acceptance_publication WHERE acceptance_identity = $1",
            acceptance.acceptance_identity,
        )
        await connection.close()
        await dispose_engine(engine)


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


async def _assert_publication_dry_run_is_read_only(
    *,
    url: str,
    store: ProvenanceStore,
    destination: Path,
    representation_identity: str,
) -> None:
    before = await store.get_run(_RUN_ID)
    async with ncit_sparql_client(url) as client:
        marker_before = await read_publication_marker(client)
        dry_run = await dry_run_corpus_publication(
            candidate_content_identity="9" * 64,
            run_id=_RUN_ID,
            source_identity="a" * 64,
            planes=PublicationPlaneBinding(
                official_persisted_representation_identity=representation_identity,
                effective_representation_identity=representation_identity,
            ),
            artifact=destination,
            destination_graph_iri=_PUBLIC,
            expected_codes=(),
            expected_worklist_count=0,
            graph=client,
            provenance=store,
        )
        marker_after = await read_publication_marker(client)
    assert dry_run.status == "passed"
    assert dry_run.postgres_read_verified is True
    assert dry_run.qlever_read_verified is True
    assert dry_run.publication_writes_performed is False
    assert marker_after == marker_before
    assert await store.get_run(_RUN_ID) == before


async def _completion_metrics(
    store: ProvenanceStore,
    run_id: str,
) -> dict[str, object]:
    counts = await store.outcome_counts(run_id)
    return {
        **counts.model_dump(),
        "residual_precoordinated_count": 0,
        "residual_precoordination_unknown_count": 0,
        "residual_precoordination": 0.0,
        "complete_definition_count": 0,
        "complete_fact_count": 0,
        "projected_fact_count": 0,
        "projection_loss_count": 0,
        "projection_loss_rate": 0.0,
        "pct_decomposed": (
            counts.decomposed / counts.total_in_scope if counts.total_in_scope else 0.0
        ),
        "roundtrip_fidelity": None,
    }


@pytest.mark.usefixtures("isolated_qlever_settings", "preserved_decomposed_graph")
async def test_qlever_update_is_transactional_and_empty_replacement_is_clean(
    isolated_qlever_url: str,
) -> None:
    await _put_graph(isolated_qlever_url, _PUBLIC, _OLD)
    await _put_graph(isolated_qlever_url, _STAGING, _NEW)

    failed = await _update(
        isolated_qlever_url,
        f"""
        CLEAR GRAPH <{_PUBLIC}>;
        ADD GRAPH <{_STAGING}> TO GRAPH <{_PUBLIC}>;
        THIS IS NOT VALID SPARQL
        """,
    )
    assert failed.is_error
    assert await _ask(
        isolated_qlever_url,
        f'ASK {{ GRAPH <{_PUBLIC}> {{ <urn:old> <urn:value> "old" }} }}',
    )
    assert await _ask(
        isolated_qlever_url,
        f'ASK {{ GRAPH <{_STAGING}> {{ <urn:new> <urn:value> "new" }} }}',
    )

    replaced = await _update(
        isolated_qlever_url,
        f"""
        CLEAR GRAPH <{_PUBLIC}>;
        ADD GRAPH <{_STAGING}> TO GRAPH <{_PUBLIC}>;
        DROP GRAPH <{_STAGING}>;
        INSERT DATA {{ GRAPH <{_PUBLIC}> {{ {_MARKER} }} }}
        """,
    )
    replaced.raise_for_status()
    assert not await _ask(
        isolated_qlever_url,
        f"ASK {{ GRAPH <{_PUBLIC}> {{ <urn:old> ?p ?o }} }}",
    )
    assert await _ask(
        isolated_qlever_url,
        f'ASK {{ GRAPH <{_PUBLIC}> {{ <urn:new> <urn:value> "new" }} }}',
    )
    assert not await _ask(
        isolated_qlever_url,
        f"ASK {{ GRAPH <{_STAGING}> {{ ?s ?p ?o }} }}",
    )

    emptied = await _update(
        isolated_qlever_url,
        f"""
        CLEAR GRAPH <{_PUBLIC}>;
        ADD SILENT GRAPH <urn:missing-empty-staging> TO GRAPH <{_PUBLIC}>;
        INSERT DATA {{ GRAPH <{_PUBLIC}> {{ {_MARKER} }} }}
        """,
    )
    emptied.raise_for_status()
    assert not await _ask(
        isolated_qlever_url,
        f"ASK {{ GRAPH <{_PUBLIC}> {{ <urn:new> ?p ?o }} }}",
    )
    assert await _ask(
        isolated_qlever_url,
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


@pytest.mark.usefixtures(
    "isolated_postgres_settings",
    "isolated_qlever_settings",
    "preserved_decomposed_graph",
)
async def test_production_publication_reconciles_marker_ahead_and_clears_stale_graph(
    isolated_qlever_url: str,
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
        collapse_policy_identity="0" * 64,
        routing_implementation_identity="1" * 64,
        mixed_chain_inventory_identity="2" * 64,
        stage_sequence_identity=RUN_STAGE_SEQUENCE_IDENTITY,
        branch="neoplasm",
        scope_root="C3262",
        scope_version="stated-genus-subclass-v1",
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
        await _put_graph(isolated_qlever_url, _PUBLIC, _OLD)
        await write_ttl([], artifact, run_id=_RUN_ID)

        async with ncit_sparql_client(isolated_qlever_url) as client:
            with pytest.raises(OSError, match="directory"):
                await publish_artifact(
                    run_id=_RUN_ID,
                    source_identity="a" * 64,
                    artifact=artifact,
                    destination=destination,
                    expected_codes=set(),
                    metrics=await _completion_metrics(store, _RUN_ID),
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
            response = await _update(
                isolated_qlever_url,
                f"INSERT DATA {{ GRAPH <{_PUBLIC}> {{ "
                '<urn:drift> <urn:value> "stale" } }',
            )
            response.raise_for_status()

            destination.rmdir()
            await write_ttl([], artifact, run_id=_RUN_ID)
            second_marker = await publish_artifact(
                run_id=_RUN_ID,
                source_identity="a" * 64,
                artifact=artifact,
                destination=destination,
                expected_codes=set(),
                metrics=await _completion_metrics(store, _RUN_ID),
                load_to_store=True,
                client=client,
                provenance=store,
            )

            assert second_marker == first_marker
            assert not await client.ask(
                f"ASK {{ GRAPH <{_PUBLIC}> {{ <urn:old> ?p ?o }} }}"
            )
            assert not await client.ask(
                f"ASK {{ GRAPH <{_PUBLIC}> {{ <urn:drift> ?p ?o }} }}"
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

        assert complete.representation_identity is not None
        await _assert_publication_dry_run_is_read_only(
            url=isolated_qlever_url,
            store=store,
            destination=destination,
            representation_identity=complete.representation_identity,
        )
    finally:
        await conn.execute("DELETE FROM decomp_run WHERE id = $1", _RUN_ID)
        await conn.close()
        await dispose_engine(engine)


@pytest.mark.usefixtures(
    "isolated_postgres_settings",
    "isolated_qlever_settings",
    "preserved_decomposed_graph",
)
async def test_concurrent_publishers_are_serialized_and_readers_see_complete_graphs(
    isolated_qlever_url: str,
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
                collapse_policy_identity="0" * 64,
                routing_implementation_identity="1" * 64,
                mixed_chain_inventory_identity="2" * 64,
                stage_sequence_identity=RUN_STAGE_SEQUENCE_IDENTITY,
                branch="neoplasm",
                scope_root="C3262",
                scope_version="stated-genus-subclass-v1",
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
                semantic_types=("Neoplastic Process",),
            )

        old_graph = f"""
        @prefix op: <{vocab.ONTOPRISM_NS}> .
        <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C0>
          op:representationStatus "legacy-precoordinated" ;
          op:decomposedBy "old-run" .
        """
        await _put_graph(isolated_qlever_url, _PUBLIC, old_graph)
        artifacts, destinations = await _write_concurrent_artifacts(
            tmp_path,
            decompositions,
        )

        observations: list[frozenset[str]] = []
        stop_reading = asyncio.Event()
        async with ncit_sparql_client(isolated_qlever_url) as client:

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
                    metrics=await _completion_metrics(
                        store, _CONCURRENT_RUN_IDS[index]
                    ),
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
