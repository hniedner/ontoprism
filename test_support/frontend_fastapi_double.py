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
_NEIGHBORHOOD_NODE_CAP = 400


def _synthetic_neighborhood(
    center: str, *, node_count: int, edge_count: int
) -> dict[str, object]:
    node_codes = [center, *(f"{center}_N{index}" for index in range(1, node_count))]
    nodes = [
        {
            "code": code,
            "label": f"Performance node {index}",
            "semantic_type": "Neoplastic Process",
            "representation_status": None,
        }
        for index, code in enumerate(node_codes)
    ]
    edges: list[dict[str, str | None]] = []
    for index in range(1, node_count):
        edges.append(
            {
                "source": node_codes[index],
                "target": node_codes[(index - 1) // 2],
                "relation": f"RPERF{index}",
                "relation_label": "is a",
                "kind": "subClassOf",
            }
        )
    for index in range(edge_count - len(edges)):
        source_index = index % node_count
        target_index = (index * 17 + 7) % node_count
        if target_index == source_index:
            target_index = (target_index + 1) % node_count
        edges.append(
            {
                "source": node_codes[source_index],
                "target": node_codes[target_index],
                "relation": f"APERF{index}",
                "relation_label": "related to",
                "kind": "association",
            }
        )
    return {
        "center": center,
        "nodes": nodes,
        "edges": edges,
        "truncated": node_count == _NEIGHBORHOOD_NODE_CAP,
    }


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
            },
            {
                "state": "ready",
                "repository": "uberon",
                "source_identity": "a" * 64,
                "manifest_identity": "b" * 64,
                "source_sha256": "c" * 64,
                "version_iri": "http://example.test/uberon/2026-06-19",
                "class_counts": {"uberon": 16071, "cl": 1484},
                "observation": {
                    "version_iri": "http://example.test/uberon/2026-06-19",
                    "triples": 900000,
                    "has_uberon_lung": True,
                    "has_cell_class": True,
                    "has_ncit_xref": True,
                    "serving": {
                        "rows": 223546,
                        "sha256": (
                            "a95beed61f43591bac4b2eee0c23a2e24e2300d6bc4df0dc"
                            "4b9e1cbd39c8a4c7"
                        ),
                        "uberon_classes": 16071,
                        "cl_classes": 1484,
                    },
                },
            },
        ],
    }


@app.get("/api/v1/ncit/list")
async def list_ncit(
    limit: Annotated[int, Query(ge=1)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    representation_status: str | None = None,
) -> dict[str, object]:
    return {
        "query": "",
        "total": 1 if representation_status == "legacy-precoordinated" else 51,
        "limit": limit,
        "offset": offset,
        "hits": [
            {
                "code": "C3262",
                "label": "SSR Neoplasm",
                "semantic_type": "Neoplastic Process",
                "matched_synonym": None,
                "representation_status": "legacy-precoordinated",
            }
        ],
    }


@app.get("/api/v1/ncit/search")
async def search_ncit(
    q: str,
    limit: Annotated[int, Query(ge=1)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    representation_status: str | None = None,
) -> dict[str, object]:
    code = "CSLOW" if q == "slow" else "C3262" if q == "neoplasm" else "C4005"
    return {
        "query": q,
        "total": 1 if representation_status == "legacy-precoordinated" else 51,
        "limit": limit,
        "offset": offset,
        "hits": [
            {
                "code": code,
                "label": f"SSR result for {q}",
                "semantic_type": "Neoplastic Process",
                "matched_synonym": None,
                "representation_status": (
                    "legacy-precoordinated" if code == "C3262" else None
                ),
            }
        ],
    }


@app.get("/api/v1/uberon/list")
async def list_uberon(
    limit: Annotated[int, Query(ge=1)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    source: str | None = None,
) -> dict[str, object]:
    return {
        "query": "",
        "total": 1,
        "limit": limit,
        "offset": offset,
        "hits": [
            {
                "code": "UBERON:0002048",
                "source": source or "uberon",
                "label": "SSR lung",
                "matched_synonym": None,
            }
        ],
    }


@app.get("/api/v1/uberon/search")
async def search_uberon(
    q: str,
    limit: Annotated[int, Query(ge=1)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    source: str | None = None,
) -> dict[str, object]:
    result = await list_uberon(limit, offset, source)
    result["query"] = q
    return result


@app.get("/api/v1/uberon/concepts/{code}/neighborhood")
async def get_uberon_neighborhood(code: str) -> dict[str, object]:
    return {
        "center": code,
        "nodes": [{"code": code, "source": "uberon", "label": "SSR lung"}],
        "edges": [],
        "truncated": False,
    }


@app.get("/api/v1/uberon/concepts/{code}")
async def get_uberon_concept(code: str) -> dict[str, object]:
    return {
        "code": code,
        "source": "uberon",
        "label": "SSR lung",
        "definition": "SSR Uberon concept definition from FastAPI.",
        "synonyms": ["pulmo"],
        "xrefs": ["NCIT:C12468"],
        "parents": [],
        "children": [],
        "relations": [],
        "truncated": False,
    }


@app.get("/api/v1/ncit/concepts/{code}/neighborhood")
async def get_ncit_neighborhood(code: str) -> dict[str, object]:
    if code == "CPERF186":
        return _synthetic_neighborhood(code, node_count=186, edge_count=191)
    if code == "CPERF400":
        return _synthetic_neighborhood(code, node_count=400, edge_count=800)
    if code != "C3262":
        return {
            "center": code,
            "nodes": [
                {
                    "code": code,
                    "label": "SSR Detail Concept",
                    "semantic_type": "Neoplastic Process",
                    "representation_status": None,
                }
            ],
            "edges": [],
            "truncated": False,
        }
    return {
        "center": code,
        "nodes": [
            {
                "code": code,
                "label": "SSR Detail Concept",
                "semantic_type": "Neoplastic Process",
                "representation_status": "legacy-precoordinated",
            },
            {
                "code": "C4005",
                "label": "Unassessed neighbor",
                "semantic_type": "Disease",
                "representation_status": None,
            },
            {
                "code": "C100",
                "label": "Flagged disease neighbor",
                "semantic_type": "Disease",
                "representation_status": "legacy-precoordinated",
            },
        ],
        "edges": [
            {
                "source": code,
                "target": "C4005",
                "relation": "subClassOf",
                "relation_label": "is a",
                "kind": "subClassOf",
            },
            {
                "source": "C4005",
                "target": "C100",
                "relation": "R1",
                "relation_label": "related to",
                "kind": "association",
            },
        ],
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
            "representation_status": (
                "legacy-precoordinated" if code == "C3262" else None
            ),
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
    concept_count = 14 if public_id == "6686721" else 1
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
                "concept_code": f"C{48885 + index}",
                "concept_name": (
                    "Tumor Stage" if index == 0 else f"Mapped concept {index + 1}"
                ),
                "concept_type": "objectClass",
                "is_primary": index == 0,
            }
            for index in range(concept_count)
        ],
    }


@app.get("/api/v1/cadsr/cdes/{public_id}/neighborhood")
async def get_cde_neighborhood(public_id: str) -> dict[str, object]:
    if public_id == "6686721":
        await asyncio.sleep(0.4)
    return _synthetic_neighborhood("C48885", node_count=14, edge_count=13)


@app.get("/api/v1/cadsr/cdes/{public_id}/similar")
async def get_similar_cdes(public_id: str) -> list[dict[str, object]]:
    return [
        {
            "public_id": f"{public_id}{index}",
            "version": "1.0",
            "short_name": f"SIMILAR_{index}",
            "long_name": (
                "Abdomen and Thoracic Visceral Organs Soft Tissue Sarcoma "
                f"AJCC Edition 8 Similar Common Data Element {index}"
            ),
            "context": "NCIP",
            "datatype": "CHARACTER",
            "score": 1.0 - index / 100,
        }
        for index in range(10)
    ]


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
