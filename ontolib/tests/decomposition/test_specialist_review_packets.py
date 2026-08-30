from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.adjudication import _parser
from scripts.research.specialist_review_packets import (
    CONCEPT_ORDER,
    Citation,
    ClinicalQuestion,
    FinalLiteratureContext,
    LiteratureDossier,
    generate_specialist_review_packets,
)
from scripts.validation.run_agent_replay import run_agent_replay

pytestmark = pytest.mark.unit


def _literature(path: Path) -> None:
    dossiers = []
    for code in CONCEPT_ORDER:
        dossiers.append(
            LiteratureDossier(
                code=code,
                exact_label=f"Label {code}",
                exact_definition=f"Definition {code}",
                specialty="specialist",
                factual_context=("Audited factual context.",),
                citations=(
                    Citation(
                        citation_id=f"citation-{code}",
                        status="cited",
                        authority_order=1,
                        citation="Verified citation",
                        locator="section 1",
                        passage="Exact audited passage",
                        support="Supports context only.",
                        non_support="Does not decide ontology action.",
                        limitations="Specialist interpretation required.",
                        newer_evidence="None identified in final pass.",
                    ),
                ),
                questions=(
                    ClinicalQuestion(
                        question_id=f"{code}-Q1",
                        pair_ids=("P1",),
                        text="Is this finding universally defining?",
                        literature_citation_ids=(f"citation-{code}",),
                    ),
                ),
            )
        )
    context = FinalLiteratureContext(
        schema_version=1,
        evidence_pass="final",  # noqa: S106
        verified_on="2026-08-30",
        dossiers=tuple(dossiers),
    )
    path.write_text(context.model_dump_json(indent=2), encoding="utf-8")


def test_generator_writes_only_seven_packets_and_bound_indexes_deterministically(
    tmp_path: Path,
) -> None:
    literature = tmp_path / "literature.json"
    _literature(literature)
    registry = Path("ontolib/tests/decomposition/golden/proposal-registry.json")
    output = tmp_path / "packets"

    first = generate_specialist_review_packets(
        literature_context_path=literature,
        proposal_registry_path=registry,
        output_directory=output,
        producing_command="pdm run agent-replay generate-specialist-review-packets",
    )
    bytes_before = {path.name: path.read_bytes() for path in output.iterdir()}
    second = generate_specialist_review_packets(
        literature_context_path=literature,
        proposal_registry_path=registry,
        output_directory=output,
        producing_command="pdm run agent-replay generate-specialist-review-packets",
    )

    assert first == second
    assert {path.name for path in output.iterdir()} == {
        *(f"{code}.md" for code in CONCEPT_ORDER),
        "index.json",
        "generation-validation.json",
    }
    assert bytes_before == {path.name: path.read_bytes() for path in output.iterdir()}
    index = json.loads((output / "index.json").read_bytes())
    assert tuple(item["code"] for item in index["packets"]) == CONCEPT_ORDER
    assert all(len(item["sha256"]) == 64 for item in index["packets"])
    markdown = (output / "C35756.md").read_text(encoding="utf-8")
    assert "blank specialist packet" in markdown.lower()
    assert "CADSR-USAGE: NOT QUERIED" in markdown
    assert "readiness remains false" in markdown
    assert "STAGE-A-RESPONSE" in markdown
    assert "STAGE-B-RESPONSE" in markdown
    assert "MINT-" not in markdown
    assert "m1-6-group-correction-revision-26.07d.md" not in markdown
    assert "sha256" not in markdown.lower()


def test_cli_and_fixed_replay_name_every_input_and_output(tmp_path: Path) -> None:
    args = _parser().parse_args(
        [
            "generate-specialist-review-packets",
            "--literature-context",
            "literature.json",
            "--proposal-registry",
            "registry.json",
            "--axis-diagnostics",
            "diagnostics.json",
            "--current-evidence",
            "evidence.json",
            "--current-comparison",
            "comparison.json",
            "--group-review-packet",
            "groups.json",
            "--output-directory",
            "packets",
            "--producing-command",
            "fixed-command",
        ]
    )
    assert args.command == "generate-specialist-review-packets"
    validate = _parser().parse_args(
        ["validate-specialist-review-packets", "--directory", "packets"]
    )
    assert validate.directory == Path("packets")

    required = (
        "scripts/adjudication.py",
        "tmp/m1-6-specialist-literature-context.json",
        "ontolib/tests/decomposition/golden/proposal-registry.json",
        "tmp/m1-6-axis-diagnostics-rev2.json",
        "ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json",
        "ontolib/tests/decomposition/golden/neoplasm-current-comparison.json",
        "tmp/m1-6-group-review-packet-rev2.json",
    )
    for relative in required:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    commands: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object):  # type: ignore[no-untyped-def]
        commands.append(command)
        return type("Result", (), {"returncode": 0})()

    assert (
        run_agent_replay(
            ["generate-specialist-review-packets"], tmp_path, runner=runner
        )
        == 0
    )
    command = commands[0]
    assert all(str(tmp_path / relative) in command for relative in required)
    assert str(tmp_path / "tmp/m1-6-specialist-packets") in command


def test_literature_dependent_question_requires_cited_evidence_or_source_request() -> (
    None
):
    with pytest.raises(ValueError, match="supply source"):
        LiteratureDossier(
            code="C27262",
            exact_label="Label",
            exact_definition="Definition",
            specialty="specialist",
            factual_context=("Fact",),
            citations=(
                Citation(
                    citation_id="closed",
                    status="access-restricted",
                    authority_order=1,
                    citation="Catalog",
                    locator="catalog",
                    passage="Access restricted",
                    support="No independently checked passage.",
                    non_support="No decision.",
                    limitations="Access restricted.",
                    newer_evidence="Unknown.",
                ),
            ),
            questions=(
                ClinicalQuestion(
                    question_id="Q1",
                    pair_ids=("P1",),
                    text="Is this universal?",
                    literature_citation_ids=("closed",),
                ),
            ),
        )
