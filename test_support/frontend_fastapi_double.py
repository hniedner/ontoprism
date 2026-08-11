"""Real FastAPI process used by the built SvelteKit SSR/BFF browser contracts."""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import TYPE_CHECKING, Annotated

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from starlette.responses import Response

app = FastAPI()
_requests: Counter[str] = Counter()


@app.middleware("http")
async def count_requests(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    query = f"?{request.url.query}" if request.url.query else ""
    _requests[f"{request.method} {request.url.path}{query}"] += 1
    return await call_next(request)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/__test__/counts")
async def request_counts() -> dict[str, int]:
    return dict(_requests)


@app.get("/api/v1/__test__/status/{status_code}")
async def status_response(status_code: int) -> JSONResponse:
    return JSONResponse(
        {"detail": f"upstream {status_code}"},
        status_code=status_code,
        headers={"x-upstream-contract": "preserved"},
    )


@app.get("/api/v1/__test__/slow")
async def slow_response() -> dict[str, bool]:
    await asyncio.sleep(0.75)
    return {"completed": True}


@app.get("/api/v1/__test__/stalled-body")
async def stalled_body() -> StreamingResponse:
    async def chunks() -> AsyncIterator[bytes]:
        yield b"{"
        await asyncio.sleep(0.75)
        yield b'"completed":true}'

    return StreamingResponse(chunks(), media_type="application/json")


@app.get("/api/v1/__test__/redirect")
async def external_redirect() -> RedirectResponse:
    return RedirectResponse("https://example.invalid/escaped", status_code=307)


@app.get("/api/v1/__test__/forwarding-headers")
async def forwarding_headers(request: Request) -> dict[str, str | None]:
    return {
        "forwarded": request.headers.get("forwarded"),
        "x_forwarded_for": request.headers.get("x-forwarded-for"),
        "x_real_ip": request.headers.get("x-real-ip"),
    }


@app.post("/api/v1/refresh")
async def refresh_repositories() -> dict[str, object]:
    await asyncio.sleep(0.25)
    return {
        "refreshed_at": "2026-08-11T02:00:00Z",
        "repositories": [
            {
                "state": "unhealthy",
                "repository": "ncit",
                "reason": "repository-unreachable",
                "message": "fixture metadata",
            }
        ],
    }


@app.get("/api/v1/ncit/list")
async def list_ncit(
    limit: Annotated[int, Query(ge=1)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    return {
        "query": "",
        "total": 51,
        "limit": limit,
        "offset": offset,
        "hits": [
            {
                "code": "C3262",
                "label": "SSR Neoplasm",
                "semantic_type": "Neoplastic Process",
                "matched_synonym": None,
            }
        ],
    }


@app.get("/api/v1/ncit/search")
async def search_ncit(
    q: str,
    limit: Annotated[int, Query(ge=1)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    code = "CSLOW" if q == "slow" else "C4005"
    return {
        "query": q,
        "total": 51,
        "limit": limit,
        "offset": offset,
        "hits": [
            {
                "code": code,
                "label": f"SSR result for {q}",
                "semantic_type": "Neoplastic Process",
                "matched_synonym": None,
            }
        ],
    }


@app.get("/api/v1/ncit/concepts/{code}/neighborhood")
async def get_ncit_neighborhood(code: str) -> dict[str, object]:
    return {
        "center": code,
        "nodes": [
            {
                "code": code,
                "label": "SSR Detail Concept",
                "semantic_type": "Neoplastic Process",
            }
        ],
        "edges": [],
        "truncated": False,
    }


@app.get("/api/v1/ncit/concepts/{code}")
async def get_ncit_concept(code: str) -> JSONResponse:
    if code == "CSLOW":
        await asyncio.sleep(0.25)
    if code == "CTIMEOUT":
        await asyncio.sleep(0.75)
    if code in {"C404", "C503"}:
        status_code = int(code[1:])
        return JSONResponse(
            {"detail": f"concept upstream {status_code}"}, status_code=status_code
        )
    return JSONResponse(
        {
            "code": code,
            "label": "SSR Detail Concept",
            "preferred_name": "SSR Detail Concept",
            "definition": "SSR concept definition from FastAPI.",
            "semantic_types": ["Neoplastic Process"],
            "synonyms": ["Server rendered concept"],
            "parents": [],
            "children": [],
            "roles": [],
            "associations": [],
            "incoming_roles": [],
        }
    )


@app.get("/api/v1/cadsr/list")
async def list_cadsr(
    limit: Annotated[int, Query(ge=1)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    return {
        "query": "",
        "total": 1,
        "limit": limit,
        "offset": offset,
        "hits": [
            {
                "public_id": "2001",
                "version": "1.0",
                "short_name": "TUMOR_STAGE",
                "long_name": "Tumor Stage Code",
                "context": "NCIP",
                "datatype": "CHARACTER",
            }
        ],
    }


@app.get("/api/v1/cadsr/search")
async def search_cadsr(
    q: str,
    limit: Annotated[int, Query(ge=1)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    result = await list_cadsr(limit, offset)
    result["query"] = q
    return result


@app.get("/api/v1/cadsr/cdes/{public_id}")
async def get_cde(public_id: str) -> dict[str, object]:
    return {
        "public_id": public_id,
        "version": "1.0",
        "short_name": "TUMOR_STAGE",
        "long_name": "Tumor Stage Code",
        "context": "NCIP",
        "datatype": "CHARACTER",
        "definition": "The stage of a tumor.",
        "workflow_status": "RELEASED",
        "registration_status": "Standard",
        "value_domain_type": "Enumerated",
        "permissible_values": [
            {"value": "I", "meaning": "Stage I", "meaning_code": "C1"}
        ],
        "concepts": [
            {
                "concept_code": "C48885",
                "concept_name": "Tumor Stage",
                "concept_type": "objectClass",
                "is_primary": True,
            }
        ],
    }


@app.post("/api/v1/clinicaltrials/search")
async def search_trials(payload: dict[str, object]) -> dict[str, object]:
    condition = str(payload.get("condition") or "")
    return {
        "condition": condition,
        "intervention": None,
        "term": None,
        "total": 1,
        "studies": [
            {
                "nct_id": "NCT01234567",
                "title": "A Study of Widgetinib",
                "status": "RECRUITING",
                "phase": "PHASE2",
                "conditions": [condition],
                "interventions": ["Widgetinib"],
                "start_date": "2024-01-01",
                "enrollment": 100,
                "relevance_score": 1.0,
            }
        ],
    }


@app.get("/api/v1/clinicaltrials/{nct_id}")
async def get_trial(nct_id: str) -> dict[str, object]:
    return {
        "nct_id": nct_id,
        "title": "A Study of Widgetinib",
        "official_title": "A Phase 2 Study of Widgetinib in Melanoma",
        "status": "RECRUITING",
        "phase": "PHASE2",
        "study_type": "INTERVENTIONAL",
        "primary_purpose": "TREATMENT",
        "conditions": ["Melanoma"],
        "interventions": [{"type": "DRUG", "name": "Widgetinib", "description": None}],
        "primary_outcomes": [],
        "secondary_outcomes": [],
        "eligibility_criteria": "Adults with measurable disease",
        "enrollment": 100,
        "start_date": "2024-01-01",
        "sponsors": [{"name": "OntoPrism", "role": "lead"}],
        "locations": [],
        "references": [],
        "url": f"https://clinicaltrials.gov/study/{nct_id}",
    }


@app.post("/api/v1/pubmed/search")
async def search_pubmed(payload: dict[str, object]) -> dict[str, object]:
    query = str(payload.get("query") or "")
    return {
        "query": query,
        "total": 1,
        "articles": [
            {
                "pmid": "12345678",
                "title": f"SSR article for {query}",
                "journal": "Journal of SSR",
                "pub_date": "2026",
                "authors": ["Example A"],
                "doi": None,
            }
        ],
    }


@app.get("/api/v1/pubmed/{pmid}/related")
async def related_pubmed(pmid: str) -> dict[str, object]:
    return {"pmid": pmid, "link_type": "similar", "related_pmids": []}


@app.get("/api/v1/pubmed/{pmid}")
async def get_pubmed(pmid: str) -> dict[str, object]:
    return {
        "pmid": pmid,
        "title": "SSR PubMed Article",
        "abstract": "SSR abstract from FastAPI.",
        "authors": [{"last_name": "Example", "fore_name": "Ada", "initials": "AE"}],
        "journal": "Journal of SSR",
        "pub_date": "2026",
        "doi": None,
        "pmc_id": None,
        "mesh_terms": [],
        "keywords": ["SSR"],
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    }
