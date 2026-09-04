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
_CORRECTION_CONTRACT_SURFACES = (
    "README.md",
    "AGENTS.md",
    "docs/ARCHITECTURE.md",
    "docs/DECISIONS.md",
    "docs/design/ontology-platform.md",
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


def _assert_no_false_correction_claim(text: str) -> None:
    forbidden = (
        r"suppressed axioms? (?:are|is) (?:absent|deleted|empty|missing|not[- ]found)",
        r"suppression (?:is|uses) (?:a )?(?:contradictory|negating) axiom",
        r"corrections? (?:are|is) shipped",
    )
    for claim in forbidden:
        assert re.search(claim, text, flags=re.IGNORECASE) is None, claim


@pytest.mark.unit
def test_current_overclaim_gate_rejects_a_shipped_generic_adapter_claim() -> None:
    with pytest.raises(AssertionError, match="implemented generic"):
        _assert_no_current_overclaim(
            "The current product has implemented generic ontology adapters."
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        ("Suppressed axioms are deleted.", "deleted"),
        ("Suppressed axiom is not found.", "not\\[- \\]found"),
        ("Suppressed axiom is absent.", "absent"),
        ("Suppression is a contradictory axiom.", "contradictory"),
        ("Corrections are shipped.", "corrections"),
    ],
)
def test_correction_claim_gate_rejects_false_absence_or_current_claim(
    claim: str, expected: str
) -> None:
    with pytest.raises(AssertionError, match=expected):
        _assert_no_false_correction_claim(claim)


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


@pytest.mark.unit
def test_target_correction_contract_preserves_source_and_exposes_suppression() -> None:
    described_surfaces: set[str] = set()
    for path in _PRODUCT_IDENTITY_SURFACES:
        text = _read(path)
        _assert_no_false_correction_claim(text)
        if "effective correction" not in text.lower():
            continue
        described_surfaces.add(path)
        assert "target" in text.lower(), path
        assert "official source" in text.lower(), path
        assert "effective" in text.lower(), path
    assert described_surfaces == set(_CORRECTION_CONTRACT_SURFACES)

    design = _read("docs/design/ontology-platform.md")
    design_flat = " ".join(design.split())
    for required in (
        "authoritative evidence of what NCI published",
        "not a claim of scientific or logical infallibility",
        "named effective-view composition subtraction before reasoning",
        "Re-reasoning the exact composition",
        "inconsistency, unsupported targets, or missing targets refuse publication",
        "`removed-from-effective`",
        "always remains retrievable in the official source view",
        "source release and canonical assertion identity",
        "correction evidence and accountable decision",
        "stated and finite-profile inferred before/after effects",
        "declared affected closure and boundary evidence",
        "dependent impacts",
    ):
        assert required in design_flat

    assert "annotation-only suppression" in design_flat
    assert "contradictory or negating axiom" in design_flat
    assert "must not represent suppression" in design_flat


@pytest.mark.unit
def test_target_correction_identity_crosswalk_and_adoption_are_unambiguous() -> None:
    design = _read("docs/design/ontology-platform.md")
    design_flat = " ".join(design.split())
    for required in (
        "every enhanced NCIt concept and role",
        "including unchanged official renditions",
        "stable OntoPrism-governed enhanced-NCIt code",
        "release-bound crosswalk",
        "unless its provenance is `new`",
        "entity kind and computed cardinality",
        "unchanged or its exact change set",
        "edit, split, merge, replacement, suppression, qualification, or new",
        "official release + official concept/role code + canonical source "
        "entity/assertion fingerprints and profile",
        "enhanced code + immutable entity revision + enhanced "
        "release/overlay/composition identities",
        "never reused and is not replaced by NCI adoption",
        "Only exact certified release/assertion evidence",
        "reconciliation does not assign lifecycle state",
    ):
        assert required in design_flat


@pytest.mark.unit
def test_target_cadsr_compatibility_impact_and_reconciliation_fail_closed() -> None:
    design = _read("docs/design/ontology-platform.md")
    design_flat = " ".join(design.split())
    for required in (
        "caDSR source rows and anchors remain official NCIt codes",
        "unique, split, merge, ambiguous, or unresolved",
        "must not heuristically select a split result",
        "official-anchor coverage and enhanced-resolution coverage",
        "preserved, changed, breaking, or unknown",
        "denominator and known breaks",
        "must not serialize edited semantics under an official NCIt IRI",
        "complete only under an explicit versioned graph closure",
        "relation, direction, bounds, and boundary witnesses",
        "stated and inferred effects remain separate",
        "dependency-registry impacts, not graph members",
        "stale-pending, recompute, revalidate, remap, or refuse",
        "nothing remains unclassified",
        "partial, ambiguous, or divergent adoption requires human review",
        "nothing is silently replayed, dropped, or overridden",
    ):
        assert required.lower() in design_flat.lower()

    assert "#262" in design_flat
    assert "#316" in design_flat
    assert "currently owns proposal transfer" in design_flat
    assert "correction-aware extension needs explicit future ownership" in design_flat


@pytest.mark.unit
def test_target_visualizations_bind_exact_view_identities_without_flattening() -> None:
    design = _read("docs/design/ontology-platform.md")
    design_flat = " ".join(design.split())
    for view in ("official source", "effective", "delta", "impact", "migration"):
        assert f"`{view}`" in design_flat
    exact_identities = (
        "exact release, overlay, composition, and entity/assertion identities"
    )
    assert exact_identities in design_flat
    assert "simultaneously inspectable" in design_flat
    assert "Edge and axiom kinds remain typed and are never flattened" in design_flat

    d86 = _decision_section(_read("docs/DECISIONS.md"), "D86")
    d86_flat = " ".join(d86.split())
    assert "#304" in d86_flat
    assert "#262" in d86_flat
    assert "currently owns proposal transfer" in d86_flat
    assert "correction-aware extension requires explicit future ownership" in d86_flat
    assert "proposed →" not in d86_flat
