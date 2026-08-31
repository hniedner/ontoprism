from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Literal

import pytest
from scripts.adjudication import _parser
from scripts.research.specialist_cadsr_usage import (
    CadsrUsageRow,
    SpecialistCadsrUsageReport,
)
from scripts.research.specialist_literature_context import (
    generate_specialist_literature_context,
)
from scripts.research.specialist_review_packets import (
    CONCEPT_ORDER,
    GenerationValidation,
    PacketIndex,
    generate_specialist_review_packets,
    validate_source_preferred_labels,
    validate_specialist_review_generation,
)
from scripts.validation.run_agent_replay import run_agent_replay

pytestmark = pytest.mark.unit


def _response_regions(text: str) -> tuple[tuple[int, int], ...]:
    starts = [
        match.start() for match in re.finditer(r"\[\[ONTOPRISM:[^\]]+:START\]\]", text)
    ]
    ends = [match.end() for match in re.finditer(r"\[\[ONTOPRISM:[^\]]+:END\]\]", text)]
    return tuple(zip(starts, ends, strict=True))


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path, tuple[Path, ...]]:
    literature = tmp_path / "literature.json"
    context = generate_specialist_literature_context(
        Path("scripts/research/data/specialist_literature_context_26_07d.json"),
        literature,
    )
    registry = tmp_path / "registry.json"
    registry.write_text('{"proposals": []}', encoding="utf-8")
    cadsr = tmp_path / "cadsr.json"
    cadsr.write_text(
        SpecialistCadsrUsageReport(
            schema_version=2,
            database_path="data/cadsr/cde_repository.db",
            source_identity="a" * 64,
            database_sha256="b" * 64,
            query_identity="c" * 64,
            report_identity="d" * 64,
            source_provenance="fixture provenance",
            producing_command="fixed",
            query_limit=10,
            rows=tuple(
                CadsrUsageRow(
                    code=code,
                    status="no-linked-cde",
                    cde_ids=(),
                    cdes=(),
                    truncated=False,
                    error=None,
                )
                for code in CONCEPT_ORDER
            ),
            interpretation=(
                "caDSR usage does not determine clinical or ontology correctness."
            ),
        ).model_dump_json(),
        encoding="utf-8",
    )
    concepts = []
    ranges = []
    draft_concepts: dict[str, object] = {}
    relation_names = (
        "expected_matched_scoreable",
        "expected_emitted_review_bearing",
        "expected_not_emitted",
        "current_only_scoreable",
        "current_only_review_bearing",
        "current_only_proposed",
    )
    for dossier in context.dossiers:
        keys = sorted(
            {
                (key.axis, key.filler)
                for question in dossier.questions
                for key in question.pair_keys
            }
        )
        relations = {name: [] for name in relation_names}
        for number, key in enumerate(keys):
            relations[relation_names[number % (len(relation_names) - 1)]].append(
                list(key)
            )
            ranges.append(
                {
                    "code": dossier.code,
                    "axis": key[0],
                    "filler": key[1],
                    "current_projection_status": "scoreable-release-bound",
                    "verdict": {"status": "valid"},
                }
            )
        concepts.append(
            {
                "code": dossier.code,
                "actual_groups": [],
                "non_scoreable_emitted_pairs": [],
                "pair_relations": relations,
                "actual_partition": [],
                "expected_partition": [],
            }
        )
        draft_concepts[dossier.code] = {
            "genus": {"code": dossier.code, "label": dossier.exact_label},
            "morphology": {"code": keys[0][1], "label": f"Label {keys[0][1]}"},
            "review_buckets": {
                "curated_projection": [
                    {
                        "axis": axis,
                        "axis_label": axis,
                        "filler": filler,
                        "filler_label": f"Label {filler}",
                    }
                    for axis, filler in keys
                ]
            },
        }
    group = tmp_path / "groups.json"
    group.write_text(
        json.dumps({"schema_version": 4, "review_boundary": {}, "concepts": concepts}),
        encoding="utf-8",
    )
    diagnostic = tmp_path / "diagnostics.json"
    diagnostic.write_text(
        json.dumps(
            {"schema_version": 3, "candidate_rows": [], "range_diagnostics": ranges}
        ),
        encoding="utf-8",
    )
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "artifact_identity": "a" * 64,
                "concepts": [
                    {
                        "code": dossier.code,
                        "constituents": [
                            {
                                "axis": axis,
                                "filler": filler,
                                "source_definition_ids": ["b" * 64],
                            }
                            for axis, filler in sorted(
                                {
                                    (key.axis, key.filler)
                                    for question in dossier.questions
                                    for key in question.pair_keys
                                }
                            )
                        ],
                    }
                    for dossier in context.dossiers
                ],
            }
        ),
        encoding="utf-8",
    )
    comparison = tmp_path / "comparison.json"
    comparison.write_text("{}", encoding="utf-8")
    labels = tmp_path / "labels.json"
    labels.write_text(
        json.dumps({"_meta": {}, "concepts": draft_concepts}), encoding="utf-8"
    )
    return (
        literature,
        registry,
        cadsr,
        (diagnostic, evidence, comparison, group, labels),
    )


def test_source_preferred_label_contract_rejects_a_synonym_substitution(
    tmp_path: Path,
) -> None:
    context_path = tmp_path / "literature.json"
    context = generate_specialist_literature_context(
        Path("scripts/research/data/specialist_literature_context_26_07d.json"),
        context_path,
    )
    labels = {row.code: row.exact_label for row in context.dossiers}
    context = context.model_copy(
        update={
            "dossiers": tuple(
                row.model_copy(
                    update={
                        "exact_label": (
                            "Conjunctival Melanocytic Intraepithelial Neoplasia"
                        )
                    }
                )
                if row.code == "C100054"
                else row
                for row in context.dossiers
            )
        }
    )

    with pytest.raises(
        ValueError,
        match=(
            "literature/context label does not match NCIt stated preferred label: "
            "C100054"
        ),
    ):
        validate_source_preferred_labels(context, labels)


def test_generator_writes_schema_three_and_bound_validation_deterministically(  # noqa: PLR0915
    tmp_path: Path,
) -> None:
    literature, registry, cadsr, additional = _inputs(tmp_path)
    output = tmp_path / "packets"
    first = generate_specialist_review_packets(
        literature_context_path=literature,
        proposal_registry_path=registry,
        cadsr_usage_path=cadsr,
        output_directory=output,
        producing_command="fixed",
        additional_input_paths=additional,
    )
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    second = generate_specialist_review_packets(
        literature_context_path=literature,
        proposal_registry_path=registry,
        cadsr_usage_path=cadsr,
        output_directory=output,
        producing_command="fixed",
        additional_input_paths=additional,
    )
    assert first == second
    assert before == {path.name: path.read_bytes() for path in output.iterdir()}
    assert (
        PacketIndex.model_validate_json(
            (output / "index.json").read_bytes()
        ).schema_version
        == 3
    )
    generated_index = PacketIndex.model_validate_json(
        (output / "index.json").read_bytes()
    )
    context_note = (
        "No context-only pairs occur in this seven-row generation; "
        "context-correction support is schema-tested but not exercised by this bundle."
    )
    assert generated_index.context_correction_note == context_note
    assert all(not entry.context_pair_ids for entry in generated_index.packets)
    assert all(not path.startswith("/") for path in generated_index.input_identities)
    validation = validate_specialist_review_generation(output)
    assert validation.status == "passed"
    assert validation.readiness is False
    assert validation.release_ready is False
    assert validation.withheld_codes
    assert {path.name for path in output.iterdir()} == {
        *(f"{code}.md" for code in CONCEPT_ORDER),
        "index.json",
        "generation-validation.json",
    }
    markdown = (output / "C102870.md").read_text(encoding="utf-8")
    assert "P97:" in markdown
    assert "[[ONTOPRISM:STAGE-A:START]]" not in markdown
    assert "C121619" in markdown
    assert "C39986" in markdown
    assert "STAGE-A-RESPONSE" not in markdown
    assert "pdm run" not in markdown
    assert "git " not in markdown.lower()
    assert "## Return workflow" not in markdown
    assert context_note in markdown
    assert "NOT FOR DISPATCH" in markdown
    if not next(
        entry for entry in generated_index.packets if entry.code == "C102870"
    ).action_pair_ids:
        assert "[[ONTOPRISM:STAGE-B:START]]" not in markdown
        assert "Stage B signature" not in markdown
        assert "not-applicable-pending-engineering" in markdown

    all_markdown = "\n".join(
        (output / f"{code}.md").read_text(encoding="utf-8") for code in CONCEPT_ORDER
    )
    assert all_markdown.count(context_note) == len(CONCEPT_ORDER)
    for entry in generated_index.packets:
        if not entry.action_pair_ids or entry.dispatch_status == "withheld":
            continue
        packet = (output / entry.path).read_text(encoding="utf-8")
        assert (
            "CUSTOM-CURRENT-MODEL response syntax: after `Groups`, write one complete "
            "group per line as semicolon-separated ending-scoreable pair IDs. Every "
            "ending-scoreable pair ID must occur exactly once across all lines."
            in packet
        )
        assert (
            "Neutral syntax example using synthetic IDs only: `PX1; PX2` on one line "
            "and `PX3` on the next line." in packet
        )
    assert (
        "no human action that creates or silently ignores an absent pair"
        in all_markdown
    )
    assert "<!-- QUESTION" not in all_markdown
    assert "<!-- Allowed actions" not in all_markdown
    for entry in generated_index.packets:
        packet = (output / entry.path).read_text(encoding="utf-8")
        if entry.dispatch_status == "dispatchable":
            workflow = packet.split("## Return workflow", 1)[1].split("## ", 1)[0]
            assert workflow.count("OntoPrism project coordinator") == 3
            assert re.findall(r"(?m)^(\d+)\.", workflow) == ["1", "2", "3", "4"]
        else:
            assert "## Return workflow" not in packet
            assert "NOT FOR DISPATCH" in packet
        for line in packet.splitlines():
            if re.search(r"(?i)(signature|attester|attestation).*(?:___|:$)", line):
                assert any(
                    start < packet.index(line) < end
                    for start, end in _response_regions(packet)
                )
    assert all(entry.generated is False for entry in generated_index.packets)
    assert all(
        entry.expected_return_validation_path == f"{entry.code}.validation.json"
        for entry in generated_index.packets
    )
    dispatch = output.parent / "m1-6-specialist-dispatch"
    manifest = json.loads((dispatch / "dispatch-manifest.json").read_bytes())
    assert {path.name for path in dispatch.iterdir()} == {
        "dispatch-manifest.json",
        *(f"{code}.md" for code in generated_index.release_ready_codes),
    }
    assert manifest["release_ready_codes"] == list(generated_index.release_ready_codes)
    assert manifest["recipient"] == "OntoPrism project coordinator"
    assert manifest["dispatch_ready"] is True
    manifest_without_identity = {
        key: value for key, value in manifest.items() if key != "manifest_identity"
    }
    recomputed_manifest_identity = hashlib.sha256(
        (
            json.dumps(manifest_without_identity, indent=2, sort_keys=True) + "\n"
        ).encode()
    ).hexdigest()
    assert manifest["manifest_identity"] == recomputed_manifest_identity
    assert all(
        item["sha256"]
        == hashlib.sha256((dispatch / item["path"]).read_bytes()).hexdigest()
        for item in manifest["packets"]
    )
    assert all(
        (dispatch / f"{code}.md").read_bytes() == (output / f"{code}.md").read_bytes()
        for code in generated_index.release_ready_codes
    )

    eye_entry = next(item for item in generated_index.packets if item.code == "C100054")
    eye = (output / eye_entry.path).read_text(encoding="utf-8")
    assert eye.startswith(
        "# C100054 — Conjunctival Melanocytic Intraepithelial Lesion\n"
    )
    assert "**Exact label:** Conjunctival Melanocytic Intraepithelial Lesion" in eye
    if eye_entry.dispatch_status == "dispatchable":
        assert (
            "Partition modes: "
            + ", ".join(eye_entry.grouping_contract.allowed_dispositions)
        ) in eye
    assert eye.count("**Workload:**") == 1
    workload_line = next(
        line for line in eye.splitlines() if line.startswith("**Workload:**")
    )
    assert workload_line == (
        f"**Workload:** asked={len(eye_entry.asked_pair_ids)}; "
        f"action={len(eye_entry.action_pair_ids)}; "
        f"engineering={len(eye_entry.engineering_pair_ids)}; "
        f"context={len(eye_entry.context_pair_ids)}."
    )


def test_cli_uses_markdown_completion_command_and_fixed_replay_inputs(
    tmp_path: Path,
) -> None:
    args = _parser().parse_args(
        [
            "validate-completed-specialist-review-row",
            "--code",
            "C27262",
            "--return-file",
            "returns/C27262.md",
            "--index",
            "packets/index.json",
            "--validation-output",
            "returns/C27262.validation.json",
        ]
    )
    assert args.code == "C27262"
    assert args.return_file == Path("returns/C27262.md")
    required = (
        "scripts/adjudication.py",
        "tmp/m1-6-specialist-literature-context.json",
        "ontolib/tests/decomposition/golden/proposal-registry.json",
        "tmp/m1-6-specialist-cadsr-usage.json",
        "tmp/m1-6-axis-diagnostics-rev2.json",
        "ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json",
        "ontolib/tests/decomposition/golden/neoplasm-current-comparison.json",
        "tmp/m1-6-group-review-packet-rev2.json",
        "ontolib/tests/decomposition/golden/neoplasm-draft.json",
        "data/ncit-owl/Thesaurus-stated.owl",
    )
    for relative in required:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    commands: list[list[str]] = []

    def runner(
        arguments: list[str],
        *,
        cwd: Path,
        shell: Literal[False],
        check: Literal[False],
        timeout: float | None,
        capture_output: bool,
        text: Literal[True],
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, shell, check, timeout, capture_output, text, env
        commands.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    assert (
        run_agent_replay(
            ["generate-specialist-review-packets"], tmp_path, runner=runner
        )
        == 0
    )
    command = commands[0]
    assert "--cadsr-usage" in command
    assert "--label-source" in command
    assert str(tmp_path / "tmp/m1-6-specialist-packets") in command


@pytest.mark.parametrize(
    ("injected", "finding"),
    [
        (
            "The pair should be promoted.",
            "C100054 answer cue in source_fact: pre-answered ontology action "
            "[The pair should be promoted.]",
        ),
        (
            "<!-- QUESTION hidden critical content -->",
            "critical review content is hidden in HTML comments",
        ),
        (
            "Run pdm run verify in /Users/reviewer/repo.",
            "rendered packets contain a local path or repository command",
        ),
        (
            "Observed MINT-deadbeef1234 candidate.",
            "suppressed MINT identifiers leaked into rendered bytes",
        ),
    ],
)
def test_generation_gates_fail_closed_on_production_shaped_primed_evidence(
    tmp_path: Path, injected: str, finding: str
) -> None:
    literature, registry, cadsr, additional = _inputs(tmp_path)
    payload = json.loads(literature.read_bytes())
    payload["dossiers"][4]["questions"][0]["claims"][0]["source_fact"] = injected
    literature.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "packets"

    with pytest.raises(ValueError, match=re.escape(finding)):
        generate_specialist_review_packets(
            literature_context_path=literature,
            proposal_registry_path=registry,
            cadsr_usage_path=cadsr,
            output_directory=output,
            producing_command="fixed",
            additional_input_paths=additional,
        )

    validation = GenerationValidation.model_validate_json(
        (output / "generation-validation.json").read_bytes()
    )
    assert validation.status == "failed"
    assert finding in validation.findings
