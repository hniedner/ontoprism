from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from ontolib.repositories.xref.coverage import (
    CdeAnchor,
    CdeAnchors,
    build_coverage_report,
    cde_anchor_map,
)
from ontolib.repositories.xref.vocab import CLOSE_MATCH, EXACT_MATCH

if TYPE_CHECKING:
    from pathlib import Path

_SCHEMA = (
    "CREATE TABLE cde_concepts ("
    "  concept_code TEXT NOT NULL, concept_name TEXT NOT NULL,"
    "  public_id TEXT NOT NULL, version TEXT NOT NULL,"
    "  concept_type TEXT, is_primary INTEGER"
    ");"
)


def _build_db(tmp_path: Path, rows: list[tuple]) -> str:
    path = tmp_path / "cadsr_coverage.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.executemany(
            "INSERT INTO cde_concepts (concept_code, concept_name, public_id, "
            "version, concept_type, is_primary) VALUES (?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    return str(path)


# ---------------------------------------------------------------------------
# CdeAnchors tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cde_anchors_is_post_coordinated_false() -> None:
    """One code per concept_type => not post-coordinated."""
    anchors = CdeAnchors(
        "100",
        "1.0",
        (
            CdeAnchor("C3262", "object_class", True),
            CdeAnchor("C16358", "property", False),
        ),
    )
    assert not anchors.is_post_coordinated
    assert anchors.codes == frozenset({"C3262", "C16358"})


@pytest.mark.unit
def test_cde_anchors_is_post_coordinated_true() -> None:
    """Two codes sharing a concept_type => post-coordinated."""
    anchors = CdeAnchors(
        "2003771",
        "1.0",
        (
            CdeAnchor("C25150", "property", True),
            CdeAnchor("C25230", "property", False),
        ),
    )
    assert anchors.is_post_coordinated
    assert anchors.codes == frozenset({"C25150", "C25230"})


@pytest.mark.unit
def test_cde_anchors_value_meaning_included() -> None:
    """value_meaning rows are included as normal anchors."""
    anchors = CdeAnchors(
        "100",
        "1.0",
        (CdeAnchor("C2916", "value_meaning", False),),
    )
    assert CdeAnchor("C2916", "value_meaning", False) in anchors.anchors


# ---------------------------------------------------------------------------
# cde_anchor_map tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cde_anchor_map_groups_by_cde(tmp_path: Path) -> None:
    db = _build_db(
        tmp_path,
        [
            ("C3262", "Neoplasm", "100", "1.0", "object_class", 1),
            ("C16358", "Histology", "100", "1.0", "property", 0),
            ("C2916", "Carcinoma", "100", "1.0", "value_meaning", 0),
            ("C25150", "Age", "2003771", "1.0", "property", 1),
        ],
    )
    result = cde_anchor_map(db)
    assert ("100", "1.0") in result
    assert ("2003771", "1.0") in result
    assert len(result) == 2
    assert result[("100", "1.0")].codes == frozenset({"C3262", "C16358", "C2916"})


@pytest.mark.unit
def test_cde_anchor_map_post_coordinated(tmp_path: Path) -> None:
    db = _build_db(
        tmp_path,
        [
            ("C25150", "Age", "2003771", "1.0", "property", 1),
            ("C25230", "Age Range", "2003771", "1.0", "property", 0),
            ("C3262", "Neoplasm", "100", "2.0", "object_class", 1),
        ],
    )
    result = cde_anchor_map(db)
    assert result[("2003771", "1.0")].is_post_coordinated
    assert not result[("100", "2.0")].is_post_coordinated


@pytest.mark.unit
def test_cde_anchor_map_accepts_file_uri(tmp_path: Path) -> None:
    db = _build_db(
        tmp_path,
        [
            ("C001", "Test", "1", "1", "property", 1),
        ],
    )
    file_uri = f"file:{db}"
    result = cde_anchor_map(file_uri)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# build_coverage_report tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_all_anchors_live_and_identity_mapped() -> None:
    anchor_map = {
        ("100", "1.0"): CdeAnchors(
            "100",
            "1.0",
            (
                CdeAnchor("C3262", "object_class", True),
                CdeAnchor("C16358", "property", False),
            ),
        ),
    }
    live_status = {"C3262": "live", "C16358": "live"}
    strength = {
        "C3262": {(EXACT_MATCH, "validated")},
        "C16358": {(EXACT_MATCH, "active")},
    }
    report = build_coverage_report(
        anchor_map,
        live_status=live_status,
        strength_by_subject=strength,
        role_codes=frozenset(),
    )
    assert report.cde_coverage == 1.0
    assert report.n_cdes == 1
    assert report.live == 2
    assert report.unresolved == 0
    assert report.anchors_identity_mapped == 2


@pytest.mark.unit
def test_close_match_not_covered() -> None:
    anchor_map = {
        ("100", "1.0"): CdeAnchors(
            "100", "1.0", (CdeAnchor("C3262", "object_class", True),)
        ),
    }
    live_status = {"C3262": "live"}
    strength = {"C3262": {(CLOSE_MATCH, "proposed")}}
    report = build_coverage_report(
        anchor_map,
        live_status=live_status,
        strength_by_subject=strength,
        role_codes=frozenset(),
    )
    assert report.cde_coverage == 0.0
    assert report.anchors_close_only == 1
    assert report.anchors_identity_mapped == 0


@pytest.mark.unit
def test_unresolved_anchor_excluded() -> None:
    anchor_map = {
        ("100", "1.0"): CdeAnchors(
            "100", "1.0", (CdeAnchor("C3262", "object_class", True),)
        ),
    }
    live_status = {"C3262": "unresolved"}
    strength = {"C3262": {(EXACT_MATCH, "validated")}}
    report = build_coverage_report(
        anchor_map,
        live_status=live_status,
        strength_by_subject=strength,
        role_codes=frozenset(),
    )
    assert report.cde_coverage == 0.0


@pytest.mark.unit
def test_post_coordinated_partial_not_covered() -> None:
    """Only 1 of 2 codes in a post-coordinated CDE is identity-mapped => NOT covered."""
    anchor_map = {
        ("2003771", "1.0"): CdeAnchors(
            "2003771",
            "1.0",
            (
                CdeAnchor("C25150", "property", True),
                CdeAnchor("C25230", "property", False),
            ),
        ),
    }
    live_status = {"C25150": "live", "C25230": "live"}
    strength = {
        "C25150": {(EXACT_MATCH, "validated")},
        "C25230": {(CLOSE_MATCH, "proposed")},
    }
    report = build_coverage_report(
        anchor_map,
        live_status=live_status,
        strength_by_subject=strength,
        role_codes=frozenset(),
    )
    assert report.cde_coverage == 0.0


@pytest.mark.unit
def test_role_codes_split() -> None:
    anchor_map = {
        ("100", "1.0"): CdeAnchors(
            "100", "1.0", (CdeAnchor("C3262", "object_class", True),)
        ),
    }
    live_status = {"C3262": "live"}
    strength = {"C3262": {(EXACT_MATCH, "validated")}}
    report = build_coverage_report(
        anchor_map,
        live_status=live_status,
        strength_by_subject=strength,
        role_codes=frozenset({"C3262"}),
    )
    assert report.anchors_in_roles == 1
    assert report.anchors_new == 0


@pytest.mark.unit
def test_empty_inputs() -> None:
    report = build_coverage_report(
        {}, live_status={}, strength_by_subject={}, role_codes=frozenset()
    )
    assert report.n_cdes == 0
    assert report.cde_coverage == 0.0
    assert report.distinct_anchors == 0
    assert report.live == 0
    assert report.unresolved == 0
    assert report.anchors_identity_mapped == 0
    assert report.anchors_close_only == 0
    assert report.anchors_unmapped == 0
    assert report.single_code_cdes == 0
    assert report.post_coordinated_cdes == 0
    assert report.anchors_in_roles == 0
    assert report.anchors_new == 0


@pytest.mark.unit
def test_as_dict_returns_all_fields() -> None:
    report = build_coverage_report(
        {}, live_status={}, strength_by_subject={}, role_codes=frozenset()
    )
    d = report.as_dict()
    assert isinstance(d, dict)
    assert "cde_coverage" in d
    assert "n_cdes" in d
    assert "distinct_anchors" in d
    assert len(d) >= 10
