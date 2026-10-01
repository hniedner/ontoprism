"""CdeRepository against a real temp SQLite DB (no mocks)."""

import sqlite3

import pytest

from ontolib.repositories.cadsr.repository import CdeRepository


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
def test_non_fts_search_applies_category_and_text_before_total_and_pagination(
    cadsr_db_path,
) -> None:
    page = CdeRepository(cadsr_db_path).search(
        "Definition",
        limit=1,
        filters={"context": ["caDSR"]},
        column_text={"name": "neoplasm"},
    )

    assert page.total == 1
    assert [hit.public_id for hit in page.hits] == ["100"]
    assert page.filters == {"context": ["caDSR"]}
    assert page.column_text == {"name": "neoplasm"}


@pytest.mark.unit
def test_find_cdes_by_concept_is_the_ncit_join(cadsr_db_path) -> None:
    hits = CdeRepository(cadsr_db_path).find_cdes_by_concept("C3262")
    assert [h.public_id for h in hits] == ["100"]


@pytest.mark.unit
def test_find_cde_ids_by_concept_is_bounded(cadsr_db_path) -> None:
    ids = CdeRepository(cadsr_db_path).find_cde_ids_by_concept("C3262", limit=1)
    assert ids == ["100:2.0"]


@pytest.mark.unit
def test_list_cdes_browses_in_natural_numeric_order(cadsr_db_path) -> None:
    # No search term: list every CDE ordered by numeric public_id (100 < 2003771).
    page = CdeRepository(cadsr_db_path).list_cdes()
    assert page.query == ""
    assert page.total == 2
    assert [h.public_id for h in page.hits] == ["100", "2003771"]


@pytest.mark.unit
def test_list_cdes_paginates(cadsr_db_path) -> None:
    repo = CdeRepository(cadsr_db_path)
    first = repo.list_cdes(limit=1, offset=0)
    second = repo.list_cdes(limit=1, offset=1)
    assert first.total == 2
    assert second.total == 2
    assert [h.public_id for h in first.hits] == ["100"]
    assert [h.public_id for h in second.hits] == ["2003771"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("value_domain_type", "Enumerated"),
        ("workflow_status", "RELEASED"),
        ("registration_status", "Standard"),
        ("context", "caDSR"),
        ("datatype", "CHARACTER"),
    ],
)
def test_each_closed_domain_filter_applies_before_pagination_and_total(
    cadsr_db_path, field: str, value: str
) -> None:
    with sqlite3.connect(cadsr_db_path) as connection:
        connection.execute(
            "UPDATE cdes SET workflow_status = 'DRAFT NEW', "
            "registration_status = 'Superceded' WHERE public_id = '2003771'"
        )
    page = CdeRepository(cadsr_db_path).list_cdes(limit=1, filters={field: [value]})

    assert page.total == 1
    assert [hit.public_id for hit in page.hits] == ["100"]
    assert page.filters == {field: [value]}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("public_id", "100"),
        ("public_id", "v2.0"),
        ("name", "neoplasm hist"),
        ("value_domain_type", "enumer"),
        ("workflow_status", "release"),
        ("registration_status", "stand"),
        ("context", "cads"),
        ("datatype", "char"),
    ],
)
def test_each_displayed_filter_text_matches_before_pagination_and_total(
    cadsr_db_path, field: str, value: str
) -> None:
    with sqlite3.connect(cadsr_db_path) as connection:
        connection.execute(
            "UPDATE cdes SET workflow_status = 'DRAFT NEW', "
            "registration_status = 'Superceded', value_domain_type = 'External' "
            "WHERE public_id = '2003771'"
        )
    page = CdeRepository(cadsr_db_path).list_cdes(limit=1, column_text={field: value})

    assert page.total == 1
    assert [hit.public_id for hit in page.hits] == ["100"]
    assert page.column_text == {field: value}


@pytest.mark.unit
def test_filter_domains_preserve_distinct_source_spellings(cadsr_db_path) -> None:
    with sqlite3.connect(cadsr_db_path) as connection:
        connection.execute(
            "UPDATE cdes SET registration_status = 'Superceded' WHERE public_id = '100'"
        )
        connection.execute(
            "UPDATE cdes SET registration_status = 'Superseded' "
            "WHERE public_id = '2003771'"
        )

    values = CdeRepository(cadsr_db_path).filter_domains("generation")[
        "registration_status"
    ]

    assert values == ["Superceded", "Superseded"]


@pytest.mark.unit
def test_filter_domains_are_cached_for_one_certified_generation(cadsr_db_path) -> None:
    repository = CdeRepository(cadsr_db_path)
    initial = repository.filter_domains("generation-1")
    with sqlite3.connect(cadsr_db_path) as connection:
        connection.execute(
            "UPDATE cdes SET workflow_status = 'DRAFT NEW' WHERE public_id = '2003771'"
        )

    assert repository.filter_domains("generation-1") == initial
    assert repository.filter_domains("generation-2")["workflow_status"] == [
        "DRAFT NEW",
        "RELEASED",
    ]


@pytest.mark.unit
def test_closed_domain_filters_accept_multiple_source_values(cadsr_db_path) -> None:
    with sqlite3.connect(cadsr_db_path) as connection:
        connection.execute(
            "UPDATE cdes SET registration_status = 'Superceded' "
            "WHERE public_id = '2003771'"
        )

    page = CdeRepository(cadsr_db_path).list_cdes(
        limit=1, filters={"registration_status": ["Standard", "Superceded"]}
    )

    assert page.total == 2
    assert len(page.hits) == 1


@pytest.mark.unit
def test_count_and_summaries_for(cadsr_db_path) -> None:
    repo = CdeRepository(cadsr_db_path)
    assert repo.count() == 2
    summaries = repo.summaries_for(["100:2.0", "2003771:1.0", "999:9"])
    assert set(summaries) == {"100:2.0", "2003771:1.0"}  # unknown doc_id dropped
    assert summaries["100:2.0"].short_name == "NEOPLASM_HIST"


@pytest.mark.unit
def test_summaries_for_empty_returns_empty_dict(cadsr_db_path) -> None:
    repo = CdeRepository(cadsr_db_path)
    assert repo.summaries_for([]) == {}
