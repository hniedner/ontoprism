"""Unit tests for the pure PubMed E-utilities parsers (ESummary JSON, EFetch XML)."""

from __future__ import annotations

import pytest
from defusedxml import ElementTree as DefusedET

from ontolib.repositories.pubmed.parser import (
    parse_efetch_article,
    parse_efetch_xml,
    parse_esummary,
)


@pytest.mark.unit
def test_esummary_minimal_doc_uses_safe_defaults() -> None:
    summary = parse_esummary("42", {"title": "Bare"})
    assert summary.pmid == "42"
    assert summary.title == "Bare"
    assert summary.journal is None
    assert summary.authors == []
    assert summary.doi is None


@pytest.mark.unit
def test_efetch_article_without_pmid_is_dropped() -> None:
    xml = (
        "<PubmedArticleSet><PubmedArticle><MedlineCitation>"
        "<Article><ArticleTitle>No PMID</ArticleTitle></Article>"
        "</MedlineCitation></PubmedArticle></PubmedArticleSet>"
    )
    assert parse_efetch_xml(xml) == []


@pytest.mark.unit
def test_efetch_multiple_articles_parsed_in_order() -> None:
    xml = (
        "<PubmedArticleSet>"
        "<PubmedArticle><MedlineCitation><PMID>1</PMID>"
        "<Article><ArticleTitle>First</ArticleTitle></Article>"
        "</MedlineCitation></PubmedArticle>"
        "<PubmedArticle><MedlineCitation><PMID>2</PMID>"
        "<Article><ArticleTitle>Second</ArticleTitle></Article>"
        "</MedlineCitation></PubmedArticle>"
        "</PubmedArticleSet>"
    )
    articles = parse_efetch_xml(xml)
    assert [a.pmid for a in articles] == ["1", "2"]
    assert [a.title for a in articles] == ["First", "Second"]


@pytest.mark.unit
def test_efetch_descriptor_major_topic_flag() -> None:
    xml = (
        "<PubmedArticle><MedlineCitation><PMID>9</PMID>"
        "<Article><ArticleTitle>T</ArticleTitle></Article>"
        "<MeshHeadingList><MeshHeading>"
        '<DescriptorName MajorTopicYN="Y">Neoplasms</DescriptorName>'
        "</MeshHeading></MeshHeadingList>"
        "</MedlineCitation></PubmedArticle>"
    )
    article = parse_efetch_article(DefusedET.fromstring(xml))
    assert article is not None
    assert article.mesh_terms[0].descriptor == "Neoplasms"
    assert article.mesh_terms[0].major_topic is True
    assert article.mesh_terms[0].qualifiers == []


@pytest.mark.unit
def test_efetch_article_missing_citation_returns_none() -> None:
    xml = "<PubmedArticle><PubmedData/></PubmedArticle>"
    assert parse_efetch_article(DefusedET.fromstring(xml)) is None


@pytest.mark.unit
def test_efetch_structured_pubdate_is_joined_with_spaces() -> None:
    # A compound <PubDate> must not be smashed into "2024Jan15".
    xml = (
        "<PubmedArticle><MedlineCitation><PMID>7</PMID><Article>"
        "<ArticleTitle>T</ArticleTitle>"
        "<Journal><JournalIssue><PubDate>"
        "<Year>2024</Year><Month>Jan</Month><Day>15</Day>"
        "</PubDate></JournalIssue></Journal>"
        "</Article></MedlineCitation></PubmedArticle>"
    )
    article = parse_efetch_article(DefusedET.fromstring(xml))
    assert article is not None
    assert article.pub_date == "2024 Jan 15"


@pytest.mark.unit
def test_efetch_medline_date_pubdate() -> None:
    xml = (
        "<PubmedArticle><MedlineCitation><PMID>8</PMID><Article>"
        "<ArticleTitle>T</ArticleTitle>"
        "<Journal><JournalIssue><PubDate>"
        "<MedlineDate>2024 Winter</MedlineDate>"
        "</PubDate></JournalIssue></Journal>"
        "</Article></MedlineCitation></PubmedArticle>"
    )
    article = parse_efetch_article(DefusedET.fromstring(xml))
    assert article is not None
    assert article.pub_date == "2024 Winter"
