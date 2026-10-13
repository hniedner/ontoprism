"""Tier-one verdicts against the installed, version-pinned Uberon source."""

import pytest

from backend.config import get_settings
from ontolib.terminologies.sparql_http_client import SparqlHttpClient
from ontolib.terminologies.uberon.corroboration import UberonCorroboration

pytestmark = [pytest.mark.integration, pytest.mark.full_store]


async def test_real_uberon_unique_xrefs_and_explicit_disjointness():
    async with SparqlHttpClient.for_qlever(get_settings().uberon_sparql_url) as client:
        source = await UberonCorroboration.read(
            client, {"C12468", "C32574", "C999999999"}
        )
    assert source.release == (
        "http://purl.obolibrary.org/obo/uberon/releases/2026-06-19/uberon.owl"
    )
    lung, lung_items = source.evidence("C12468", "op:PrimarySite")
    # Same externally observable verdict as the explicit RDF fixture in the unit lane.
    assert lung == "supported"
    assert "NCIT:C12468" in lung_items[0].statement
    assert "UBERON_0000171" in lung_items[0].statement
    verdict, items = source.evidence("C32574", "op:PrimarySite")
    assert verdict == "contradicted"
    assert "UBERON_0034921" in items[0].statement
    assert "owl:disjointWith" in items[0].statement
    assert source.evidence("C999999999", "op:PrimarySite") == ("no mapping", ())
