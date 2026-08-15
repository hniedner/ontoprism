"""Unit tests for the decomposition value models (pure; no store)."""

import pytest

from ontolib.decomposition.minting import MintedConcept
from ontolib.decomposition.models import (
    CompleteDefinition,
    Constituent,
    Decomposition,
    DefinitionGroup,
    DetectionResult,
    GenusDefinitionFact,
    RestrictionDefinitionFact,
    RoleRestriction,
    canonical_definition_fact_id,
    canonical_definition_group_id,
)


@pytest.mark.unit
def test_role_restriction_label_is_optional() -> None:
    r = RoleRestriction(role_code="R105", filler_code="C36761")
    assert r.role_label is None
    assert r.role_code == "R105"
    assert r.filler_code == "C36761"


@pytest.mark.unit
def test_constituent_defaults_are_conservative() -> None:
    c = Constituent(axis="R101", filler_code="C12400", axis_source="role")
    # A constituent is not assumed most-specific or reviewed unless stated.
    assert c.most_specific is False
    assert c.needs_review is False
    assert c.source_roles == ("R101",)


@pytest.mark.unit
def test_normalized_role_constituent_requires_source_roles() -> None:
    with pytest.raises(ValueError, match="source_roles"):
        Constituent(
            axis="op:PrimarySite",
            filler_code="C12400",
            axis_source="role",
        )


@pytest.mark.unit
@pytest.mark.parametrize("axis_source", ["parent", "nlp"])
def test_non_role_constituent_rejects_source_roles(axis_source: str) -> None:
    with pytest.raises(ValueError, match="empty source_roles"):
        Constituent(
            axis="op:Morphology",
            filler_code="C12400",
            axis_source=axis_source,  # type: ignore[arg-type]
            source_roles=("R101",),
        )


@pytest.mark.unit
def test_constituent_canonicalizes_source_roles() -> None:
    constituent = Constituent(
        axis="op:PrimarySite",
        filler_code="C12400",
        axis_source="role",
        source_roles=("R101", "R100", "R101"),
    )

    assert constituent.source_roles == ("R100", "R101")


@pytest.mark.unit
@pytest.mark.parametrize(
    "axis",
    [
        "PrimarySite",  # missing the op: prefix
        "op:",
        "op:Primary Site",
        "op:Primary_Site",
        "R",
        "R101x",
        "R101 ",
        "",
    ],
)
def test_constituent_rejects_an_axis_that_is_neither_op_nor_a_role(axis: str) -> None:
    """The axis is rendered straight into an IRI by ``legacy_writer._axis_uri``.

    Trailing-garbage cases are what pin ``fullmatch``: ``match`` would accept
    ``"R101x"`` and emit a relation IRI no reader can resolve.
    """
    with pytest.raises(ValueError, match="axis is invalid"):
        Constituent(axis=axis, filler_code="C12400", axis_source="role")


@pytest.mark.unit
@pytest.mark.parametrize(
    "filler_code",
    [
        "12400",  # missing the C prefix
        "C12400 ",
        "C12400junk",
        "MINT-XYZ",  # not lowercase hex
        "MINT-0abc12345de",  # 11 hex, not 12
        "MINT-0abc12345deff",  # 13 hex, not 12
        "",
    ],
)
def test_constituent_rejects_a_filler_that_is_neither_ncit_nor_minted(
    filler_code: str,
) -> None:
    """Only ``C<digits>`` and ``MINT-`` + 12 lowercase hex are producible.

    ``decomp_constituent.filler_code`` is plain ``text`` with no CHECK (migration
    0003), and ``legacy_writer._filler_iri`` renders the value straight into an
    IRI, so this validator is the only guard: anything else is persisted and
    published silently.
    """
    with pytest.raises(ValueError, match="filler_code is invalid"):
        Constituent(axis="R101", filler_code=filler_code, axis_source="role")


@pytest.mark.unit
def test_constituent_accepts_a_minted_filler_of_the_exact_produced_shape() -> None:
    minted = MintedConcept(axis="op:Laterality", label="left")
    constituent = Constituent(
        axis="op:Laterality", filler_code=minted.id, axis_source="nlp"
    )
    assert constituent.filler_code == minted.id


@pytest.mark.unit
@pytest.mark.parametrize("code", ["6135", "C6135x", "C6135 ", "MINT-0abc12345def", ""])
def test_decomposition_and_definition_reject_a_non_ncit_code(code: str) -> None:
    with pytest.raises(ValueError, match="code is invalid"):
        Decomposition(code=code, semantic_type=None)
    with pytest.raises(ValueError, match="root_code is invalid"):
        CompleteDefinition(root_code=code, facts=())


@pytest.mark.unit
def test_models_are_frozen() -> None:
    c = Constituent(axis="R101", filler_code="C12400", axis_source="role")
    with pytest.raises((AttributeError, TypeError)):
        c.filler_code = "C0"  # type: ignore[misc]


@pytest.mark.unit
def test_decomposition_canonicalizes_constituents_to_an_immutable_tuple() -> None:
    source = [Constituent(axis="R101", filler_code="C12400", axis_source="role")]
    decomposition = Decomposition(
        code="C1", semantic_type="Neoplastic Process", constituents=source
    )

    source.clear()

    assert isinstance(decomposition.constituents, tuple)
    assert [item.filler_code for item in decomposition.constituents] == ["C12400"]


@pytest.mark.unit
def test_decomposition_rejects_multiple_primary_sites() -> None:
    with pytest.raises(ValueError, match="PrimarySite cardinality"):
        Decomposition(
            code="C1",
            semantic_type="Neoplastic Process",
            constituents=(
                Constituent(
                    axis="op:PrimarySite",
                    filler_code="C12400",
                    axis_source="role",
                    source_roles=("R101",),
                ),
                Constituent(
                    axis="op:PrimarySite",
                    filler_code="C12401",
                    axis_source="role",
                    source_roles=("R101",),
                ),
            ),
        )


@pytest.mark.unit
def test_decomposition_axes_are_the_distinct_constituent_axes() -> None:
    decomp = Decomposition(
        code="C6135",
        semantic_type="Neoplastic Process",
        constituents=[
            Constituent(axis="R88", filler_code="C27970", axis_source="role"),
            Constituent(axis="R101", filler_code="C12400", axis_source="role"),
            Constituent(
                axis="op:Morphology", filler_code="C40384", axis_source="parent"
            ),
            # A second filler on an existing axis must not inflate the axis set.
            Constituent(axis="R101", filler_code="C12468", axis_source="role"),
        ],
    )
    assert decomp.axes == {"R88", "R101", "op:Morphology"}


@pytest.mark.unit
def test_detection_result_carries_the_gate_inputs() -> None:
    d = DetectionResult(
        code="C6135",
        is_precoordinated=True,
        defining_role_count=4,
        semantic_type="Neoplastic Process",
        label_multi_aspect=True,
    )
    assert d.is_precoordinated
    assert d.defining_role_count == 4


@pytest.mark.unit
def test_constituent_group_defaults_none() -> None:
    assert (
        Constituent(axis="R101", filler_code="C12400", axis_source="role").group is None
    )


@pytest.mark.unit
def test_constituent_accepts_group_id() -> None:
    c = Constituent(
        axis="op:AssociatedRegion",
        filler_code="C12418",
        axis_source="role",
        source_roles=("R101",),
        group="op:AssociatedRegion",
    )
    assert c.group == "op:AssociatedRegion"


@pytest.mark.unit
def test_role_restriction_anchoring_genus_defaults_none() -> None:
    assert RoleRestriction("R101", "C12400").anchoring_genus is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "source_role", ["101", "R", "R101x", "R101 ", "op:PrimarySite"]
)
def test_explicit_source_roles_must_be_ncit_role_codes(source_role: str) -> None:
    """Only the ABSENT source_role case was covered.

    `decomp_constituent.source_roles` carries equivalent JSONB checks (migration
    0022), so persistence fails closed; this validator is what makes the failure
    happen at construction rather than at run commit, and it is the only guard on
    the non-persisting path -- `legacy_writer` renders `source_role` straight into
    an NCIt IRI.
    """
    with pytest.raises(
        ValueError, match="source_roles must contain only NCIt role codes"
    ):
        Constituent(
            axis="op:PrimarySite",
            filler_code="C12400",
            axis_source="role",
            source_roles=(source_role,),
        )


def _restriction_definition(
    anchor_code: str = "C6135",
) -> tuple[CompleteDefinition, str]:
    group_id = canonical_definition_group_id(anchor_code, ("restriction:R101:C12400",))
    fact_id = canonical_definition_fact_id(
        anchor_code, group_id, "restriction", "R101", "C12400"
    )
    return (
        CompleteDefinition(
            root_code=anchor_code,
            facts=(
                RestrictionDefinitionFact(
                    fact_id=fact_id,
                    anchor_code=anchor_code,
                    group_id=group_id,
                    depth=0,
                    role_code="R101",
                    filler_code="C12400",
                ),
            ),
            groups=(
                DefinitionGroup(group_id=group_id, anchor_code=anchor_code, depth=0),
            ),
            root_group_ids=(group_id,),
        ),
        fact_id,
    )


@pytest.mark.unit
def test_nlp_constituents_cannot_claim_a_stated_definition_fact() -> None:
    """An NLP-derived constituent has no stated provenance to cite.

    Letting it reference a fact would launder a label heuristic into the
    proof-bearing projection trace that `source_definition_ids` exists to carry.
    """
    definition, fact_id = _restriction_definition()
    with pytest.raises(
        ValueError, match="NLP constituents cannot reference definition facts"
    ):
        Decomposition(
            code="C6135",
            semantic_type=None,
            constituents=[
                Constituent(
                    axis="op:PrimarySite",
                    filler_code="C12400",
                    axis_source="nlp",
                    source_definition_ids=(fact_id,),
                )
            ],
            complete_definition=definition,
        )


@pytest.mark.unit
def test_parent_constituent_must_reference_its_own_genus_fact() -> None:
    group_id = canonical_definition_group_id("C6135", ("genus:C141041:defined",))
    fact_id = canonical_definition_fact_id(
        "C6135", group_id, "genus", "C141041", "defined"
    )
    definition = CompleteDefinition(
        root_code="C6135",
        facts=(
            GenusDefinitionFact(
                fact_id=fact_id,
                anchor_code="C6135",
                group_id=group_id,
                depth=0,
                genus_code="C141041",
                is_defined=True,
            ),
        ),
        groups=(DefinitionGroup(group_id=group_id, anchor_code="C6135", depth=0),),
        root_group_ids=(group_id,),
    )
    with pytest.raises(
        ValueError, match="parent constituent references an unrelated genus fact"
    ):
        Decomposition(
            code="C6135",
            semantic_type=None,
            constituents=[
                Constituent(
                    axis="op:Parent",
                    filler_code="C99999",  # not the genus the fact records
                    axis_source="parent",
                    source_definition_ids=(fact_id,),
                )
            ],
            complete_definition=definition,
        )


@pytest.mark.unit
def test_complete_definition_rejects_a_non_canonical_group_id() -> None:
    """Group IDs are content-derived, so a mismatched one breaks the proof chain."""
    # The fact's own ID is canonical over this group ID, so the earlier fact-ID
    # and unknown-group checks pass and only the group-ID rule is left to fire.
    bogus_group = "f" * 64
    fact_id = canonical_definition_fact_id(
        "C6135", bogus_group, "restriction", "R101", "C12400"
    )
    with pytest.raises(
        ValueError, match="complete-definition group ID is not canonical"
    ):
        CompleteDefinition(
            root_code="C6135",
            facts=(
                RestrictionDefinitionFact(
                    fact_id=fact_id,
                    anchor_code="C6135",
                    group_id=bogus_group,
                    depth=0,
                    role_code="R101",
                    filler_code="C12400",
                ),
            ),
            groups=(
                DefinitionGroup(group_id=bogus_group, anchor_code="C6135", depth=0),
            ),
            root_group_ids=(bogus_group,),
        )
