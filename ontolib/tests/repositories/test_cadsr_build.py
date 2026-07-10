"""Tests for the caDSR XML→SQLite builder (round-tripped through the read model)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ontolib.repositories.cadsr.build import (
    _Concept,
    _dedupe,
    _pv_concepts,
    _structured_concepts,
    build_database,
    iter_cdes,
    parse_cde,
)
from ontolib.repositories.cadsr.repository import CdeRepository

if TYPE_CHECKING:
    from pathlib import Path

from defusedxml.ElementTree import fromstring

# A minimal releasedCDEs-shaped document: two DataElements exercising the DEC object
# class / property concepts, the value-domain datatype, and a permissible value whose
# meaning carries an NCIt code.
_XML = """<DataElementsList>
  <DataElement>
    <PUBLICID>100</PUBLICID>
    <VERSION>2.0</VERSION>
    <PREFERREDNAME>NEOPLASM_HIST</PREFERREDNAME>
    <LONGNAME>Neoplasm Histology</LONGNAME>
    <PREFERREDDEFINITION>The histology of a neoplasm.</PREFERREDDEFINITION>
    <WORKFLOWSTATUS>RELEASED</WORKFLOWSTATUS>
    <REGISTRATIONSTATUS>Standard</REGISTRATIONSTATUS>
    <CONTEXTNAME>NCIP</CONTEXTNAME>
    <DATAELEMENTCONCEPT>
      <LongName>Neoplasm Histology</LongName>
      <PreferredDefinition>DEC definition.</PreferredDefinition>
      <ObjectClass>
        <ConceptDetails>
          <ConceptDetails_ITEM>
            <PREFERRED_NAME>C3262</PREFERRED_NAME>
            <LONG_NAME>Neoplasm</LONG_NAME>
            <PRIMARY_FLAG_IND>Yes</PRIMARY_FLAG_IND>
          </ConceptDetails_ITEM>
        </ConceptDetails>
      </ObjectClass>
      <Property>
        <ConceptDetails>
          <ConceptDetails_ITEM>
            <PREFERRED_NAME>C16358</PREFERRED_NAME>
            <LONG_NAME>Histology</LONG_NAME>
            <PRIMARY_FLAG_IND>No</PRIMARY_FLAG_IND>
          </ConceptDetails_ITEM>
        </ConceptDetails>
      </Property>
    </DATAELEMENTCONCEPT>
    <VALUEDOMAIN>
      <Datatype>CHARACTER</Datatype>
      <ValueDomainType>Enumerated</ValueDomainType>
      <LongName>Histology VD</LongName>
      <PermissibleValues>
        <PermissibleValues_ITEM>
          <VALIDVALUE>Carcinoma</VALIDVALUE>
          <VALUEMEANING>Carcinoma</VALUEMEANING>
          <MEANINGCONCEPTS>C2916</MEANINGCONCEPTS>
          <MEANINGCONCEPTDISPLAYORDER>0</MEANINGCONCEPTDISPLAYORDER>
        </PermissibleValues_ITEM>
      </PermissibleValues>
    </VALUEDOMAIN>
  </DataElement>
  <DataElement>
    <PUBLICID>200</PUBLICID>
    <VERSION>1.0</VERSION>
    <PREFERREDNAME>PT_AGE</PREFERREDNAME>
    <LONGNAME>Patient Age</LONGNAME>
    <PREFERREDDEFINITION>Age of the patient.</PREFERREDDEFINITION>
    <CONTEXTNAME>CTEP</CONTEXTNAME>
    <DATAELEMENTCONCEPT>
      <ObjectClass>
        <ConceptDetails>
          <ConceptDetails_ITEM>
            <PREFERRED_NAME>C25150</PREFERRED_NAME>
            <LONG_NAME>Age</LONG_NAME>
            <PRIMARY_FLAG_IND>Yes</PRIMARY_FLAG_IND>
          </ConceptDetails_ITEM>
        </ConceptDetails>
      </ObjectClass>
    </DATAELEMENTCONCEPT>
    <VALUEDOMAIN><Datatype>NUMBER</Datatype></VALUEDOMAIN>
  </DataElement>
</DataElementsList>"""


@pytest.fixture
def built_db(tmp_path: Path) -> Path:
    xml = tmp_path / "cdes.xml"
    xml.write_text(_XML)
    db = tmp_path / "cde_repository.db"
    count = build_database([xml], db)
    assert count == 2
    return db


@pytest.mark.unit
def test_parse_cde_extracts_fields_concepts_and_pv() -> None:
    root = fromstring(_XML)
    parsed = parse_cde(root.find("DataElement"))
    assert parsed is not None
    assert parsed.cde_json["public_id"] == "100"
    assert parsed.cde_json["datatype"] == "CHARACTER"
    assert parsed.cde_json["value_domain_type"] == "Enumerated"
    assert parsed.cde_json["permissible_values"][0] == {
        "value": "Carcinoma",
        "meaning": "Carcinoma",
        "meaning_code": "C2916",
    }
    by_code = {c.code: c for c in parsed.concepts}
    assert by_code["C3262"].concept_type == "object_class"
    assert by_code["C3262"].is_primary is True
    assert by_code["C16358"].concept_type == "property"
    assert by_code["C2916"].concept_type == "value_meaning"
    # search_text folds names/PVs but excludes short/long/definition and value meanings.
    assert "Neoplasm" in parsed.search_text
    assert "Carcinoma" in parsed.search_text


@pytest.mark.unit
def test_parse_cde_missing_id_returns_none() -> None:
    root = fromstring("<DataElement><PUBLICID/><VERSION/></DataElement>")
    assert parse_cde(root) is None


@pytest.mark.unit
def test_iter_cdes_skips_unparseable_and_clears_memory(tmp_path: Path) -> None:
    bad = tmp_path / "bad.xml"
    bad.write_text(
        "<DataElementsList>"
        "<DataElement><PUBLICID>1</PUBLICID><VERSION>1</VERSION></DataElement>"
        "<NotDataElement>ignored</NotDataElement>"
        "</DataElementsList>"
    )
    results = list(iter_cdes(bad))
    assert len(results) == 1
    assert results[0].cde_json["public_id"] == "1"
    assert results[0].cde_json["version"] == "1"


@pytest.mark.unit
def test_built_db_round_trips_through_read_model(built_db: Path) -> None:
    repo = CdeRepository(built_db)
    cde = repo.get_cde("100")
    assert cde is not None
    assert cde.long_name == "Neoplasm Histology"
    assert cde.datatype == "CHARACTER"
    assert cde.permissible_values[0].value == "Carcinoma"
    assert cde.permissible_values[0].meaning_code == "C2916"
    codes = {c.concept_code for c in cde.concepts}
    assert {"C3262", "C16358", "C2916"} <= codes


@pytest.mark.unit
def test_built_db_fts_search_and_concept_join(built_db: Path) -> None:
    repo = CdeRepository(built_db)
    # FTS index (built via 'rebuild') powers search.
    page = repo.search("neoplasm")
    assert [h.public_id for h in page.hits] == ["100"]
    # The caDSR↔NCIt concept join works off cde_concepts.
    joined = repo.find_cdes_by_concept("C3262")
    assert [c.public_id for c in joined] == ["100"]


@pytest.mark.unit
def test_structured_concepts_empty_details() -> None:
    xml = b"<ObjectClass><ConceptDetails/></ObjectClass>"
    elem = fromstring(xml)
    assert _structured_concepts(elem, "object_class") == []


@pytest.mark.unit
def test_structured_concepts_missing_details_node() -> None:
    xml = b"<ObjectClass/>"
    elem = fromstring(xml)
    assert _structured_concepts(elem, "object_class") == []


@pytest.mark.unit
def test_structured_concepts_none_entity() -> None:
    assert _structured_concepts(None, "object_class") == []


@pytest.mark.unit
def test_structured_concepts_skip_invalid_code() -> None:
    xml = b"""<ObjectClass>
      <ConceptDetails>
        <ConceptDetails_ITEM>
          <PREFERRED_NAME>Bad Code!</PREFERRED_NAME>
          <LONG_NAME>Bad</LONG_NAME>
        </ConceptDetails_ITEM>
      </ConceptDetails>
    </ObjectClass>"""
    elem = fromstring(xml)
    assert _structured_concepts(elem, "object_class") == []


@pytest.mark.unit
def test_pv_concepts_empty_meaning_concepts() -> None:
    xml = b"""<PermissibleValues_ITEM>
      <VALUEMEANING>Some Meaning</VALUEMEANING>
    </PermissibleValues_ITEM>"""
    elem = fromstring(xml)
    assert _pv_concepts(elem) == []


@pytest.mark.unit
def test_pv_concepts_skip_non_ncit_code() -> None:
    xml = b"""<PermissibleValues_ITEM>
      <VALIDVALUE>X</VALIDVALUE>
      <VALUEMEANING>Unknown</VALUEMEANING>
      <MEANINGCONCEPTS>X9999</MEANINGCONCEPTS>
    </PermissibleValues_ITEM>"""
    elem = fromstring(xml)
    assert _pv_concepts(elem) == []


@pytest.mark.unit
def test_dedupe_keeps_first_occurrence() -> None:
    dupes = [
        _Concept("C1", "First", "object_class", is_primary=True),
        _Concept("C1", "Second", "property", is_primary=False),
    ]
    result = _dedupe(dupes)
    assert len(result) == 1
    assert result[0].name == "First"


@pytest.mark.unit
def test_iter_cdes_handles_parse_failure(tmp_path: Path) -> None:
    xml = tmp_path / "bad.xml"
    xml.write_text(
        "<DataElementsList>"
        "<DataElement><PUBLICID>1</PUBLICID><VERSION>1</VERSION></DataElement>"
        "</DataElementsList>"
    )
    results = list(iter_cdes(xml))
    assert len(results) == 1  # CDE 1 parses fine with minimal fields
    assert results[0].cde_json["public_id"] == "1"


@pytest.mark.unit
def test_iter_cdes_skips_element_when_parse_cde_returns_none(tmp_path: Path) -> None:
    xml = tmp_path / "no_id.xml"
    xml.write_text(
        "<DataElementsList>"
        "<DataElement><PUBLICID/><VERSION/></DataElement>"
        "<DataElement><PUBLICID>1</PUBLICID><VERSION>1</VERSION></DataElement>"
        "</DataElementsList>"
    )
    results = list(iter_cdes(xml))
    assert len(results) == 1
    assert results[0].cde_json["public_id"] == "1"


@pytest.mark.unit
def test_iter_cdes_exception_skips_bad_element(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _broken(*args: object, **kwargs: object) -> object:
        raise ValueError("broken")

    monkeypatch.setattr("ontolib.repositories.cadsr.build._collect_concepts", _broken)
    xml = tmp_path / "broken.xml"
    xml.write_text(
        "<DataElementsList>"
        "<DataElement><PUBLICID>1</PUBLICID><VERSION>1</VERSION></DataElement>"
        "</DataElementsList>"
    )
    results = list(iter_cdes(xml))
    assert len(results) == 0  # The only CDE failed to parse
