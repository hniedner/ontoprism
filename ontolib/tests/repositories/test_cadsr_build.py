"""Tests for the caDSR XML→SQLite builder (round-tripped through the read model)."""

from __future__ import annotations

import sqlite3
import zipfile
from contextlib import closing, contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING
from xml.etree.ElementTree import Element, SubElement

import pytest

from ontolib.core.download_cache import CacheManifest, DownloadOutcome
from ontolib.core.exceptions import StorageError
from ontolib.repositories.cadsr.archive import (
    CadsrSource,
    ExtractedCadsrArchive,
    extract_cadsr_archive,
)
from ontolib.repositories.cadsr.build import (
    ValidatedCadsrCandidate,
    _Concept,
    _dedupe,
    _pv_concepts,
    _structured_concepts,
    build_database,
    iter_cdes,
    parse_cde,
    validate_database,
)
from ontolib.repositories.cadsr.repository import CdeRepository

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
    from types import TracebackType

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

_SOURCE = CadsrSource(
    url="https://example.test/cadsr.zip",
    downloaded_at="2026-07-26T00:00:00+00:00",
    etag='"source-v1"',
    last_modified="Thu, 02 Jul 2026 02:19:40 GMT",
    archive_size=1234,
    archive_sha256="a" * 64,
    member_count=1,
    member_names_sha256="b" * 64,
    first_member_timestamp="2026-07-01T12:00:00",
    last_member_timestamp="2026-07-01T12:00:00",
)


@contextmanager
def _extracted(tmp_path: Path, members: list[str]) -> Iterator[ExtractedCadsrArchive]:
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        for sequence, xml in enumerate(members, start=1):
            stream.writestr(
                f"cde_xml_20260701120000_{sequence}.xml",
                xml,
            )
    outcome = DownloadOutcome(
        path=str(archive),
        status="downloaded",
        manifest=CacheManifest(
            url=_SOURCE.url,
            downloaded_at=_SOURCE.downloaded_at,
            size_bytes=archive.stat().st_size,
            etag=_SOURCE.etag,
            last_modified=_SOURCE.last_modified,
        ),
    )
    with extract_cadsr_archive(
        outcome,
        expected_url=_SOURCE.url,
        workspace_parent=tmp_path / "workspaces",
    ) as extracted:
        yield extracted


@pytest.fixture
def built_candidate(tmp_path: Path) -> ValidatedCadsrCandidate:
    db = tmp_path / "cde_repository.db"
    with _extracted(tmp_path, [_XML]) as extracted:
        candidate = build_database(extracted, db)
    assert candidate.cde_count == 2
    return candidate


@pytest.fixture
def built_db(built_candidate: ValidatedCadsrCandidate) -> Path:
    return built_candidate.path


def _database_source(db_path: Path) -> CadsrSource:
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM cadsr_source").fetchone()
    assert row is not None
    return CadsrSource(*row)


@pytest.mark.unit
def test_built_db_contains_exact_source_provenance(
    built_candidate: ValidatedCadsrCandidate,
) -> None:
    built_db = built_candidate.path
    with closing(sqlite3.connect(built_db)) as conn:
        row = conn.execute("SELECT * FROM cadsr_source").fetchone()

    source = built_candidate.source
    assert row == (
        source.url,
        source.downloaded_at,
        source.etag,
        source.last_modified,
        source.archive_size,
        source.archive_sha256,
        source.member_count,
        source.member_names_sha256,
        source.first_member_timestamp,
        source.last_member_timestamp,
    )
    forged = built_db.with_name("unvalidated.db")
    forged.write_bytes(b"not sqlite")
    with pytest.raises(TypeError, match="InitVar '_seal'"):
        replace(built_candidate, path=forged)


@pytest.mark.unit
def test_build_rejects_an_xml_member_without_usable_cdes(tmp_path: Path) -> None:
    with (
        _extracted(
            tmp_path, ["<DataElementsList><DataElement/></DataElementsList>"]
        ) as extracted,
        pytest.raises(StorageError, match="contains no usable CDEs"),
    ):
        build_database(extracted, tmp_path / "candidate.db")


@pytest.mark.unit
def test_build_rejects_empty_member_after_a_valid_member(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.db"

    with (
        _extracted(
            tmp_path,
            [_XML, "<DataElementsList><DataElement/></DataElementsList>"],
        ) as extracted,
        pytest.raises(
            StorageError,
            match=r"contains no usable CDEs: cde_xml_20260701120000_2\.xml",
        ),
    ):
        build_database(extracted, candidate)

    assert not candidate.exists()


@pytest.mark.unit
def test_build_orders_members_by_sequence_and_counts_final_unique_cdes(
    tmp_path: Path,
) -> None:
    def member(long_name: str) -> str:
        return (
            "<DataElementsList><DataElement><PUBLICID>100</PUBLICID>"
            f"<VERSION>1</VERSION><LONGNAME>{long_name}</LONGNAME>"
            "</DataElement></DataElementsList>"
        )

    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("cde_xml_20260701120000_2.xml", member("Second"))
        stream.writestr("cde_xml_20260701120000_1.xml", member("First"))
    outcome = DownloadOutcome(
        path=str(archive),
        status="downloaded",
        manifest=CacheManifest(
            url=_SOURCE.url,
            downloaded_at=_SOURCE.downloaded_at,
            size_bytes=archive.stat().st_size,
        ),
    )

    with extract_cadsr_archive(
        outcome, expected_url=_SOURCE.url, workspace_parent=tmp_path / "workspaces"
    ) as extracted:
        candidate = build_database(extracted, tmp_path / "candidate.db")

    assert candidate.cde_count == 1
    detail = CdeRepository(candidate.path).get_cde("100", "1")
    assert detail is not None
    assert detail.long_name == "Second"


@pytest.mark.unit
def test_candidate_validation_accepts_complete_standalone_database(
    built_db: Path,
) -> None:
    validate_database(
        built_db, expected_source=_database_source(built_db), expected_cde_count=2
    )


@pytest.mark.unit
def test_candidate_validation_rejects_missing_required_index(built_db: Path) -> None:
    with closing(sqlite3.connect(built_db)) as conn:
        conn.execute("DROP INDEX idx_concept_code")
        conn.commit()

    with pytest.raises(StorageError, match="missing required SQLite objects"):
        validate_database(
            built_db, expected_source=_database_source(built_db), expected_cde_count=2
        )


@pytest.mark.unit
def test_candidate_validation_rejects_wrong_same_named_index(built_db: Path) -> None:
    with closing(sqlite3.connect(built_db)) as conn:
        conn.execute("DROP INDEX idx_concept_code")
        conn.execute("CREATE INDEX idx_concept_code ON cde_concepts(public_id)")
        conn.commit()

    with pytest.raises(StorageError, match="index definition"):
        validate_database(
            built_db, expected_source=_database_source(built_db), expected_cde_count=2
        )


@pytest.mark.unit
def test_candidate_validation_rejects_wrong_same_named_table(built_db: Path) -> None:
    with closing(sqlite3.connect(built_db)) as conn:
        conn.execute("DROP TABLE cde_concepts")
        conn.execute("CREATE TABLE cde_concepts (concept_code TEXT, public_id TEXT)")
        conn.execute("CREATE INDEX idx_concept_code ON cde_concepts(concept_code)")
        conn.commit()

    with pytest.raises(StorageError, match="schema definition"):
        validate_database(
            built_db, expected_source=_database_source(built_db), expected_cde_count=2
        )


@pytest.mark.unit
def test_candidate_validation_rejects_missing_foreign_key_definition(
    built_db: Path,
) -> None:
    with closing(sqlite3.connect(built_db)) as conn:
        conn.execute("DROP TABLE cde_concepts")
        conn.execute(
            "CREATE TABLE cde_concepts ("
            "concept_code TEXT NOT NULL, concept_name TEXT NOT NULL, "
            "public_id TEXT NOT NULL, version TEXT NOT NULL, concept_type TEXT, "
            "is_primary INTEGER)"
        )
        conn.execute("CREATE INDEX idx_concept_code ON cde_concepts(concept_code)")
        conn.commit()

    with pytest.raises(StorageError, match="foreign key definition"):
        validate_database(
            built_db, expected_source=_database_source(built_db), expected_cde_count=2
        )


@pytest.mark.unit
def test_candidate_validation_rejects_wrong_record_count(built_db: Path) -> None:
    with pytest.raises(StorageError, match="CDE count does not match"):
        validate_database(
            built_db, expected_source=_database_source(built_db), expected_cde_count=3
        )


@pytest.mark.unit
def test_candidate_validation_rejects_foreign_key_violation(built_db: Path) -> None:
    with closing(sqlite3.connect(built_db)) as conn:
        conn.execute(
            "INSERT INTO cde_concepts "
            "(concept_code, concept_name, public_id, version) "
            "VALUES ('C1', 'orphan', 'missing', '1')"
        )
        conn.commit()

    with pytest.raises(StorageError, match="foreign key check failed"):
        validate_database(
            built_db, expected_source=_database_source(built_db), expected_cde_count=2
        )


@pytest.mark.unit
def test_candidate_validation_rejects_source_provenance_mismatch(
    built_db: Path,
) -> None:
    changed_source = replace(_database_source(built_db), archive_sha256="c" * 64)

    with pytest.raises(StorageError, match="source provenance does not match"):
        validate_database(
            built_db, expected_source=changed_source, expected_cde_count=2
        )


@pytest.mark.unit
def test_candidate_validation_rejects_fts_content_drift(built_db: Path) -> None:
    with closing(sqlite3.connect(built_db)) as conn:
        conn.execute(
            "UPDATE cdes SET short_name = 'changed after FTS build', "
            "cde_json = json_set(cde_json, '$.short_name', "
            "'changed after FTS build') "
            "WHERE public_id = '100'"
        )
        conn.commit()

    with pytest.raises(StorageError, match="FTS5 content"):
        validate_database(
            built_db, expected_source=_database_source(built_db), expected_cde_count=2
        )


@pytest.mark.unit
def test_candidate_validation_rejects_ordinary_table_named_like_fts(
    built_db: Path,
) -> None:
    with closing(sqlite3.connect(built_db)) as conn:
        conn.execute("DROP TABLE cdes_fts")
        conn.execute("CREATE TABLE cdes_fts (cdes_fts TEXT, rank INTEGER)")
        conn.commit()

    with pytest.raises(StorageError, match="FTS5 definition"):
        validate_database(
            built_db, expected_source=_database_source(built_db), expected_cde_count=2
        )


@pytest.mark.unit
def test_candidate_validation_rejects_empty_fts_index_with_external_content(
    built_db: Path,
) -> None:
    with closing(sqlite3.connect(built_db)) as conn:
        conn.execute("DROP TABLE cdes_fts")
        conn.execute(
            "CREATE VIRTUAL TABLE cdes_fts USING fts5("
            "public_id UNINDEXED, version UNINDEXED, short_name, long_name, "
            "definition, search_text, content='cdes', content_rowid='rowid')"
        )
        conn.commit()

    with pytest.raises(StorageError, match="FTS5 content"):
        validate_database(
            built_db, expected_source=_database_source(built_db), expected_cde_count=2
        )


@pytest.mark.unit
def test_candidate_validation_rejects_invalid_cde_json(built_db: Path) -> None:
    with closing(sqlite3.connect(built_db)) as conn:
        conn.execute("UPDATE cdes SET cde_json = 'not JSON' WHERE public_id = '100'")
        conn.commit()

    with pytest.raises(StorageError, match="invalid CDE row content"):
        validate_database(
            built_db, expected_source=_database_source(built_db), expected_cde_count=2
        )


@pytest.mark.unit
def test_candidate_validation_rejects_divergent_nullable_json_field(
    built_db: Path,
) -> None:
    with closing(sqlite3.connect(built_db)) as conn:
        conn.execute(
            "UPDATE cdes SET cde_json = json_set(cde_json, '$.context', 'CTEP') "
            "WHERE public_id = '100'"
        )
        conn.commit()

    with pytest.raises(StorageError, match="invalid CDE row content"):
        validate_database(
            built_db, expected_source=_database_source(built_db), expected_cde_count=2
        )


@pytest.mark.unit
def test_candidate_validation_rejects_invalid_permissible_value_json(
    built_db: Path,
) -> None:
    with closing(sqlite3.connect(built_db)) as conn:
        conn.execute(
            "UPDATE cdes SET cde_json = json_set("
            "cde_json, '$.permissible_values[0].value', 7) "
            "WHERE public_id = '100'"
        )
        conn.commit()

    with pytest.raises(StorageError, match="invalid CDE row content"):
        validate_database(
            built_db, expected_source=_database_source(built_db), expected_cde_count=2
        )


@pytest.mark.unit
def test_candidate_validation_rejects_null_concept_primary_flag(
    built_db: Path,
) -> None:
    with closing(sqlite3.connect(built_db)) as conn:
        conn.execute(
            "UPDATE cde_concepts SET is_primary = NULL WHERE concept_code = 'C3262'"
        )
        conn.commit()

    with pytest.raises(StorageError, match="invalid CDE row content"):
        validate_database(
            built_db, expected_source=_database_source(built_db), expected_cde_count=2
        )


@pytest.mark.unit
def test_candidate_validation_rejects_wal_journal_mode(built_db: Path) -> None:
    with closing(sqlite3.connect(built_db)) as conn:
        assert conn.execute("PRAGMA journal_mode = WAL").fetchone() == ("wal",)

    with pytest.raises(StorageError, match="not in DELETE journal mode"):
        validate_database(
            built_db, expected_source=_database_source(built_db), expected_cde_count=2
        )


@pytest.mark.unit
def test_candidate_validation_rejects_corrupt_sqlite(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.db"
    candidate.write_bytes(b"not sqlite")

    with pytest.raises(StorageError, match="invalid caDSR candidate database"):
        validate_database(candidate, expected_source=_SOURCE, expected_cde_count=2)


@pytest.mark.unit
def test_parse_cde_extracts_fields_concepts_and_pv() -> None:
    root = fromstring(_XML)
    element = root.find("DataElement")
    assert element is not None
    parsed = parse_cde(element)
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
def test_iter_cdes_ignores_other_elements(tmp_path: Path) -> None:
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
def test_iter_cdes_clears_processed_root_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Element("DataElementsList")
    first = SubElement(root, "DataElement")
    SubElement(first, "PUBLICID").text = "1"
    SubElement(first, "VERSION").text = "1"
    second = SubElement(root, "DataElement")
    SubElement(second, "PUBLICID").text = "2"
    SubElement(second, "VERSION").text = "1"

    def streaming_events(_path: str, *, events: tuple[str, ...]):
        assert events == ("start", "end")
        return iter(
            (
                ("start", root),
                ("start", first),
                ("end", first),
                ("start", second),
                ("end", second),
                ("end", root),
            )
        )

    monkeypatch.setattr("ontolib.repositories.cadsr.build.iterparse", streaming_events)

    public_ids = [
        cde.cde_json["public_id"] for cde in iter_cdes(tmp_path / "unused.xml")
    ]
    assert public_ids == [
        "1",
        "2",
    ]
    assert len(root) == 0


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
def test_iter_cdes_accepts_minimal_cde(tmp_path: Path) -> None:
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
def test_iter_cdes_propagates_bad_element_exception(
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
    with pytest.raises(ValueError, match="broken"):
        list(iter_cdes(xml))


@pytest.mark.unit
def test_build_error_remains_primary_when_sqlite_close_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_connect = sqlite3.connect

    class CloseFailingConnection:
        def __init__(self, path: Path) -> None:
            self._connection = real_connect(path)

        def __getattr__(self, name: str) -> object:
            return getattr(self._connection, name)

        def __enter__(self) -> CloseFailingConnection:
            self._connection.__enter__()
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> bool | None:
            return self._connection.__exit__(exc_type, exc, traceback)

        def close(self) -> None:
            self._connection.close()
            raise sqlite3.OperationalError("injected close failure")

    monkeypatch.setattr(
        "ontolib.repositories.cadsr.build.sqlite3.connect", CloseFailingConnection
    )

    with (
        _extracted(
            tmp_path, ["<DataElementsList><DataElement/></DataElementsList>"]
        ) as extracted,
        pytest.raises(StorageError, match="contains no usable CDEs") as captured,
    ):
        build_database(extracted, tmp_path / "candidate.db")

    assert any("injected close failure" in note for note in captured.value.__notes__)


@pytest.mark.unit
def test_validation_error_remains_primary_when_sqlite_close_fails(
    built_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _database_source(built_db)
    with closing(sqlite3.connect(built_db)) as conn:
        conn.execute(
            "UPDATE cdes SET cde_json = 'invalid JSON' WHERE public_id = '100'"
        )
        conn.commit()
    real_connect = sqlite3.connect

    class CloseFailingConnection:
        def __init__(self, path: Path) -> None:
            self._connection = real_connect(path)

        def __getattr__(self, name: str) -> object:
            return getattr(self._connection, name)

        def close(self) -> None:
            self._connection.close()
            raise sqlite3.OperationalError("injected validation close failure")

    monkeypatch.setattr(
        "ontolib.repositories.cadsr.build.sqlite3.connect", CloseFailingConnection
    )

    with pytest.raises(StorageError, match="invalid CDE row content") as captured:
        validate_database(built_db, expected_source=source, expected_cde_count=2)

    assert any(
        "injected validation close failure" in note for note in captured.value.__notes__
    )
