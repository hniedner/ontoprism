"""Guarded read-only raw SPARQL endpoint for the query interface.

Read-only by construction: each query is parsed and only SELECT/ASK forms are accepted.
Returned SELECT bindings are truncated after store execution. This is a power-user
escape hatch, not a general write surface.
"""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from pyparsing import ParseBaseException, ParseResults
from rdflib.plugins.sparql.parser import parseQuery
from rdflib.plugins.sparql.parserutils import CompValue

from backend.config import get_settings
from backend.dependencies import NcitClient
from ontolib.core.exceptions import StorageError
from ontolib.terminologies.oxigraph_http_client import (
    flatten_bindings,
    parse_ask_result,
)

router = APIRouter(prefix="/api/v1/sparql", tags=["sparql"])


class SparqlRequest(BaseModel):
    """A raw SPARQL query submitted to the guarded endpoint."""

    query: str = Field(min_length=1, max_length=20_000)


class SparqlResponse(BaseModel):
    """Raw SPARQL-JSON plus whether the row cap truncated the result."""

    result: dict[str, Any]
    truncated: bool


def _has_service_pattern(node: object) -> bool:
    if isinstance(node, CompValue):
        return node.name == "ServiceGraphPattern" or any(
            _has_service_pattern(value) for value in node.values()
        )
    if isinstance(node, (ParseResults, list, tuple)):
        return any(_has_service_pattern(value) for value in node)
    return False


def _projected_variables(parsed_query: object) -> frozenset[str]:
    if not isinstance(parsed_query, CompValue):
        return frozenset()
    projection = parsed_query.get("projection")
    if not isinstance(projection, list):
        return frozenset()
    variables: set[str] = set()
    for item in projection:
        if not isinstance(item, CompValue):
            continue
        variable = item["var"] if "var" in item else item.get("evar")
        if variable is not None:
            variables.add(str(variable))
    return frozenset(variables)


def _query_form(query: str) -> tuple[Literal["select", "ask"], frozenset[str]]:
    try:
        parsed = parseQuery(query)
        parsed_query = parsed[1]
        query_name = str(getattr(parsed_query, "name", ""))
    except ParseBaseException as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only read-only SPARQL SELECT/ASK queries are permitted.",
        ) from exc
    if _has_service_pattern(parsed):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Federated SPARQL SERVICE clauses are not permitted.",
        )
    if query_name == "SelectQuery":
        return "select", _projected_variables(parsed_query)
    if query_name == "AskQuery":
        return "ask", frozenset()
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        "Only read-only SPARQL SELECT/ASK queries are permitted.",
    )


@router.post("", response_model=SparqlResponse)
async def run_sparql(client: NcitClient, body: SparqlRequest) -> SparqlResponse:
    """Execute read-only SELECT/ASK and truncate oversized SELECT responses."""
    query_form, projected_variables = _query_form(body.query)
    try:
        result = await client.select_raw(body.query)
        if query_form == "select":
            flatten_bindings(result, required_variables=projected_variables)
        else:
            parse_ask_result(result)
    except StorageError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    cap = get_settings().sparql_row_cap
    truncated = False
    bindings = result.get("results", {}).get("bindings")
    if isinstance(bindings, list) and len(bindings) > cap:
        result["results"]["bindings"] = bindings[:cap]
        truncated = True
    return SparqlResponse(result=result, truncated=truncated)
