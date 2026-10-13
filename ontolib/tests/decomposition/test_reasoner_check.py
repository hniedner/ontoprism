"""Real ELK checks of additive subclass restrictions, not equivalent definitions."""

import pytest
from test_support.robot import require_robot

from ontolib.core.robot import RobotConfig
from ontolib.decomposition.reasoner_check import check_projection

pytestmark = [pytest.mark.integration, pytest.mark.mutating_integration]


def test_real_elk_reports_new_entailment_and_conflict(tmp_path):
    require_robot()
    stated = tmp_path / "stated.ttl"
    stated.write_text("""
      @prefix n: <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#> .
      @prefix owl: <http://www.w3.org/2002/07/owl#> .
      @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
      n:C1 a owl:Class . n:C2 a owl:Class . n:C3 a owl:Class .
      n:C4 a owl:Class . n:C5 a owl:Class . n:C6 a owl:Class .
      n:R101 a owl:ObjectProperty ; rdfs:domain n:C2 ; rdfs:range n:C3 .
      n:C4 owl:disjointWith n:C3 .
      n:C5 owl:equivalentClass [a owl:Restriction; owl:onProperty n:R101;
                               owl:someValuesFrom n:C3] .
    """)
    good = [
        {
            "concept_code": "C1",
            "axis": "op:PrimarySite",
            "filler_code": "C3",
            "source_roles": ["R101"],
        }
    ]
    config = RobotConfig(heap_gib=1, max_element_depth=256, timeout_seconds=60)
    report = check_projection(stated, good, tmp_path / "good", config)
    assert report["baseline"]["unsatisfiable"] == []
    assert report["enhanced"]["unsatisfiable"] == []
    assert ["C1", "C2"] in report["new_subsumptions"]
    assert ["C1", "C5"] in report["new_subsumptions"]
    assert ["C5", "C1"] not in report["new_subsumptions"]
    assert report["lost_subsumptions"] == []
    assert report["projected_properties"] == {"source_role": 1, "op_axis_fallback": 0}
    bad = [{**good[0], "filler_code": "C4"}]
    conflict = check_projection(stated, good + bad, tmp_path / "conflict", config)
    assert conflict["new_unsatisfiable"] == ["C1"]
    assert conflict["contradictory_assertions"] == bad
    assert conflict["enhanced"]["consistency"] == "consistent"


def test_joint_conflict_is_reported_as_unattributed(tmp_path):
    require_robot()
    stated = tmp_path / "stated.ttl"
    stated.write_text("""
      @prefix n: <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#> .
      @prefix owl: <http://www.w3.org/2002/07/owl#> .
      @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
      n:C1 a owl:Class . n:C2 a owl:Class . n:C3 a owl:Class .
      n:C4 a owl:Class . n:C5 a owl:Class ; rdfs:subClassOf n:C1 .
      n:C2 owl:disjointWith n:C3 .
      n:R1 a owl:ObjectProperty ; rdfs:domain n:C2 .
      n:R2 a owl:ObjectProperty ; rdfs:domain n:C3 .
    """)
    rows = [
        {
            "concept_code": "C1",
            "axis": "op:PrimarySite",
            "filler_code": "C4",
            "source_roles": [role],
        }
        for role in ("R1", "R2")
    ]
    report = check_projection(
        stated,
        rows,
        tmp_path / "check",
        RobotConfig(heap_gib=1, max_element_depth=256, timeout_seconds=60),
    )
    assert report["new_unsatisfiable"] == ["C1", "C5"]
    assert report["contradictory_assertions"] == []
    assert report["unattributed_unsatisfiable"] == ["C1", "C5"]
