from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest
from defusedxml.ElementTree import iterparse

from ontolib.decomposition.axes import is_unsupported_filler
from ontolib.decomposition.r103_review import build_r103_review_packet

_NCIT = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#"
_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_OWL = "http://www.w3.org/2002/07/owl#"


def _code(value: str | None) -> str | None:
    if value is None or not value.startswith(_NCIT):
        return None
    return value.removeprefix(_NCIT)


def _real_c3264_scope(
    path: Path,
) -> tuple[frozenset[str], frozenset[tuple[str, str, str]]]:
    class_tag = f"{{{_OWL}}}Class"
    equivalent_tag = f"{{{_OWL}}}equivalentClass"
    intersection_tag = f"{{{_OWL}}}intersectionOf"
    restriction_tag = f"{{{_OWL}}}Restriction"
    description_tag = f"{{{_RDF}}}Description"
    about = f"{{{_RDF}}}about"
    resource = f"{{{_RDF}}}resource"
    on_property = f"{{{_OWL}}}onProperty"
    some_values = f"{{{_OWL}}}someValuesFrom"
    parents: dict[str, list[str]] = defaultdict(list)
    restrictions: list[tuple[str, str, str]] = []
    depth = 0
    for event, element in iterparse(path, events=("start", "end")):
        if element.tag != class_tag:
            continue
        if event == "start":
            depth += 1
            continue
        if depth == 1:
            subject = _code(element.get(about))
            if subject is not None:
                _record_definition_members(
                    element,
                    subject,
                    parents,
                    restrictions,
                    equivalent_tag=equivalent_tag,
                    intersection_tag=intersection_tag,
                    description_tag=description_tag,
                    restriction_tag=restriction_tag,
                    resource=resource,
                    on_property=on_property,
                    some_values=some_values,
                )
            element.clear()
        depth -= 1
    descendants = {"C3264"}
    changed = True
    while changed:
        before = len(descendants)
        descendants.update(
            subject
            for subject, genera in parents.items()
            if descendants.intersection(genera)
        )
        changed = len(descendants) != before
    descendants.remove("C3264")
    descendant_restrictions = frozenset(
        item for item in restrictions if item[0] in descendants
    )
    return frozenset(descendants), descendant_restrictions


def _record_definition_members(
    element: Any,
    subject: str,
    parents: dict[str, list[str]],
    restrictions: list[tuple[str, str, str]],
    **tags: str,
) -> None:
    intersections = element.findall(
        f"./{tags['equivalent_tag']}/{_OWL_CLASS}/{tags['intersection_tag']}"
    )
    for intersection in intersections:
        for member in intersection:
            if member.tag == tags["description_tag"]:
                parent = _code(member.get(tags["resource"]) or member.get(_ABOUT))
                if parent is not None:
                    parents[subject].append(parent)
            elif member.tag == tags["restriction_tag"]:
                role = member.find(tags["on_property"])
                filler = member.find(tags["some_values"])
                role_code = _code(
                    role.get(tags["resource"]) if role is not None else None
                )
                filler_code = _code(
                    filler.get(tags["resource"]) if filler is not None else None
                )
                if role_code == "R103" and filler_code is not None:
                    restrictions.append((subject, role_code, filler_code))


_OWL_CLASS = f"{{{_OWL}}}Class"
_ABOUT = f"{{{_RDF}}}about"


@pytest.mark.integration
@pytest.mark.full_store
def test_r103_review_offline_packet_matches_pinned_stated_source() -> None:
    packet = build_r103_review_packet(
        Path("data/ncit-owl/Thesaurus-stated.owl"),
        Path("data/qlever-ncit/.ontoprism-ncit-candidate.json"),
        Path("ontolib/tests/decomposition/golden/proposal-registry.json"),
    )
    assert tuple(
        (row.subject_code, row.role_code, row.filler_code) for row in packet.rows
    ) == (
        ("C2860", "R103", "C12950"),
        ("C3264", "R103", "C12950"),
        ("C3716", "R103", "C34228"),
    )
    assert packet.source_release == "26.07d"
    assert (
        packet.inventory_scope == "issue-declared assertions, source-presence certified"
    )
    assert packet.method_reference.subject_code == "C3708"
    assert packet.method_reference.is_decision_row is False


@pytest.mark.integration
@pytest.mark.full_store
def test_c3264_exclusion_is_exact_and_does_not_propagate_to_real_descendants() -> None:
    descendants, direct_r103 = _real_c3264_scope(
        Path("data/ncit-owl/Thesaurus-stated.owl")
    )

    assert len(descendants) == 420
    assert direct_r103 == frozenset(
        {
            ("C27291", "R103", "C34222"),
            ("C3716", "R103", "C34228"),
            ("C3717", "R103", "C34222"),
            ("C4827", "R103", "C12397"),
            ("C7063", "R103", "C48689"),
            ("C7646", "R103", "C12397"),
        }
    )
    assert not {assertion for assertion in direct_r103 if assertion[2] == "C12950"}, (
        "SEND BACK: a real C3264 descendant directly asserts R103/C12950"
    )
    assert is_unsupported_filler("C3264", "R103", "C12950")
    assert all(
        not is_unsupported_filler(subject, role, filler)
        for subject, role, filler in direct_r103
    )
