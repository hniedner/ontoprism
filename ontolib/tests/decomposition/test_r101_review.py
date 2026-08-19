from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
import pytest_asyncio
from openpyxl import load_workbook
from openpyxl.cell import Cell
from scripts import adjudication
from scripts.adjudication import _parser

from ontolib.decomposition.provenance_models import NcitSourceSnapshot
from ontolib.decomposition.r101_conservation import (
    R101ConservationReport,
    R101ConservationValidationError,
    load_r101_conservation_report,
    validate_r101_publication,
)
from ontolib.decomposition.r101_review import (
    QLeverReviewLabels,
    R101DecisionRegistry,
    R101ReviewPacket,
    R101ReviewValidationError,
    ReviewPath,
    ReviewPattern,
    apply_r101_authorization,
    build_r101_review_packet,
    dry_run_r101_authorization,
    import_r101_review_decisions,
    load_r101_decision_registry,
    load_r101_review_packet,
    write_r101_review_packet,
    write_r101_review_workbook,
)

if TYPE_CHECKING:
    from openpyxl.worksheet.worksheet import Worksheet


GOLDEN = Path(__file__).parent / "golden"
REPORT_PATH = GOLDEN / "neoplasm-r101-v4-conservation.json.gz"
SOURCE_MANIFEST = Path("data/qlever-ncit/.ontoprism-ncit-candidate.json")


class _Labels:
    def __init__(self, overrides: dict[str, list[str]] | None = None) -> None:
        self.query_count = 0
        self.overrides = overrides or {}

    async def labels_for_review(
        self, codes: tuple[str, ...]
    ) -> dict[str, tuple[str, ...]]:
        self.query_count += 1
        return {
            code: tuple(self.overrides.get(code, [f"TEST label {code}"]))
            for code in codes
        }


def _headers(sheet: Worksheet) -> dict[str, int]:
    result: dict[str, int] = {}
    for cell in sheet[1]:
        assert isinstance(cell.column, int)
        result[str(cell.value)] = cell.column
    return result


def _test_identity(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def packet_and_labels():
    report = load_r101_conservation_report(REPORT_PATH)
    labels = _Labels()
    packet = await build_r101_review_packet(report, SOURCE_MANIFEST, labels)
    return report, packet, labels


@pytest.mark.unit
async def test_packet_exhaustively_binds_the_generated_report(
    packet_and_labels,
) -> None:
    report, packet, labels = packet_and_labels

    assert len(packet.patterns) == len(report.grouping_presentation) == 162
    assert sum(row.occurrence_count for row in packet.patterns) == 3291
    assert len(packet.occurrences) == 3291
    assert {row.occurrence_id for row in packet.occurrences} == {
        item.occurrence_id
        for item in report.occurrences
        if item.disposition == "covered-by-retained-r82"
    }
    assert labels.query_count == 1
    assert packet.bindings.report_identity == report.report_identity
    assert packet.bindings.json_identity == report.json_identity
    assert packet.bindings.tsv_identity == report.tsv_identity
    assert packet.bindings.source_identity == report.source_identity
    assert packet.bindings.proof_identity == report.proof_identity
    assert all(row.axis == "op:PrimarySite" for row in packet.patterns)
    assert all(row.row_identity and row.pattern_id for row in packet.patterns)
    assert all(
        path.source_identity == report.source_identity
        for row in packet.patterns
        for path in row.paths
    )
    assert sum(row.evidence_kind_counts.one_step for row in packet.patterns) == 1954
    assert sum(row.evidence_kind_counts.closure_only for row in packet.patterns) == 1337
    for row in packet.patterns:
        assert row.sentinel_c6135 == ("C6135" in row.affected_concept_ids)
        assert row.sentinel_c101539 == ("C101539" in row.affected_concept_ids)
        assert row.sentinel_c4791 == ("C4791" in row.affected_concept_ids)
    assert packet.review_scope == (
        "Decide coverage under OntoPrism project policy for the directed stated R82 "
        "path; this is not equivalence. Source occurrences remain preserved, and a "
        "decision applies only to this exact packet/report/source digest."
    )


@pytest.mark.unit
async def test_packet_identity_and_label_boundary_fail_closed(
    packet_and_labels, tmp_path
) -> None:
    report, packet, _ = packet_and_labels
    payload = packet.model_dump(mode="json")
    payload["patterns"][0]["old_broader_label"] = "tampered"
    path = tmp_path / "packet.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(R101ReviewValidationError, match="identity differs"):
        load_r101_review_packet(path)

    for overrides, message in (
        ({"C12727": []}, "missing label"),
        ({"C12727": ["one", "two"]}, "multiple labels"),
        ({"C12727": [""]}, "malformed label"),
    ):
        with pytest.raises(R101ReviewValidationError, match=message):
            await build_r101_review_packet(report, SOURCE_MANIFEST, _Labels(overrides))


def _fill_test_only_decisions(workbook: Path, *, reject_row: int | None = None) -> None:
    book = load_workbook(workbook)
    sheet = book["Pattern Decisions"]
    headers = _headers(sheet)
    for row_number in range(2, sheet.max_row + 1):
        sheet.cell(
            row_number,
            headers["Decision"],
            "reject" if row_number == reject_row else "approve",
        )
        sheet.cell(
            row_number, headers["Rationale"], "TEST-ONLY synthetic preflight decision"
        )
        sheet.cell(
            row_number, headers["Reviewer Identity"], "TEST-ONLY automated preflight"
        )
        sheet.cell(row_number, headers["Review Date"], "2099-01-01")
    book.save(workbook)


@pytest.mark.unit
async def test_real_xlsx_roundtrip_and_exact_dry_run_path(
    packet_and_labels, tmp_path
) -> None:
    report, packet, _ = packet_and_labels
    packet_path = tmp_path / "packet.json"
    workbook = tmp_path / "review.xlsx"
    registry_path = tmp_path / "registry.json"
    write_r101_review_packet(packet_path, packet)
    write_r101_review_workbook(workbook, packet)

    book = load_workbook(workbook)
    assert book.sheetnames == [
        "Instructions",
        "Bindings",
        "Pattern Decisions",
        "Occurrence Evidence",
    ]
    assert book["Bindings"].sheet_state == "veryHidden"
    assert book["Pattern Decisions"].max_row == 163
    assert book["Occurrence Evidence"].max_row == 3292
    assert not book.calculation.fullCalcOnLoad
    assert workbook.stat().st_size < 10_000_000
    _fill_test_only_decisions(workbook)

    registry = import_r101_review_decisions(
        packet, workbook, registry_path, provenance="test-only"
    )
    assert load_r101_decision_registry(registry_path) == registry
    assert len(registry.decisions) == 162
    assert registry.status == "proposed"
    assert registry.provenance == "test-only"
    result = dry_run_r101_authorization(report, packet, registry)
    assert result.verdict == "logically-eligible"
    assert result.report_identity == report.report_identity
    assert result.provenance == "test-only"
    assert result.writes_performed is False
    with pytest.raises(
        R101ConservationValidationError, match="content-authorization-missing"
    ):
        validate_r101_publication(report)

    rejected = tmp_path / "rejected.xlsx"
    write_r101_review_workbook(rejected, packet)
    _fill_test_only_decisions(rejected, reject_row=2)
    rejected_registry = import_r101_review_decisions(
        packet, rejected, tmp_path / "rejected.json", provenance="test-only"
    )
    assert (
        dry_run_r101_authorization(report, packet, rejected_registry).verdict
        == "blocked"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("evidence", "evidence"),
        ("reorder", "order"),
        ("missing", "exactly one decision"),
        ("duplicate", "pattern"),
        ("extra", "extra"),
        ("formula", "formula"),
        ("stale-binding", "binding"),
    ],
)
async def test_workbook_tampering_and_non_total_decisions_refuse(
    packet_and_labels, tmp_path, mutation: str, message: str
) -> None:
    _, packet, _ = packet_and_labels
    workbook = tmp_path / f"{mutation}.xlsx"
    write_r101_review_workbook(workbook, packet)
    _fill_test_only_decisions(workbook)
    book = load_workbook(workbook)
    sheet = book["Pattern Decisions"]
    headers = _headers(sheet)
    if mutation == "evidence":
        sheet.cell(2, headers["Old Broader Code"], "C999")
    elif mutation == "reorder":
        first = str(sheet.cell(2, headers["Pattern ID"]).value)
        second = str(sheet.cell(3, headers["Pattern ID"]).value)
        first_cell = sheet.cell(2, headers["Pattern ID"])
        second_cell = sheet.cell(3, headers["Pattern ID"])
        assert isinstance(first_cell, Cell)
        assert isinstance(second_cell, Cell)
        first_cell.value = second
        second_cell.value = first
    elif mutation == "missing":
        sheet.cell(2, headers["Decision"]).value = None
    elif mutation == "duplicate":
        sheet.cell(3, headers["Pattern ID"], sheet.cell(2, headers["Pattern ID"]).value)
    elif mutation == "extra":
        sheet.cell(sheet.max_row + 1, headers["Pattern ID"], "extra")
    elif mutation == "formula":
        sheet.cell(2, headers["Rationale"], "=1+1")
    elif mutation == "stale-binding":
        book["Bindings"]["B2"] = "0" * 64
    book.save(workbook)

    with pytest.raises(R101ReviewValidationError, match=message):
        import_r101_review_decisions(
            packet,
            workbook,
            tmp_path / "must-not-exist.json",
            provenance="test-only",
        )
    assert not (tmp_path / "must-not-exist.json").exists()


@pytest.mark.unit
async def test_xlsx_macros_external_links_and_stale_registry_refuse(
    packet_and_labels, tmp_path
) -> None:
    report, packet, _ = packet_and_labels
    workbook = tmp_path / "review.xlsx"
    write_r101_review_workbook(workbook, packet)
    _fill_test_only_decisions(workbook)

    for member, message in (
        ("xl/vbaProject.bin", "macro"),
        ("xl/externalLinks/externalLink1.xml", "external link"),
    ):
        tampered = tmp_path / f"{Path(member).name}.xlsx"
        tampered.write_bytes(workbook.read_bytes())
        with ZipFile(tampered, "a", ZIP_DEFLATED) as archive:
            archive.writestr(member, b"TEST-ONLY")
        with pytest.raises(R101ReviewValidationError, match=message):
            import_r101_review_decisions(
                packet, tampered, tmp_path / "out.json", provenance="test-only"
            )

    registry = import_r101_review_decisions(
        packet, workbook, tmp_path / "registry.json", provenance="test-only"
    )
    stale = registry.model_copy(update={"packet_identity": "0" * 64})
    with pytest.raises(R101ReviewValidationError, match="packet identity"):
        dry_run_r101_authorization(report, packet, stale)


@pytest.mark.unit
async def test_registry_loader_and_dry_run_refusal_gates_are_live(
    packet_and_labels, tmp_path
) -> None:
    report, packet, _ = packet_and_labels
    workbook = tmp_path / "review.xlsx"
    registry_path = tmp_path / "registry.json"
    write_r101_review_workbook(workbook, packet)
    _fill_test_only_decisions(workbook)
    registry = import_r101_review_decisions(
        packet, workbook, registry_path, provenance="test-only"
    )

    for name, mutation, message in (
        (
            "missing",
            lambda payload: payload["decisions"].pop(),
            "registry identity",
        ),
        (
            "extra",
            lambda payload: payload["decisions"].append(payload["decisions"][0]),
            "canonical and unique",
        ),
        (
            "duplicate",
            lambda payload: payload["decisions"][1].update(
                {"pattern_id": payload["decisions"][0]["pattern_id"]}
            ),
            "canonical and unique",
        ),
        (
            "invalid-decision",
            lambda payload: payload["decisions"][0].update({"decision": "maybe"}),
            "decision",
        ),
        (
            "empty-rationale",
            lambda payload: payload["decisions"][0].update({"rationale": ""}),
            "rationale",
        ),
    ):
        payload = registry.model_dump(mode="json")
        mutation(payload)
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload))
        with pytest.raises(R101ReviewValidationError, match=message):
            load_r101_decision_registry(path)

    incomplete_payload = registry.model_dump(mode="json", exclude={"registry_identity"})
    incomplete_payload["decisions"].pop()
    incomplete_path = tmp_path / "bound-incomplete.json"
    incomplete_path.write_text(
        json.dumps(
            {
                **incomplete_payload,
                "registry_identity": _test_identity(incomplete_payload),
            }
        )
    )
    incomplete = load_r101_decision_registry(incomplete_path)
    with pytest.raises(R101ReviewValidationError, match="pattern inventory"):
        dry_run_r101_authorization(report, packet, incomplete)

    for stale, message in (
        (registry.model_copy(update={"report_identity": "0" * 64}), "report identity"),
        (registry.model_copy(update={"source_identity": "0" * 64}), "source identity"),
    ):
        with pytest.raises(R101ReviewValidationError, match=message):
            dry_run_r101_authorization(report, packet, stale)


@pytest.mark.unit
def test_path_and_pattern_schema_refusal_gates_are_live(packet_and_labels) -> None:
    _, packet, _ = packet_and_labels
    path_payload = packet.patterns[0].paths[0].model_dump(mode="json")
    for field, value, message in (
        ("labels", ["too", "short"], "path labels"),
        ("fact_identities", ["0" * 64], "path facts"),
        ("path_identity", "0" * 64, "path identity"),
    ):
        mutated = {**path_payload, field: value}
        with pytest.raises(ValueError, match=message):
            ReviewPath.model_validate_json(json.dumps(mutated))

    pattern_payload = packet.patterns[0].model_dump(mode="json")
    for mutation, message in (
        (
            lambda payload: payload.update(
                {"affected_concept_count": payload["affected_concept_count"] + 1}
            ),
            "affected concept count",
        ),
        (
            lambda payload: payload.update(
                {"affected_occurrence_count": payload["affected_occurrence_count"] + 1}
            ),
            "affected occurrence count",
        ),
        (
            lambda payload: payload.update(
                {"occurrence_count": payload["occurrence_count"] + 1}
            ),
            "pattern occurrence count",
        ),
        (
            lambda payload: payload["evidence_kind_counts"].update(
                {"one_step": payload["evidence_kind_counts"]["one_step"] + 1}
            ),
            "pattern evidence counts",
        ),
        (
            lambda payload: payload.update({"row_identity": "0" * 64}),
            "row identity",
        ),
    ):
        mutated = json.loads(json.dumps(pattern_payload))
        mutation(mutated)
        with pytest.raises(ValueError, match=message):
            ReviewPattern.model_validate_json(json.dumps(mutated))


class _SelectRows:
    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = rows

    async def select(self, query: str, *, required_variables=()):
        assert "VALUES ?c" in query
        assert "versionInfo" not in query
        assert "SELECT DISTINCT ?c ?label" in query
        assert "MIN(" not in query
        assert tuple(required_variables) == ("c", "label")
        return self.rows


class _ProductionShapedLabelRows:
    async def select(self, query: str, *, required_variables=()):
        codes = sorted(set(re.findall(r"#(C[0-9]+)>", query)))
        rows = [
            {
                "c": f"http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#{code}",
                "label": f"TEST label {code}",
            }
            for code in codes
        ]
        rows.append(
            {
                "c": "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C12727",
                "label": "Second distinct stated label",
            }
        )
        return rows


@pytest.mark.unit
async def test_qlever_label_reader_refusal_gates_are_live() -> None:
    iri = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C1"
    valid = {"c": iri, "label": "One"}
    reader = QLeverReviewLabels(_SelectRows([valid]))
    assert await reader.labels_for_review(("C1",)) == {"C1": ("One",)}
    assert reader.query_count == 1

    duplicate = QLeverReviewLabels(_SelectRows([valid, valid]))
    assert await duplicate.labels_for_review(("C1",)) == {"C1": ("One",)}

    distinct = QLeverReviewLabels(
        _SelectRows([valid, {**valid, "label": "Other stated label"}])
    )
    assert await distinct.labels_for_review(("C1",)) == {
        "C1": ("One", "Other stated label")
    }

    for row, message in (
        ({**valid, "c": "not-an-ncit-iri"}, "code binding"),
        ({key: value for key, value in valid.items() if key != "label"}, "label"),
    ):
        with pytest.raises(R101ReviewValidationError, match=message):
            await QLeverReviewLabels(_SelectRows([row])).labels_for_review(("C1",))


@pytest.mark.unit
async def test_real_reader_and_double_both_refuse_distinct_stated_labels() -> None:
    report = load_r101_conservation_report(REPORT_PATH)
    for labels in (
        QLeverReviewLabels(_ProductionShapedLabelRows()),
        _Labels({"C12727": ["TEST label C12727", "Second distinct stated label"]}),
    ):
        with pytest.raises(
            R101ReviewValidationError, match="multiple labels for C12727"
        ):
            await build_r101_review_packet(report, SOURCE_MANIFEST, labels)


@pytest.mark.unit
async def test_prepare_certifies_explicit_live_source_before_label_read(
    monkeypatch, tmp_path, capsys
) -> None:
    report = load_r101_conservation_report(REPORT_PATH)
    events: list[str] = []

    async def source_snapshot(manifest: Path, endpoint: str) -> NcitSourceSnapshot:
        events.append("source-certified")
        assert manifest == SOURCE_MANIFEST
        assert endpoint == "http://qlever.test"
        return NcitSourceSnapshot(
            source_identity=report.source_identity,
            ontology_version=report.source_release_id,
        )

    class ClientContext:
        async def __aenter__(self):
            events.append("label-client-opened")
            return object()

        async def __aexit__(self, *_args):
            return None

    class Labels:
        query_count = 1

        def __init__(self, _client) -> None:
            events.append("label-reader-created")

    packet = SimpleNamespace(packet_identity="a" * 64, patterns=(1,), occurrences=(1,))

    async def build(*_args):
        events.append("labels-read")
        return packet

    monkeypatch.setattr(adjudication, "_source_snapshot", source_snapshot)
    monkeypatch.setattr(
        adjudication, "ncit_sparql_client", lambda _endpoint: ClientContext()
    )
    monkeypatch.setattr(adjudication, "QLeverReviewLabels", Labels)
    monkeypatch.setattr(adjudication, "build_r101_review_packet", build)
    monkeypatch.setattr(adjudication, "write_r101_review_packet", lambda *_args: None)
    monkeypatch.setattr(adjudication, "write_r101_review_workbook", lambda *_args: None)

    await adjudication._prepare_r101_review(
        cast(
            "Any",
            SimpleNamespace(
                report=REPORT_PATH,
                source_manifest=SOURCE_MANIFEST,
                endpoint="http://qlever.test",
                output_packet=tmp_path / "packet.json",
                output_xlsx=tmp_path / "packet.xlsx",
            ),
        )
    )

    assert events == [
        "source-certified",
        "label-client-opened",
        "label-reader-created",
        "labels-read",
    ]
    assert "source_checks=9 label_reads=1 qlever_reads=10" in capsys.readouterr().err


@pytest.mark.unit
@pytest.mark.parametrize(
    "observed",
    [
        NcitSourceSnapshot(source_identity="0" * 64, ontology_version="26.07d"),
        NcitSourceSnapshot(source_identity="a" * 64, ontology_version="drifted"),
    ],
)
async def test_prepare_refuses_source_identity_or_release_drift_before_labels(
    monkeypatch, tmp_path, observed
) -> None:
    report = load_r101_conservation_report(REPORT_PATH)
    if observed.source_identity == "a" * 64:
        observed = observed.model_copy(
            update={"source_identity": report.source_identity}
        )

    async def source_snapshot(_manifest: Path, _endpoint: str) -> NcitSourceSnapshot:
        return observed

    monkeypatch.setattr(adjudication, "_source_snapshot", source_snapshot)
    monkeypatch.setattr(
        adjudication,
        "ncit_sparql_client",
        lambda _endpoint: pytest.fail("labels read before source refusal"),
    )

    with pytest.raises(R101ConservationValidationError, match="live source"):
        await adjudication._prepare_r101_review(
            cast(
                "Any",
                SimpleNamespace(
                    report=REPORT_PATH,
                    source_manifest=SOURCE_MANIFEST,
                    endpoint="http://qlever.test",
                    output_packet=tmp_path / "packet.json",
                    output_xlsx=tmp_path / "packet.xlsx",
                ),
            )
        )
    assert not (tmp_path / "packet.json").exists()
    assert not (tmp_path / "packet.xlsx").exists()


@pytest.mark.unit
async def test_xlsx_generation_is_byte_deterministic_and_explains_its_boundaries(
    packet_and_labels, tmp_path
) -> None:
    _, packet, _ = packet_and_labels
    first = tmp_path / "first.xlsx"
    second = tmp_path / "second.xlsx"

    write_r101_review_workbook(first, packet)
    write_r101_review_workbook(second, packet)

    assert first.read_bytes() == second.read_bytes()
    with ZipFile(first) as archive:
        assert archive.namelist() == sorted(archive.namelist())
        assert {item.date_time for item in archive.infolist()} == {
            (1980, 1, 1, 0, 0, 0)
        }
    book = load_workbook(first)
    instructions = "\n".join(
        str(cell.value) for row in book["Instructions"].iter_rows() for cell in row
    )
    assert "anti-accident" in instructions
    assert "revalidates every cell" in instructions
    assert "FALSE means no covered occurrence" in instructions


@pytest.mark.unit
async def test_registry_provenance_identity_and_real_application_refusal(
    packet_and_labels, tmp_path
) -> None:
    report, packet, _ = packet_and_labels
    workbook = tmp_path / "review.xlsx"
    write_r101_review_workbook(workbook, packet)
    _fill_test_only_decisions(workbook)
    registry = import_r101_review_decisions(
        packet,
        workbook,
        tmp_path / "test-registry.json",
        provenance="test-only",
    )

    payload = registry.model_dump(mode="python")
    payload["provenance"] = "sme"
    with pytest.raises(ValueError, match="registry identity"):
        R101DecisionRegistry.model_validate(payload)
    with pytest.raises(R101ReviewValidationError, match="test-only"):
        apply_r101_authorization(report, packet, registry)

    sme_registry = import_r101_review_decisions(
        packet, workbook, tmp_path / "sme-registry.json", provenance="sme"
    )
    candidate = apply_r101_authorization(report, packet, sme_registry)
    validate_r101_publication(candidate)
    reloaded = R101ConservationReport.model_validate_json(candidate.model_dump_json())
    assert reloaded == candidate
    assert candidate.content_authorization.authorized_digest == report.json_identity


@pytest.mark.unit
async def test_authorization_application_exercises_every_publication_refusal_branch(
    packet_and_labels, tmp_path
) -> None:
    report, packet, _ = packet_and_labels
    workbook = tmp_path / "review.xlsx"
    write_r101_review_workbook(workbook, packet)
    _fill_test_only_decisions(workbook)
    registry = import_r101_review_decisions(
        packet, workbook, tmp_path / "registry.json", provenance="sme"
    )
    accepted = apply_r101_authorization(report, packet, registry)
    validate_r101_publication(accepted)

    mutations = (
        (
            accepted.model_copy(
                update={
                    "counts": accepted.counts.model_copy(update={"non_r101_delta": 1})
                }
            ),
            "non-r101-delta",
        ),
        (accepted.model_copy(update={"mechanical_status": "incomplete"}), "unresolved"),
        (
            accepted.model_copy(
                update={
                    "content_authorization": accepted.content_authorization.model_copy(
                        update={"status": "pending", "authorized_digest": None}
                    )
                }
            ),
            "authorization-missing",
        ),
        (
            accepted.model_copy(
                update={
                    "content_authorization": accepted.content_authorization.model_copy(
                        update={
                            "status": "digest-mismatch",
                            "authorized_digest": "0" * 64,
                        }
                    )
                }
            ),
            "digest-mismatch",
        ),
        (accepted.model_copy(update={"publication_gate": "blocked"}), "missing"),
    )
    for mutated, message in mutations:
        with pytest.raises(R101ConservationValidationError, match=message):
            validate_r101_publication(mutated)


@pytest.mark.unit
def test_packet_and_registry_totals_are_report_derived(packet_and_labels) -> None:
    _, packet, _ = packet_and_labels
    retained_pattern = packet.patterns[0]
    packet_payload = packet.model_dump(mode="python", exclude={"packet_identity"})
    packet_payload["patterns"] = (retained_pattern.model_dump(mode="python"),)
    packet_payload["occurrences"] = tuple(
        row.model_dump(mode="python")
        for row in packet.occurrences
        if row.pattern_id == retained_pattern.pattern_id
    )
    smaller_packet = R101ReviewPacket.model_validate(
        {**packet_payload, "packet_identity": _test_identity(packet_payload)}
    )
    decision = {
        "pattern_id": retained_pattern.pattern_id,
        "decision": "approve",
        "rationale": "TEST-ONLY",
        "reviewer_identity": "TEST-ONLY",
        "review_date": "2099-01-01",
    }
    registry_payload = {
        "schema_version": 1,
        "status": "proposed",
        "provenance": "test-only",
        "packet_identity": smaller_packet.packet_identity,
        "report_identity": smaller_packet.bindings.report_identity,
        "source_identity": smaller_packet.bindings.source_identity,
        "decisions": (decision,),
    }
    registry = R101DecisionRegistry.model_validate(
        {**registry_payload, "registry_identity": _test_identity(registry_payload)}
    )
    assert len(registry.decisions) == len(smaller_packet.patterns) == 1


@pytest.mark.unit
def test_import_cli_requires_explicit_registry_provenance() -> None:
    common = [
        "import-r101-review-decisions",
        "--packet",
        "packet.json",
        "--reviewed-xlsx",
        "review.xlsx",
        "--output",
        "registry.json",
    ]
    with pytest.raises(SystemExit):
        _parser().parse_args(common)
    assert _parser().parse_args([*common, "--provenance", "test-only"]).provenance == (
        "test-only"
    )


@pytest.mark.unit
async def test_packet_and_workbook_container_refusal_gates_are_live(
    packet_and_labels, tmp_path
) -> None:
    _, packet, _ = packet_and_labels
    duplicate_json = tmp_path / "duplicate.json"
    duplicate_json.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(R101ReviewValidationError, match="duplicate JSON key"):
        load_r101_review_packet(duplicate_json)

    invalid_xlsx = tmp_path / "invalid.xlsx"
    invalid_xlsx.write_bytes(b"not an xlsx")
    with pytest.raises(R101ReviewValidationError, match="invalid XLSX"):
        import_r101_review_decisions(
            packet, invalid_xlsx, tmp_path / "out.json", provenance="test-only"
        )

    workbook = tmp_path / "visible-bindings.xlsx"
    write_r101_review_workbook(workbook, packet)
    book = load_workbook(workbook)
    book["Bindings"].sheet_state = "visible"
    book.save(workbook)
    with pytest.raises(R101ReviewValidationError, match="visibility"):
        import_r101_review_decisions(
            packet, workbook, tmp_path / "out.json", provenance="test-only"
        )


@pytest.mark.unit
async def test_packet_builder_report_refusal_gates_are_live(
    packet_and_labels,
) -> None:
    report, _, _ = packet_and_labels
    for changed, message in (
        (report.model_copy(update={"source_identity": "0" * 64}), "source manifest"),
        (report.model_copy(update={"source_release_id": "stale"}), "source manifest"),
        (report.model_copy(update={"mechanical_status": "incomplete"}), "incomplete"),
        (
            report.model_copy(
                update={
                    "counts": report.counts.model_copy(
                        update={
                            "covered_by_retained_r82": (
                                report.counts.covered_by_retained_r82 + 1
                            )
                        }
                    )
                }
            ),
            "does not exhaust",
        ),
        (
            report.model_copy(update={"grouping_presentation": ()}),
            "does not exhaust",
        ),
    ):
        with pytest.raises(R101ReviewValidationError, match=message):
            await build_r101_review_packet(changed, SOURCE_MANIFEST, _Labels())

    covered_index = next(
        index
        for index, occurrence in enumerate(report.occurrences)
        if occurrence.disposition == "covered-by-retained-r82"
    )
    covered = report.occurrences[covered_index]
    for changed_occurrence, message in (
        (covered.model_copy(update={"old_links": ()}), "review-groupable"),
        (
            covered.model_copy(
                update={
                    "retained_r82_target": covered.retained_r82_target.model_copy(
                        update={"axis": "op:Other"}
                    )
                }
            ),
            "cross-axis",
        ),
    ):
        occurrences = list(report.occurrences)
        occurrences[covered_index] = changed_occurrence
        changed = report.model_copy(update={"occurrences": tuple(occurrences)})
        with pytest.raises(R101ReviewValidationError, match=message):
            await build_r101_review_packet(changed, SOURCE_MANIFEST, _Labels())
