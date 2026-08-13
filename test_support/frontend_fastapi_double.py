"""Real FastAPI process used by the built SvelteKit SSR/BFF browser contracts."""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import TYPE_CHECKING, Annotated

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from backend.api.v1 import clinicaltrials, pubmed
from ontolib.repositories.clinicaltrials.client import ClinicalTrialsClient
from ontolib.repositories.pubmed.client import PubMedClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from starlette.responses import Response

app = FastAPI()
app.state.clinicaltrials_client = ClinicalTrialsClient(
    "http://127.0.0.1:18012", connect_timeout=0.01, read_timeout=0.01
)
app.state.pubmed_client = PubMedClient(
    "http://127.0.0.1:18012",
    requests_per_second=0,
    connect_timeout=0.01,
    read_timeout=0.01,
)
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
        "activation_identity": "d" * 64,
        "serving_identity": "e" * 64,
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
                        "rows": 223834,
                        "sha256": (
                            "ed3efa224d1e7445d2dc17fb053cea61feee698232dc8404e"
                            "1c615525b0dffb0"
                        ),
                        "uberon_classes": 16362,
                        "cl_classes": 1484,
                        "uberon_searchable_classes": 16071,
                        "cl_searchable_classes": 1484,
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


def _require_icdo(value: str | None) -> None:
    if value != "licensed":
        raise HTTPException(403, "ICD-O entitlement required.")


@app.get("/api/v1/icdo/{edition}/{axis}/list")
async def list_icdo(
    edition: str,
    axis: str,
    limit: int = 25,
    offset: int = 0,
    x_icdo_entitlement: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    _require_icdo(x_icdo_entitlement)
    return {
        "edition": edition,
        "axis": axis,
        "query": "",
        "total": 1,
        "limit": limit,
        "offset": offset,
        "hits": [
            {
                "code": "8503/0",
                "level": "morphology",
                "preferred": "Protected intraductal papilloma",
                "behaviour": "0",
                "base_morphology": "8503",
                "synonyms": [],
                "related": [],
                "notes": [],
                "code_references": [],
                "see_also": [],
                "see_notes": [],
                "includes": [],
                "excludes": [],
                "other_text": [],
            }
        ],
    }


@app.get("/api/v1/icdo/{edition}/{axis}/search")
async def search_icdo(
    edition: str,
    axis: str,
    q: str,
    limit: int = 25,
    offset: int = 0,
    x_icdo_entitlement: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    result = await list_icdo(edition, axis, limit, offset, x_icdo_entitlement)
    result["query"] = q
    return result


@app.get("/api/v1/icdo/{edition}/{axis}/concepts/{code}")
async def icdo_detail(
    code: str,
    x_icdo_entitlement: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    _require_icdo(x_icdo_entitlement)
    if code != "ODUwMy8w":
        raise HTTPException(404, "ICD-O code not found.")
    return {
        "activation_identity": "d" * 64,
        "serving_identity": "e" * 64,
        "record": {
            "code": "8503/0",
            "level": "morphology",
            "preferred": "Protected intraductal papilloma",
            "base_morphology": "8503",
            "behaviour": "0",
            "synonyms": ["Protected papilloma synonym"],
            "related": [],
            "notes": ["Publisher note"],
            "code_references": ["Code reference"],
            "see_also": ["See also term"],
            "see_notes": ["See note"],
            "includes": ["Included term"],
            "excludes": ["Excluded term"],
            "other_text": ["Other publisher text"],
        },
        "ncit_alignments": [
            {
                "code": code,
                "system": "ncit",
                "version": "26.07d",
                "predicate": "http://www.w3.org/2004/02/skos/core#closeMatch",
                "lifecycle": "proposed",
            }
            for code in (
                "C45194",
                "C71720",
                "C80281",
                "C80289",
                "C80291",
                "C8851",
                "C9496",
            )
        ],
    }


@app.get("/api/v1/icdo/4.0/topography/congruence")
async def icdo_congruence(
    x_icdo_entitlement: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    _require_icdo(x_icdo_entitlement)
    return {
        "report_identity": "a" * 64,
        "icdo_serving_identity": "b" * 64,
        "uberon_serving_identity": "c" * 64,
        "total": 406,
        "counts": {"one-supported-candidate": 1, "no-candidate": 405},
        "rows": [
            {
                "code": "C34.9",
                "classification": "one-supported-candidate",
                "reason": "one lexical candidate retained for inspection",
                "candidates": ["UBERON:0002048"],
                "evidence": [],
            }
        ],
    }


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


@app.get("/api/v1/uberon/concepts/{code}/alignments")
async def get_uberon_alignments(code: str) -> dict[str, object]:
    return {
        "code": code,
        "repository_source_identity": "a" * 64,
        "repository_serving_identity": "b" * 64,
        "alignments": [],
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


@app.get("/api/v1/ncit/concepts/{code}/mappings")
async def get_ncit_mappings(
    code: str,
    x_icdo_entitlement: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    mappings = (
        [
            {
                "object_id": value,
                "system": "icdo",
                "version": "3.2",
                "predicate": "http://www.w3.org/2004/02/skos/core#closeMatch",
                "lifecycle": "proposed",
                "confidence": 0.9,
                "is_identity": False,
            }
            for value in ("8240/3", "8241/3", "8248/1")
        ]
        if code == "C188218" and x_icdo_entitlement == "licensed"
        else []
    )
    return {"code": code, "mappings": mappings}


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


app.include_router(clinicaltrials.router)
app.include_router(pubmed.router)
