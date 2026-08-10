"""Run the issue #283 NCIt query parity workload against a real candidate store."""

from __future__ import annotations

import argparse
import asyncio
import json
from time import perf_counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from ontolib.terminologies.sparql_http_client import SparqlHttpClient

from ontolib.core.exceptions import StorageError
from ontolib.decomposition.complete_definition import (
    build_complete_definition_query,
    read_complete_definition,
)
from ontolib.decomposition.scope import enumerate_scope_codes
from ontolib.decomposition.stated_queries import (
    resolve_part_of_pairs,
    walk_genus_chain,
)
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.client import ncit_sparql_client
from ontolib.terminologies.ncit.graph_store import NcitGraphStore
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.ncit.role_queries import build_role_relationships_query


async def _timed[T](name: str, operation: Callable[[], Awaitable[T]]) -> T:
    started = perf_counter()
    result = await operation()
    print(
        json.dumps(
            {
                "elapsed_seconds": perf_counter() - started,
                "operation": name,
                "result": result,
            },
            default=str,
            sort_keys=True,
        ),
        flush=True,
    )
    return result


async def _complete_summary(client: SparqlHttpClient) -> dict[str, object]:
    complete = await read_complete_definition(client.select_once, "C27262")
    return {
        "facts": len(complete.facts),
        "groups": len(complete.groups),
        "identity": complete.identity,
        "root_groups": len(complete.root_group_ids),
    }


async def _genus_summary(client: SparqlHttpClient, code: str) -> dict[str, int]:
    levels = await walk_genus_chain(client.select_once, code, max_depth=5)
    return {"levels": len(levels)}


async def _scope_summary(client: SparqlHttpClient) -> dict[str, object]:
    codes = await enumerate_scope_codes(client, "C3262")
    return {"codes": len(codes), "first": codes[:3], "last": codes[-3:]}


async def _detail_summary(store: NcitGraphStore) -> dict[str, object] | None:
    detail = await store.get_concept_detail("C3262")
    if detail is None:
        return None
    return {
        "associations": len(detail.associations),
        "children": len(detail.children),
        "incoming_roles": len(detail.incoming_roles),
        "label": detail.label,
        "parents": len(detail.parents),
        "roles": len(detail.roles),
    }


async def _search_summary(store: NcitGraphStore) -> dict[str, object]:
    page = await store.search("neoplasm", limit=25)
    return {"codes": [hit.code for hit in page.hits], "total": page.total}


async def _neighborhood_summary(store: NcitGraphStore) -> dict[str, int]:
    neighborhood = await store.get_neighborhood("C3262", depth=2)
    return {"edges": len(neighborhood.edges), "nodes": len(neighborhood.nodes)}


async def _concurrent_summary(
    client: SparqlHttpClient,
    store: NcitGraphStore,
) -> dict[str, int]:
    scope, neighborhood = await asyncio.gather(
        enumerate_scope_codes(client, "C3262"),
        store.get_neighborhood("C3262", depth=2),
    )
    return {
        "neighborhood_edges": len(neighborhood.edges),
        "neighborhood_nodes": len(neighborhood.nodes),
        "scope_codes": len(scope),
    }


async def _timeout_summary(client: SparqlHttpClient) -> dict[str, object]:
    try:
        await client.select_once(
            build_complete_definition_query("C27262", nesting_depth=4)
        )
    except StorageError as exc:
        message = str(exc)
        if "HTTP 429" not in message or "timed out" not in message.lower():
            raise
        return {"http_status": 429, "server_cancelled": True}
    raise RuntimeError("pathological query escaped the selected server timeout")


async def run(
    endpoint: str,
    *,
    concurrent: bool,
    timeout_proof: bool,
) -> None:
    async with ncit_sparql_client(endpoint, query_timeout=40.0) as client:
        store = NcitGraphStore(client)
        if timeout_proof:
            await _timed("server-timeout", lambda: _timeout_summary(client))
            return
        if concurrent:
            await _timed(
                "concurrent-scope-and-neighborhood",
                lambda: _concurrent_summary(client, store),
            )
            return
        await _timed(
            "version-default",
            lambda: client.select_once(
                "PREFIX owl: <http://www.w3.org/2002/07/owl#> "
                "SELECT ?version WHERE { ?ont a owl:Ontology ; "
                "owl:versionInfo ?version } LIMIT 2",
                required_variables={"version"},
            ),
        )
        await _timed(
            "version-stated",
            lambda: client.select_once(
                "PREFIX owl: <http://www.w3.org/2002/07/owl#> "
                f"SELECT ?version WHERE {{ GRAPH <{STATED_GRAPH_IRI}> {{ "
                "?ont a owl:Ontology ; owl:versionInfo ?version } } LIMIT 2",
                required_variables={"version"},
            ),
        )
        await _timed(
            "restriction-count-stated",
            lambda: client.select_once(
                "PREFIX owl: <http://www.w3.org/2002/07/owl#> "
                "SELECT (COUNT(DISTINCT ?restriction) AS ?count) WHERE { "
                f"GRAPH <{STATED_GRAPH_IRI}> {{ ?restriction a owl:Restriction ; "
                "owl:onProperty ?property ; owl:someValuesFrom ?filler } }",
                required_variables={"count"},
            ),
        )
        await _timed(
            "roles-C3262",
            lambda: client.select_once(
                build_role_relationships_query("C3262", NCIT_NS),
                required_variables={"rel", "target"},
            ),
        )
        await _timed("complete-definition-C27262", lambda: _complete_summary(client))
        for code in ("C6135", "C27787"):
            await _timed(
                f"genus-walk-{code}",
                lambda code=code: _genus_summary(client, code),
            )
        await _timed(
            "R82-closure-controls",
            lambda: resolve_part_of_pairs(
                client,
                ("C12917", "C36220", "C37060", "C41063", "C41397"),
            ),
        )
        await _timed("scope-C3262", lambda: _scope_summary(client))
        await _timed("detail-C3262", lambda: _detail_summary(store))
        await _timed("search-neoplasm", lambda: _search_summary(store))
        await _timed("neighborhood-C3262-depth-2", lambda: _neighborhood_summary(store))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("endpoint")
    parser.add_argument("--concurrent", action="store_true")
    parser.add_argument("--timeout-proof", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(
        run(
            args.endpoint,
            concurrent=args.concurrent,
            timeout_proof=args.timeout_proof,
        )
    )
