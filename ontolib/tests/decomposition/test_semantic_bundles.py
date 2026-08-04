from __future__ import annotations

from dataclasses import replace

import pytest

from ontolib.decomposition.semantic_bundles import (
    SemanticBundle,
    SemanticBundleMember,
    SemanticBundleRule,
    SourceFactReference,
    generate_semantic_bundles,
    score_semantic_bundles,
)


def _source_fact(
    filler_code: str,
    *,
    group_id: str,
) -> SourceFactReference:
    return SourceFactReference(
        ncit_release="26.07d",
        root_code="C115057",
        role_code="R88",
        filler_code=filler_code,
        anchor_code="C132736",
        depth=2,
        source_group_id=group_id,
    )


def _member(
    role: str,
    filler_code: str,
    *,
    group_id: str = "a" * 64,
) -> SemanticBundleMember:
    axis = "op:StageValue" if role == "stage-value" else "op:StageSystem"
    return SemanticBundleMember(
        role=role,
        axis=axis,
        filler_code=filler_code,
        source_facts=(_source_fact(filler_code, group_id=group_id),),
    )


def _rule(
    rule_id: str,
    name: str,
    stage_type: str,
    stage_value: str,
    *,
    edition: str,
) -> SemanticBundleRule:
    return SemanticBundleRule(
        rule_id=rule_id,
        subject_code="C115057",
        kind="cancer-stage-classification",
        name=name,
        members=(
            _member("stage-type", stage_type, group_id="a" * 64),
            _member("stage-value", stage_value, group_id="b" * 64),
        ),
        qualifiers=(("authority", "AJCC"), ("edition", edition)),
        evidence_ids=("mcode-4.0.0", f"ajcc-{edition}"),
    )


@pytest.mark.unit
def test_generation_preserves_overlapping_edition_bundles() -> None:
    rules = (
        _rule(
            "stage-c115057-ajcc-v6",
            "AJCC v6 Stage I lip and oral cavity squamous cell carcinoma",
            "C90529",
            "C27966",
            edition="6",
        ),
        _rule(
            "stage-c115057-ajcc-v7",
            "AJCC v7 Stage I lip and oral cavity squamous cell carcinoma",
            "C90530",
            "C27966",
            edition="7",
        ),
    )

    result = generate_semantic_bundles(
        "C115057",
        {
            ("op:StageSystem", "C90529"),
            ("op:StageSystem", "C90530"),
            ("op:StageValue", "C27966"),
        },
        rules,
    )

    assert [bundle.name for bundle in result.bundles] == [
        "AJCC v6 Stage I lip and oral cavity squamous cell carcinoma",
        "AJCC v7 Stage I lip and oral cavity squamous cell carcinoma",
    ]
    assert result.incomplete == ()
    assert (
        sum(
            member.pair == ("op:StageValue", "C27966")
            for bundle in result.bundles
            for member in bundle.members
        )
        == 2
    )
    assert {
        member.source_facts[0].source_group_id for member in result.bundles[0].members
    } == {"a" * 64, "b" * 64}


@pytest.mark.unit
def test_generation_reports_incomplete_bundle_without_inventing_it() -> None:
    rule = _rule(
        "stage-c115057-ajcc-v7",
        "AJCC v7 Stage I lip and oral cavity squamous cell carcinoma",
        "C90530",
        "C27966",
        edition="7",
    )

    result = generate_semantic_bundles(
        "C115057",
        {("op:StageValue", "C27966")},
        (rule,),
    )

    assert result.bundles == ()
    assert result.incomplete[0].rule_id == rule.rule_id
    assert [member.pair for member in result.incomplete[0].missing_members] == [
        ("op:StageSystem", "C90530")
    ]
    assert [member.pair for member in result.incomplete[0].present_members] == [
        ("op:StageValue", "C27966")
    ]


@pytest.mark.unit
def test_stage_bundle_requires_one_value_and_splits_distinct_stage_types() -> None:
    valid = _rule(
        "stage-c115057-ajcc-v7",
        "AJCC v7 Stage I lip and oral cavity squamous cell carcinoma",
        "C90530",
        "C27966",
        edition="7",
    )

    with pytest.raises(ValueError, match="exactly one stage-value"):
        replace(valid, members=(valid.members[0],))
    with pytest.raises(ValueError, match="at most one stage-type"):
        replace(
            valid,
            members=(
                valid.members[0],
                _member("stage-type", "C90529"),
                valid.members[1],
            ),
        )


@pytest.mark.unit
def test_bundle_identity_is_semantic_not_editorial_or_evidentiary() -> None:
    rule = _rule(
        "stage-c115057-ajcc-v7",
        "AJCC v7 Stage I lip and oral cavity squamous cell carcinoma",
        "C90530",
        "C27966",
        edition="7",
    )
    bundle = SemanticBundle.from_rule(rule)
    revised = replace(
        bundle,
        name="Editorially improved name",
        evidence_ids=("ajcc-7", "mcode-4.0.0", "loinc-75620-5"),
    )

    assert revised.identity == bundle.identity


@pytest.mark.unit
def test_bundle_score_detects_wrong_associations_with_identical_flat_pairs() -> None:
    expected = (
        SemanticBundle.from_rule(
            _rule("one", "Edition 6", "C90529", "C27966", edition="6")
        ),
        SemanticBundle.from_rule(
            _rule("two", "Edition 7", "C90530", "C27970", edition="7")
        ),
    )
    actual = (
        SemanticBundle.from_rule(
            _rule("wrong-one", "Wrong edition 6", "C90529", "C27970", edition="6")
        ),
        SemanticBundle.from_rule(
            _rule("wrong-two", "Wrong edition 7", "C90530", "C27966", edition="7")
        ),
    )

    assert {member.pair for bundle in expected for member in bundle.members} == {
        member.pair for bundle in actual for member in bundle.members
    }

    result = score_semantic_bundles(expected, actual)

    assert result.exact_bundle.true_positive == 0
    assert result.exact_bundle.expected == 2
    assert result.exact_bundle.actual == 2
    assert result.association.true_positive == 0
    assert result.association.expected == 2
    assert result.association.actual == 2
    assert result.contextual_member.true_positive == 2
    assert result.contextual_member.expected == 4
    assert result.contextual_member.actual == 4


@pytest.mark.unit
def test_generation_rejects_duplicate_rule_identity() -> None:
    rule = _rule(
        "stage-c115057-ajcc-v7",
        "AJCC v7 Stage I lip and oral cavity squamous cell carcinoma",
        "C90530",
        "C27966",
        edition="7",
    )

    with pytest.raises(ValueError, match="rule IDs must be unique"):
        generate_semantic_bundles(
            "C115057",
            {member.pair for member in rule.members},
            (rule, rule),
        )


@pytest.mark.unit
def test_member_requires_source_fact_or_explicit_external_evidence() -> None:
    external = SemanticBundleMember(
        role="stage-type",
        axis="op:StageSystem",
        filler_code="C141685",
        source_facts=(),
        evidence_ids=("mcode-4.0.0-stage-method", "nci-pdq-sclc"),
    )

    assert external.source_facts == ()
    assert external.evidence_ids == (
        "mcode-4.0.0-stage-method",
        "nci-pdq-sclc",
    )
    with pytest.raises(ValueError, match="source fact or external evidence"):
        SemanticBundleMember(
            role="stage-type",
            axis="op:StageSystem",
            filler_code="C141685",
            source_facts=(),
            evidence_ids=(),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"ncit_release": ""}, "ncit_release must not be empty"),
        ({"root_code": "not-a-code"}, "root_code is invalid"),
        ({"role_code": "P88"}, "role_code is invalid"),
        ({"filler_code": "C-1"}, "filler_code is invalid"),
        ({"anchor_code": ""}, "anchor_code is invalid"),
        ({"depth": -1}, "depth must be non-negative"),
        ({"source_group_id": "short"}, "source_group_id is invalid"),
    ],
)
def test_source_fact_reference_rejects_invalid_provenance(
    changes: dict[str, object], message: str
) -> None:
    fact = _source_fact("C27966", group_id="a" * 64)

    with pytest.raises(ValueError, match=message):
        replace(fact, **changes)


@pytest.mark.unit
def test_member_rejects_invalid_or_conflicting_support() -> None:
    fact = _source_fact("C27966", group_id="a" * 64)

    for axis in ("R88", "op:"):
        with pytest.raises(ValueError, match="axis must be an op: term"):
            SemanticBundleMember(
                role="stage-value",
                axis=axis,
                filler_code="C27966",
                source_facts=(fact,),
            )
    with pytest.raises(ValueError, match="source fact references must be unique"):
        SemanticBundleMember(
            role="stage-value",
            axis="op:StageValue",
            filler_code="C27966",
            source_facts=(fact, fact),
        )
    with pytest.raises(ValueError, match="source fact filler must match"):
        SemanticBundleMember(
            role="stage-value",
            axis="op:StageValue",
            filler_code="C27970",
            source_facts=(fact,),
        )
    with pytest.raises(ValueError, match="evidence IDs must be unique"):
        SemanticBundleMember(
            role="staging-method",
            axis="op:StageSystem",
            filler_code="C141685",
            source_facts=(),
            evidence_ids=("nci-pdq", "nci-pdq"),
        )


@pytest.mark.unit
def test_rule_rejects_ambiguous_metadata_and_invalid_members() -> None:
    valid = _rule(
        "stage-c115057-ajcc-v7",
        "AJCC v7 Stage I lip and oral cavity squamous cell carcinoma",
        "C90530",
        "C27966",
        edition="7",
    )

    with pytest.raises(ValueError, match="qualifier keys must be unique"):
        replace(valid, qualifiers=(("edition", "7"), ("edition", "8")))
    with pytest.raises(ValueError, match="evidence IDs must be unique"):
        replace(valid, evidence_ids=("source", "source"))
    with pytest.raises(ValueError, match="at least one member"):
        replace(valid, kind="other", members=())
    with pytest.raises(ValueError, match="members must be unique"):
        replace(valid, kind="other", members=(valid.members[0], valid.members[0]))
    with pytest.raises(ValueError, match="source fact root must match"):
        replace(valid, subject_code="C1")

    non_stage = replace(valid, kind="other", members=(valid.members[0],))
    assert non_stage.members == (valid.members[0],)


@pytest.mark.unit
def test_stage_bundle_allows_at_most_one_staging_method() -> None:
    valid = _rule(
        "stage-c115057-ajcc-v7",
        "AJCC v7 Stage I lip and oral cavity squamous cell carcinoma",
        "C90530",
        "C27966",
        edition="7",
    )
    methods = (
        SemanticBundleMember(
            role="staging-method",
            axis="op:StageSystem",
            filler_code=code,
            source_facts=(),
            evidence_ids=("external",),
        )
        for code in ("C141685", "C15432")
    )

    with pytest.raises(ValueError, match="at most one staging-method"):
        replace(valid, members=(*methods, valid.members[1]))


@pytest.mark.unit
def test_generation_rejects_duplicate_semantics_and_ignores_other_subjects() -> None:
    rule = _rule(
        "stage-c115057-ajcc-v7",
        "AJCC v7 Stage I lip and oral cavity squamous cell carcinoma",
        "C90530",
        "C27966",
        edition="7",
    )
    duplicate = replace(rule, rule_id="renamed", name="Editorial rename")

    with pytest.raises(ValueError, match="rule identities must be unique"):
        generate_semantic_bundles(
            "C115057",
            {member.pair for member in rule.members},
            (rule, duplicate),
        )
    result = generate_semantic_bundles(
        "C999999",
        {member.pair for member in rule.members},
        (rule,),
    )
    assert result.bundles == ()
    assert result.incomplete == ()


@pytest.mark.unit
def test_semantic_score_metrics_cover_exact_empty_and_zero_match_cases() -> None:
    expected = SemanticBundle.from_rule(
        _rule("one", "Expected", "C90529", "C27966", edition="6")
    )
    wrong = SemanticBundle.from_rule(
        _rule("two", "Wrong", "C90530", "C27970", edition="6")
    )

    exact = score_semantic_bundles((expected,), (expected,)).exact_bundle
    assert exact.exact
    assert exact.precision == 1.0
    assert exact.recall == 1.0
    assert exact.f1 == 1.0

    empty = score_semantic_bundles((), ()).exact_bundle
    assert empty.precision == 1.0
    assert empty.recall == 1.0
    assert empty.f1 == 1.0

    no_match = score_semantic_bundles((expected,), (wrong,)).exact_bundle
    assert not no_match.exact
    assert no_match.precision == 0.0
    assert no_match.recall == 0.0
    assert no_match.f1 == 0.0

    with pytest.raises(ValueError, match="expected bundles must have unique"):
        score_semantic_bundles((expected, expected), ())
