"""Tests for non-circular validation harness with ELK/ROBOT (PR-A4, #73).

Every test written BEFORE production code (strict TDD).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ontolib.repositories.xref.evidence import (
    LABEL_AGREEMENT,
    STRUCTURAL_CORROBORATION,
    Evidence,
)
from ontolib.repositories.xref.models import SSSOMRecord
from ontolib.repositories.xref.validation import (
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
        Evidence(kind=STRUCTURAL_CORROBORATION, source="elk:anchored-ancestor"),
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
def test_to_el_profile_check_fails_on_nonzero_exit(
    mock_run: MagicMock,
) -> None:
    """to_el_profile_and_check returns False when ROBOT exits non-zero."""
    mock_run.return_value = MagicMock(returncode=1)
    assert to_el_profile_and_check("/fake/path.owl") is False


@pytest.mark.unit
@patch("ontolib.repositories.xref.validation.subprocess.run")
def test_to_el_profile_check_warns_on_failure(
    mock_run: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """to_el_profile_and_check logs a warning on profile failure."""
    mock_run.return_value = MagicMock(returncode=1)
    with caplog.at_level(logging.WARNING):
        assert to_el_profile_and_check("/fake/path.owl") is False
    assert "not in OWL 2 EL profile" in caplog.text


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
    out_path = classify("/fake/input.owl")
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
    """Minimal OWL 2 EL ontology (3 classes, 1 subclass axiom)."""
    return """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:owl="http://www.w3.org/2002/07/owl#"
         xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#">
  <owl:Ontology rdf:about="http://example.org/test-ontology"/>
  <owl:Class rdf:about="http://example.org/A"/>
  <owl:Class rdf:about="http://example.org/B"/>
  <owl:Class rdf:about="http://example.org/C"/>
  <owl:SubClassOf>
    <owl:Class rdf:about="http://example.org/A"/>
    <owl:Class rdf:about="http://example.org/B"/>
  </owl:SubClassOf>
</rdf:RDF>"""


@pytest.mark.integration
def test_robot_elk_smoke(tmp_path: Path) -> None:
    """Small EL ontology classified by ROBOT/ELK returns a valid output path."""
    if shutil.which("robot") is None:
        pytest.skip("robot not on PATH")

    onto_path = tmp_path / "test.owl"
    onto_path.write_text(_owl_el_fixture())

    assert to_el_profile_and_check(str(onto_path)) is True
    out_path = classify(str(onto_path))
    assert out_path.exists()
    assert out_path.suffix == ".owl"


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
