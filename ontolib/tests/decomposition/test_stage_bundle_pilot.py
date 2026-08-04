from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from typing import cast

import pytest
from scripts.research.stage_bundle_pilot import (
    STAGE_BUNDLE_RULES,
    build_stage_bundle_report,
    validate_source_audit,
)


@pytest.mark.unit
def test_registry_splits_overlapping_stage_systems_into_named_bundles() -> None:
    counts = Counter(rule.subject_code for rule in STAGE_BUNDLE_RULES)

    assert len(STAGE_BUNDLE_RULES) == 15
    assert counts["C115057"] == 2
    assert counts["C27787"] == 2
    assert counts["C35756"] == 2
    assert all(rule.name for rule in STAGE_BUNDLE_RULES)
    assert all(len(rule.members) == 2 for rule in STAGE_BUNDLE_RULES)


@pytest.mark.unit
def test_valg_method_uses_external_evidence_not_fabricated_ncit_source() -> None:
    valg = next(
        rule
        for rule in STAGE_BUNDLE_RULES
        if rule.rule_id == "stage-c35756-valg-extensive"
    )
    method = next(member for member in valg.members if member.role == "staging-method")

    assert method.filler_code == "C141685"
    assert method.source_facts == ()
    assert set(method.evidence_ids) == {
        "mcode-4.0.0-stage-method",
        "nci-pdq-sclc",
    }


@pytest.mark.unit
def test_registry_source_occurrences_must_exist_in_bound_audit() -> None:
    facts = {
        fact
        for rule in STAGE_BUNDLE_RULES
        for member in rule.members
        for fact in member.source_facts
    }
    audit = {
        "ncit_release": "26.07d",
        "facts": [asdict(fact) | {"group_id": fact.source_group_id} for fact in facts],
    }
    for fact in audit["facts"]:
        del fact["source_group_id"]

    validate_source_audit(audit)
    audit["facts"].pop()
    with pytest.raises(ValueError, match="missing semantic-bundle source fact"):
        validate_source_audit(audit)


@pytest.mark.unit
def test_report_separates_rule_satisfaction_from_unevaluable_associations() -> None:
    engine_pairs: defaultdict[str, set[tuple[str, str]]] = defaultdict(set)
    for rule in STAGE_BUNDLE_RULES:
        engine_pairs[rule.subject_code].update(member.pair for member in rule.members)
    engine_pairs["C101539"].remove(("op:StageSystem", "C140961"))
    engine_pairs["C206219"].remove(("op:StageSystem", "C206211"))
    engine_pairs["C35756"].remove(("op:StageSystem", "C141685"))

    report = build_stage_bundle_report(dict(engine_pairs))

    assert report["scope"] == {
        "family": "cancer-stage-classification",
        "source_value_groups": 13,
        "semantic_bundles": 15,
        "excluded_context_only_subjects": ["C198031"],
    }
    scoring = cast("dict[str, object]", report["engine_rule_satisfaction"])
    assert scoring["bundles"] == {
        "expected": 15,
        "satisfied": 12,
        "incomplete": 3,
        "recall": pytest.approx(0.8),
    }
    assert scoring["member_occurrences"] == {
        "expected": 30,
        "present": 27,
        "missing": 3,
        "recall": pytest.approx(0.9),
    }
    semantic_scores = cast("dict[str, dict[str, str]]", scoring["semantic_scores"])
    assert semantic_scores["exact_bundle"]["status"] == "not-evaluable"
    assert semantic_scores["association"]["status"] == "not-evaluable"
    rules = cast("list[dict[str, object]]", scoring["rules"])
    assert {
        cast("str", item["rule_id"]) for item in rules if item["status"] == "incomplete"
    } == {
        "stage-c101539-ajcc-v7",
        "stage-c206219-figo-2023",
        "stage-c35756-valg-extensive",
    }
