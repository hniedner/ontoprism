"""Contracts for current and target OntoPrism product identity documentation."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]
_PRODUCT_IDENTITY_SURFACES = (
    "README.md",
    "AGENTS.md",
    "docs/ARCHITECTURE.md",
    "docs/DECISIONS.md",
    "docs/DATA_SETUP.md",
    "docs/design/README.md",
    "docs/design/ontology-platform.md",
    "docs/design/ncit-alignment-integration.md",
    "docs/ecosystem/ncit-cadsr-naaccr.md",
    "docs/evidence/README.md",
    "frontend/README.md",
    "pyproject.toml",
)
_CURRENT_FACING_SURFACES = (
    "README.md",
    "frontend/README.md",
    "pyproject.toml",
)
_BASE_REVISION = "260b971613410dd6fdfc1ddb30ab00e5c5490945"
_FORBIDDEN_CURRENT_CLAIMS = (
    r"ships? (?:an? )?ontology-generic",
    r"implemented generic (?:ontology )?adapters?",
    r"generic (?:ontology )?(?:editing|reasoning|AI authoring) (?:is|are) implemented",
    r"release-forward reconciliation (?:is|has been) implemented",
    r"fully backward compatible",
)


def _read(path: str) -> str:
    return (_ROOT / path).read_text(encoding="utf-8")


def _decision_section(document: str, decision: str) -> str:
    match = re.search(
        rf"^### {decision}\..*?(?=^## \d{{4}}-|\Z)",
        document,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"missing {decision} section"
    return match.group(0)


def _assert_no_current_overclaim(text: str) -> None:
    for claim in _FORBIDDEN_CURRENT_CLAIMS:
        assert re.search(claim, text, flags=re.IGNORECASE) is None, claim


@pytest.mark.unit
def test_current_overclaim_gate_rejects_a_shipped_generic_adapter_claim() -> None:
    with pytest.raises(AssertionError, match="implemented generic"):
        _assert_no_current_overclaim(
            "The current product has implemented generic ontology adapters."
        )


@pytest.mark.unit
def test_product_identity_surfaces_separate_current_from_target() -> None:
    assert _PRODUCT_IDENTITY_SURFACES
    missing = [
        path for path in _PRODUCT_IDENTITY_SURFACES if not (_ROOT / path).is_file()
    ]
    assert not missing, f"missing product-identity surfaces: {missing}"

    design = _read("docs/design/ontology-platform.md")
    assert "**Status:** Target architecture, not current implementation" in design
    assert "## Current implementation" in design
    assert "## Target architecture" in design
    for boundary in ("Platform core", "Ontology adapters", "Domain policy"):
        assert f"### {boundary}" in design

    current_text = "\n".join(_read(path) for path in _CURRENT_FACING_SURFACES)
    _assert_no_current_overclaim(current_text)

    metadata = _read("pyproject.toml").lower()
    assert "ontology-generic framework target" in metadata
    assert "current enhanced ncit implementation" in metadata


@pytest.mark.unit
def test_d86_is_newest_and_preserves_d60_verbatim() -> None:
    decisions = _read("docs/DECISIONS.md")
    ids = [
        int(value) for value in re.findall(r"^### D(\d+)\.", decisions, re.MULTILINE)
    ]
    assert ids[:2] == [86, 85]
    assert ids.count(86) == 1
    assert max(ids) == 86

    current_d60 = _decision_section(decisions, "D60")
    git = shutil.which("git")
    assert git is not None
    base = subprocess.run(  # noqa: S603 - fixed git operation and revision
        [git, "show", f"{_BASE_REVISION}:docs/DECISIONS.md"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert current_d60 == _decision_section(base, "D60")

    d86 = _decision_section(decisions, "D86")
    assert "qualifies D60" in d86
    assert "does not supersede D60" in d86
    for excluded_meaning in (
        "conservative extension",
        "logical equivalence",
        "query equivalence",
        "arbitrary drop-in",
        "D43 reversibility",
        "official endorsement",
    ):
        assert excluded_meaning in d86


@pytest.mark.unit
def test_target_contract_keeps_release_mapping_and_ai_boundaries_distinct() -> None:
    design = _read("docs/design/ontology-platform.md")

    for compatibility_target in (
        "source containment",
        "release-bound anchors",
        "source-view recoverability",
        "provenance and view distinction",
    ):
        assert compatibility_target in design
    assert (
        "Byte recovery requires retention of the original artifact and its digest"
        in design
    )
    assert "#316" in design
    assert "refuses automatic replay and publication" in design

    for mapping_field in (
        "endpoint ontology, release, and identity",
        "relation type and direction",
        "evidence, provenance, and status",
        "license",
        "remote availability, cache, and freshness",
    ):
        assert mapping_field in design
    assert "A shared CUI is not equivalence evidence" in design
    assert "A link-out is neither an import nor a runtime dependency" in design

    outcomes = re.search(r"AI outcome is exactly one of: ([^\n]+)", design)
    assert outcomes is not None
    assert set(re.findall(r"`([^`]+)`", outcomes.group(1))) == {
        "candidate",
        "abstain",
        "failure",
    }
    assert "Human accountable authority" in design
    assert "cannot approve, publish, submit, or adopt" in design
