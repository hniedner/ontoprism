"""Tests for non-circular validation harness with ELK/ROBOT (PR-A4, #73).

Every test written BEFORE production code (strict TDD).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ontolib.repositories.xref.evidence import (
    LABEL_AGREEMENT,
    STRUCTURAL_CORROBORATION,
    Evidence,
)
from ontolib.repositories.xref.models import SSSOMRecord
from ontolib.repositories.xref.promotion import (
    _reachable_ancestors,
    parse_inferred_subclasses,
)
from ontolib.repositories.xref.validation import (
    ReasonerUnavailableError,
    classify,
    promote_candidate,
    to_el_profile_and_check,
    validate_and_classify,
)
from ontolib.repositories.xref.vocab import CLOSE_MATCH, EXACT_MATCH

# ── shared fixture ─────────────────────────────────────────────────────


@pytest.fixture
def close_match_record() -> SSSOMRecord:
    return SSSOMRecord(
        subject_id="C3262",
        predicate_id=CLOSE_MATCH,
        object_id="UBERON:0002107",
        mapping_justification="semapv:LexicalMatching",
        confidence=0.8,
        subject_source_version="26.02d",
        object_source_version="uberon-2026-01",
    )


def _independent_evidence() -> list[Evidence]:
    """Two distinct signals, neither of them the mapping itself."""
    return [
        Evidence(kind=LABEL_AGREEMENT, source="rdfs:label"),
        Evidence(kind=STRUCTURAL_CORROBORATION, source="structural:anchored-ancestor"),
    ]


# ── test 1: reject SKOS as own evidence (MOST IMPORTANT) ───────────────


@pytest.mark.unit
def test_promotion_rejects_skos_as_own_evidence(
    close_match_record: SSSOMRecord,
) -> None:
    """A SKOS annotation cannot even be *expressed* as evidence (D28).

    The invariant is structural, not a runtime check the caller can forget: the
    only thing ``promote_candidate`` accepts is ``Evidence``, and ``Evidence``
    refuses to hold a skos:*Match IRI — the record's own predicate or any other.
    """
    with pytest.raises(ValueError, match="SKOS"):
        Evidence(kind=LABEL_AGREEMENT, source=close_match_record.predicate_id)
    with pytest.raises(ValueError, match="SKOS"):
        Evidence(kind=LABEL_AGREEMENT, source=EXACT_MATCH)


# ── test 2: non-equivalent pair stays closeMatch ───────────────────────


@pytest.mark.unit
def test_known_non_equivalent_pair_demoted(
    close_match_record: SSSOMRecord,
) -> None:
    """Pair that does not classify as equivalent stays closeMatch."""
    result = promote_candidate(
        close_match_record,
        _independent_evidence(),
        el_valid=False,
    )
    assert result is None


@pytest.mark.unit
def test_promotion_rejects_a_single_signal(
    close_match_record: SSSOMRecord,
) -> None:
    """EL-valid but only one corroborating signal → stays a proposed closeMatch."""
    result = promote_candidate(
        close_match_record,
        [Evidence(kind=LABEL_AGREEMENT, source="rdfs:label")],
        el_valid=True,
    )
    assert result is None


# ── test 3a: profile gate subprocess paths ────────────────────────────


@pytest.mark.unit
@patch("ontolib.repositories.xref.validation.subprocess.run")
def test_to_el_profile_check_passes_on_zero_exit(
    mock_run: MagicMock,
) -> None:
    """to_el_profile_and_check returns True when ROBOT exits with 0."""
    mock_run.return_value = MagicMock(returncode=0)
    assert to_el_profile_and_check("/fake/path.owl") is True
    mock_run.assert_called_once()


@pytest.mark.unit
@patch("ontolib.repositories.xref.validation.subprocess.run")
def test_el_profile_gate_invokes_robots_validate_profile_command(
    mock_run: MagicMock,
) -> None:
    """The EL gate must shell out to ``robot validate-profile``.

    ``robot profile`` is not a ROBOT command: it exits non-zero for *every* input,
    so the gate would reject every merge and nothing could ever be promoted — a
    failure that looks exactly like 'no candidate qualified'.
    """
    mock_run.return_value = MagicMock(returncode=0)
    to_el_profile_and_check("/fake/path.owl")

    argv = mock_run.call_args[0][0]
    assert argv[1] == "validate-profile"
    assert argv[argv.index("--profile") + 1] == "EL"
    assert argv[argv.index("--input") + 1] == "/fake/path.owl"


@pytest.mark.unit
@patch("ontolib.repositories.xref.validation.subprocess.run")
def test_to_el_profile_check_reports_a_real_profile_violation(
    mock_run: MagicMock,
) -> None:
    """A non-zero exit that *reports a violation* is a genuine verdict: False."""
    mock_run.return_value = MagicMock(
        returncode=1,
        stdout="EL Profile Violation: Use of undeclared object property",
        stderr="",
    )
    assert to_el_profile_and_check("/fake/path.owl") is False


@pytest.mark.unit
@patch("ontolib.repositories.xref.validation.subprocess.run")
def test_a_broken_robot_is_not_reported_as_a_profile_violation(
    mock_run: MagicMock,
) -> None:
    """THE silent-failure guard: a non-zero exit with no violation report means ROBOT
    could not run (no Java, corrupt jar, OOM). That is NOT an EL verdict, and returning
    False for it would silently reject every merge and promote nothing — which looks
    exactly like 'no candidate qualified'.
    """
    mock_run.return_value = MagicMock(
        returncode=127, stdout="", stderr="Error: Unable to access jarfile robot.jar"
    )
    with pytest.raises(ReasonerUnavailableError, match="environment failure"):
        to_el_profile_and_check("/fake/path.owl")


@pytest.mark.unit
@patch("ontolib.repositories.xref.validation.subprocess.run")
def test_a_missing_robot_binary_raises_rather_than_rejecting(
    mock_run: MagicMock,
) -> None:
    mock_run.side_effect = FileNotFoundError("robot")
    with pytest.raises(ReasonerUnavailableError, match="not on PATH"):
        to_el_profile_and_check("/fake/path.owl")


@pytest.mark.unit
@patch("ontolib.repositories.xref.validation.subprocess.run")
def test_a_robot_timeout_raises_rather_than_rejecting(
    mock_run: MagicMock,
) -> None:
    """A hung JVM is an environment failure, not a verdict."""
    mock_run.side_effect = subprocess.TimeoutExpired(["robot"], 300)
    with pytest.raises(ReasonerUnavailableError, match="timed out"):
        to_el_profile_and_check("/fake/path.owl")


@pytest.mark.unit
@patch("ontolib.repositories.xref.validation.subprocess.run")
def test_classify_returns_none_when_elk_refutes_the_merge(
    mock_run: MagicMock,
) -> None:
    """An unsatisfiable merge is refuted — the satisfiability gate."""
    mock_run.return_value = MagicMock(
        returncode=1, stdout="", stderr="ERROR unsatisfiable classes: ..."
    )
    assert classify("/fake/input.ttl") is None


@pytest.mark.unit
@patch("ontolib.repositories.xref.validation.subprocess.run")
def test_classify_raises_when_elk_dies_for_an_unrelated_reason(
    mock_run: MagicMock,
) -> None:
    """OOM / JVM crash must never be recorded as 'the merge was unsatisfiable'."""
    mock_run.return_value = MagicMock(
        returncode=137, stdout="", stderr="java.lang.OutOfMemoryError: Java heap space"
    )
    with pytest.raises(ReasonerUnavailableError, match="NOT a verdict"):
        classify("/fake/input.ttl")


@pytest.mark.unit
@patch("ontolib.repositories.xref.validation.subprocess.run")
def test_to_el_profile_check_warns_on_a_real_violation(
    mock_run: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A genuine profile violation is logged with ROBOT's own report, not a guess."""
    mock_run.return_value = MagicMock(
        returncode=1, stdout="EL Profile Violation: bad axiom", stderr=""
    )
    with caplog.at_level(logging.WARNING):
        assert to_el_profile_and_check("/fake/path.owl") is False
    assert "EL profile violation" in caplog.text


# ── test 3b: classify subprocess paths (unit) ──────────────────────────


@pytest.mark.unit
@patch("ontolib.repositories.xref.validation.os.close")
@patch("ontolib.repositories.xref.validation.subprocess.run")
@patch("ontolib.repositories.xref.validation.tempfile.mkstemp")
def test_classify_creates_temp_output(
    mock_mkstemp: MagicMock,
    mock_run: MagicMock,
    mock_close: MagicMock,
) -> None:
    """classify creates a temp file and calls robot reason --reasoner ELK."""
    # mkstemp returns a fake fd; os.close is mocked so the fake fd (3) is never
    # actually closed — closing a real in-use fd corrupts the xdist worker pipe
    # (BrokenPipe) or pytest's dup'd fds (teardown EBADF).
    mock_mkstemp.return_value = (3, "/fake/temp/test_elk.owl")
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    out_path = classify("/fake/input.owl")
    assert out_path is not None
    assert str(out_path) == "/fake/temp/test_elk.owl"
    # verify subprocess was called with ELK
    args = mock_run.call_args[0][0]
    assert "ELK" in args
    assert "--reasoner" in args
    mock_mkstemp.assert_called_once_with(suffix=".owl")
    mock_close.assert_called_once_with(3)


# ── test 3c: validate_and_classify returns classify path on pass ────────


@pytest.mark.unit
@patch("ontolib.repositories.xref.validation.classify")
@patch(
    "ontolib.repositories.xref.validation.to_el_profile_and_check",
    return_value=True,
)
def test_validate_and_classify_returns_classify_path(
    mock_profile: MagicMock,
    mock_classify: MagicMock,
) -> None:
    """validate_and_classify returns classify's output when profile passes."""
    mock_classify.return_value = Path("/fake/temp/inferred.owl")
    result = validate_and_classify("/fake/path.owl")
    assert result == Path("/fake/temp/inferred.owl")
    mock_classify.assert_called_once()


# ── integration: ROBOT/ELK smoke (requires robot on PATH) ──────────────


def _owl_el_fixture() -> str:
    """Minimal OWL 2 EL ontology (3 classes, 2 subclass axioms).

    ``rdfs:subClassOf`` inside the ``owl:Class`` node is the *only* way RDF/XML
    expresses a subclass axiom.  A bare ``<owl:SubClassOf>`` element (as this
    fixture used to carry) is not one: RDF/XML reads it as a typed node using
    ``owl:Class`` as an undeclared property, which is itself an EL-profile
    violation — so the gate correctly rejected it, and the smoke test asserted the
    opposite.  It never surfaced because ROBOT was not installed anywhere the suite
    ran, so this test always skipped.
    """
    return """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:owl="http://www.w3.org/2002/07/owl#"
         xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#">
  <owl:Ontology rdf:about="http://example.org/test-ontology"/>
  <owl:Class rdf:about="http://example.org/C"/>
  <owl:Class rdf:about="http://example.org/B">
    <rdfs:subClassOf rdf:resource="http://example.org/C"/>
  </owl:Class>
  <owl:Class rdf:about="http://example.org/A">
    <rdfs:subClassOf rdf:resource="http://example.org/B"/>
  </owl:Class>
</rdf:RDF>"""


@pytest.mark.integration
def test_robot_elk_smoke(tmp_path: Path) -> None:
    """A small EL ontology passes the profile gate and is classified by ELK.

    The assertion is on the property the promotion logic actually depends on: after
    parsing ROBOT's output, ``A`` must resolve to ``C`` as an ancestor.  Note it does
    **not** assert ``("A", "C") in inferred`` — ROBOT transitively reduces the
    hierarchy, so ``A ⊑ C`` is never *stated* even though it holds.  Asserting
    membership here is the very mistake that made corroboration miss any anchor above
    a direct parent; the ancestry must be walked.
    """
    if shutil.which("robot") is None:
        pytest.skip("robot not on PATH")

    onto_path = tmp_path / "test.owl"
    onto_path.write_text(_owl_el_fixture())

    assert to_el_profile_and_check(str(onto_path)) is True

    out_path = classify(str(onto_path))
    assert out_path.exists()

    inferred = parse_inferred_subclasses(out_path)
    ancestors = _reachable_ancestors("http://example.org/A", inferred)
    assert "http://example.org/C" in ancestors


# ── test 4: non-EL input rejected before classify ──────────────────────


@pytest.mark.unit
@patch(
    "ontolib.repositories.xref.validation.to_el_profile_and_check",
    return_value=False,
)
@patch("ontolib.repositories.xref.validation.classify")
def test_non_el_input_rejected_before_classify(
    mock_classify: MagicMock,
    mock_profile: MagicMock,
    tmp_path: Path,
) -> None:
    """EL-escaping ontology fails profile gate; classify is NEVER called."""
    onto_path = tmp_path / "test.owl"
    onto_path.write_text("<rdf:RDF/>")

    result = validate_and_classify(str(onto_path))
    assert result is None
    mock_classify.assert_not_called()


# ── test 5: promoted record has exactMatch + validated ─────────────────


@pytest.mark.unit
def test_promoted_record_has_exact_match(
    close_match_record: SSSOMRecord,
) -> None:
    """Independent evidence + valid classification → exactMatch + validated."""
    result = promote_candidate(
        close_match_record,
        _independent_evidence(),
        el_valid=True,
    )
    assert result is not None
    assert result.predicate_id == EXACT_MATCH
    assert result.lifecycle_state == "validated"
    # original record must be unchanged (immutability)
    assert close_match_record.predicate_id == CLOSE_MATCH
    assert close_match_record.lifecycle_state == "proposed"
