"""Unit tests for pure decomposition row assembly (read layer)."""

import pytest

from ontolib.decomposition import vocab
from ontolib.decomposition.models import AxisSource
from ontolib.decomposition.read import decomposition_from_rows
from ontolib.decomposition.read_models import DecompositionConstituent
from ontolib.terminologies.namespaces import NCIT_NS


def _ncit(code: str) -> str:
    return f"{NCIT_NS}{code}"


def _row(**kw: str) -> dict[str, str | None]:
    # legacy_writer always emits op:axisSource, so a realistic row always carries it.
    row = (
        dict.fromkeys(
            (
                "status",
                "decomposedOn",
                "axis",
                "filler",
                "mostSpecific",
                "group",
                "needsReview",
                "sourceRole",
                "sourceDefinitionFact",
            ),
            None,
        )
        | {"axisSource": "role"}
        | kw
    )
    axis = row["axis"]
    if (
        row["axisSource"] == "role"
        and isinstance(axis, str)
        and axis.startswith(NCIT_NS + "R")
        and row["sourceRole"] is None
    ):
        row["sourceRole"] = axis
    return row


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
def test_normalized_axis_preserves_valid_ncit_source_roles() -> None:
    d = decomposition_from_rows(
        "C6135",
        [
            _row(
                status=vocab.LEGACY_PRECOORDINATED,
                axis=f"{vocab.ONTOPRISM_NS}PrimarySite",
                filler=_ncit("C12400"),
                sourceRole=_ncit("R101"),
            )
        ],
    )

    assert d.constituents[0].axis == "op:PrimarySite"
    assert d.constituents[0].source_roles == ("R101",)


@pytest.mark.unit
def test_repeated_role_rows_merge_to_canonical_source_roles() -> None:
    common = {
        "axis": f"{vocab.ONTOPRISM_NS}PrimarySite",
        "filler": _ncit("C12316"),
    }

    decomposition = decomposition_from_rows(
        "C150094",
        [
            _row(**common, sourceRole=_ncit("R101")),
            _row(**common, sourceRole=_ncit("R100")),
        ],
    )

    assert len(decomposition.constituents) == 1
    assert decomposition.constituents[0].source_roles == ("R100", "R101")


@pytest.mark.unit
@pytest.mark.parametrize(
    "source_role",
    [
        # The first four are foreign or absent namespaces: the local part looks
        # like a role code, so only the namespace guard can reject them. The last
        # has the right namespace and a concept code, so only the role-code guard
        # can.
        "https://example.org/R101",
        "http://example.org/ns#R101",
        "http://purl.obolibrary.org/obo/R101",
        "R101",
        f"{NCIT_NS}C101",
    ],
)
def test_invalid_source_role_fails_closed(source_role: str) -> None:
    with pytest.raises(ValueError, match="source role"):
        decomposition_from_rows(
            "C6135",
            [
                _row(
                    axis=f"{vocab.ONTOPRISM_NS}PrimarySite",
                    filler=_ncit("C12400"),
                    sourceRole=source_role,
                )
            ],
        )


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
@pytest.mark.parametrize("axis_source", [None, "", "unknown", "Role", "role "])
def test_absent_or_unknown_axis_source_fails_closed(axis_source: str | None) -> None:
    """An absent op:axisSource must not be reported to clients as role-derived.

    legacy_writer emits the triple for every constituent, so its absence means the
    graph is corrupt; silently defaulting would relabel NLP- or parent-derived
    provenance as role-derived.
    """
    row = _row(
        status=vocab.LEGACY_PRECOORDINATED,
        axis=_ncit("R88"),
        filler=_ncit("C27970"),
    )
    row["axisSource"] = axis_source
    with pytest.raises(ValueError, match="axis source is not a known provenance"):
        decomposition_from_rows("C6135", [row])


@pytest.mark.unit
def test_group_review_flag_and_all_definition_sources_round_trip() -> None:
    fact_a = f"{vocab.DEFINITION_FACT_NS}C6135/{'a' * 64}"
    fact_b = f"{vocab.DEFINITION_FACT_NS}C6135/{'b' * 64}"
    common = {
        "status": vocab.LEGACY_PRECOORDINATED,
        "axis": _ncit("R101"),
        "filler": _ncit("C12400"),
        "axisSource": "role",
        "group": "anatomy-1",
        "needsReview": "true",
    }
    d = decomposition_from_rows(
        "C6135",
        [
            _row(**common, sourceDefinitionFact=fact_b),
            _row(**common, sourceDefinitionFact=fact_a),
        ],
    )

    assert len(d.constituents) == 1
    constituent = d.constituents[0]
    assert constituent.group == "anatomy-1"
    assert constituent.needs_review is True
    assert constituent.source_definition_ids == ("a" * 64, "b" * 64)


@pytest.mark.unit
@pytest.mark.parametrize("value", ["TRUE", "yes", "2", ""])
def test_malformed_persisted_boolean_fails_closed(value: str) -> None:
    with pytest.raises(ValueError, match="RDF boolean"):
        decomposition_from_rows(
            "C6135",
            [
                _row(
                    axis=_ncit("R101"),
                    filler=_ncit("C12400"),
                    needsReview=value,
                )
            ],
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "source_iri",
    [
        "https://example.org/not-an-ontoprism-fact",
        f"{vocab.DEFINITION_FACT_NS}not-a-digest",
        f"{vocab.DEFINITION_FACT_NS}C2/{'a' * 64}",
        # Right length, wrong alphabet: only the hex check rejects these, and the
        # DB CHECK is ^[0-9a-f]{64}$, so they could never join to a persisted fact.
        f"{vocab.DEFINITION_FACT_NS}C1/{'A' * 64}",
        f"{vocab.DEFINITION_FACT_NS}C1/{'z' * 64}",
    ],
)
def test_invalid_projection_source_fact_fails_closed(source_iri: str) -> None:
    with pytest.raises(ValueError, match="source definition fact"):
        decomposition_from_rows(
            "C1",
            [
                _row(
                    axis=_ncit("R101"),
                    filler=_ncit("C2"),
                    sourceDefinitionFact=source_iri,
                )
            ],
        )


@pytest.mark.unit
def test_conflicting_rows_for_one_constituent_fail_closed() -> None:
    common = {"axis": _ncit("R101"), "filler": _ncit("C2")}
    with pytest.raises(ValueError, match="conflicting"):
        decomposition_from_rows(
            "C1",
            [
                _row(**common, group="one"),
                _row(**common, group="two"),
            ],
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("axis_source", "source_roles", "message"),
    [
        ("role", (), "role-derived"),
        ("parent", ("R101",), "parent/NLP"),
        ("nlp", ("R101",), "parent/NLP"),
        ("role", ("C101",), "NCIt role"),
    ],
)
def test_read_constituent_rejects_invalid_source_role_invariants(
    axis_source: AxisSource,
    source_roles: tuple[str, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        DecompositionConstituent(
            axis="op:PrimarySite",
            filler="C12400",
            axis_source=axis_source,
            source_roles=source_roles,
        )


@pytest.mark.unit
def test_parent_read_row_with_source_role_fails_closed() -> None:
    with pytest.raises(ValueError, match="parent/NLP"):
        decomposition_from_rows(
            "C1",
            [
                _row(
                    axis=f"{vocab.ONTOPRISM_NS}Morphology",
                    filler=_ncit("C3499"),
                    axisSource="parent",
                    sourceRole=_ncit("R101"),
                )
            ],
        )


@pytest.mark.unit
def test_role_read_row_without_source_role_fails_closed() -> None:
    with pytest.raises(ValueError, match="role-derived"):
        decomposition_from_rows(
            "C1",
            [
                _row(
                    axis=f"{vocab.ONTOPRISM_NS}PrimarySite",
                    filler=_ncit("C12400"),
                    axisSource="role",
                )
            ],
        )


@pytest.mark.unit
def test_repeated_valid_role_rows_merge_and_revalidate_all_sources() -> None:
    common = {
        "axis": f"{vocab.ONTOPRISM_NS}PrimarySite",
        "filler": _ncit("C12316"),
        "axisSource": "role",
    }

    decomposition = decomposition_from_rows(
        "C150094",
        [
            _row(**common, sourceRole=_ncit("R101")),
            _row(**common, sourceRole=_ncit("R100")),
        ],
    )

    assert decomposition.constituents[0].source_roles == ("R100", "R101")
