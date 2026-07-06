"""Unit tests for the pure ClinicalTrials.gov v2 JSON parsers.

The parser is where CT.gov's deeply nested, frequently-partial ``protocolSection``
module tree bites, so it earns dedicated isolation tests covering missing modules
and malformed values — the high-value cases fairdata's client tests exercised.
"""

from __future__ import annotations

import pytest

from ontolib.repositories.clinicaltrials.parser import (
    parse_study_detail,
    parse_study_summary,
)


def _study(**modules: dict) -> dict:
    return {"protocolSection": modules}


@pytest.mark.unit
def test_summary_of_minimal_study_uses_safe_defaults() -> None:
    # Only an identification module — every other module absent must not crash and
    # must yield empty/None defaults, not KeyErrors.
    study = _study(identificationModule={"nctId": "NCT00000001", "briefTitle": "Bare"})
    summary = parse_study_summary(study)
    assert summary.nct_id == "NCT00000001"
    assert summary.title == "Bare"
    assert summary.status is None
    assert summary.phase is None
    assert summary.conditions == []
    assert summary.interventions == []
    assert summary.enrollment is None


@pytest.mark.unit
def test_summary_joins_multiple_phases() -> None:
    study = _study(
        identificationModule={"nctId": "NCT00000002", "briefTitle": "Two phase"},
        designModule={"phases": ["PHASE1", "PHASE2"]},
    )
    assert parse_study_summary(study).phase == "PHASE1, PHASE2"


@pytest.mark.unit
def test_summary_enrollment_non_int_is_none() -> None:
    study = _study(
        identificationModule={"nctId": "NCT00000003", "briefTitle": "x"},
        designModule={"enrollmentInfo": {"count": "not-a-number"}},
    )
    assert parse_study_summary(study).enrollment is None


@pytest.mark.unit
def test_summary_relevance_reflects_position() -> None:
    study = _study(identificationModule={"nctId": "NCT00000004", "briefTitle": "x"})
    first = parse_study_summary(study, index=0, total=4)
    last = parse_study_summary(study, index=3, total=4)
    assert first.relevance_score == 1.0
    assert last.relevance_score == 0.25


@pytest.mark.unit
def test_detail_of_minimal_study_has_empty_collections() -> None:
    study = _study(identificationModule={"nctId": "NCT00000005", "briefTitle": "Bare"})
    detail = parse_study_detail(study)
    assert detail.interventions == []
    assert detail.primary_outcomes == []
    assert detail.secondary_outcomes == []
    assert detail.sponsors == []
    assert detail.locations == []
    assert detail.references == []
    assert detail.eligibility_criteria is None
    # The public trial URL is derived from the NCT id.
    assert detail.url == "https://clinicaltrials.gov/study/NCT00000005"


@pytest.mark.unit
def test_detail_sponsors_order_lead_then_collaborators() -> None:
    study = _study(
        identificationModule={"nctId": "NCT00000006", "briefTitle": "x"},
        sponsorCollaboratorsModule={
            "leadSponsor": {"name": "Lead Co"},
            "collaborators": [{"name": "Collab A"}, {"name": "Collab B"}],
        },
    )
    sponsors = parse_study_detail(study).sponsors
    assert [(s.name, s.role) for s in sponsors] == [
        ("Lead Co", "lead"),
        ("Collab A", "collaborator"),
        ("Collab B", "collaborator"),
    ]


@pytest.mark.unit
def test_detail_drops_references_without_citation() -> None:
    study = _study(
        identificationModule={"nctId": "NCT00000007", "briefTitle": "x"},
        referencesModule={
            "references": [
                {"pmid": "111", "citation": "Kept et al."},
                {"pmid": "222"},  # no citation → dropped
            ]
        },
    )
    refs = parse_study_detail(study).references
    assert len(refs) == 1
    assert refs[0].pmid == "111"


@pytest.mark.unit
def test_detail_url_empty_without_nct_id() -> None:
    study = _study(identificationModule={"briefTitle": "no id"})
    assert parse_study_detail(study).url == ""


@pytest.mark.unit
def test_parses_modules_present_with_explicit_null() -> None:
    # CT.gov v2 sometimes emits a module/field key with an explicit JSON null rather
    # than omitting it; `.get(key, {})` would return None and crash a chained access.
    # The null-safe helpers must treat null like absent (no crash, safe defaults).
    study = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT00000008", "briefTitle": "Nulls"},
            "designModule": {
                "enrollmentInfo": None,
                "phases": None,
                "designInfo": None,
            },
            "statusModule": {"startDateStruct": None},
            "armsInterventionsModule": {"interventions": None},
            "outcomesModule": {"primaryOutcomes": None, "secondaryOutcomes": None},
            "sponsorCollaboratorsModule": {"leadSponsor": None, "collaborators": None},
            "contactsLocationsModule": {"locations": None},
            "referencesModule": {"references": None},
            "conditionsModule": {"conditions": None},
        }
    }
    summary = parse_study_summary(study)
    assert summary.enrollment is None
    assert summary.phase is None
    assert summary.conditions == []
    assert summary.interventions == []
    detail = parse_study_detail(study)
    assert detail.interventions == []
    assert detail.primary_outcomes == []
    assert detail.sponsors == []
    assert detail.locations == []
    assert detail.references == []
    assert detail.start_date is None
    assert detail.primary_purpose is None
