"""Pure parsers mapping the ClinicalTrials.gov v2 JSON tree to read models.

Kept free of I/O so the field-navigation logic (which is where CT.gov's deeply
nested ``protocolSection`` module tree bites) is unit-testable in isolation.
"""

from __future__ import annotations

from typing import Any

from ontolib.repositories.clinicaltrials.models import (
    CTInterventionDetail,
    CTLocation,
    CTOutcome,
    CTReference,
    CTSponsor,
    CTStudyDetail,
    CTStudySummary,
)

_STUDY_URL = "https://clinicaltrials.gov/study/"


def _dict(obj: dict[str, Any], key: str) -> dict[str, Any]:
    """Return ``obj[key]`` as a dict, or ``{}`` if absent/null/non-dict.

    ``dict.get(key, {})`` only defaults when the key is *absent*; CT.gov v2 sometimes
    emits a key with an explicit ``null``, which would then break a chained ``.get``.
    """
    value = obj.get(key)
    return value if isinstance(value, dict) else {}


def _list(obj: dict[str, Any], key: str) -> list[Any]:
    """Return ``obj[key]`` as a list, or ``[]`` if absent/null/non-list."""
    value = obj.get(key)
    return value if isinstance(value, list) else []


def _section(study: dict[str, Any], module: str) -> dict[str, Any]:
    """Return ``protocolSection.<module>`` as a dict (empty if absent)."""
    return _dict(_dict(study, "protocolSection"), module)


def _phase(design: dict[str, Any]) -> str | None:
    phases = _list(design, "phases")
    if phases:
        return ", ".join(str(p) for p in phases)
    return None


def _enrollment(design: dict[str, Any]) -> int | None:
    count = _dict(design, "enrollmentInfo").get("count")
    return count if isinstance(count, int) else None


def _intervention_names(arms: dict[str, Any]) -> list[str]:
    return [
        i["name"]
        for i in _list(arms, "interventions")
        if isinstance(i, dict) and i.get("name")
    ]


def parse_study_summary(
    study: dict[str, Any], *, index: int = 0, total: int = 1
) -> CTStudySummary:
    """Map one CT.gov v2 study object to a :class:`CTStudySummary`.

    ``relevance_score`` is synthesized from result position (``1 - index/total``)
    because the API returns results in relevance order but no explicit score.
    """
    ident = _section(study, "identificationModule")
    status_mod = _section(study, "statusModule")
    design = _section(study, "designModule")
    conditions = _list(_section(study, "conditionsModule"), "conditions")
    arms = _section(study, "armsInterventionsModule")
    score = 1.0 - (index / total) if total else 0.0
    return CTStudySummary(
        nct_id=ident.get("nctId", ""),
        title=ident.get("briefTitle", ""),
        status=status_mod.get("overallStatus"),
        phase=_phase(design),
        conditions=[c for c in conditions if isinstance(c, str)],
        interventions=_intervention_names(arms),
        start_date=_dict(status_mod, "startDateStruct").get("date"),
        enrollment=_enrollment(design),
        relevance_score=round(score, 4),
    )


def _parse_outcomes(outcomes: list[dict[str, Any]]) -> list[CTOutcome]:
    return [
        CTOutcome(
            measure=o.get("measure", ""),
            description=o.get("description"),
            time_frame=o.get("timeFrame"),
        )
        for o in outcomes
        if isinstance(o, dict) and o.get("measure")
    ]


def _parse_interventions(arms: dict[str, Any]) -> list[CTInterventionDetail]:
    return [
        CTInterventionDetail(
            type=i.get("type"), name=i["name"], description=i.get("description")
        )
        for i in _list(arms, "interventions")
        if isinstance(i, dict) and i.get("name")
    ]


def _parse_sponsors(sponsor_mod: dict[str, Any]) -> list[CTSponsor]:
    sponsors: list[CTSponsor] = []
    lead = _dict(sponsor_mod, "leadSponsor")
    if lead.get("name"):
        sponsors.append(CTSponsor(name=lead["name"], role="lead"))
    for collab in _list(sponsor_mod, "collaborators"):
        if isinstance(collab, dict) and collab.get("name"):
            sponsors.append(CTSponsor(name=collab["name"], role="collaborator"))
    return sponsors


def _parse_locations(contacts: dict[str, Any]) -> list[CTLocation]:
    return [
        CTLocation(
            facility=loc.get("facility"),
            city=loc.get("city"),
            state=loc.get("state"),
            country=loc.get("country"),
            status=loc.get("status"),
        )
        for loc in _list(contacts, "locations")
        if isinstance(loc, dict)
    ]


def _parse_references(references_mod: dict[str, Any]) -> list[CTReference]:
    return [
        CTReference(
            pmid=ref.get("pmid"),
            citation=ref.get("citation", ""),
            reference_type=ref.get("type"),
        )
        for ref in _list(references_mod, "references")
        if isinstance(ref, dict) and ref.get("citation")
    ]


def parse_study_detail(study: dict[str, Any]) -> CTStudyDetail:
    """Map one CT.gov v2 study object to a full :class:`CTStudyDetail`."""
    ident = _section(study, "identificationModule")
    status_mod = _section(study, "statusModule")
    design = _section(study, "designModule")
    arms = _section(study, "armsInterventionsModule")
    outcomes = _section(study, "outcomesModule")
    nct_id = ident.get("nctId", "")
    return CTStudyDetail(
        nct_id=nct_id,
        title=ident.get("briefTitle", ""),
        official_title=ident.get("officialTitle"),
        status=status_mod.get("overallStatus"),
        phase=_phase(design),
        study_type=design.get("studyType"),
        primary_purpose=_dict(design, "designInfo").get("primaryPurpose"),
        conditions=[
            c
            for c in _list(_section(study, "conditionsModule"), "conditions")
            if isinstance(c, str)
        ],
        interventions=_parse_interventions(arms),
        primary_outcomes=_parse_outcomes(_list(outcomes, "primaryOutcomes")),
        secondary_outcomes=_parse_outcomes(_list(outcomes, "secondaryOutcomes")),
        eligibility_criteria=_section(study, "eligibilityModule").get(
            "eligibilityCriteria"
        ),
        enrollment=_enrollment(design),
        start_date=_dict(status_mod, "startDateStruct").get("date"),
        sponsors=_parse_sponsors(_section(study, "sponsorCollaboratorsModule")),
        locations=_parse_locations(_section(study, "contactsLocationsModule")),
        references=_parse_references(_section(study, "referencesModule")),
        url=f"{_STUDY_URL}{nct_id}" if nct_id else "",
    )
