from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from scripts.research.specialist_cadsr_usage import SpecialistCadsrUsageReport
from scripts.research.specialist_review_packets import (
    DispatchManifest,
    PacketIndex,
    validate_dispatch_bundle,
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
    assert validation.readiness_meaning == (
        "ontology/publication readiness; generation validation status is structural "
        "readiness; dispatch readiness is separate"
    )
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
    context_note = (
        "This packet contains 56 context-only pairs. Their optional context-correction "
        "response regions are schema-supported and do not request clinical answers or "
        "ontology actions."
    )
    assert index.context_correction_note == context_note
    assert sum(len(entry.context_pair_ids) for entry in index.packets) == 56
    rendered_context_note = context_note.replace("56 context-only", "5 context-only")
    assert rendered.count(rendered_context_note) == 1
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
    assert tuple(entry.code for entry in index.packets if entry.asked_pair_ids) == (
        "C100054",
    )
    assert all(
        set(entry.engineering_pair_ids).isdisjoint(entry.action_pair_ids)
        for entry in index.packets
    )
    assert index.registered_mint_expected_set == ()
    assert {entry.code: entry.action_pair_ids for entry in index.packets} == {
        "C27262": (),
        "C102870": (),
        "C6135": (),
        "C4791": (),
        "C100054": ("P3", "P4"),
        "C198031": (),
        "C35756": (),
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
        == (len(entry.asked_pair_ids) if entry.dispatch_status == "dispatchable" else 0)
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
    assert index.release_ready_codes == ("C100054",)
    assert index.withheld_codes == (
        "C27262",
        "C102870",
        "C6135",
        "C4791",
        "C198031",
        "C35756",
    )
    assert index.release_ready is False
    assert {entry.code: entry.dispatch_status for entry in index.packets} == {
        "C27262": "withheld",
        "C102870": "withheld",
        "C6135": "withheld",
        "C4791": "withheld",
        "C100054": "dispatchable",
        "C198031": "withheld",
        "C35756": "withheld",
    }
    assert all(
        entry.withholding_reasons
        for entry in index.packets
        if entry.dispatch_status == "withheld"
    )
    assert all(
        contract.citation_ids
        for entry in index.packets
        for contract in entry.pair_contracts
        if contract.pair_id in entry.asked_pair_ids
    )
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
    assert "NOT FOR DISPATCH" in ovarian
    assert "## Return workflow" not in ovarian
    thyroid = (_PACKETS / "C6135.md").read_text(encoding="utf-8")
    assert "not a generic malignant neuroendocrine-cell operand" not in thyroid
    assert "without making poor differentiation universal" not in thyroid
    for entry in index.packets:
        if entry.action_pair_ids and entry.dispatch_status == "dispatchable":
            packet = (_PACKETS / entry.path).read_text(encoding="utf-8")
            assert "CUSTOM-CURRENT-MODEL response syntax" in packet
            assert "synthetic IDs only: `PX1; PX2`" in packet
    eye = (_PACKETS / "C100054.md").read_text(encoding="utf-8")
    eye_entry = next(entry for entry in index.packets if entry.code == "C100054")
    assert eye_entry.asked_pair_ids == ("P3", "P4")
    assert eye_entry.action_pair_ids == ("P3", "P4")
    assert eye_entry.engineering_pair_ids == ()
    assert eye_entry.context_pair_ids == ("P1", "P2", "P5", "P6", "P7")
    assert eye_entry.workload.model_dump() == {
        "asked": 2,
        "action": 2,
        "engineering": 0,
        "context": 5,
    }
    assert eye.startswith(
        "# C100054 — Conjunctival Melanocytic Intraepithelial Lesion\n"
    )
    assert "Conjunctival Melanocytic Intraepithelial Neoplasia" not in eye
    assert "WHO-EYE04" in eye
    assert "WHO-EYE05" in eye
    assert "### Clinical question for P3" in eye
    assert "### Clinical question for P4" in eye
    assert "**Current scoreable baseline partition:**" in eye
    assert "**Historical proposal warning and partition:**" in eye
    assert "[[ONTOPRISM:STAGE-A:START]]" in eye
    assert "[[ONTOPRISM:STAGE-B:START]]" in eye
    recipient = "R. Hannes Niedner, M.D., OntoPrism project coordinator"
    assert recipient in eye
    assert (
        f"Packet reference: C100054 / NCIt 26.07d / full row contract SHA-256: "
        f"{eye_entry.row_contract_identity}"
    ) in eye
    assert "manifest binds the full packet SHA-256" in eye
    assert (
        "If the secure delivery channel is unavailable, contact R. Hannes Niedner, "
        "M.D., OntoPrism project coordinator before transmitting review material."
    ) in eye
    assert "request receipt confirmation" in eye.lower()
    assert (
        "## Engineering blockers and consequences\n\nNone for this packet generation."
        in eye
    )
    baseline = "G1={P1}; G2={P2}; G3={P5}; G4={P6}; G5={P7}"
    assert "**Current scoreable baseline partition:** G1={P1 " in eye
    assert "**Historical proposal warning and partition:**" in eye
    assert "op:" in eye.split("**Current scoreable baseline partition:**", 1)[1]
    assert " — " in eye.split("**Current scoreable baseline partition:**", 1)[1]
    assert eye_entry.grouping_contract.allowed_dispositions == ("CUSTOM-CURRENT-MODEL",)
    assert "Partition modes: CUSTOM-CURRENT-MODEL" in eye
    assert (
        "If both P3 and P4 are removed, reproduce the baseline exactly: " + baseline
        in eye
    )
    assert "If P3 or P4 is promoted, place each promoted pair ID explicitly" in eye
    assert all(
        forbidden not in eye
        for forbidden in (
            "RETAIN-CURRENT",
            "GROUP-SPECIFIED-PAIRS-TOGETHER",
            "KEEP-SPECIFIED-PAIRS-SEPARATE",
            "EMPTY",
        )
    )
    assert eye_entry.pair_contracts[2].citation_ids == ("mudhar-2024",)
    assert eye_entry.pair_contracts[3].citation_ids == (
        "milman-2023-low-grade",
        "milman-2023-high-grade",
    )
    assert "so atypia degree is classification-dependent" not in eye
    assert "does not by itself establish" not in eye
    for pair_id in ("P3", "P4"):
        question = eye.split(f"### Clinical question for {pair_id}", 1)[1].split(
            "[[ONTOPRISM:STAGE-A-PAIR", 1
        )[0]
        assert "Supporting source facts:" in question
        source_binding = (
            "NCIt source `C36027` — Non-Invasive Lesion"
            if pair_id == "P3"
            else "NCIt source `C8326` — Cytologic Atypia"
        )
        assert source_binding in question
        assert "Source feature signature:" in question
        assert "Passage scope: exclusive" in question
        assert "does not establish clinical entailment" in question
        if pair_id == "P3":
            assert "**mudhar-2024:**" in question
            assert "**milman-2023:**" not in question
        else:
            assert "**milman-2023-low-grade:**" in question
            assert "**milman-2023-high-grade:**" in question
            assert "**mudhar-2024:**" not in question
    for pair_id in ("P1", "P2", "P5", "P6", "P7"):
        assert f"{pair_id}: context-not-under-review." in eye
        assert f"### Clinical question for {pair_id}" not in eye
    assert "A factual correction is optional." in eye
    dispatch = Path("tmp/m1-6-specialist-dispatch")
    assert {path.name for path in dispatch.iterdir()} == {
        "C100054.md",
        "dispatch-manifest.json",
    }
    assert (dispatch / "C100054.md").read_bytes() == (
        _PACKETS / "C100054.md"
    ).read_bytes()
    manifest_payload = (dispatch / "dispatch-manifest.json").read_bytes()
    manifest = DispatchManifest.model_validate_json(manifest_payload)
    assert manifest.dispatch_ready is True
    assert manifest.recipient == recipient
    assert "R. Hannes Niedner, M.D." in manifest.contact_instruction
    assert manifest.release_ready_codes == ("C100054",)
    assert (
        validate_dispatch_bundle(
            dispatch_directory=dispatch,
            packet_directory=_PACKETS,
            index=index,
        )
        == manifest
    )
    manifest_values = json.loads(manifest_payload)
    manifest_values.pop("manifest_identity")
    assert (
        manifest.manifest_identity
        == hashlib.sha256(
            (json.dumps(manifest_values, indent=2, sort_keys=True) + "\n").encode()
        ).hexdigest()
    )
    assert (
        manifest.packets[0].sha256
        == hashlib.sha256((dispatch / "C100054.md").read_bytes()).hexdigest()
    )
    assert "Expected post-return output (not a current bundle file)" in eye
