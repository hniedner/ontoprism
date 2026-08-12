"""Exercise mutable graph contracts against a real issue #283 candidate store."""

# Assertions deliberately make this executable research contract fail closed.
# ruff: noqa: S101

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from time import perf_counter

from ontolib.decomposition.publication import (
    PublicationMarker,
    build_replacement_update,
    staging_graph_iri,
)
from ontolib.decomposition.vocab import (
    DECOMPOSED_GRAPH_IRI,
    PUBLICATION_MARKER,
    PUBLICATION_RUN,
    REPRESENTATION_STATUS,
)
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.sparql_http_client import SparqlHttpClient

CAS_GRAPH = "urn:ontoprism:bakeoff:283:cas"
PROPOSAL = "urn:ontoprism:proposal:283"
REVISION = "urn:ontoprism:proposal:revision"
LABEL = "urn:ontoprism:proposal:label"
RUN_ID = "issue-283-bakeoff"


async def measured(name: str, operation: object) -> object:
    started = perf_counter()
    try:
        result = await operation  # type: ignore[misc]
    except Exception as exc:
        print(
            json.dumps(
                {
                    "elapsed_seconds": perf_counter() - started,
                    "error": f"{type(exc).__name__}: {exc}",
                    "operation": name,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        raise
    print(
        json.dumps(
            {
                "elapsed_seconds": perf_counter() - started,
                "operation": name,
                "result": result,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return result


async def run(endpoint: str, *, verify_only: bool) -> None:
    async with SparqlHttpClient.for_qlever(
        endpoint,
        named_graphs=(STATED_GRAPH_IRI, DECOMPOSED_GRAPH_IRI, CAS_GRAPH),
        query_timeout=40.0,
    ) as client:
        if not verify_only:
            staging = staging_graph_iri(RUN_ID)
            payload = (
                "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C999999> "
                f"<{REPRESENTATION_STATUS}> "
                '"legacy-precoordinated" .\n'
            ).encode()
            await measured(
                "graph-store-staging-replace",
                client.load(
                    payload,
                    content_type="text/turtle",
                    graph_iri=staging,
                    replace=True,
                ),
            )
            marker = PublicationMarker(
                run_id=RUN_ID,
                source_identity="a" * 64,
                representation_identity="b" * 64,
                built_at=datetime(2026, 8, 9, tzinfo=UTC),
            )
            await measured(
                "atomic-decomposition-replacement",
                client.update(build_replacement_update(marker, staging)),
            )
            initial = (
                f"CLEAR SILENT GRAPH <{CAS_GRAPH}>; INSERT DATA {{ "
                f"GRAPH <{CAS_GRAPH}> {{ "
                f'<{PROPOSAL}> <{REVISION}> 1 ; <{LABEL}> "initial" . }} }}'
            )
            await measured("initialize-proposal-revision", client.update(initial))
            revisions = [
                (
                    f"DELETE {{ GRAPH <{CAS_GRAPH}> {{ <{PROPOSAL}> <{REVISION}> 1 ; "
                    f"<{LABEL}> ?old . }} }} INSERT {{ GRAPH <{CAS_GRAPH}> {{ "
                    f'<{PROPOSAL}> <{REVISION}> 2 ; <{LABEL}> "writer-{writer}" . '
                    f"}} }} WHERE {{ GRAPH <{CAS_GRAPH}> {{ <{PROPOSAL}> <{REVISION}> "
                    f"1 ; <{LABEL}> ?old . }} }}"
                )
                for writer in ("a", "b")
            ]
            await measured(
                "concurrent-optimistic-revisions",
                asyncio.gather(*(client.update(update) for update in revisions)),
            )

        publication = await measured(
            "read-decomposition-publication",
            client.select_once(
                "SELECT (COUNT(*) AS ?count) ?run WHERE { "
                f"GRAPH <{DECOMPOSED_GRAPH_IRI}> {{ ?s ?p ?o . OPTIONAL {{ "
                f"<{PUBLICATION_MARKER}> "
                f"<{PUBLICATION_RUN}> ?run }} }} }} "
                "GROUP BY ?run",
                required_variables={"count"},
            ),
        )
        revision = await measured(
            "read-proposal-revision",
            client.select_once(
                f"SELECT ?revision ?label WHERE {{ GRAPH <{CAS_GRAPH}> {{ "
                f"<{PROPOSAL}> <{REVISION}> ?revision ; <{LABEL}> ?label . }} }}",
                required_variables={"revision", "label"},
            ),
        )
        assert publication == [{"count": "6", "run": RUN_ID}]
        assert revision in (
            [{"revision": "2", "label": "writer-a"}],
            [{"revision": "2", "label": "writer-b"}],
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("endpoint")
    parser.add_argument("--verify-only", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(run(args.endpoint, verify_only=args.verify_only))
