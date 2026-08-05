"""Unit tests for the stated-graph SPARQL builders (string shape + injection guard)."""

from collections.abc import Collection

import pytest

from ontolib.decomposition.complete_definition import CompleteDefinitionError
from ontolib.decomposition.stated_queries import (
    _intersection_hop_pattern,
    _is_staging_concept_label,
    build_ancestor_pairs_query,
    build_genus_walk_members_query,
    build_in_scope_concepts_query,
    build_morphology_query,
    build_part_of_pairs_queries,
    build_part_of_pairs_query,
    build_role_restrictions_query,
    build_semantic_type_of_query,
    build_semantic_type_query,
    resolve_morphology_filler,
    walk_genus_chain,
)
from ontolib.terminologies.namespaces import NCIT_NS, OWL_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI


def _iri(code: str) -> str:
    return f"{NCIT_NS}{code}"


@pytest.mark.unit
def test_role_query_is_scoped_to_the_stated_graph() -> None:
    q = build_role_restrictions_query("C6135")
    assert f"GRAPH <{STATED_GRAPH_IRI}>" in q
    # The restriction-traversal pattern (roles are OWL someValuesFrom, not triples).
    assert "owl:onProperty" in q
    assert "owl:someValuesFrom" in q
    # The concept IRI is interpolated safely.
    assert "Thesaurus.owl#C6135" in q


@pytest.mark.unit
def test_role_query_projects_role_label_and_target() -> None:
    q = build_role_restrictions_query("C6135")
    assert "?rel" in q
    assert "?target" in q
    assert "?relLabel" in q


@pytest.mark.unit
def test_semantic_type_query_uses_p106_in_the_stated_graph() -> None:
    q = build_semantic_type_query("C6135")
    assert f"GRAPH <{STATED_GRAPH_IRI}>" in q
    assert "P106" in q
    assert "Thesaurus.owl#C6135" in q


@pytest.mark.unit
def test_ancestor_pairs_query_binds_the_code_set_and_uses_a_transitive_path() -> None:
    q = build_ancestor_pairs_query(["C12400", "C12401"])
    assert "rdfs:subClassOf+" in q  # transitive closure over the stated hierarchy
    assert f"GRAPH <{STATED_GRAPH_IRI}>" in q
    # Both endpoints are restricted to the supplied set via VALUES.
    assert "Thesaurus.owl#C12400" in q
    assert "Thesaurus.owl#C12401" in q


@pytest.mark.unit
def test_ancestor_pairs_query_empty_set_is_valid_and_matches_nothing() -> None:
    q = build_ancestor_pairs_query([])
    assert "VALUES" in q  # an empty VALUES block is valid SPARQL (zero rows)


@pytest.mark.unit
@pytest.mark.parametrize(
    "builder",
    [build_role_restrictions_query, build_semantic_type_query],
)
def test_builders_reject_injection_unsafe_codes(builder) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match=r"[Uu]nsafe"):
        builder("C6135> } INJECT {")


@pytest.mark.unit
def test_ancestor_pairs_query_rejects_unsafe_codes() -> None:
    with pytest.raises(ValueError, match=r"[Uu]nsafe"):
        build_ancestor_pairs_query(["C123", "bad code"])


@pytest.mark.unit
def test_in_scope_concepts_query_scoped_to_stated_graph() -> None:
    q = build_in_scope_concepts_query(["Neoplastic Process"])
    assert f"GRAPH <{STATED_GRAPH_IRI}>" in q
    assert "P106" in q
    assert "Neoplastic Process" in q


@pytest.mark.unit
def test_in_scope_concepts_query_binds_multiple_semantic_types() -> None:
    q = build_in_scope_concepts_query(["Neoplastic Process", "Disease or Syndrome"])
    assert "Neoplastic Process" in q
    assert "Disease or Syndrome" in q


@pytest.mark.unit
def test_in_scope_concepts_query_projects_code_and_paginates() -> None:
    q = build_in_scope_concepts_query(["Neoplastic Process"], limit=100, offset=200)
    assert "?concept" in q
    assert "LIMIT 100" in q
    assert "OFFSET 200" in q
    assert "ORDER BY" in q  # deterministic paging


@pytest.mark.unit
def test_in_scope_concepts_query_rejects_injection_unsafe_semantic_type() -> None:
    with pytest.raises(ValueError, match=r"[Uu]nsafe"):
        build_in_scope_concepts_query(['Neoplastic Process" ; DROP {} #'])


@pytest.mark.unit
def test_intersection_hop_pattern_zero() -> None:
    uri = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C6135"
    assert "rdf:first ?member" in _intersection_hop_pattern(uri, 0)
    assert "owl:equivalentClass ?ec" in _intersection_hop_pattern(uri, 0)


@pytest.mark.unit
def test_intersection_hop_pattern_one() -> None:
    uri = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C6135"
    p = _intersection_hop_pattern(uri, 1)
    assert "rdf:first ?member" in p
    assert "rdf:rest" in p
    assert "?mid0" in p


@pytest.mark.unit
def test_intersection_hop_pattern_two() -> None:
    uri = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C6135"
    p = _intersection_hop_pattern(uri, 2)
    assert "rdf:first ?member" in p
    assert "?mid0" in p
    assert "?mid1" in p


@pytest.mark.unit
def test_build_genus_walk_members_query_shape() -> None:
    queries = build_genus_walk_members_query("C6135")
    assert isinstance(queries, list)
    assert len(queries) >= 3
    for q in queries:
        assert "owl:equivalentClass" in q
        assert "owl:intersectionOf" in q
        assert "?member" in q
        assert "?role" in q
        assert "?target" in q
        assert "?roleLabel" in q
        assert f"GRAPH <{STATED_GRAPH_IRI}>" in q


@pytest.mark.unit
def test_build_genus_walk_members_query_rejects_unsafe_code() -> None:
    with pytest.raises(ValueError, match=r"[Uu]nsafe"):
        build_genus_walk_members_query("C6135 > INJECT {")


@pytest.mark.unit
def test_semantic_type_of_query_projects_code_and_type() -> None:
    q = build_semantic_type_of_query(["C6135", "C12400"])
    assert "?code" in q
    assert "?st" in q
    assert "VALUES ?concept" in q
    assert "Thesaurus.owl#C6135" in q
    assert "Thesaurus.owl#C12400" in q
    assert "P106" in q


@pytest.mark.unit
def test_semantic_type_of_query_empty_list_returns_valid_query() -> None:
    q = build_semantic_type_of_query([])
    assert "BIND" in q


@pytest.mark.unit
def test_part_of_pairs_queries_tile_both_dimensions() -> None:
    assert len(build_part_of_pairs_queries(f"C{i}" for i in range(17))) == 4


@pytest.mark.unit
@pytest.mark.parametrize("unsafe_endpoint", ["part", "whole"])
def test_part_of_pairs_query_rejects_unsafe_code(unsafe_endpoint: str) -> None:
    part_codes = ["C1\n"] if unsafe_endpoint == "part" else ["C1"]
    whole_codes = ["C1\n"] if unsafe_endpoint == "whole" else ["C1"]
    with pytest.raises(ValueError, match=r"[Uu]nsafe"):
        build_part_of_pairs_query(
            part_codes=part_codes,
            whole_codes=whole_codes,
        )


@pytest.mark.unit
@pytest.mark.parametrize("invalid_code", ["R82", "Cfoo", "C123x"])
@pytest.mark.parametrize("invalid_endpoint", ["part", "whole"])
def test_part_of_pairs_query_rejects_non_ncit_concept_code(
    invalid_code: str,
    invalid_endpoint: str,
) -> None:
    part_codes = [invalid_code] if invalid_endpoint == "part" else ["C1"]
    whole_codes = [invalid_code] if invalid_endpoint == "whole" else ["C1"]
    with pytest.raises(ValueError, match="NCIt concept code"):
        build_part_of_pairs_query(part_codes=part_codes, whole_codes=whole_codes)


@pytest.mark.unit
def test_part_of_pairs_query_requires_directional_keywords() -> None:
    with pytest.raises(TypeError):
        build_part_of_pairs_query(["C1"], ["C2"])  # type: ignore[misc]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("part_codes", "whole_codes"),
    [([], ["C2"]), (["C1"], [])],
)
def test_part_of_pairs_query_empty_endpoint_matches_nothing(
    part_codes: list[str],
    whole_codes: list[str],
) -> None:
    query = build_part_of_pairs_query(
        part_codes=part_codes,
        whole_codes=whole_codes,
    )
    assert "FILTER(false)" in query


@pytest.mark.unit
@pytest.mark.parametrize("oversized_endpoint", ["part", "whole"])
def test_part_of_pairs_query_rejects_more_than_measured_tile_limit(
    oversized_endpoint: str,
) -> None:
    codes = [f"C{i}" for i in range(17)]
    part_codes = codes if oversized_endpoint == "part" else ["C100"]
    whole_codes = codes if oversized_endpoint == "whole" else ["C100"]
    with pytest.raises(ValueError, match="at most 16 codes per endpoint"):
        build_part_of_pairs_query(part_codes=part_codes, whole_codes=whole_codes)


@pytest.mark.unit
def test_build_morphology_query_is_scoped_to_stated_graph() -> None:
    q = build_morphology_query("C6135")
    assert f"GRAPH <{STATED_GRAPH_IRI}>" in q
    assert "rdfs:label" in q
    assert "?label" in q


@pytest.mark.unit
def test_build_morphology_query_interpolates_code_safely() -> None:
    q = build_morphology_query("C6135")
    assert "Thesaurus.owl#C6135" in q


@pytest.mark.unit
def test_build_morphology_query_rejects_unsafe_code() -> None:
    with pytest.raises(ValueError, match=r"[Uu]nsafe"):
        build_morphology_query("C6135 > INJECT {")


@pytest.mark.unit
async def test_resolve_morphology_filler_returns_first_non_staging_genus() -> None:
    call_count = 0

    async def fake_select(
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str | None]]:
        nonlocal call_count
        call_count += 1
        expected_variables = {"label"} if "SELECT ?label" in query else {"member"}
        assert set(required_variables) == expected_variables
        if call_count == 1:
            return [{"member": _iri("C141041"), "type": None}]
        if call_count == 2:
            return [{"label": "Thyroid Gland Medullary Carcinoma by AJCC v7 Stage"}]
        if call_count == 3:
            return [{"member": _iri("C3879"), "type": None}]
        if call_count == 4:
            return [{"label": "Thyroid Gland Medullary Carcinoma"}]
        return []

    morphology = await resolve_morphology_filler(fake_select, "C6135")
    assert morphology == "C3879"


@pytest.mark.unit
async def test_resolve_morphology_filler_returns_none_when_no_genus() -> None:
    async def fake_select(
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str | None]]:
        del query
        assert set(required_variables) == {"member"}
        return []

    morphology = await resolve_morphology_filler(fake_select, "C6135")
    assert morphology is None


@pytest.mark.unit
@pytest.mark.parametrize("ambiguous_result", ["member", "label"])
async def test_resolve_morphology_filler_rejects_ambiguous_source_rows(
    ambiguous_result: str,
) -> None:
    call_count = 0

    async def fake_select(
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str | None]]:
        nonlocal call_count
        del query, required_variables
        call_count += 1
        if ambiguous_result == "member":
            return [
                {"member": _iri("C3879"), "type": None},
                {"member": _iri("C141041"), "type": None},
            ]
        if call_count == 1:
            return [{"member": _iri("C3879"), "type": None}]
        return [{"label": "Label one"}, {"label": "Label two"}]

    with pytest.raises(ValueError, match="multiple"):
        await resolve_morphology_filler(fake_select, "C6135")


@pytest.mark.unit
@pytest.mark.parametrize("missing_binding", ["member", "label"])
async def test_resolve_morphology_filler_rejects_missing_required_binding(
    missing_binding: str,
) -> None:
    call_count = 0

    async def fake_select(
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str | None]]:
        nonlocal call_count
        del query, required_variables
        call_count += 1
        if missing_binding == "member":
            return [{"member": _iri("C3879"), "type": None}, {}]
        if call_count == 2:
            return [{}]
        return [{"member": _iri("C3879"), "type": None}]

    with pytest.raises(ValueError, match=missing_binding):
        await resolve_morphology_filler(fake_select, "C6135")


@pytest.mark.unit
async def test_resolve_morphology_filler_rejects_non_ncit_genus() -> None:
    async def fake_select(
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str | None]]:
        del query, required_variables
        return [{"member": "http://example.org/genus", "type": None}]

    with pytest.raises(ValueError, match="not an NCIt IRI"):
        await resolve_morphology_filler(fake_select, "C6135")


@pytest.mark.unit
async def test_resolve_morphology_filler_rejects_non_concept_ncit_member() -> None:
    async def fake_select(
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str | None]]:
        del query, required_variables
        return [{"member": _iri("R101"), "type": None}]

    with pytest.raises(ValueError, match="not an NCIt concept code"):
        await resolve_morphology_filler(fake_select, "C6135")


@pytest.mark.unit
async def test_resolve_morphology_filler_skips_restriction_before_named_genus() -> None:
    call_count = 0

    async def fake_select(
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str | None]]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            assert set(required_variables) == {"member"}
            return [
                {"member": "_:restriction", "type": OWL_NS + "Restriction"},
                {"member": _iri("C3879"), "type": None},
            ]
        assert set(required_variables) == {"label"}
        return [{"label": "Thyroid Gland Medullary Carcinoma"}]

    assert await resolve_morphology_filler(fake_select, "C6135") == "C3879"


@pytest.mark.unit
async def test_resolve_morphology_filler_continues_past_unlabelled_genus() -> None:
    responses: list[list[dict[str, str | None]]] = [
        [{"member": _iri("C141041"), "type": None}],
        [],
        [{"member": _iri("C3879"), "type": None}],
        [{"label": "Thyroid Gland Medullary Carcinoma"}],
    ]

    async def fake_select(
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str | None]]:
        expected_variables = {"label"} if "SELECT ?label" in query else {"member"}
        assert set(required_variables) == expected_variables
        return responses.pop(0)

    assert await resolve_morphology_filler(fake_select, "C6135") == "C3879"
    assert responses == []


@pytest.mark.unit
async def test_resolve_morphology_filler_stops_on_genus_cycle() -> None:
    responses: list[list[dict[str, str | None]]] = [
        [{"member": _iri("C141041"), "type": None}],
        [{"label": "Stage II Thyroid Gland Medullary Carcinoma"}],
        [{"member": _iri("C6135"), "type": None}],
    ]

    async def fake_select(
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str | None]]:
        del query, required_variables
        return responses.pop(0)

    assert await resolve_morphology_filler(fake_select, "C6135") is None
    assert responses == []


@pytest.mark.unit
async def test_resolve_morphology_filler_stops_at_depth_bound() -> None:
    responses: list[list[dict[str, str | None]]] = [
        [{"member": _iri("C141041"), "type": None}],
        [{"label": "Stage II Thyroid Gland Medullary Carcinoma"}],
    ]

    async def fake_select(
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str | None]]:
        del query, required_variables
        return responses.pop(0)

    assert await resolve_morphology_filler(fake_select, "C6135", max_depth=1) is None
    assert responses == []


@pytest.mark.unit
async def test_resolve_morphology_filler_returns_first_genus_if_not_staging() -> None:
    call_count = 0

    async def fake_select(
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str | None]]:
        nonlocal call_count
        call_count += 1
        expected_variables = {"label"} if "SELECT ?label" in query else {"member"}
        assert set(required_variables) == expected_variables
        # Call 1: build_genus_walk_members_query(C6135) then select_fn(queries[0])
        # Call 2: select_fn(label_query) for C3879
        if call_count == 1:
            return [{"member": _iri("C3879"), "type": None}]
        if call_count == 2 and "rdfs:label" in query:
            return [{"label": "Thyroid Gland Medullary Carcinoma"}]
        return []

    morphology = await resolve_morphology_filler(fake_select, "C6135")
    assert morphology == "C3879"


@pytest.mark.unit
@pytest.mark.parametrize(
    "label",
    [
        "Stage III Colon Cancer",
        "Thyroid Gland Medullary Carcinoma by AJCC v7 Stage",
        "Unresectable Pancreatic Carcinoma",
        "Recurrent Glioblastoma",
        "Metastatic Breast Carcinoma",
    ],
)
def test_staging_label_markers_identify_staging_concepts(label: str) -> None:
    assert _is_staging_concept_label(label) is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "label",
    [
        "Thyroid Gland Medullary Carcinoma",
        "Colon Adenocarcinoma",
        "Small Cell Lung Carcinoma",
        "Invasive Ductal Carcinoma",
    ],
)
def test_staging_label_markers_do_not_match_morphology_concepts(label: str) -> None:
    assert _is_staging_concept_label(label) is False


def _definition_rows(
    expression: str,
    *members: tuple[str, str | None, str | None, bool],
) -> list[dict[str, str | None]]:
    return [
        {
            "expression": expression,
            "parentExpression": None,
            "nestingDepth": "0",
            "position": str(position),
            "member": member,
            "role": role,
            "target": target,
            "childExpression": "_:defined" if is_defined else None,
            "nestedExpression": None,
            "overflow": "false",
        }
        for position, (member, role, target, is_defined) in enumerate(members)
    ]


def _walker_select_double(
    rows_by_code: dict[str, list[dict[str, str | None]]],
    *,
    role_labels: dict[str, str] | None = None,
    queried_codes: list[str] | None = None,
):  # type: ignore[no-untyped-def]
    labels = role_labels or {}

    async def select(
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str | None]]:
        if "SELECT ?role ?roleLabel" in query:
            assert set(required_variables) == {"role"}
            return [
                {"role": _iri(role_code), "roleLabel": label}
                for role_code, label in labels.items()
                if f"#{role_code}>" in query
            ]
        assert set(required_variables) == {
            "expression",
            "list",
            "cell",
        }
        code = next(code for code in rows_by_code if f"#{code}>" in query)
        if queried_codes is not None:
            queried_codes.append(code)
        return rows_by_code[code]

    return select


@pytest.mark.unit
async def test_walk_genus_chain_populates_anchoring_genus() -> None:
    """The walker must record which genus anchored each restriction so D20 lineage
    routing (``filler_selection.route_axis``) can fire. Regression guard for the
    PR-B gap where ``anchoring_genus`` was left ``None`` on the walker path, which
    silently disabled ``op:AssociatedLineageClassification`` routing entirely.
    """
    rows_by_code = {
        "C6135": _definition_rows(
            "_:root",
            (_iri("C3809"), None, None, True),
        ),
        "C3809": _definition_rows(
            "_:genus",
            ("_:r1", _iri("R101"), _iri("C12704"), False),
        ),
    }
    select = _walker_select_double(
        rows_by_code,
        role_labels={"R101": "Disease_Has_Primary_Anatomic_Site"},
    )

    roles = await walk_genus_chain(select, "C6135", max_depth=3)

    lineage = [r for r in roles if r.filler_code == "C12704"]
    assert len(lineage) == 1
    assert lineage[0].role_code == "R101"
    # The genus that anchored the restriction, not the starting concept.
    assert lineage[0].anchoring_genus == "C3809"


@pytest.mark.unit
async def test_walk_genus_chain_projects_adjudicated_inherited_roles_only() -> None:
    rows_by_code = {
        "C1": _definition_rows("_:root", (_iri("C2"), None, None, True)),
        "C2": _definition_rows(
            "_:genus",
            ("_:r103", _iri("R103"), _iri("C103"), False),
            ("_:r104", _iri("R104"), _iri("C104"), False),
            ("_:r107", _iri("R107"), _iri("C107"), False),
            ("_:r108", _iri("R108"), _iri("C108"), False),
        ),
    }
    select = _walker_select_double(
        rows_by_code,
        role_labels={
            "R103": "Disease_Has_Normal_Tissue_Origin",
            "R104": "Disease_Has_Normal_Cell_Origin",
            "R107": "Disease_Has_Cytogenetic_Abnormality",
            "R108": "Disease_Has_Finding",
        },
    )

    roles = await walk_genus_chain(select, "C1", max_depth=3)

    assert {(role.role_code, role.filler_code) for role in roles} == {
        ("R103", "C103"),
        ("R108", "C108"),
    }


@pytest.mark.unit
async def test_walk_genus_chain_anchors_depth0_roles_on_the_start_concept() -> None:
    """A restriction found directly on the starting concept is anchored on that
    concept's own code (depth 0), not left ``None``."""
    rows_by_code = {
        "C6135": _definition_rows(
            "_:root",
            ("_:r0", _iri("R88"), _iri("C27970"), False),
        )
    }
    select = _walker_select_double(
        rows_by_code,
        role_labels={"R88": "Disease_Is_Stage"},
    )

    roles = await walk_genus_chain(select, "C6135", max_depth=2)

    assert len(roles) == 1
    assert roles[0].anchoring_genus == "C6135"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("label_rows", "message"),
    [
        ([{"role": None, "roleLabel": "Site"}], "required role"),
        (
            [{"role": "https://example.org/R101", "roleLabel": "Site"}],
            "not an NCIt IRI",
        ),
        (
            [{"role": _iri("R999"), "roleLabel": "Unexpected"}],
            "unrequested role",
        ),
        (
            [
                {"role": _iri("R101"), "roleLabel": "Site A"},
                {"role": _iri("R101"), "roleLabel": "Site B"},
            ],
            "conflicting labels",
        ),
    ],
)
async def test_walk_genus_chain_fails_closed_on_invalid_role_label_rows(
    label_rows: list[dict[str, str | None]],
    message: str,
) -> None:
    definition_rows = _definition_rows(
        "_:root",
        ("_:restriction", _iri("R101"), _iri("C12400"), False),
    )

    async def select(
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str | None]]:
        return label_rows if "SELECT ?role ?roleLabel" in query else definition_rows

    with pytest.raises(ValueError, match=message):
        await walk_genus_chain(select, "C6135")


@pytest.mark.unit
async def test_walk_genus_chain_prefers_a_bound_label_over_an_unbound_duplicate() -> (
    None
):
    definition_rows = _definition_rows(
        "_:root",
        ("_:restriction", _iri("R101"), _iri("C12400"), False),
    )

    async def select(
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str | None]]:
        if "SELECT ?role ?roleLabel" in query:
            return [
                {"role": _iri("R101"), "roleLabel": None},
                {
                    "role": _iri("R101"),
                    "roleLabel": "Disease_Has_Primary_Anatomic_Site",
                },
            ]
        return definition_rows

    roles = await walk_genus_chain(select, "C6135")

    assert len(roles) == 1
    assert roles[0].role_label == "Disease_Has_Primary_Anatomic_Site"


@pytest.mark.unit
async def test_walk_genus_chain_returns_empty_for_an_undefined_concept() -> None:
    select = _walker_select_double({"C6135": []})

    assert await walk_genus_chain(select, "C6135") == []


@pytest.mark.unit
async def test_walk_genus_chain_rejects_an_incomplete_nested_group() -> None:
    rows = _definition_rows(
        "_:root",
        ("_:nested-class", None, None, False),
    )
    rows[0]["nestedExpression"] = "_:nested-class"
    select = _walker_select_double({"C6135": rows})

    with pytest.raises(CompleteDefinitionError, match="missing nested group"):
        await walk_genus_chain(select, "C6135")


@pytest.mark.unit
async def test_walk_genus_chain_deduplicates_roles_and_terminates_on_cycle() -> None:
    queried_codes: list[str] = []
    rows_by_code = {
        "C6135": _definition_rows(
            "_:root",
            (_iri("C3809"), None, None, True),
        ),
        "C3809": _definition_rows(
            "_:genus",
            ("_:core", _iri("R101"), _iri("C12704"), False),
            ("_:core-copy", _iri("R101"), _iri("C12704"), False),
            ("_:non-core", _iri("R999"), _iri("C999"), False),
            (_iri("C6135"), None, None, True),
        ),
    }
    select = _walker_select_double(
        rows_by_code,
        role_labels={
            "R101": "Disease_Has_Primary_Anatomic_Site",
            "R999": "Not_A_Core_Neoplasm_Role",
        },
        queried_codes=queried_codes,
    )

    roles = await walk_genus_chain(select, "C6135", max_depth=5)

    assert [(role.role_code, role.filler_code) for role in roles] == [
        ("R101", "C12704")
    ]
    assert roles[0].anchoring_genus == "C3809"
    assert queried_codes == ["C6135", "C3809"]


@pytest.mark.unit
async def test_walk_genus_chain_limits_role_projection_without_truncating_record() -> (
    None
):
    queried_codes: list[str] = []
    select = _walker_select_double(
        {
            "C6135": _definition_rows(
                "_:root",
                (_iri("C3809"), None, None, True),
            ),
            "C3809": [],
        },
        queried_codes=queried_codes,
    )

    assert await walk_genus_chain(select, "C6135", max_depth=1) == []
    assert queried_codes == ["C6135", "C3809"]
