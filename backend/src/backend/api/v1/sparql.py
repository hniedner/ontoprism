"""Guarded read-only raw SPARQL endpoint for the query interface.

Read-only by construction: each query is parsed and only SELECT/ASK forms are accepted;
SELECT rows are capped. This is a power-user escape hatch, not a general write surface.
"""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from pyparsing import ParseBaseException
from rdflib.plugins.sparql.parser import parseQuery

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


def _query_form(query: str) -> Literal["select", "ask"]:
    try:
        parsed_query = parseQuery(query)[1]
        query_name = str(getattr(parsed_query, "name", ""))
    except ParseBaseException as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only read-only SPARQL SELECT/ASK queries are permitted.",
        ) from exc
    if query_name == "SelectQuery":
        return "select"
    if query_name == "AskQuery":
        return "ask"
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        "Only read-only SPARQL SELECT/ASK queries are permitted.",
    )


@router.post("", response_model=SparqlResponse)
async def run_sparql(client: NcitClient, body: SparqlRequest) -> SparqlResponse:
    """Execute a read-only SELECT or ASK against the NCIt store, row-capped."""
    query_form = _query_form(body.query)
    try:
        result = await client.select_raw(body.query)
        if query_form == "select":
            flatten_bindings(result)
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
