"""The pinned publisher release agrees with the parser's external RDF contract."""

import pytest

from ontolib.terminologies.mondo import BASIS, VERSION, published_mondo

pytestmark = [pytest.mark.integration, pytest.mark.full_store]


def test_pinned_mondo_published_hierarchy_and_qualified_xrefs():
    source = published_mondo()
    verdict, items = source.evidence("C118172", "C34588")
    assert verdict == "supported"
    assert items[0].source_release == VERSION
    assert BASIS in items[0].statement
    assert "MONDO_0000022 rdfs:subClassOf" in items[0].statement
    assert "MONDO_0024290" in items[0].statement
    assert source.evidence("C34588", "C118172") == ("disagreement", ())
    assert source.evidence("C4099", "C34588") == ("unmapped", ())
    assert source.evidence("C3915", "C34588") == ("multi-mapped", ())
    assert source.exclusions == {
        "malformed": 1,
        "non-equivalence": 128,
        "deprecated": 56,
    }
