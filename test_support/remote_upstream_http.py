"""Local PubMed and ClinicalTrials upstream used by browser contract tests."""

import asyncio

from fastapi import FastAPI, HTTPException, Request, Response

app = FastAPI()

_STUDY = {
    "protocolSection": {
        "identificationModule": {
            "nctId": "NCT01234567",
            "briefTitle": "A Study of Widgetinib",
            "officialTitle": "A Phase 2 Study of Widgetinib in Melanoma",
        },
        "statusModule": {"overallStatus": "RECRUITING"},
        "designModule": {
            "phases": ["PHASE2"],
            "enrollmentInfo": {"count": 100},
            "designInfo": {"primaryPurpose": "TREATMENT"},
            "studyType": "INTERVENTIONAL",
        },
        "conditionsModule": {"conditions": ["Melanoma"]},
        "armsInterventionsModule": {
            "interventions": [{"type": "DRUG", "name": "Widgetinib"}]
        },
        "eligibilityModule": {"eligibilityCriteria": "Adults with measurable disease"},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "OntoPrism"}},
    }
}


@app.get("/studies")
async def studies(request: Request) -> dict[str, object]:
    condition = request.query_params.get("query.cond", "")
    if condition == "unavailable-private-query":
        raise HTTPException(503, "private upstream response must stay hidden")
    if condition == "rate-limit-private-query":
        raise HTTPException(429, "private upstream response must stay hidden")
    if condition == "timeout-private-query":
        await asyncio.sleep(0.2)
    return {"studies": [_STUDY], "totalCount": 1}


@app.get("/studies/{nct_id}")
async def study(nct_id: str) -> dict[str, object]:
    if nct_id != "NCT01234567":
        raise HTTPException(404)
    return dict(_STUDY)


@app.get("/esearch.fcgi")
async def esearch(request: Request) -> dict[str, object]:
    query = request.query_params.get("term", "")
    if query == "rate-limit-private-query":
        raise HTTPException(429, "private upstream response must stay hidden")
    if query == "unavailable-private-query":
        raise HTTPException(503, "private upstream response must stay hidden")
    if query == "timeout-private-query":
        await asyncio.sleep(0.2)
    return {"esearchresult": {"count": "1", "idlist": ["12345678"]}}


@app.get("/esummary.fcgi")
async def esummary() -> dict[str, object]:
    return {
        "result": {
            "uids": ["12345678"],
            "12345678": {
                "uid": "12345678",
                "title": "SSR article for immunotherapy",
                "fulljournalname": "Journal of SSR",
                "pubdate": "2026",
                "authors": [{"name": "Example A"}],
            },
        }
    }


@app.get("/efetch.fcgi")
async def efetch() -> Response:
    xml = """<?xml version="1.0"?><PubmedArticleSet><PubmedArticle>
    <MedlineCitation><PMID>12345678</PMID><Article>
    <ArticleTitle>SSR PubMed Article</ArticleTitle>
    <Abstract><AbstractText>SSR abstract from FastAPI.</AbstractText></Abstract>
    </Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"""
    return Response(xml, media_type="application/xml")


@app.get("/elink.fcgi")
async def elink() -> dict[str, object]:
    return {"linksets": [{"linksetdbs": []}]}
