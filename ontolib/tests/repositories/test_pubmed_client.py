"""Behavioral tests for the PubMed E-utilities client (mocked local HTTP server)."""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import parse_qs, urlparse

import pytest

from ontolib.core.exceptions import StorageError
from ontolib.repositories.pubmed.client import (
    PubMedClient,
    _extract_elink_pmids,
    _linkset_pmids,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

_ESEARCH = {"esearchresult": {"count": "57", "idlist": ["111", "222"]}}
_ESUMMARY = {
    "result": {
        "uids": ["111", "222"],
        "111": {
            "uid": "111",
            "title": "Widgetinib in melanoma",
            "fulljournalname": "Journal of Oncology",
            "pubdate": "2024 Jan",
            "authors": [{"name": "Smith J"}, {"name": "Doe A"}],
            "articleids": [{"idtype": "doi", "value": "10.1/abc"}],
        },
        "222": {
            "uid": "222",
            "title": "Second article",
            "source": "Short J",
            "pubdate": "2023",
            "authors": [],
            "articleids": [],
        },
    }
}
_EFETCH = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>111</PMID>
      <Article>
        <Journal><Title>Journal of Oncology</Title></Journal>
        <ArticleTitle>Widgetinib in melanoma</ArticleTitle>
        <Abstract>
          <AbstractText Label="BACKGROUND">Melanoma is common.</AbstractText>
          <AbstractText Label="RESULTS">Widgetinib worked.</AbstractText>
        </Abstract>
        <AuthorList>
          <Author><LastName>Smith</LastName><ForeName>Jane</ForeName><Initials>J</Initials></Author>
        </AuthorList>
      </Article>
      <MeshHeadingList>
        <MeshHeading>
          <DescriptorName MajorTopicYN="N">Melanoma</DescriptorName>
          <QualifierName MajorTopicYN="Y">drug therapy</QualifierName>
        </MeshHeading>
      </MeshHeadingList>
      <KeywordList><Keyword>oncology</Keyword></KeywordList>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="doi">10.1/abc</ArticleId>
        <ArticleId IdType="pmc">PMC123</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>"""
_ELINK = {
    "linksets": [
        {"linksetdbs": [{"linkname": "pubmed_pubmed", "links": ["111", "333", "444"]}]}
    ]
}


class _Handler(BaseHTTPRequestHandler):
    # Query params captured per endpoint (e.g. "esearch", "esummary", "elink").
    queries: ClassVar[dict[str, dict[str, list[str]]]] = {}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        endpoint = parsed.path.rsplit("/", 1)[-1].removesuffix(".fcgi")
        _Handler.queries[endpoint] = parse_qs(parsed.query)
        if parsed.path.endswith("/esearch.fcgi"):
            self._json(_ESEARCH)
        elif parsed.path.endswith("/esummary.fcgi"):
            self._json(_ESUMMARY)
        elif parsed.path.endswith("/efetch.fcgi"):
            self._xml(_EFETCH)
        elif parsed.path.endswith("/elink.fcgi"):
            self._json(_ELINK)
        else:
            self.send_response(404)
            self.end_headers()

    def _json(self, payload: dict[str, Any]) -> None:
        self._send(json.dumps(payload).encode(), "application/json")

    def _xml(self, text: str) -> None:
        self._send(text.encode(), "application/xml")

    def _send(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a: Any) -> None:
        pass


@pytest.fixture
def pubmed_url() -> Iterator[str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        srv.shutdown()
        srv.server_close()


def _client(base: str) -> PubMedClient:
    # requests_per_second=0 disables throttling so tests don't sleep.
    return PubMedClient(base, requests_per_second=0)


@pytest.mark.unit
async def test_search_resolves_idlist_to_summaries(pubmed_url: str) -> None:
    async with _client(pubmed_url) as client:
        result = await client.search_articles("melanoma", retmax=20)
    assert result.total == 57
    assert [a.pmid for a in result.articles] == ["111", "222"]
    first = result.articles[0]
    assert first.title == "Widgetinib in melanoma"
    assert first.journal == "Journal of Oncology"
    assert first.authors == ["Smith J", "Doe A"]
    assert first.doi == "10.1/abc"
    # Fallback fields: second article uses `source` for journal.
    assert result.articles[1].journal == "Short J"


@pytest.mark.unit
async def test_search_retmax_is_clamped(pubmed_url: str) -> None:
    async with _client(pubmed_url) as client:
        await client.search_articles("melanoma", retmax=9999)
    assert _Handler.queries["esearch"]["retmax"] == ["100"]


@pytest.mark.unit
async def test_search_empty_idlist_skips_esummary(pubmed_url: str) -> None:
    # A query with no hits must not issue an ESummary with an empty id list.
    class _Empty(_Handler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path.endswith("/esummary.fcgi"):
                raise AssertionError("ESummary must not be called for zero hits")
            self._json({"esearchresult": {"count": "0", "idlist": []}})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Empty)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address[:2]
    try:
        async with _client(f"http://{host}:{port}") as client:
            result = await client.search_articles("no-hits-xyzzy")
    finally:
        srv.shutdown()
        srv.server_close()
    assert result.total == 0
    assert result.articles == []


@pytest.mark.unit
async def test_get_article_parses_efetch(pubmed_url: str) -> None:
    async with _client(pubmed_url) as client:
        article = await client.get_article("111")
    assert article is not None
    assert article.pmid == "111"
    assert article.title == "Widgetinib in melanoma"
    assert article.abstract == "Melanoma is common. Widgetinib worked."
    assert article.authors[0].last_name == "Smith"
    assert article.doi == "10.1/abc"
    assert article.pmc_id == "PMC123"
    assert article.mesh_terms[0].descriptor == "Melanoma"
    assert article.mesh_terms[0].qualifiers == ["drug therapy"]
    assert article.mesh_terms[0].major_topic is True  # via major qualifier
    assert article.keywords == ["oncology"]
    assert article.url == "https://pubmed.ncbi.nlm.nih.gov/111/"


@pytest.mark.unit
async def test_get_article_missing_returns_none(pubmed_url: str) -> None:
    class _EmptySet(_Handler):
        def do_GET(self) -> None:
            self._xml('<?xml version="1.0"?><PubmedArticleSet></PubmedArticleSet>')

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _EmptySet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address[:2]
    try:
        async with _client(f"http://{host}:{port}") as client:
            assert await client.get_article("999999") is None
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.unit
async def test_related_pmids_drops_self(pubmed_url: str) -> None:
    async with _client(pubmed_url) as client:
        related = await client.get_related_pmids("111", link_type="similar")
    assert related.link_type == "similar"
    # "111" (the source) is filtered out of its own similar set.
    assert related.related_pmids == ["333", "444"]
    assert _Handler.queries["elink"]["linkname"] == ["pubmed_pubmed"]


@pytest.mark.unit
async def test_related_invalid_link_type_rejected(pubmed_url: str) -> None:
    async with _client(pubmed_url) as client:
        with pytest.raises(ValueError, match="link_type"):
            await client.get_related_pmids("111", link_type="bogus")


@pytest.mark.unit
async def test_transport_error_raises_storage_error() -> None:
    async with _client("http://127.0.0.1:9") as client:
        with pytest.raises(StorageError):
            await client.search_articles("x")


class _BadHandler(BaseHTTPRequestHandler):
    """Returns malformed XML for efetch and non-JSON for esearch."""

    def do_GET(self) -> None:
        if self.path.split("?")[0].endswith("/efetch.fcgi"):
            body, ctype = b"<PubmedArticleSet><broken", "application/xml"
        else:
            body, ctype = b"<html>not json</html>", "text/html"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a: Any) -> None:
        pass


@pytest.mark.unit
async def test_malformed_efetch_xml_raises_storage_error() -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _BadHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address[:2]
    try:
        async with _client(f"http://{host}:{port}") as client:
            with pytest.raises(StorageError, match="unparseable XML"):
                await client.get_article("111")
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.unit
async def test_non_json_esearch_raises_storage_error() -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _BadHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address[:2]
    try:
        async with _client(f"http://{host}:{port}") as client:
            with pytest.raises(StorageError, match="JSON"):
                await client.search_articles("x")
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.unit
async def test_api_key_is_sent_when_configured(pubmed_url: str) -> None:
    async with PubMedClient(pubmed_url, api_key="secret", requests_per_second=0) as c:
        await c.search_articles("melanoma")
    assert _Handler.queries["esearch"]["api_key"] == ["secret"]


# -- module-level helper tests -----------------------------------------------


@pytest.mark.unit
def test_linkset_pmids_non_dict_returns_empty() -> None:
    assert _linkset_pmids("not a dict", "pubmed_pubmed") == []


@pytest.mark.unit
def test_linkset_pmids_skips_non_dict_entries() -> None:
    linkset = {
        "linksetdbs": [
            "string_entry",
            {"linkname": "pubmed_pubmed", "links": ["333", "444"]},
            None,
        ]
    }
    assert _linkset_pmids(linkset, "pubmed_pubmed") == ["333", "444"]


@pytest.mark.unit
def test_extract_elink_pmids_non_dict_returns_empty() -> None:
    assert _extract_elink_pmids([], "pubmed_pubmed", source_pmid="111") == []


@pytest.mark.unit
def test_extract_elink_pmids_drops_source_pmid() -> None:
    data = {
        "linksets": [
            {
                "linksetdbs": [
                    {"linkname": "pubmed_pubmed", "links": ["111", "333", "444"]}
                ]
            }
        ]
    }
    assert _extract_elink_pmids(data, "pubmed_pubmed", source_pmid="111") == [
        "333",
        "444",
    ]


@pytest.mark.unit
async def test_throttle_sleeps_on_concurrent_calls() -> None:
    class _FastHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._json(_ESEARCH)

        def _json(self, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_a: object) -> None:
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FastHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address[:2]
    async with (
        PubMedClient(f"http://{host}:{port}", requests_per_second=10) as client,
        asyncio.TaskGroup() as tg,
    ):
        tg.create_task(client.search_articles("first"))
        await asyncio.sleep(0)
        tg.create_task(client.search_articles("second"))
    srv.shutdown()
    srv.server_close()


@pytest.mark.unit
async def test_non_200_response_raises_storage_error() -> None:
    class _FailHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(500)
            self.end_headers()

        def log_message(self, *_a: object) -> None:
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FailHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address[:2]
    try:
        async with _client(f"http://{host}:{port}") as client:
            with pytest.raises(StorageError, match="HTTP 500"):
                await client.search_articles("x")
    finally:
        srv.shutdown()
        srv.server_close()
