"""Guarded read-only raw SPARQL endpoint for the query interface.

Read-only by construction: any query containing a SPARQL update/management keyword
is rejected, and the returned rows are capped. This is a power-user escape hatch, not
a general write surface.
"""

import re
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from backend.config import get_settings
from backend.dependencies import NcitClient
from ontolib.core.exceptions import StorageError

router = APIRouter(prefix="/api/v1/sparql", tags=["sparql"])

# Update / management forms that must never run through this read endpoint.
_FORBIDDEN = re.compile(
    r"\b(INSERT|DELETE|LOAD|CLEAR|DROP|CREATE|ADD|MOVE|COPY|WITH)\b",
    re.IGNORECASE,
)


class SparqlRequest(BaseModel):
    """A raw SPARQL query submitted to the guarded endpoint."""

    query: str = Field(min_length=1, max_length=20_000)


class SparqlResponse(BaseModel):
    """Raw SPARQL-JSON plus whether the row cap truncated the result."""

    result: dict[str, Any]
    truncated: bool


@router.post("", response_model=SparqlResponse)
async def run_sparql(client: NcitClient, body: SparqlRequest) -> SparqlResponse:
    """Execute a read-only SPARQL query against the NCIt store, row-capped."""
    if _FORBIDDEN.search(body.query):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only read-only SPARQL (SELECT/ASK/CONSTRUCT/DESCRIBE) is permitted.",
        )
    try:
        result = await client.select_raw(body.query)
    except StorageError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    cap = get_settings().sparql_row_cap
    truncated = False
    bindings = result.get("results", {}).get("bindings")
    if isinstance(bindings, list) and len(bindings) > cap:
        result["results"]["bindings"] = bindings[:cap]
        truncated = True
    return SparqlResponse(result=result, truncated=truncated)
