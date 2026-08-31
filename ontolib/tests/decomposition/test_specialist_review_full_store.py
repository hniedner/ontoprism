from __future__ import annotations

import re
from pathlib import Path

import pytest
from scripts.research.specialist_cadsr_usage import SpecialistCadsrUsageReport
from scripts.research.specialist_review_packets import (
    PacketIndex,
    validate_specialist_review_generation,
)

pytestmark = [pytest.mark.integration, pytest.mark.full_store]

_PACKETS = Path("tmp/m1-6-specialist-packets")


def test_actual_seven_row_packets_bind_ncit_and_cadsr_without_per_pair_reads() -> None:
    validation = validate_specialist_review_generation(_PACKETS)
    index = PacketIndex.model_validate_json((_PACKETS / "index.json").read_bytes())
    cadsr = SpecialistCadsrUsageReport.model_validate_json(
        Path("tmp/m1-6-specialist-cadsr-usage.json").read_bytes()
    )

    assert validation.status == "passed"
    assert len(index.packets) == 7
    assert tuple(row.status for row in cadsr.rows) == (
        "usage-found",
        "usage-found",
        "usage-found",
        "no-linked-cde",
        "no-linked-cde",
        "no-linked-cde",
        "no-linked-cde",
    )
    assert cadsr.rows[0].cde_ids[:3] == (
        "2494565:3",
        "2677021:3",
        "2794346:1",
    )

    rendered = "\n".join(
        (_PACKETS / entry.path).read_text(encoding="utf-8") for entry in index.packets
    )
    assert "Label unavailable" not in rendered
    assert "MINT-" not in rendered
    assert "P97:" in rendered
    assert "Disease_Has_Primary_Anatomic_Site" in rendered

    ovarian = (_PACKETS / "C102870.md").read_text(encoding="utf-8")
    assert "P4 | `op:Morphology C121619`" in ovarian
    assert "P5 | `op:Morphology C39986`" in ovarian
    lung = (_PACKETS / "C35756.md").read_text(encoding="utf-8")
    assert "P16 | `op:StageSystem C141685`" in lung
    assert "P16 | expected-not-emitted" not in lung
    assert "P19 | `op:StageValue C28064`" in lung
    primary_site = next(
        line for line in lung.splitlines() if "`op:PrimarySite C12468`" in line
    )
    assert primary_site.count("depth=4") == 2
    assert len(re.findall(r"^\| P[0-9]+ \| `", lung, re.MULTILINE)) == 21

    assert "Classify this exact semantic pair" not in rendered
    assert "UNRESOLVED |" not in rendered
    assert "op:NormalTissueOrigin is non-defining" not in rendered
    assert "complete machine inventory included" in rendered
    assert "MINT-" not in rendered
    assert "axis contract legend" in rendered.lower()
    assert "D23" in rendered
    assert "Allowed actions:" in rendered
    assert "source-backed-coordinate-missing" in rendered
    assert "not-found" not in rendered
    assert "specialist must supply" not in rendered.lower()
    assert all(entry.asked_pair_ids for entry in index.packets)
    assert all(
        set(entry.engineering_pair_ids).isdisjoint(entry.action_pair_ids)
        for entry in index.packets
    )
