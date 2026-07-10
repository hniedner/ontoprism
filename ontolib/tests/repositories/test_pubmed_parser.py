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
def test_esummary_article_ids_without_doi_returns_none() -> None:
    summary = parse_esummary(
        "42",
        {
            "title": "No DOI",
            "articleids": [
                {"idtype": "pmid", "value": "42"},
                {"idtype": "pmc", "value": "PMC12345"},
            ],
        },
    )
    assert summary.doi is None


@pytest.mark.unit
def test_esummary_article_ids_not_a_list_is_handled() -> None:
    summary = parse_esummary(
        "42",
        {
            "title": "Bad ids",
            "articleids": None,
        },
    )
    assert summary.doi is None


@pytest.mark.unit
def test_efetch_author_without_last_or_fore_name_is_skipped() -> None:
    xml = (
        "<PubmedArticle><MedlineCitation><PMID>10</PMID><Article>"
        "<ArticleTitle>Test</ArticleTitle>"
        "<AuthorList><Author><Initials>AB</Initials></Author></AuthorList>"
        "</Article></MedlineCitation></PubmedArticle>"
    )
    article = parse_efetch_article(DefusedET.fromstring(xml))
    assert article is not None
    assert article.authors == []


@pytest.mark.unit
def test_efetch_mesh_heading_without_descriptor_returns_none() -> None:
    xml = (
        "<PubmedArticle><MedlineCitation><PMID>11</PMID><Article>"
        "<ArticleTitle>T</ArticleTitle></Article>"
        "<MeshHeadingList><MeshHeading>"
        "<QualifierName>therapy</QualifierName>"
        "</MeshHeading></MeshHeadingList>"
        "</MedlineCitation></PubmedArticle>"
    )
    article = parse_efetch_article(DefusedET.fromstring(xml))
    assert article is not None
    assert article.mesh_terms == []


@pytest.mark.unit
def test_esummary_doi_found_in_article_ids() -> None:
    summary = parse_esummary(
        "42",
        {
            "title": "With DOI",
            "articleids": [
                {"idtype": "pmid", "value": "42"},
                {"idtype": "doi", "value": "10.1000/xyz123"},
            ],
        },
    )
    assert summary.doi == "10.1000/xyz123"


@pytest.mark.unit
def test_efetch_article_with_named_author_and_article_ids() -> None:
    xml = (
        "<PubmedArticle><MedlineCitation><PMID>12</PMID><Article>"
        "<ArticleTitle>Named Author</ArticleTitle>"
        "<AuthorList><Author>"
        "<LastName>Smith</LastName><ForeName>John</ForeName><Initials>JS</Initials>"
        "</Author></AuthorList>"
        "</Article></MedlineCitation>"
        "<PubmedData><ArticleIdList>"
        '<ArticleId IdType="doi">10.1000/abc123</ArticleId>'
        '<ArticleId IdType="pmc">PMC99999</ArticleId>'
        "</ArticleIdList></PubmedData>"
        "</PubmedArticle>"
    )
    article = parse_efetch_article(DefusedET.fromstring(xml))
    assert article is not None
    assert article.authors[0].last_name == "Smith"
    assert article.authors[0].fore_name == "John"
    assert article.authors[0].initials == "JS"
    assert article.doi == "10.1000/abc123"
    assert article.pmc_id == "PMC99999"


@pytest.mark.unit
def test_efetch_qualifier_major_topic_flag() -> None:
    xml = (
        "<PubmedArticle><MedlineCitation><PMID>13</PMID>"
        "<Article><ArticleTitle>T</ArticleTitle></Article>"
        "<MeshHeadingList><MeshHeading>"
        "<DescriptorName>Neoplasms</DescriptorName>"
        '<QualifierName MajorTopicYN="Y">therapy</QualifierName>'
        "</MeshHeading></MeshHeadingList>"
        "</MedlineCitation></PubmedArticle>"
    )
    article = parse_efetch_article(DefusedET.fromstring(xml))
    assert article is not None
    assert article.mesh_terms[0].descriptor == "Neoplasms"
    assert article.mesh_terms[0].major_topic is True
    assert article.mesh_terms[0].qualifiers == ["therapy"]


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
