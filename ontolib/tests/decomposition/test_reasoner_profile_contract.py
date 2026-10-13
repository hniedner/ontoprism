"""A profile limitation is reported without claiming complete consistency."""

import pytest
from test_support.robot import require_robot

from ontolib.core.robot import RobotConfig
from ontolib.decomposition.reasoner_check import check_projection

pytestmark = [pytest.mark.integration, pytest.mark.mutating_integration]


def test_real_union_profile_is_not_reported_as_full_consistency(tmp_path):
    require_robot()
    source = tmp_path / "source.ttl"
    source.write_text("""@prefix owl: <http://www.w3.org/2002/07/owl#> .
      @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
      <urn:A> a owl:Class ; rdfs:subClassOf [owl:unionOf (<urn:B> <urn:C>)] .
      <urn:B> a owl:Class . <urn:C> a owl:Class .""")
    report = check_projection(
        source,
        [],
        tmp_path / "check",
        RobotConfig(heap_gib=1, max_element_depth=100, timeout_seconds=60),
    )
    assert report["baseline"]["consistency"] == "not-proved-outside-EL"
    assert report["enhanced"]["consistency"] == "not-proved-outside-EL"
    assert report["el_profile"]["baseline"]["in_profile"] is False
    assert "ObjectUnionOf" in report["el_profile"]["baseline"]["diagnostic"]
