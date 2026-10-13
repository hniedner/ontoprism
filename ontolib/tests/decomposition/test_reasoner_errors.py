"""External failures never masquerade as a reasoner verdict."""

from subprocess import CompletedProcess, TimeoutExpired

import pytest

from ontolib.core.robot import RobotConfig
from ontolib.decomposition import reasoner_check as subject
from ontolib.repositories.xref.validation import ReasonerUnavailableError

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("failure", ["timeout", "killed", "parser", "truncated"])
def test_reasoner_failure_never_returns_a_clean_report(tmp_path, monkeypatch, failure):
    def run(args, config):
        if failure == "timeout":
            raise TimeoutExpired(args, 1)
        if args[0] in {"merge", "validate-profile"}:
            return CompletedProcess(args, 0, "", "")
        errors = {
            "killed": (-9, "unsatisfiable"),
            "parser": (1, "parse error"),
            "truncated": (
                1,
                "ReasonerHelper - There are 2 unsatisfiable classes in the ontology.\n"
                "ReasonerHelper - unsatisfiable: urn:one",
            ),
        }
        code, message = errors[failure]
        return CompletedProcess(args, code, "", message)

    monkeypatch.setattr(subject, "run_robot", run)
    message = {
        "timeout": "timed out",
        "killed": "killed",
        "parser": "parse error",
        "truncated": "There are 2 unsatisfiable classes",
    }[failure]
    with pytest.raises((TimeoutExpired, ReasonerUnavailableError), match=message):
        subject.check_projection(
            tmp_path / "source",
            [],
            tmp_path / "run",
            RobotConfig(heap_gib=1, max_element_depth=100, timeout_seconds=1),
        )
