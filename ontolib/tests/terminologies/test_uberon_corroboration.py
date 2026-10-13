"""Explicit OWL statements, not absent relationships, corroborate anatomy."""

import pytest
from rdflib import OWL, RDFS, URIRef
from test_support.uberon_graph import GraphClient

from ontolib.core.exceptions import StorageError
from ontolib.terminologies.uberon.corroboration import UberonCorroboration

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("both", [False, True])
async def test_region_contradiction_requires_excluding_both_permitted_categories(both):
    extra = "obo:UBERON_9999999 owl:disjointWith obo:UBERON_0000479 ." if both else ""
    source = await UberonCorroboration.read(
        GraphClient(
            """
        obo:UBERON_9999999 oio:hasDbXref "NCIT:C999" ;
            owl:disjointWith obo:UBERON_0000475 .
    """
            + extra
        ),
        {"C999"},
    )
    verdict, evidence = source.evidence("C999", "op:AssociatedRegion")
    assert verdict == ("contradicted" if both else "mapped but unresolved")
    if both:
        assert "UBERON_0000475" in evidence[0].statement
        assert "UBERON_0000479" in evidence[0].statement
    else:
        assert evidence == ()


@pytest.mark.parametrize("multiple", [False, True])
async def test_missing_relation_or_multiple_targets_never_support(multiple):
    client = GraphClient(
        'obo:UBERON_9999999 oio:hasDbXref "NCIT:C12468" .' if multiple else ""
    )
    if not multiple:
        client.graph.remove((None, RDFS.subClassOf, None))
    source = await UberonCorroboration.read(client, {"C12468", "C999"})
    assert source.evidence("C12468", "op:PrimarySite") == ("mapped but unresolved", ())
    assert source.evidence("C999", "op:PrimarySite") == ("no mapping", ())


async def test_part_of_requires_unique_endpoints_and_existential_statements():
    client = GraphClient("""
        obo:UBERON_0001004 oio:hasDbXref "NCIT:C12641" .
        obo:UBERON_0000062 rdfs:subClassOf [owl:onProperty obo:BFO_0000050;
            owl:someValuesFrom obo:UBERON_0001004] .
        obo:UBERON_0000344 obo:BFO_0000050 obo:UBERON_0001004 .
    """)
    source = await UberonCorroboration.read(client, {"C12468", "C12641", "C12470"})
    verdict, evidence = source.evidence("C12468", "op:AssociatedSite", ("C12641",))
    assert verdict == "supported"
    assert len(evidence) == 1
    assert "NCIT:C12641" in evidence[0].statement
    assert "rdfs:subClassOf" in evidence[0].statement
    assert "owl:someValuesFrom" in evidence[0].statement
    assert source.evidence("C12470", "op:AssociatedSite", ("C12641",)) == (
        "mapped but unresolved",
        (),
    )
    assert source.evidence("C12468", "op:NormalTissueOrigin", ("C12641",)) == (
        "mapped but unresolved",
        (),
    )


async def test_symmetric_conflict_cites_the_asserted_direction():
    client = GraphClient("""
        obo:UBERON_0000062 owl:disjointWith obo:UBERON_0000479 .
    """)
    organ = URIRef("http://purl.obolibrary.org/obo/UBERON_0000062")
    tissue = URIRef("http://purl.obolibrary.org/obo/UBERON_0000479")
    client.graph.remove((tissue, OWL.disjointWith, organ))
    source = await UberonCorroboration.read(client, {"C12470"})
    verdict, evidence = source.evidence("C12470", "op:PrimarySite")
    assert verdict == "contradicted"
    assert f"{organ} owl:disjointWith {tissue}" in evidence[0].statement


@pytest.mark.parametrize(
    "extra",
    [
        "obo:UBERON_0002048 rdfs:subClassOf obo:UBERON_0000064 .",
        "obo:UBERON_0002048 rdfs:subClassOf obo:UBERON_0000475 .",
    ],
)
async def test_mixed_categories_do_not_resolve_routing(extra):
    source = await UberonCorroboration.read(GraphClient(extra), {"C12468"})
    assert source.evidence("C12468", "op:PrimarySite") == ("mapped but unresolved", ())


@pytest.mark.parametrize("malformed", ["binding", "target"])
async def test_malformed_source_is_not_reported_as_missing_mapping(malformed):
    class MalformedSource(GraphClient):
        async def select(self, query):
            if malformed == "binding" and "versionIRI" in query:
                return [{}]
            return await super().select(query)

    extra = (
        'obo:UBERON_invalid oio:hasDbXref "NCIT:C12468" .'
        if malformed == "target"
        else ""
    )
    with pytest.raises(StorageError, match=r"Incomplete|Malformed"):
        await UberonCorroboration.read(MalformedSource(extra), {"C12468"})


async def test_part_of_does_not_choose_an_ambiguous_peer_mapping():
    source = await UberonCorroboration.read(
        GraphClient("""
        obo:UBERON_0001004 oio:hasDbXref "NCIT:C12641" .
        obo:UBERON_0001005 oio:hasDbXref "NCIT:C12641" .
        obo:UBERON_0002048 rdfs:subClassOf [owl:onProperty obo:BFO_0000050;
            owl:someValuesFrom obo:UBERON_0001004] .
    """),
        {"C12468", "C12641"},
    )
    assert source.evidence("C12468", "op:AssociatedSite", ("C12641",)) == (
        "mapped but unresolved",
        (),
    )
