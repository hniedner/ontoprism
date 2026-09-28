"""Static expert packet renders the approved questions and source observations."""

from __future__ import annotations

import csv
import io

import pytest
from scripts.research.expert_assembly import ExpertConcept
from scripts.research.expert_packet import render_packet

pytestmark = pytest.mark.unit


def _concept(**overrides: object) -> ExpertConcept:
    fields = {
        "code": "C10",
        "cohort": "oracle",
        "label": "Tumor <script>bad()</script>",
        "outcome": "decomposed",
        "outcome_reason": "engine emitted 1 constituent",
        "parents": (("C20", "Parent"),),
        "stated_roles": (("R101", "Site", "C30", "Lung"),),
        "constituents": (
            (
                "op:Morphology",
                "C20",
                "Carcinoma",
                True,
                "constituent op:Morphology / C20 needs review",
            ),
        ),
        "flags": (("needs-review", "constituent op:Morphology / C20 needs review"),),
        "genus": (("C20", "Parent"),),
        "anchors": (("C10", "Tumor <script>bad()</script>", True),),
        "cdes": {"C10": (("42", "1", "Test CDE"),), "C20": ()},
    }
    return ExpertConcept(**(fields | overrides))  # type: ignore[arg-type]


def test_packet_escapes_source_text_and_preserves_all_questions() -> None:
    second = _concept(
        code="C11",
        cohort="random",
        label="No decomposition",
        outcome="atomic-no-op",
        outcome_reason="not detected",
        parents=(),
        stated_roles=(),
        constituents=(),
        flags=(),
        genus=(),
        anchors=(),
        cdes={"C11": ()},
    )

    html, csv_text = render_packet(
        (_concept(), second),
        seed=472,
        frame_run="neoplasm-example",
        axis_definitions={"op:Morphology": "Type of neoplasm"},
    )

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "C10 (self)" in html
    assert "1 anchor" in html
    assert "0 anchors" in html
    assert "none found" in html
    assert "R101 Site some C30 — Lung" in html
    assert "42 v1 — Test CDE" in html
    assert "needs review" in html
    assert "Type of neoplasm" in html
    assert "neoplasm-example" in html
    assert "seed 472" in html
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert ("constituent", "C10") in {(r["question"], r["concept_code"]) for r in rows}
    assert ("morphology-choice", "C11") in {
        (r["question"], r["concept_code"]) for r in rows
    }
    assert ("completeness", "C11") in {(r["question"], r["concept_code"]) for r in rows}
    assert ("axis-cardinality", "") in {
        (r["question"], r["concept_code"]) for r in rows
    }
    assert ("cadsr-usefulness", "") in {
        (r["question"], r["concept_code"]) for r in rows
    }
    assert all(not r["verdict"] for r in rows)
    assert "Does the decomposition miss anything" in html
    assert "Are the caDSR links shown useful" in html
    assert "at least 10 decided" in html
    assert "GO WITH CORRECTIONS" in html


def test_packet_rejects_missing_outcome_reason_and_flag_reason() -> None:
    for concept in (
        _concept(outcome_reason=""),
        _concept(flags=(("needs-review", ""),)),
    ):
        with pytest.raises(ValueError, match=r"outcome|flag"):
            render_packet((concept,), seed=472, frame_run="run", axis_definitions={})


def test_cde_cap_and_lookup_limit_label() -> None:
    known = tuple((str(i), "1", f"CDE {i}") for i in range(12))
    limited = tuple((str(i), "1", f"CDE {i}") for i in range(1000))
    concept = _concept(cdes={"C10": known, "C20": limited})

    html, csv_text = render_packet(
        (concept,),
        seed=472,
        frame_run="run",
        axis_definitions={"op:Morphology": "Type of neoplasm"},
    )

    assert html.count("CDE 0</li>") == 2
    assert "CDE 9</li>" in html
    assert "CDE 10</li>" not in html
    assert "2 more" in html
    assert "at least 1,000 (lookup limit)" in html
    assert "990 more" not in html
    assert "CDE 999" not in html
    _, baseline_csv = render_packet(
        (_concept(),),
        seed=472,
        frame_run="run",
        axis_definitions={"op:Morphology": "Type of neoplasm"},
    )
    assert csv_text == baseline_csv
