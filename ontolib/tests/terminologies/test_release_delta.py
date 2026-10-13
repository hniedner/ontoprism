"""Release history events are not interchangeable with removed OWL declarations."""

from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

import pytest

from ontolib.core import robot as launcher
from ontolib.core.robot import RobotConfig
from ontolib.terminologies.ncit import release_delta as module
from ontolib.terminologies.ncit.release_delta import (
    AxiomDelta,
    parse_history,
    parse_robot_report,
    robot_diff,
    summarize_delta,
)


def test_publisher_history_fixture_uses_observed_six_column_format():
    source = Path(__file__).with_name("fixtures") / "ncit-history-26.08e.txt"
    events = parse_history(source.read_text())
    assert [(e.concept, e.action, e.reference) for e in events] == [
        ("C123981", "merge", "C123981"),
        ("C227354", "merge", "C123981"),
        ("C227354", "retire", None),
        ("C17941", "split", "C17941"),
        ("C17941", "split", "C228011"),
    ]


def test_delta_separates_retirement_scope_change_and_nested_logical_changes():
    ns = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#"
    axiom = (
        f"SubClassOf(<{ns}C1> ObjectSomeValuesFrom(<{ns}R101> "
        f"ObjectIntersectionOf(<{ns}C8> <{ns}C9>)))"
    )
    result = summarize_delta(
        "26.07d",
        "26.09d",
        {"C1", "C2", "C3"},
        {"C1", "C2", "C3", "C4"},
        {"C1", "C2"},
        {"C1", "C4"},
        AxiomDelta(removed=(axiom,), added=(f"Declaration(Class(<{ns}C4>))",)),
        parse_history(
            "C2||merge|31-Aug-26|C1|\nC1||merge|31-Aug-26|C1|\nC2||retire|31-Aug-26|null|\nC4||create|28-Sep-26|null|"
        ),
    )
    assert result["whole_release"]["added"] == ["C4"]
    assert result["whole_release"]["removed"] == []
    assert result["neoplasm"]["retired"] == ["C2"]
    assert result["neoplasm"]["merged_into"] == {"C2": ["C1"]}
    assert result["neoplasm"]["scope_before"] == 2
    assert result["neoplasm"]["scope_after"] == 2
    assert result["neoplasm"]["entered"] == ["C4"]
    assert result["neoplasm"]["exited"] == ["C2"]
    assert result["logical_changes"]["C1"]["removed"] == [axiom]
    assert "C8" not in result["logical_changes"]
    assert result["whole_release"]["changed_definitions"] == ["C1"]
    assert result["history_event_counts"] == {"merge": 2, "retire": 1, "create": 1}


@pytest.mark.integration
@pytest.mark.mutating_integration
def test_real_robot_fixture_diff_preserves_changed_existential(tmp_path):
    template = """Prefix(:=<urn:test:>)
Ontology(<urn:test>
Declaration(Class(:C1)) Declaration(Class(:C2)) Declaration(Class(:C3))
Declaration(ObjectProperty(:R1))
SubClassOf(:C1 ObjectSomeValuesFrom(:R1 :FILLER))
)"""
    left, right = tmp_path / "left.ofn", tmp_path / "right.ofn"
    left.write_text(template.replace("FILLER", "C2"))
    right.write_text(template.replace("FILLER", "C3"))
    result = robot_diff(
        left,
        right,
        RobotConfig(heap_gib=1, max_element_depth=100, timeout_seconds=60),
    )
    assert result.removed == (
        "SubClassOf(<urn:test:C1> ObjectSomeValuesFrom(<urn:test:R1> <urn:test:C2>))",
    )
    assert result.added == (
        "SubClassOf(<urn:test:C1> ObjectSomeValuesFrom(<urn:test:R1> <urn:test:C3>))",
    )
    unchanged = robot_diff(
        left,
        left,
        RobotConfig(heap_gib=1, max_element_depth=100, timeout_seconds=60),
    )
    assert unchanged.removed == unchanged.added == ()
    right.write_text(
        template.replace("FILLER", "C2").replace(
            "Declaration(Class(:C3))", "Declaration(Class(:C3)) Declaration(Class(:C4))"
        )
    )
    added = robot_diff(
        left,
        right,
        RobotConfig(heap_gib=1, max_element_depth=100, timeout_seconds=60),
    )
    assert added.removed == ()
    assert added.added == ("Declaration(Class(<urn:test:C4>))",)
    removed = robot_diff(
        right,
        left,
        RobotConfig(heap_gib=1, max_element_depth=100, timeout_seconds=60),
    )
    assert removed.added == ()
    assert removed.removed == added.added


@pytest.mark.parametrize(
    "report",
    [
        "1 axioms in left ontology but not in left ontology:\n"
        "- Declaration(Class(<urn:C1>))\n",
        "1 axioms in left ontology but not in right ontology:\n"
        "- SubClassOf(<urn:C1> <urn:C2>)\n"
        "1 axioms in right ontology but not in left ontology:\n+ broken\n",
        "1 axioms in left ontology but not in right ontology:\n"
        "- Declaration(Class(<urn:C1>)\n",
        "1 axioms in left ontology but not in right ontology:\n"
        "- Declaration(Class(<urn:C1>))\n"
        "1 axioms in left ontology but not in right ontology:\n"
        "- Declaration(Class(<urn:C1>))\n",
    ],
)
def test_malformed_robot_output_never_becomes_a_delta(report):
    with pytest.raises(ValueError, match=r"ROBOT|axiom"):
        parse_robot_report(report)


def test_annotated_equivalence_identifies_top_level_classes_not_literal_or_filler():
    ns = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#"
    axiom = (
        'EquivalentClasses(Annotation(<urn:note> "literal (text) \\"quoted\\" '
        f'{ns}C99") '
        f"<{ns}C1> ObjectSomeValuesFrom(<{ns}R1> <{ns}C2>) <{ns}C3>)"
    )
    result = summarize_delta(
        "old",
        "new",
        {"C1", "C2", "C3"},
        {"C1", "C2", "C3"},
        {"C1", "C3"},
        {"C1", "C3"},
        AxiomDelta(removed=(axiom,), added=()),
        (),
    )
    assert set(result["logical_changes"]) == {"C1", "C3"}
    assert result["logical_changes"]["C1"]["removed"] == [axiom]


def test_robot_timeout_never_becomes_identical_success(tmp_path, monkeypatch):
    monkeypatch.setattr(
        launcher, "configured_robot_installation", lambda: (tmp_path, None)
    )

    def timeout(*args, **kwargs):
        raise TimeoutExpired(
            args[0], kwargs["timeout"], output="Ontologies are identical"
        )

    monkeypatch.setattr(launcher.subprocess, "run", timeout)
    with pytest.raises(TimeoutExpired):
        robot_diff(
            tmp_path / "left",
            tmp_path / "right",
            RobotConfig(heap_gib=1, max_element_depth=100, timeout_seconds=1),
        )


def test_robot_report_keeps_nested_axioms_and_refuses_truncated_counts():
    old = (
        "SubClassOf(<urn:C1> ObjectSomeValuesFrom(<urn:R1> "
        "ObjectIntersectionOf(<urn:C2> <urn:C3>)))"
    )
    new = "SubClassOf(<urn:C1> ObjectSomeValuesFrom(<urn:R1> <urn:C4>))"
    report = (
        f"1 axioms in left ontology but not in right ontology:\n- {old}\n\n"
        f"1 axioms in right ontology but not in left ontology:\n+ {new}\n"
    )
    delta = parse_robot_report(report)
    assert delta.removed == (old,)
    assert delta.added == (new,)
    with pytest.raises(ValueError, match="count"):
        parse_robot_report(report.replace(f"+ {new}\n", ""))
    with pytest.raises(ValueError, match="unrecognized ROBOT"):
        parse_robot_report("garbled output")


def test_robot_configuration_drives_jvm_and_process_failure_is_not_empty_success(
    tmp_path, monkeypatch
):
    left, right = tmp_path / "left.owl", tmp_path / "right.owl"
    left.write_text("fixture")
    right.write_text("fixture")
    monkeypatch.setattr(
        launcher, "configured_robot_installation", lambda: (tmp_path, None)
    )
    seen = []

    def execute(args, **kwargs):
        seen.append(args)
        return CompletedProcess(
            args,
            0,
            "0 axioms in left ontology but not in right ontology:\n"
            "0 axioms in right ontology but not in left ontology:\n",
            "",
        )

    monkeypatch.setattr(launcher.subprocess, "run", execute)
    config = RobotConfig(heap_gib=2, max_element_depth=128, timeout_seconds=9)
    delta = module.robot_diff(left, right, config)
    assert delta.added == delta.removed == ()
    assert "-Xmx2g" in seen[0]
    assert "-Djdk.xml.maxElementDepth=128" in seen[0]
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda args, **kw: CompletedProcess(args, 1, "", "parser error"),
    )
    with pytest.raises(RuntimeError, match="parser error"):
        module.robot_diff(left, right, config)


def test_history_preserves_merge_survivor_split_targets_and_retirement():
    events = parse_history(
        "C1||merge|31-Aug-26|C1|\n"
        "C2||merge|31-Aug-26|C1|\n"
        "C2||retire|31-Aug-26|null|\n"
        "C3||split|28-Sep-26|C3|\n"
        "C3||split|28-Sep-26|C4|\n"
        "C4||create|28-Sep-26|null|\n"
        "C5||modify|28-Sep-26|null|\n"
    )
    assert [(e.concept, e.action, e.reference) for e in events] == [
        ("C1", "merge", "C1"),
        ("C2", "merge", "C1"),
        ("C2", "retire", None),
        ("C3", "split", "C3"),
        ("C3", "split", "C4"),
        ("C4", "create", None),
        ("C5", "modify", None),
    ]
    assert events[0].date.isoformat() == "2026-08-31"


@pytest.mark.parametrize(
    "row",
    [
        "C1|merge|31-Aug-26|C2",
        "C1|extra|merge|31-Aug-26|C2|",
        "C1||merge|31-Aug-26|C2|extra",
        "C1||invented|31-Aug-26|C2|",
        "foreign||retire|31-Aug-26|null|",
        "C1||merge|31-Aug-26|foreign|",
        "C1||merge|99-Aug-26|C2|",
        "C1||merge|31-Aug-26|null|",
        "C1||split|31-Aug-26|null|",
        "",
        "\n",
    ],
)
def test_history_rejects_malformed_or_empty_source(row):
    with pytest.raises(
        ValueError, match=r"history|validation error|does not match format"
    ):
        parse_history(row)


@pytest.mark.parametrize(
    "expression",
    [
        '"not a constructor"(<urn:C1>)',
        "SubClassOf()",
        "SubClassOf(<urn:C1> ObjectSomeValuesFrom <urn:R1> <urn:C2>)",
    ],
)
def test_balanced_but_malformed_expression_is_refused(expression):
    report = (
        "1 axioms in left ontology but not in right ontology:\n"
        f"- {expression}\n"
        "0 axioms in right ontology but not in left ontology:\n"
    )
    with pytest.raises(ValueError, match="axiom"):
        parse_robot_report(report)


@pytest.mark.parametrize(
    "profile",
    [
        {"heap_gib": 0},
        {"heap_gib": "24"},
        {"max_element_depth": -1},
        {"max_element_depth": 0},
        {"timeout_seconds": 0},
    ],
)
def test_invalid_jvm_configuration_is_rejected(profile):
    values = {"heap_gib": 1, "max_element_depth": 100, "timeout_seconds": 60}
    with pytest.raises(ValueError, match="validation error"):
        RobotConfig.model_validate(values | profile)


def test_missing_java_cannot_produce_an_empty_delta(tmp_path, monkeypatch):
    monkeypatch.setattr(
        launcher, "configured_robot_installation", lambda: (tmp_path, None)
    )
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="Java is required"):
        robot_diff(
            tmp_path / "left",
            tmp_path / "right",
            RobotConfig(heap_gib=1, max_element_depth=100, timeout_seconds=1),
        )
