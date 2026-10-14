"""Publisher xrefs and hierarchy, not labels or mapping presence, corroborate genus."""

from io import BytesIO
from xml.etree.ElementTree import ParseError

import pytest
from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from ontolib.core.data_build_tools import ToolIdentityError
from ontolib.terminologies.mondo import MondoHierarchy, published_mondo

pytestmark = pytest.mark.unit
M = Namespace("http://purl.obolibrary.org/obo/MONDO_")
OBO = Namespace("http://www.geneontology.org/formats/oboInOwl#")
VERSION = "http://purl.obolibrary.org/obo/mondo/releases/2026-09-01/mondo-base.owl"


def source_file(tmp_path, *, edges=((1, 2), (2, 3)), mappings=None, deprecated=()):
    graph = Graph()
    ontology = URIRef("http://purl.obolibrary.org/obo/mondo/mondo-base.owl")
    graph.add((ontology, RDF.type, OWL.Ontology))
    graph.add((ontology, OWL.versionIRI, URIRef(VERSION)))
    mappings = (
        mappings
        if mappings is not None
        else ((1, "C1", "MONDO:equivalentTo"), (3, "C3", "MONDO:equivalentTo"))
    )
    for child, parent in edges:
        for node in (child, parent):
            graph.add((M[str(node)], RDF.type, OWL.Class))
        graph.add((M[str(child)], RDFS.subClassOf, M[str(parent)]))
    for node, code, qualifier in mappings:
        subject, target = M[str(node)], Literal("NCIT:" + code)
        graph.add((subject, RDF.type, OWL.Class))
        graph.add((subject, OBO.hasDbXref, target))
        if qualifier:
            axiom = BNode()
            for predicate, value in (
                (RDF.type, OWL.Axiom),
                (OWL.annotatedSource, subject),
                (OWL.annotatedProperty, OBO.hasDbXref),
                (OWL.annotatedTarget, target),
                (OBO.source, Literal(qualifier)),
            ):
                graph.add((axiom, predicate, value))
    for node in deprecated:
        graph.add((M[str(node)], OWL.deprecated, Literal(True)))
    path = tmp_path / "mondo.owl"
    graph.serialize(path, format="xml")
    return path


def test_genus_support_cites_qualified_xrefs_and_every_published_edge(tmp_path):
    source = MondoHierarchy.read(source_file(tmp_path))
    verdict, items = source.evidence("C1", "C3")
    assert verdict == "supported"
    assert len(items) == 1
    item = items[0]
    assert (item.tier, item.source, item.source_release) == (1, "Mondo", VERSION)
    assert "Mondo published release hierarchy (publisher-classified)" in item.statement
    assert "NCIT:C1" in item.statement
    assert "NCIT:C3" in item.statement
    assert "MONDO:equivalentTo" in item.statement
    assert f"{M['1']} rdfs:subClassOf {M['2']}" in item.statement
    assert f"{M['2']} rdfs:subClassOf {M['3']}" in item.statement
    assert "not nearest-frontier" in item.statement
    assert source.evidence("C3", "C1") == ("disagreement", ())


@pytest.mark.parametrize(
    ("mappings", "edges", "deprecated", "child", "expected"),
    [
        (
            [(1, "C1", None), (3, "C3", "MONDO:equivalentTo")],
            [(1, 3)],
            (),
            "C1",
            "unmapped",
        ),
        (
            [(1, "C1", "MONDO:relatedTo"), (3, "C3", "MONDO:equivalentTo")],
            [(1, 3)],
            (),
            "C1",
            "unmapped",
        ),
        (
            [(1, "C1", "MONDO:equivalentObsolete"), (3, "C3", "MONDO:equivalentTo")],
            [(1, 3)],
            (),
            "C1",
            "unmapped",
        ),
        (
            [
                (1, "C1", "MONDO:equivalentTo"),
                (2, "C1", "MONDO:equivalentTo"),
                (3, "C3", "MONDO:equivalentTo"),
            ],
            [(1, 3)],
            (),
            "C1",
            "multi-mapped",
        ),
        (
            [
                (1, "C1", "MONDO:equivalentTo"),
                (1, "C2", "MONDO:relatedTo"),
                (3, "C3", "MONDO:equivalentTo"),
            ],
            [(1, 3)],
            (),
            "C1",
            "multi-mapped",
        ),
        (
            [(1, "C1", "MONDO:equivalentTo"), (1, "C3", "MONDO:equivalentTo")],
            [],
            (),
            "C1",
            "multi-mapped",
        ),
        (None, [(1, 2), (2, 3)], (2,), "C1", "disagreement"),
        (None, [(1, 3)], (1,), "C1", "unmapped"),
        (None, [(1, 2), (2, 1)], (), "C1", "disagreement"),
        (None, [(1, 3)], (), "C999", "unmapped"),
        (None, [(1, 3)], (), "C3", "disagreement"),
        (
            [(1, "3921", "MONDO:equivalentTo"), (3, "C3", "MONDO:equivalentTo")],
            [(1, 3)],
            (),
            "C3921",
            "unmapped",
        ),
    ],
    ids=[
        "unqualified",
        "related",
        "obsolete-equivalence",
        "forward-ambiguous",
        "reverse-ambiguous",
        "shared-target",
        "deprecated-intermediate",
        "deprecated-child",
        "cycle-no-path",
        "absent",
        "zero-edge",
        "malformed-not-repaired",
    ],
)
def test_only_unique_equivalence_mappings_and_active_nonempty_paths_support(
    tmp_path, mappings, edges, deprecated, child, expected
):
    source = MondoHierarchy.read(
        source_file(tmp_path, mappings=mappings, edges=edges, deprecated=deprecated)
    )
    assert source.evidence(child, "C3") == (expected, ())


def test_source_exclusions_are_visible_and_definitions_are_not_unfolded(tmp_path):
    path = source_file(
        tmp_path,
        edges=[],
        mappings=[
            (1, "C1", "MONDO:equivalentTo"),
            (3, "C3", "MONDO:equivalentTo"),
            (4, "C4", "MONDO:relatedTo"),
            (5, "3921", "MONDO:equivalentTo"),
            (6, "C6", "MONDO:equivalentTo"),
        ],
        deprecated=(6,),
    )
    graph = Graph().parse(path)
    graph.add((M["1"], OWL.equivalentClass, M["3"]))
    graph.serialize(path, format="xml")
    source = MondoHierarchy.read(path)
    assert source.evidence("C1", "C3") == ("disagreement", ())
    assert source.exclusions == {"malformed": 1, "non-equivalence": 1, "deprecated": 1}


@pytest.mark.parametrize("problem", ["version", "imports", "empty", "broken-xml"])
def test_unusable_release_is_not_an_empty_success(tmp_path, problem):
    path = source_file(tmp_path)
    graph = Graph().parse(path)
    ontology = next(graph.subjects(RDF.type, OWL.Ontology))
    if problem == "version":
        graph.set((ontology, OWL.versionIRI, URIRef("https://example.org/wrong")))
    elif problem == "imports":
        graph.add((ontology, OWL.imports, URIRef("https://example.org/other")))
    elif problem == "empty":
        graph = Graph()
    graph.serialize(path, format="xml")
    if problem == "broken-xml":
        path.write_text("<broken>")
    with pytest.raises((ValueError, ParseError)):
        MondoHierarchy.read(path)


def test_download_rejects_changed_release_bytes(monkeypatch):
    class Response(BytesIO):
        def geturl(self):
            return "https://example.org/mondo-base.owl"

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Response(b"changed"))
    with pytest.raises(ToolIdentityError, match="digest mismatch"):
        published_mondo()
