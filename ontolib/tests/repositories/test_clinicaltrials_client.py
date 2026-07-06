"""Behavioral tests for the ClinicalTrials.gov v2 client (mocked local HTTP server)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import parse_qs, urlparse

import pytest

from ontolib.core.exceptions import StorageError
from ontolib.repositories.clinicaltrials.client import ClinicalTrialsClient

if TYPE_CHECKING:
    from collections.abc import Iterator

_STUDY_ONE = {
    "protocolSection": {
        "identificationModule": {
            "nctId": "NCT01234567",
            "briefTitle": "A Study of Widgetinib in Solid Tumors",
            "officialTitle": "A Phase 2 Study of Widgetinib",
        },
        "statusModule": {
            "overallStatus": "RECRUITING",
            "startDateStruct": {"date": "2024-01-15"},
        },
        "designModule": {
            "studyType": "INTERVENTIONAL",
            "phases": ["PHASE2"],
            "enrollmentInfo": {"count": 120},
            "designInfo": {"primaryPurpose": "TREATMENT"},
        },
        "conditionsModule": {"conditions": ["Solid Tumor", "Melanoma"]},
        "armsInterventionsModule": {
            "interventions": [
                {"type": "DRUG", "name": "Widgetinib", "description": "Oral, daily"}
            ]
        },
        "outcomesModule": {
            "primaryOutcomes": [
                {"measure": "Overall Response Rate", "timeFrame": "6 months"}
            ],
            "secondaryOutcomes": [{"measure": "Overall Survival"}],
        },
        "eligibilityModule": {"eligibilityCriteria": "Adults with measurable disease"},
        "sponsorCollaboratorsModule": {
            "leadSponsor": {"name": "Acme Oncology"},
            "collaborators": [{"name": "NCI"}],
        },
        "contactsLocationsModule": {
            "locations": [
                {"facility": "Cancer Center", "city": "Boston", "country": "US"}
            ]
        },
        "referencesModule": {
            "references": [
                {"pmid": "12345678", "citation": "Smith et al.", "type": "BACKGROUND"}
            ]
        },
    }
}
_STUDY_TWO = {
    "protocolSection": {
        "identificationModule": {"nctId": "NCT07654321", "briefTitle": "Trial Two"},
        "statusModule": {"overallStatus": "COMPLETED"},
        "designModule": {"phases": ["PHASE3"]},
        "conditionsModule": {"conditions": ["Melanoma"]},
    }
}


class _Handler(BaseHTTPRequestHandler):
    last_query: ClassVar[dict[str, list[str]]] = {}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        _Handler.last_query = parse_qs(parsed.query)
        if parsed.path == "/studies":
            self._json({"studies": [_STUDY_ONE, _STUDY_TWO], "totalCount": 42})
        elif parsed.path == "/studies/NCT01234567":
            self._json(_STUDY_ONE)
        elif parsed.path == "/studies/NCT00000000":
            self.send_response(404)
            self.end_headers()
        else:
            self.send_response(400)
            self.end_headers()

    def _json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a: Any) -> None:
        pass


@pytest.fixture
def ct_base_url() -> Iterator[str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.unit
async def test_search_maps_query_params_and_parses_summaries(ct_base_url: str) -> None:
    async with ClinicalTrialsClient(ct_base_url) as client:
        page = await client.search_studies(
            condition="melanoma", intervention="widgetinib", term="oral", page_size=20
        )
    assert _Handler.last_query["query.cond"] == ["melanoma"]
    assert _Handler.last_query["query.intr"] == ["widgetinib"]
    assert _Handler.last_query["query.term"] == ["oral"]
    assert _Handler.last_query["countTotal"] == ["true"]
    assert _Handler.last_query["pageSize"] == ["20"]
    assert page.total == 42
    assert [s.nct_id for s in page.studies] == ["NCT01234567", "NCT07654321"]
    first = page.studies[0]
    assert first.title == "A Study of Widgetinib in Solid Tumors"
    assert first.status == "RECRUITING"
    assert first.phase == "PHASE2"
    assert first.conditions == ["Solid Tumor", "Melanoma"]
    assert first.interventions == ["Widgetinib"]
    assert first.enrollment == 120
    # Relevance is synthesized from position: first > second.
    assert page.studies[0].relevance_score > page.studies[1].relevance_score


@pytest.mark.unit
async def test_status_and_phase_filters_are_sent(ct_base_url: str) -> None:
    async with ClinicalTrialsClient(ct_base_url) as client:
        await client.search_studies(
            condition="melanoma", status="RECRUITING", phase="PHASE2"
        )
    assert _Handler.last_query["filter.overallStatus"] == ["RECRUITING"]
    # CT.gov v2 aggFilters phase buckets are numeric ids: PHASE2 -> "2" (sending the
    # enum name would silently return zero results).
    assert _Handler.last_query["aggFilters"] == ["phase:2"]


@pytest.mark.unit
async def test_page_size_is_clamped_to_api_maximum(ct_base_url: str) -> None:
    async with ClinicalTrialsClient(ct_base_url) as client:
        await client.search_studies(condition="melanoma", page_size=1000)
    assert _Handler.last_query["pageSize"] == ["100"]


@pytest.mark.unit
async def test_invalid_status_or_phase_rejected(ct_base_url: str) -> None:
    async with ClinicalTrialsClient(ct_base_url) as client:
        with pytest.raises(ValueError, match="status"):
            await client.search_studies(condition="x", status="BOGUS")
        with pytest.raises(ValueError, match="phase"):
            await client.search_studies(condition="x", phase="PHASE9")
        # "NA" has no aggFilters phase bucket and must be rejected, not sent.
        with pytest.raises(ValueError, match="phase"):
            await client.search_studies(condition="x", phase="NA")


@pytest.mark.unit
async def test_get_study_parses_full_detail(ct_base_url: str) -> None:
    async with ClinicalTrialsClient(ct_base_url) as client:
        detail = await client.get_study("NCT01234567")
    assert detail is not None
    assert detail.nct_id == "NCT01234567"
    assert detail.official_title == "A Phase 2 Study of Widgetinib"
    assert detail.study_type == "INTERVENTIONAL"
    assert detail.primary_purpose == "TREATMENT"
    assert detail.interventions[0].name == "Widgetinib"
    assert detail.interventions[0].type == "DRUG"
    assert detail.primary_outcomes[0].measure == "Overall Response Rate"
    assert detail.secondary_outcomes[0].measure == "Overall Survival"
    assert detail.eligibility_criteria == "Adults with measurable disease"
    assert [s.role for s in detail.sponsors] == ["lead", "collaborator"]
    assert detail.locations[0].city == "Boston"
    assert detail.references[0].pmid == "12345678"
    assert detail.url == "https://clinicaltrials.gov/study/NCT01234567"


@pytest.mark.unit
async def test_get_study_404_returns_none(ct_base_url: str) -> None:
    async with ClinicalTrialsClient(ct_base_url) as client:
        assert await client.get_study("NCT00000000") is None


@pytest.mark.unit
async def test_get_study_rejects_malformed_nct_id(ct_base_url: str) -> None:
    async with ClinicalTrialsClient(ct_base_url) as client:
        with pytest.raises(ValueError, match="NCT id"):
            await client.get_study("NCT123")


@pytest.mark.unit
async def test_transport_error_raises_storage_error() -> None:
    # Unreachable host → transport error → retried to exhaustion, then StorageError.
    async with ClinicalTrialsClient("http://127.0.0.1:9") as client:
        with pytest.raises(StorageError):
            await client.search_studies(condition="x")


class _StatusHandler(BaseHTTPRequestHandler):
    """Returns a configurable status/body — for the 5xx and bad-JSON error paths."""

    status: ClassVar[int] = 500
    body: ClassVar[bytes] = b"upstream boom"

    def do_GET(self) -> None:
        self.send_response(_StatusHandler.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(_StatusHandler.body)))
        self.end_headers()
        self.wfile.write(_StatusHandler.body)

    def log_message(self, *_a: Any) -> None:
        pass


def _serve(handler: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address[:2]
    return srv, f"http://{host}:{port}"


@pytest.mark.unit
async def test_server_5xx_response_raises_storage_error() -> None:
    _StatusHandler.status, _StatusHandler.body = 500, b"upstream boom"
    srv, base = _serve(_StatusHandler)
    try:
        async with ClinicalTrialsClient(base) as client:
            with pytest.raises(StorageError, match="500"):
                await client.search_studies(condition="x")
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.unit
async def test_non_json_200_response_raises_storage_error() -> None:
    _StatusHandler.status, _StatusHandler.body = 200, b"<html>not json</html>"
    srv, base = _serve(_StatusHandler)
    try:
        async with ClinicalTrialsClient(base) as client:
            with pytest.raises(StorageError, match="JSON"):
                await client.search_studies(condition="x")
    finally:
        srv.shutdown()
        srv.server_close()


class _EmptyHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps({"studies": [], "totalCount": 0}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a: Any) -> None:
        pass


@pytest.mark.unit
async def test_empty_result_set_parses_to_empty_page() -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _EmptyHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address[:2]
    try:
        async with ClinicalTrialsClient(f"http://{host}:{port}") as client:
            page = await client.search_studies(term="no-such-condition-xyzzy")
    finally:
        srv.shutdown()
        srv.server_close()
    assert page.total == 0
    assert page.studies == []


class _NullHandler(BaseHTTPRequestHandler):
    """Returns a body with present-but-null fields (studies/totalCount = null)."""

    def do_GET(self) -> None:
        body = json.dumps({"studies": None, "totalCount": None}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a: Any) -> None:
        pass


@pytest.mark.unit
async def test_null_studies_body_parses_to_empty_page() -> None:
    # A `"studies": null` body must not crash — it yields an empty page (total 0).
    srv, base = _serve(_NullHandler)
    try:
        async with ClinicalTrialsClient(base) as client:
            page = await client.search_studies(condition="x")
    finally:
        srv.shutdown()
        srv.server_close()
    assert page.total == 0
    assert page.studies == []
