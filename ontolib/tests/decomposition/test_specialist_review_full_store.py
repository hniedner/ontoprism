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


def test_actual_seven_row_packets_bind_ncit_and_cadsr_without_per_pair_reads() -> None:  # noqa: PLR0915
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
    assert cadsr.database_path == "data/cadsr/cde_repository.db"
    assert not Path(cadsr.database_path).is_absolute()
    assert cadsr.database_sha256
    assert cadsr.query_identity
    assert cadsr.report_identity
    assert cadsr.rows[0].cdes
    assert all(item.long_name and item.short_name for item in cadsr.rows[0].cdes)

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
    p16_section = next(line for line in lung.splitlines() if "P16 | `" in line)
    assert "engineering-only" in p16_section
    assert "QUESTION P16" not in lung
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
    assert index.registered_mint_expected_set == ()
    assert {entry.code: entry.action_pair_ids for entry in index.packets} == {
        "C27262": ("P3", "P4"),
        "C102870": (),
        "C6135": ("P6", "P8", "P9", "P12"),
        "C4791": ("P5", "P6"),
        "C100054": ("P3", "P4"),
        "C198031": ("P8", "P9"),
        "C35756": (
            "P4",
            "P6",
            "P7",
            "P8",
            "P10",
            "P11",
            "P12",
            "P18",
            "P19",
            "P20",
            "P21",
        ),
    }
    assert all(
        set(contract.allowed_actions)
        <= {
            "RETAIN-SCOREABLE",
            "PROMOTE-SCOREABLE",
            "REMOVE-FROM-PROJECTION",
        }
        for entry in index.packets
        for contract in entry.pair_contracts
    )
    assert all(
        entry.dispatch_status == "dispatchable" or entry.withholding_reasons
        for entry in index.packets
    )
    assert all(not Path(entry.path).is_absolute() for entry in index.packets)
    assert all(
        len(
            re.findall(
                r"^### Clinical question for P[0-9]+$",
                (_PACKETS / entry.path).read_text(),
                re.MULTILINE,
            )
        )
        == len(entry.asked_pair_ids)
        for entry in index.packets
    )
    assert all(
        contract.source_evidence_status != "unavailable"
        for entry in index.packets
        for contract in entry.pair_contracts
        if contract.allowed_actions
    )
    assert all(
        set(contract.allowed_actions) == set(contract.consequence_by_action)
        for entry in index.packets
        for contract in entry.pair_contracts
    )
    assert all(entry.dispatch_status == "dispatchable" for entry in index.packets)
    assert {
        "PMC6821118",
        "PMC8683221",
        "PMC11905437",
        "PMC4063430",
        "PMC10646822",
        "PMC3351680",
    } <= set(re.findall(r"PMC[0-9]+", rendered))
    assert all("/Users/" not in key for key in index.input_identities)
    assert "pdm run" not in rendered
    assert "<!-- QUESTION" not in rendered
    assert "<!-- Allowed actions" not in rendered
    ovarian_entry = next(entry for entry in index.packets if entry.code == "C102870")
    assert ovarian_entry.stage_b_mode == "not-applicable-pending-engineering"
    assert "[[ONTOPRISM:STAGE-B:START]]" not in ovarian
    assert "Stage B signature" not in ovarian
