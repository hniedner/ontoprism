from __future__ import annotations

import json
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
    PacketIndex,
    generate_specialist_review_packets,
    validate_specialist_review_generation,
)
from scripts.validation.run_agent_replay import run_agent_replay

pytestmark = pytest.mark.unit


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


def test_generator_writes_schema_three_and_bound_validation_deterministically(
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
    assert all(not path.startswith("/") for path in generated_index.input_identities)
    validation = validate_specialist_review_generation(output)
    assert validation.status == "passed"
    assert validation.readiness is False
    assert {path.name for path in output.iterdir()} == {
        *(f"{code}.md" for code in CONCEPT_ORDER),
        "index.json",
        "generation-validation.json",
    }
    markdown = (output / "C102870.md").read_text(encoding="utf-8")
    assert "P97:" in markdown
    assert "RESPONSE-CELLS-START A" in markdown
    assert "C121619" in markdown
    assert "C39986" in markdown
    assert "STAGE-A-RESPONSE" not in markdown


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
