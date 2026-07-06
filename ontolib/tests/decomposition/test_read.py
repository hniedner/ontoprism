"""Unit tests for pure decomposition row assembly (read layer)."""

import pytest

from ontolib.decomposition import vocab
from ontolib.decomposition.read import decomposition_from_rows
from ontolib.terminologies.namespaces import NCIT_NS


def _ncit(code: str) -> str:
    return f"{NCIT_NS}{code}"


def _row(**kw: str) -> dict[str, str | None]:
    return (
        dict.fromkeys(
            ("status", "decomposedOn", "axis", "filler", "axisSource", "mostSpecific"),
            None,
        )
        | kw
    )


@pytest.mark.unit
def test_assembles_flag_date_and_constituents() -> None:
    rows = [
        _row(
            status=vocab.LEGACY_PRECOORDINATED,
            decomposedOn="2026-07-06",
            axis=_ncit("R88"),
            filler=_ncit("C27970"),
            axisSource="role",
            mostSpecific="false",
        ),
        _row(
            status=vocab.LEGACY_PRECOORDINATED,
            decomposedOn="2026-07-06",
            axis=_ncit("R101"),
            filler=_ncit("C12400"),
            axisSource="role",
            mostSpecific="true",
        ),
    ]
    d = decomposition_from_rows("C6135", rows)
    assert d.code == "C6135"
    assert d.is_legacy_precoordinated is True
    assert d.decomposed_on == "2026-07-06"
    assert [(c.axis, c.filler, c.most_specific) for c in d.constituents] == [
        ("R101", "C12400", True),
        ("R88", "C27970", False),
    ]


@pytest.mark.unit
def test_op_axis_keeps_its_prefix() -> None:
    rows = [
        _row(
            status=vocab.LEGACY_PRECOORDINATED,
            axis=f"{vocab.ONTOPRISM_NS}Morphology",
            filler=_ncit("C40384"),
            axisSource="parent",
        )
    ]
    d = decomposition_from_rows("C6135", rows)
    assert d.constituents[0].axis == "op:Morphology"
    assert d.constituents[0].axis_source == "parent"


@pytest.mark.unit
def test_not_decomposed_concept_resolves_without_flag() -> None:
    # No status row (concept absent from the decomposed graph) → not-decomposed, empty.
    d = decomposition_from_rows("C0", [_row()])
    assert d.is_legacy_precoordinated is False
    assert d.constituents == []


@pytest.mark.unit
def test_constituents_are_deduplicated() -> None:
    same = _row(
        status=vocab.LEGACY_PRECOORDINATED, axis=_ncit("R88"), filler=_ncit("C27970")
    )
    d = decomposition_from_rows("C6135", [same, dict(same)])
    assert len(d.constituents) == 1


@pytest.mark.unit
def test_axis_source_defaults_to_role_when_absent() -> None:
    d = decomposition_from_rows(
        "C6135",
        [
            _row(
                status=vocab.LEGACY_PRECOORDINATED,
                axis=_ncit("R88"),
                filler=_ncit("C27970"),
            )
        ],
    )
    assert d.constituents[0].axis_source == "role"
