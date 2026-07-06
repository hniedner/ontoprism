"""Pure parsers for NCBI E-utilities payloads (ESummary JSON, EFetch XML).

I/O-free so the fiddly PubMed XML/JSON shape handling is unit-testable in isolation.
EFetch XML is parsed with :mod:`defusedxml` (the response is external, untrusted input).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from defusedxml import ElementTree as DefusedET

from ontolib.repositories.pubmed.models import (
    MeshTerm,
    PubMedArticleDetail,
    PubMedArticleSummary,
    PubMedAuthor,
)

if TYPE_CHECKING:
    from xml.etree.ElementTree import Element

_ARTICLE_URL = "https://pubmed.ncbi.nlm.nih.gov/"


def _doi_from_articleids(article_ids: list[dict[str, Any]]) -> str | None:
    for entry in article_ids:
        if isinstance(entry, dict) and entry.get("idtype") == "doi":
            return entry.get("value")
    return None


def parse_esummary(uid: str, doc: dict[str, Any]) -> PubMedArticleSummary:
    """Map one ESummary result document to a :class:`PubMedArticleSummary`."""
    authors = [
        a["name"]
        for a in doc.get("authors", [])
        if isinstance(a, dict) and a.get("name")
    ]
    article_ids = doc.get("articleids", [])
    doi = _doi_from_articleids(article_ids if isinstance(article_ids, list) else [])
    return PubMedArticleSummary(
        pmid=str(doc.get("uid", uid)),
        title=doc.get("title", ""),
        journal=doc.get("fulljournalname") or doc.get("source"),
        pub_date=doc.get("pubdate"),
        authors=authors,
        doi=doi,
    )


def _text(element: Element | None) -> str | None:
    if element is None:
        return None
    text = "".join(element.itertext()).strip()
    return text or None


def _parse_authors(article: Element) -> list[PubMedAuthor]:
    authors: list[PubMedAuthor] = []
    for author in article.findall(".//AuthorList/Author"):
        last = _text(author.find("LastName"))
        fore = _text(author.find("ForeName"))
        initials = _text(author.find("Initials"))
        if last or fore:
            authors.append(
                PubMedAuthor(last_name=last, fore_name=fore, initials=initials)
            )
    return authors


def _parse_abstract(article: Element) -> str | None:
    # Abstracts may be split into labelled sections; join them in document order.
    parts = [_text(node) for node in article.findall(".//Abstract/AbstractText")]
    joined = " ".join(p for p in parts if p)
    return joined or None


def _mesh_is_major(descriptor: Element, qualifier_nodes: list[Element]) -> bool:
    """A MeSH heading is a major topic if the descriptor or any qualifier is flagged."""
    if descriptor.get("MajorTopicYN") == "Y":
        return True
    return any(q.get("MajorTopicYN") == "Y" for q in qualifier_nodes)


def _parse_mesh_heading(heading: Element) -> MeshTerm | None:
    """Map one ``<MeshHeading>`` element to a :class:`MeshTerm` (None if unnamed)."""
    descriptor = heading.find("DescriptorName")
    name = _text(descriptor)
    if descriptor is None or not name:
        return None
    qualifier_nodes = heading.findall("QualifierName")
    qualifiers = [q for q in (_text(q) for q in qualifier_nodes) if q]
    return MeshTerm(
        descriptor=name,
        qualifiers=qualifiers,
        major_topic=_mesh_is_major(descriptor, qualifier_nodes),
    )


def _parse_mesh(citation: Element) -> list[MeshTerm]:
    headings = citation.findall(".//MeshHeadingList/MeshHeading")
    terms = (_parse_mesh_heading(h) for h in headings)
    return [t for t in terms if t is not None]


def _article_id(pubmed_article: Element, id_type: str) -> str | None:
    for node in pubmed_article.findall(".//ArticleIdList/ArticleId"):
        if node.get("IdType") == id_type:
            return _text(node)
    return None


def _parse_keywords(citation: Element) -> list[str]:
    nodes = citation.findall(".//KeywordList/Keyword")
    return [k for k in (_text(k) for k in nodes) if k]


def _parse_pub_date(article: Element) -> str | None:
    """Format the ``<PubDate>`` — its Year/Month/Day children (or MedlineDate).

    ``<PubDate>`` is a compound element, so joining its raw itertext would smash the
    parts together (``2024Jan15``); assemble the named children with spaces instead.
    """
    pub_date = article.find(".//Journal/JournalIssue/PubDate")
    if pub_date is None:
        return None
    medline = _text(pub_date.find("MedlineDate"))
    if medline:
        return medline
    parts = [_text(pub_date.find(tag)) for tag in ("Year", "Month", "Day")]
    return " ".join(p for p in parts if p) or None


def parse_efetch_article(pubmed_article: Element) -> PubMedArticleDetail | None:
    """Map one ``<PubmedArticle>`` element to a :class:`PubMedArticleDetail`.

    Returns None if the element carries no PMID (a malformed / empty record).
    """
    citation = pubmed_article.find("MedlineCitation")
    if citation is None:
        return None
    pmid = _text(citation.find("PMID"))
    if not pmid:
        return None
    found = citation.find("Article")
    article = found if found is not None else citation
    return PubMedArticleDetail(
        pmid=pmid,
        title=_text(article.find("ArticleTitle")) or "",
        abstract=_parse_abstract(article),
        authors=_parse_authors(article),
        journal=_text(article.find(".//Journal/Title")),
        pub_date=_parse_pub_date(article),
        doi=_article_id(pubmed_article, "doi"),
        pmc_id=_article_id(pubmed_article, "pmc"),
        mesh_terms=_parse_mesh(citation),
        keywords=_parse_keywords(citation),
        url=f"{_ARTICLE_URL}{pmid}/",
    )


def parse_efetch_xml(xml_text: str) -> list[PubMedArticleDetail]:
    """Parse an EFetch ``PubmedArticleSet`` document into article details."""
    root = DefusedET.fromstring(xml_text)
    articles = [parse_efetch_article(node) for node in root.findall(".//PubmedArticle")]
    return [a for a in articles if a is not None]
