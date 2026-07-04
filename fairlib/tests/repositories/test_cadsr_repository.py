"""CdeRepository against a real temp SQLite DB (no mocks)."""

import pytest

from fairlib.repositories.cadsr.repository import CdeRepository


@pytest.mark.unit
def test_get_cde_returns_detail_with_concepts_and_pvs(cadsr_db_path) -> None:
    repo = CdeRepository(cadsr_db_path)
    cde = repo.get_cde("100", "2.0")

    assert cde is not None
    assert cde.short_name == "NEOPLASM_HIST"
    assert cde.datatype == "CHARACTER"
    assert [pv.value for pv in cde.permissible_values] == ["Carcinoma"]
    codes = {c.concept_code for c in cde.concepts}
    assert {"C3262", "C16358"} <= codes
    assert any(c.is_primary and c.concept_code == "C3262" for c in cde.concepts)


@pytest.mark.unit
def test_get_cde_defaults_to_latest_version(cadsr_db_path) -> None:
    repo = CdeRepository(cadsr_db_path)
    cde = repo.get_cde("100")
    assert cde is not None
    assert cde.version == "2.0"


@pytest.mark.unit
def test_get_cde_unknown_returns_none(cadsr_db_path) -> None:
    assert CdeRepository(cadsr_db_path).get_cde("999999") is None


@pytest.mark.unit
def test_search_matches_name(cadsr_db_path) -> None:
    page = CdeRepository(cadsr_db_path).search("neoplasm")
    assert page.total == 1
    assert page.hits[0].public_id == "100"


@pytest.mark.unit
def test_find_cdes_by_concept_is_the_ncit_join(cadsr_db_path) -> None:
    hits = CdeRepository(cadsr_db_path).find_cdes_by_concept("C3262")
    assert [h.public_id for h in hits] == ["100"]


@pytest.mark.unit
def test_count_and_summaries_for(cadsr_db_path) -> None:
    repo = CdeRepository(cadsr_db_path)
    assert repo.count() == 2
    summaries = repo.summaries_for(["100:2.0", "2003771:1.0", "999:9"])
    assert set(summaries) == {"100:2.0", "2003771:1.0"}  # unknown doc_id dropped
    assert summaries["100:2.0"].short_name == "NEOPLASM_HIST"
