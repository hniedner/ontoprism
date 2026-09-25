"""Publication body-limit contract, using only a nonce-owned QLever."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import httpx
import pytest
from test_support.publication_qlever import (
    isolated_qlever_url as publication_qlever_fixture,  # noqa: F401 - pytest fixture
)
from test_support.publication_qlever import publication_marker

from ontolib.core.exceptions import StorageError
from ontolib.decomposition import vocab
from ontolib.decomposition.publication import (
    _replace_graph,
    _replacement_committed,
    build_replacement_update,
    read_publication_marker,
    staging_graph_iri,
)
from ontolib.terminologies.sparql_http_client import (
    SparqlEndpointProfile,
    SparqlHttpClient,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.integration, pytest.mark.mutating_integration]


class _CountBeforePromotion(SparqlHttpClient):
    expected_triples: int
    staging: str
    load_seconds: float = 0
    promotion_seconds: float = 0

    async def load(self, data, **kwargs) -> None:
        started = time.monotonic()
        await super().load(data, **kwargs)
        self.load_seconds += time.monotonic() - started

    async def update(
        self, update: str, *, timeout_seconds: float | None = None
    ) -> None:
        rows = await self.select_once(
            f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH <{self.staging}> "
            "{ ?s ?p ?o } }",
            required_variables={"n"},
        )
        assert int(rows[0]["n"]) == self.expected_triples
        started = time.monotonic()
        await super().update(update, timeout_seconds=timeout_seconds)
        self.promotion_seconds = time.monotonic() - started


async def _publication_size_contract(
    tmp_path: Path,
    url: str,
    triples: int,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, bool]:
    artifact = tmp_path / "publication.ttl"
    with artifact.open("xb") as stream:
        for index in range(triples):
            prefix = (
                f'<urn:publication-test:{index:08d}> <urn:publication-test:property> "'
            ).encode()
            stream.write(prefix + b"x" * (200 - len(prefix) - 4) + b'" .\n')
    size = artifact.stat().st_size
    assert size == triples * 200
    marker = publication_marker()
    profile = SparqlEndpointProfile.for_qlever(url, named_graphs=None)
    async with _CountBeforePromotion(profile, query_timeout=1800) as client:
        client.expected_triples = triples
        client.staging = staging_graph_iri(marker.run_id)
        started = time.monotonic()
        try:
            await _replace_graph(client, artifact, marker, predecessor=None)
            assert await read_publication_marker(client) == marker
            counts = await client.select_once(
                "SELECT (COUNT(*) AS ?n) WHERE { "
                f"GRAPH <{vocab.DECOMPOSED_GRAPH_IRI}> "
                "{ ?s ?p ?o } }",
                required_variables={"n"},
            )
            staging_exists = await client.ask(
                f"ASK {{ GRAPH <{client.staging}> {{ ?s ?p ?o }} }}"
            )
            return int(counts[0]["n"]), staging_exists
        finally:
            with capsys.disabled():
                print(
                    f"publication bytes={size} triples={triples} "
                    f"seconds={time.monotonic() - started:.3f} "
                    f"load_seconds={client.load_seconds:.3f} "
                    f"promotion_seconds={client.promotion_seconds:.3f}"
                )


async def test_small_publication_with_production_qlever_flags(
    tmp_path: Path,
    isolated_qlever_url: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert await _publication_size_contract(
        tmp_path, isolated_qlever_url, 100, capsys
    ) == (105, False)


async def test_production_flags_accept_900_second_request_timeout(
    isolated_qlever_url: str,
) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{isolated_qlever_url}/",
            params={"timeout": "900s"},
            content=(
                b"INSERT DATA { GRAPH <urn:publication-test:timeout> "
                b'{ <urn:test:s> <urn:test:p> "probe" } }'
            ),
            headers={"Content-Type": "application/sparql-update"},
        )
        assert response.is_success, (
            f"QLever per-request timeout response: HTTP {response.status_code}\n"
            f"{response.text}"
        )


async def test_timed_out_promotion_reconciles_real_marker_and_counts(
    isolated_qlever_url: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marker = publication_marker()
    staging = staging_graph_iri(marker.run_id)
    async with SparqlHttpClient.for_qlever(
        isolated_qlever_url, query_timeout=60
    ) as client:
        await client.load(
            b'<urn:test:old> <urn:test:p> "old" .',
            content_type="application/n-triples",
            graph_iri=vocab.DECOMPOSED_GRAPH_IRI,
        )
        payload = b"".join(
            f'<urn:test:s{index}> <urn:test:p> "new" .\n'.encode()
            for index in range(100_000)
        )
        await client.load(
            payload, content_type="application/n-triples", graph_iri=staging
        )
        with pytest.raises(StorageError, match="Operation timed out") as failure:
            await client.update(
                build_replacement_update(marker, staging), timeout_seconds=0.001
            )
        observed = await read_publication_marker(client)
        counts = []
        for graph in (vocab.DECOMPOSED_GRAPH_IRI, staging):
            rows = await client.select_once(
                f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH <{graph}> {{ ?s ?p ?o }} }}",
                required_variables={"n"},
            )
            counts.append(int(rows[0]["n"]))
        committed = await _replacement_committed(client, marker, failure.value)
        with capsys.disabled():
            print(
                f"timed-out promotion marker={observed} "
                f"counts={counts} committed={committed}"
            )
        assert committed == (observed == marker)
        assert (committed, counts) in ((False, [1, 100_000]), (True, [100_005, 0]))


@pytest.mark.full_build
async def test_publication_loads_1_2gb_with_default_request_limit(
    tmp_path: Path,
    isolated_qlever_url: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert await _publication_size_contract(
        tmp_path, isolated_qlever_url, 6_000_000, capsys
    ) == (6_000_005, False)
