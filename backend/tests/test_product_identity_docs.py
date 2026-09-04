"""Contracts for current and target OntoPrism product identity documentation."""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]
_PRODUCT_IDENTITY_SURFACES = (
    ".opencode/agent/plan-adversary.md",
    "README.md",
    "AGENTS.md",
    "CLAUDE.local.md",
    "docs/ARCHITECTURE.md",
    "docs/DECISIONS.md",
    "docs/DATA_SETUP.md",
    "docs/design/README.md",
    "docs/evidence/README.md",
    "docs/design/ontology-platform.md",
    "frontend/README.md",
)
_GENERATED_IDENTITY_SURFACES = ("CHANGELOG.md",)
_FROZEN_IDENTITY_SURFACES = ("backend/tests/fixtures/d60.md",)
_HISTORICAL_SCIENTIFIC_IDENTITY_SURFACES = {
    "docs/design/ncit-alignment-integration.md": "D86 scope",
    "docs/design/ncit-decomposition-assessment.md": "not a blanket",
    "docs/design/ncit-decomposition-engine.md": (
        "not an enhanced-product compatibility claim"
    ),
    "docs/postcoordination-literature-review.md": (
        "not a blanket backward-compatibility guarantee"
    ),
    "ontolib/tests/decomposition/golden/README.md": "historical promotion",
}
_HISTORICAL_OVERCLAIM_DECISIONS = frozenset({"D24"})
_CORRECTION_CONTRACT_SURFACES = (
    "README.md",
    "AGENTS.md",
    "docs/ARCHITECTURE.md",
    "docs/DECISIONS.md",
    "docs/design/ontology-platform.md",
)
_D60_FIXTURE = "backend/tests/fixtures/d60.md"
_D60_SHA256 = "f0ca06891ad846ef28317330e7f8d5cbc4e12140e7d5f843ef8cd12c9aac58fa"
_FORBIDDEN_CURRENT_CLAIMS = (
    r"ships? (?:an? )?ontology-generic",
    r"is (?:an? )?ontology-generic",
    r"provides? generic (?:ontology )?editing",
    r"implemented generic (?:ontology )?(?:adapters?|reasoning)",
    r"generic (?:ontology )?(?:editing|reasoning|AI authoring) (?:is|are) implemented",
    r"release-forward reconciliation (?:is|has been) implemented",
    r"current correction systems?",
    r"correction systems? (?:is|are) (?:implemented|shipped|provided)",
    r"backwards?[- ]compatib\w*",
)


def _read(path: str) -> str:
    return (_ROOT / path).read_text(encoding="utf-8")


def _assignment_expression(path: str, name: str) -> ast.expr:
    module = ast.parse(_read(path), filename=path)
    matches: list[ast.expr] = []
    for node in module.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == name for target in targets
        ):
            continue
        assert node.value is not None, f"{name} has no value in {path}"
        matches.append(node.value)
    assert len(matches) == 1, f"expected one {name} assignment in {path}"
    return matches[0]


def _qualified_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name(node.value)
        return f"{prefix}.{node.attr}" if prefix is not None else None
    return None


def _static_string(expression: ast.expr, references: dict[str, str]) -> str:
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        return expression.value
    if isinstance(expression, ast.JoinedStr):
        parts: list[str] = []
        for value in expression.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
                continue
            if isinstance(value, ast.FormattedValue):
                name = _qualified_name(value.value)
                assert name in references, f"unresolved static string reference: {name}"
                parts.append(references[name])
                continue
            raise AssertionError(
                f"unsupported static string component: {ast.dump(value)}"
            )
        return "".join(parts)
    raise AssertionError(f"{ast.dump(expression)} is not a static string expression")


def _function_source(path: str, function_name: str) -> str:
    source = _read(path)
    module = ast.parse(source, filename=path)
    matches = [
        node
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    ]
    assert len(matches) == 1, f"expected one {function_name} in {path}"
    segment = ast.get_source_segment(source, matches[0])
    assert segment is not None
    return segment


def _tracked_production_sources() -> tuple[str, ...]:
    git = shutil.which("git")
    assert git is not None, "git is required to enumerate tracked production sources"
    pathspecs = (
        ":(glob)ontolib/src/**/*.py",
        ":(glob)backend/src/**/*.py",
        ":(glob)frontend/src/**/*.ts",
        ":(glob)frontend/src/**/*.svelte",
    )
    result = subprocess.run(  # noqa: S603
        [git, "ls-files", "--", *pathspecs],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    paths = tuple(line for line in result.stdout.splitlines() if line)
    assert paths, "tracked production source discovery returned no files"
    for root in ("ontolib/src/", "backend/src/", "frontend/src/"):
        assert any(path.startswith(root) for path in paths), (
            f"no sources found under {root}"
        )
    return paths


def _tracked_markdown_identity_surfaces() -> tuple[str, ...]:
    git = shutil.which("git")
    assert git is not None, "git is required to enumerate tracked Markdown"
    result = subprocess.run(  # noqa: S603 -- fixed tracked-file inventory
        [git, "ls-files", "-z", "--", "*.md", "**/*.md"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
    )
    paths = result.stdout.decode("utf-8", errors="strict").split("\0")
    boundary = re.compile(
        r"backwards?[- ]compatib\w*|\bcompatibility\b|ontology-generic|"
        r"generic ontology|dual-canonical|\bupstream\b|external content|"
        r"borrowed from|depends on",
        flags=re.IGNORECASE,
    )
    return tuple(
        path for path in paths if path and boundary.search(_read(path)) is not None
    )


def _decision_section(document: str, decision: str) -> str:
    match = re.search(
        rf"^### {decision}\..*?(?=^## \d{{4}}-|\Z)",
        document,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"missing {decision} section"
    return match.group(0).rstrip() + "\n"


def _canonical_type_block(document: str, type_name: str) -> str:
    matches = re.findall(
        rf"```text\n({re.escape(type_name)} =.*?\n)```",
        document,
        flags=re.DOTALL,
    )
    assert len(matches) == 1, f"expected one canonical {type_name} block"
    return matches[0]


def _assert_no_current_overclaim(text: str) -> None:
    for claim in _FORBIDDEN_CURRENT_CLAIMS:
        for match in re.finditer(claim, text, flags=re.IGNORECASE):
            heading_start = text.rfind("\n### ", 0, match.start())
            heading_end = text.find("\n", heading_start + 1)
            heading = text[heading_start:heading_end] if heading_start >= 0 else ""
            decision = re.match(r"\n### (D\d+)\.", heading)
            if (
                decision is not None
                and decision.group(1) in _HISTORICAL_OVERCLAIM_DECISIONS
            ):
                continue
            separator = text.rfind("\n\n", 0, match.start())
            paragraph_start = separator + 2 if separator >= 0 else 0
            paragraph_end = text.find("\n\n", match.end())
            if paragraph_end == -1:
                paragraph_end = len(text)
            paragraph = text[paragraph_start:paragraph_end]
            relative_start = match.start() - paragraph_start
            relative_end = match.end() - paragraph_start
            boundaries = [
                boundary.start()
                for boundary in re.finditer(r"(?<=[.!?])\s+", paragraph)
            ]
            sentence_start = max(
                (position + 1 for position in boundaries if position < relative_start),
                default=0,
            )
            sentence_end = min(
                (position for position in boundaries if position >= relative_end),
                default=len(paragraph),
            )
            context = paragraph[sentence_start:sentence_end]
            claim_start = relative_start - sentence_start
            prefix = context[:claim_start]
            target_or_negated = re.search(
                r"\b((?:the )?target\s*$|(?:the )?(?:target|future|intended) "
                r"(?:architecture|contract|design|capabilit\w*|system|release)|"
                r"not (?:a )?current|not (?:currently )?"
                r"implemented|does not currently|do not currently|no .{0,60} currently|"
                r"not shipped|not a shipped|does not ship|do not use|"
                r"not (?:a (?:product\s+)?)?claim|cannot claim|"
                r"contradicts\s*$|not\s*$)",
                prefix,
                flags=re.IGNORECASE,
            )
            assert target_or_negated is not None, f"{claim}: {context}"


def _assert_no_false_correction_claim(text: str) -> None:
    forbidden = (
        r"suppressed axioms? (?:(?:are|is) (?:absent|deleted|empty|missing|"
        r"not[- ]found|not retrievable|no longer present)|returns? 404)",
        r"suppression (?:is|uses) (?:a )?(?:contradictory|negating) axiom",
        r"corrections? (?:are|is) shipped",
    )
    for claim in forbidden:
        assert re.search(claim, text, flags=re.IGNORECASE) is None, claim


@pytest.mark.unit
@pytest.mark.parametrize(
    ("pattern", "claim"),
    [
        (r"ships?", "The product ships an ontology-generic platform."),
        (r"is ", "OntoPrism is ontology-generic."),
        (r"provides", "OntoPrism provides generic editing."),
        (r"implemented generic", "We have implemented generic reasoning."),
        (r"generic .* implemented", "Generic AI authoring is implemented."),
        (r"release-forward", "Release-forward reconciliation is implemented."),
        (r"current correction", "OntoPrism has a current correction system."),
        (r"correction systems", "The correction system is shipped."),
        (r"fully backward", "The enhanced release is fully backward compatible."),
        (
            r"is (?:an? )?ontology-generic",
            "OntoPrism is an ontology-generic platform designed to target future "
            "ontologies.",
        ),
        (
            r"provides? generic",
            "OntoPrism provides generic ontology editing for target ontologies.",
        ),
        (
            r"provides? generic",
            "OntoPrism for target ontologies provides generic ontology editing.",
        ),
        (
            r"backwards?[- ]compatib",
            "Our target consumers get backward-compatible behavior today.",
        ),
    ],
)
def test_documents_current_claim_gate_rejects_every_forbidden_pattern(
    pattern: str, claim: str
) -> None:
    with pytest.raises(AssertionError, match=pattern):
        _assert_no_current_overclaim(claim)


@pytest.mark.unit
@pytest.mark.parametrize(
    "claim",
    [
        "The target is ontology-generic.",
        "Generic ontology editing is a future capability.",
        "We have not implemented generic reasoning.",
        "A correction system is target architecture, not a shipped capability.",
        "The target architecture provides generic ontology editing.",
    ],
)
def test_documents_current_claim_gate_allows_explicit_target_or_negation(
    claim: str,
) -> None:
    _assert_no_current_overclaim(claim)


@pytest.mark.unit
def test_only_explicit_load_bearing_historical_decision_is_exempt() -> None:
    _assert_no_current_overclaim(
        "\n### D24. Historical proposal\n\n"
        "The product is an ontology-generic platform.\n"
    )
    for decision in ("D76", "D81"):
        with pytest.raises(AssertionError, match="ontology-generic"):
            _assert_no_current_overclaim(
                f"\n### {decision}. Historical record\n\n"
                "The product is an ontology-generic platform.\n"
            )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        ("Suppressed axioms are deleted.", "deleted"),
        ("Suppressed axiom is not found.", "not\\[- \\]found"),
        ("Suppressed axiom is absent.", "absent"),
        ("Suppressed axiom is not retrievable.", "not retrievable"),
        ("Suppressed axiom is no longer present.", "no longer present"),
        ("Suppressed axiom returns 404.", "404"),
        ("Suppression is a contradictory axiom.", "contradictory"),
        ("Corrections are shipped.", "corrections"),
    ],
)
def test_documents_correction_claim_gate_rejects_false_absence_or_current_claim(
    claim: str, expected: str
) -> None:
    with pytest.raises(AssertionError, match=expected):
        _assert_no_false_correction_claim(claim)


@pytest.mark.unit
def test_documents_product_identity_surfaces_separate_current_from_target() -> None:
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

    for path in _PRODUCT_IDENTITY_SURFACES:
        _assert_no_current_overclaim(_read(path))

    metadata = _read("pyproject.toml").lower()
    assert "ontology-generic framework target" in metadata
    assert "current enhanced ncit storage" in metadata
    assert metadata.index("current enhanced ncit") < metadata.index(
        "ontology-generic framework target"
    )


@pytest.mark.unit
def test_every_tracked_markdown_identity_match_has_an_explicit_classification() -> None:
    discovered = set(_tracked_markdown_identity_surfaces())
    classified = (
        set(_PRODUCT_IDENTITY_SURFACES)
        | set(_HISTORICAL_SCIENTIFIC_IDENTITY_SURFACES)
        | set(_GENERATED_IDENTITY_SURFACES)
        | set(_FROZEN_IDENTITY_SURFACES)
    )

    assert discovered == classified, (
        f"unclassified={sorted(discovered - classified)}, "
        f"stale={sorted(classified - discovered)}"
    )


@pytest.mark.unit
def test_historical_scientific_compatibility_claims_are_locally_scoped() -> None:
    literature = _read("docs/postcoordination-literature-review.md")
    assessment = _read("docs/design/ncit-decomposition-assessment.md")

    assert "specified reasoner, entailment profile, and query pattern" in literature
    assert "not a blanket backward-compatibility guarantee" in literature
    assert "guarantees code retention and source-anchor resolution" in assessment
    assert "not a blanket\nbackward-compatibility guarantee" in assessment
    for path, required_scope_marker in _HISTORICAL_SCIENTIFIC_IDENTITY_SURFACES.items():
        assert required_scope_marker in _read(path), path
    assert _GENERATED_IDENTITY_SURFACES == ("CHANGELOG.md",)
    assert _FROZEN_IDENTITY_SURFACES == (_D60_FIXTURE,)


@pytest.mark.unit
def test_frontend_readme_names_the_authoritative_quality_gate() -> None:
    readme = _read("frontend/README.md")

    assert "`pdm run verify` from the repository root is authoritative" in readme
    assert "debugging and are not the complete quality gate" in readme
    assert "npm --prefix frontend run test:coverage" in readme
    assert "npm --prefix frontend run fallow" in readme


@pytest.mark.unit
def test_documents_frontend_development_uses_supported_root_command() -> None:
    frontend_readme = _read("frontend/README.md")
    scripts = _read("pyproject.toml")
    assert "Run these commands from the repository root" in frontend_readme
    assert "pdm run start-frontend" in frontend_readme
    assert (
        'start-frontend = { shell = "bash scripts/dev.sh start frontend" }' in scripts
    )
    assert "port `5175`" in frontend_readme


@pytest.mark.unit
def test_documents_d86_is_newest_and_preserves_d60_verbatim() -> None:
    decisions = _read("docs/DECISIONS.md")
    ids = [
        int(value) for value in re.findall(r"^### D(\d+)\.", decisions, re.MULTILINE)
    ]
    assert ids[:2] == [86, 85]
    assert ids.count(86) == 1
    assert max(ids) == 86

    current_d60 = _decision_section(decisions, "D60")
    fixture = _read(_D60_FIXTURE)
    assert sha256(fixture.encode()).hexdigest() == _D60_SHA256
    assert current_d60 == fixture

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
def test_documents_target_release_mapping_and_ai_boundaries() -> None:
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
def test_documents_target_correction_preserves_source_and_suppression() -> None:
    for path in _CORRECTION_CONTRACT_SURFACES:
        text = _read(path)
        _assert_no_false_correction_claim(text)
        assert "target" in text.lower(), path
        assert "official source" in text.lower(), path
        assert "effective" in text.lower(), path
        assert "removed-from-effective" in text, path
        text_flat = " ".join(text.lower().split())
        assert "retrievable" in text_flat, path
        assert "nonempty `removed-from-effective`" in text_flat, path

    described_surfaces = {
        path
        for path in _PRODUCT_IDENTITY_SURFACES
        if "effective correction" in _read(path).lower()
    }
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
        assert required.lower() in design_flat.lower()

    assert "annotation-only suppression" in design_flat
    assert "contradictory or negating axiom" in design_flat
    assert "must not represent suppression" in design_flat


@pytest.mark.unit
def test_documents_target_entity_crosswalk_and_assertion_types() -> None:
    design = _read("docs/design/ontology-platform.md")
    design_flat = " ".join(design.split())
    for required in (
        "`EnhancedEntityOrigin`",
        "`DerivedFromOfficial`",
        "required release-bound official entity references",
        "`NewEnhancedEntity`",
        "forbids official entity references",
        "`EntityCrosswalkOutcome`",
        "`unchanged`, `edited`, `split`, `merge`, `replacement`, or `new`",
        "cardinality is derived from endpoint sets and is never stored unchecked",
        "| `new` | 0 → 1 |",
        "| `unchanged`, `edited`, and `replacement` | 1 → 1 |",
        "| `split` | 1 → N, N ≥ 2 |",
        "| `merge` | M → 1, M ≥ 2 |",
        "`complex-restructure` requiring human review",
        "`AssertionDeltaKind`",
        "`added-to-effective`, `removed-from-effective`, `replaced-in-effective`, "
        "`qualified-in-effective`, `annotation-changed`, or `unchanged-context`",
        "Suppression is not entity disappearance or an entity-crosswalk outcome",
        "nonempty `removed-from-effective` record",
        "canonical source axiom",
        "qualification is an assertion operation",
        "official release + official concept/role code + canonical source "
        "entity/assertion fingerprints and profile",
        "globally unique content-addressed revision under its enhanced code",
        "release, overlay, and composition are membership contexts",
        "Code equality, revision equality, and cache keys",
        "never reused and is not replaced by NCI adoption",
    ):
        assert required.lower() in design_flat.lower()

    routing = _canonical_type_block(design, "CrosswalkRouting")
    assert "CrosswalkRouting = EntityCrosswalkOutcome | ComplexRestructure" in routing
    assert (
        "ComplexRestructure { sources: Set<M>, M ≥ 2, targets: Set<N>, N ≥ 2, "
        "humanReviewRequired: true, reason: NonEmptyText }"
    ) in " ".join(routing.split())
    assert "additional many-to-many arity family" in design_flat.lower()


@pytest.mark.unit
def test_documents_target_resolution_compatibility_and_graph_types() -> None:
    design = _read("docs/design/ontology-platform.md")
    design_flat = " ".join(design.split())
    for required in (
        "caDSR source rows and anchors remain official NCIt codes",
        "`CadsrEnhancedResolution`",
        "one-to-many crosswalk is `split` and returns all targets",
        "further qualified, approved mapping selects one target",
        "`ambiguous` means competing or incomplete records",
        "`MigrationReferenceOutcome`",
        "derived from `CrosswalkRouting` plus consumer context",
        "combines the crosswalk with consumer context and may refuse",
        "official-anchor coverage and enhanced-resolution coverage",
        "`preserved`, `changed`, and `breaking` require a tested denominator "
        "of at least one",
        "`unknown` carries a blocker or reason and cannot carry a success denominator",
        "Overall compatibility cannot be compatible when any required result "
        "is `unknown`",
        "must not serialize edited semantics under an official NCIt IRI",
        "source export profile remains distinct",
        "`AffectedGraphDiff`",
        "cannot carry certified complete change sets or publication permission",
        "profile, relation, direction, bounds, and boundary witnesses",
        "stated changes and finite-profile inferred changes remain separate",
        "`complete-for-profile` or `incomplete`",
        "An incomplete diff cannot publish an incremental result",
        "exact finite entailment, query, and signature set selected by a "
        "versioned profile",
        "runtime reasoning is disabled",
        "offline identified reasoner and profile",
        "dependency-registry impacts, not graph members",
        "explicit non-success/currentness treatment owned by #262",
        "full-run fallback or refusal",
        "`OverlayIntent`",
        "`correction`, `enrichment`, or `modelling-alternative`",
        "modelling alternative is not a correction error",
        "add or qualify only",
        "partial, ambiguous, or divergent adoption requires human review",
        "nothing is silently replayed, dropped, or overridden",
    ):
        assert required.lower() in design_flat.lower()

    assert "#262" in design_flat
    assert "#316" in design_flat
    assert "currently owns proposal transfer" in design_flat
    assert "correction-aware extension needs explicit future ownership" in design_flat
    assert "stale-pending, recompute, revalidate, remap, or refuse" not in design_flat

    cadsr = " ".join(_canonical_type_block(design, "CadsrEnhancedResolution").split())
    for variant in (
        "split { allTargets: Set<N>, N ≥ 2, crosswalk }",
        "merged { target, sources: Set<M>, M ≥ 2, crosswalk }",
        "ambiguous { candidates: NonEmptySet, reason: NonEmptyText }",
        "unresolved { reason: NonEmptyText }",
        "complex-restructure { sources: Set<M>, M ≥ 2, targets: Set<N>, N ≥ 2, "
        "humanReviewRequired: true, reason: NonEmptyText }",
    ):
        assert variant in cadsr

    migration = " ".join(
        _canonical_type_block(design, "MigrationReferenceOutcome").split()
    )
    for variant in (
        "retained { source, target }",
        "redirected { source, target, crosswalk }",
        "expanded { source, allTargets: Set<N>, N ≥ 2, crosswalk, "
        "humanReviewRequired: true }",
        "suppressed { source, replacement?, reason: NonEmptyText, "
        "humanReviewRequired: true }",
        "ambiguous { candidates: NonEmptySet, reason: NonEmptyText }",
        "unsupported { reason: NonEmptyText }",
        "unresolved { reason: NonEmptyText }",
        "complex-restructure { sources: Set<M>, M ≥ 2, targets: Set<N>, N ≥ 2, "
        "humanReviewRequired: true, reason: NonEmptyText }",
    ):
        assert variant in migration

    graph = " ".join(_canonical_type_block(design, "AffectedGraphDiff").split())
    assert (
        "CompleteForProfile { closure, statedChanges, finiteProfileInferredChanges }"
        in graph
    )
    assert (
        "Incomplete { closure, causes: NonEmptySet<Blocker | MissingBoundary> }"
    ) in graph
    assert "completeness:" not in graph


@pytest.mark.unit
def test_documents_target_views_bind_exact_identities_without_flattening() -> None:
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
    assert (
        "Correction-aware reconciliation requires a future amendment or new owner"
        in d86_flat
    )
    assert "proposed →" not in d86_flat


@pytest.mark.unit
def test_documents_current_graph_iri_debt_and_source_containment_are_explicit() -> None:
    design = _read("docs/design/ontology-platform.md")
    design_flat = " ".join(design.split())
    for required in (
        "`read_queries.py` contains no `STATED_GRAPH_IRI` reference",
        "targets `DECOMPOSED_GRAPH_IRI`",
        "current OntoPrism-authored NCI-domain graph IRIs",
        "decomposition, upstream-xref, enhanced-showcase, and scoped "
        "staging/generation graphs",
        "`DECOMPOSED_GRAPH_IRI/staging/{sha256(run_id)}`",
        "`SHOWCASE_GRAPH_IRI/staging/{sha256(run_id)}`",
        "`NCIT_UPSTREAM_XREF_GRAPH_IRI/generation/{component}/{generation_id}`",
        "`NCIT_UPSTREAM_XREF_GRAPH_IRI/active/{component}`",
        "`component` is `source.casefold()` with each non-alphanumeric run replaced "
        "by `-` and leading/trailing `-` removed",
        "must not be represented as an official NCI identifier",
        "future enhanced export must use an OntoPrism-governed enhanced namespace",
        "future implementation issue",
        "immutable official-source preservation",
        "effective enhanced view intentionally need not contain official assertions",
        "unchanged rendition is representable",
        "suppression leaves the enhanced entity and its source crosswalk intact",
    ):
        assert required in design_flat

    alignment = " ".join(_read("docs/design/ncit-alignment-integration.md").split())
    assert "generation/{component}/{generation_id}" in alignment
    assert (
        "`component` is `source.casefold()` with each non-alphanumeric run replaced "
        "by `-` and leading/trailing `-` removed"
    ) in alignment


@pytest.mark.unit
def test_current_read_planes_are_distinct_in_production_source() -> None:
    projection_reader = _read("ontolib/src/ontolib/decomposition/read_queries.py")
    assert "STATED_GRAPH_IRI" not in projection_reader
    assert "DECOMPOSED_GRAPH_IRI" in projection_reader

    stated_readers = (
        "ontolib/src/ontolib/decomposition/stated_queries.py",
        "ontolib/src/ontolib/decomposition/scope.py",
        "ontolib/src/ontolib/decomposition/walker.py",
    )
    assert stated_readers
    for path in stated_readers:
        assert "STATED_GRAPH_IRI" in _read(path), path


@pytest.mark.unit
def test_current_authored_ncit_graph_iris_are_explicit_technical_debt() -> None:
    decomposed = _static_string(
        _assignment_expression(
            "ontolib/src/ontolib/decomposition/vocab.py", "DECOMPOSED_GRAPH_IRI"
        ),
        {},
    )
    xref = _static_string(
        _assignment_expression(
            "ontolib/src/ontolib/repositories/xref/vocab.py",
            "NCIT_UPSTREAM_XREF_GRAPH_IRI",
        ),
        {},
    )
    showcase = _static_string(
        _assignment_expression(
            "ontolib/src/ontolib/decomposition/enhanced_showcase.py",
            "SHOWCASE_GRAPH_IRI",
        ),
        {"vocab.DECOMPOSED_GRAPH_IRI": decomposed},
    )

    assert {
        "decomposition": decomposed,
        "upstream-xref": xref,
        "enhanced-showcase": showcase,
    } == {
        "decomposition": (
            "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-decomposed.owl"
        ),
        "upstream-xref": (
            "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-upstream-xref.owl"
        ),
        "enhanced-showcase": (
            "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-decomposed.owl/"
            "enhanced-ncit-showcase"
        ),
    }

    assert 'f"{vocab.DECOMPOSED_GRAPH_IRI}/staging/{digest}"' in _function_source(
        "ontolib/src/ontolib/decomposition/publication.py", "staging_graph_iri"
    )
    assert (
        'f"{SHOWCASE_GRAPH_IRI}/staging/{hashlib.sha256(run_id.encode()).hexdigest()}"'
        in _function_source(
            "ontolib/src/ontolib/decomposition/enhanced_showcase.py",
            "showcase_staging_graph_iri",
        )
    )
    xref_publication = _read("ontolib/src/ontolib/repositories/xref/publication.py")
    assert "/generation/{component}/{generation_id}" in xref_publication
    assert "/active/{component}" in xref_publication


@pytest.mark.unit
def test_no_generic_ontology_adapter_symbol_in_tracked_production_source() -> None:
    paths = _tracked_production_sources()
    matches = [path for path in paths if "OntologyAdapter" in _read(path)]
    assert not matches, f"unexpected current OntologyAdapter implementation: {matches}"


@pytest.mark.unit
def test_current_overlay_writers_cannot_target_the_stated_graph() -> None:
    stated_iri = _static_string(
        _assignment_expression(
            "ontolib/src/ontolib/terminologies/ncit/owl_load.py", "STATED_GRAPH_IRI"
        ),
        {},
    )
    publication_update = _function_source(
        "ontolib/src/ontolib/decomposition/publication.py",
        "build_replacement_update",
    )
    showcase_update = _function_source(
        "ontolib/src/ontolib/decomposition/enhanced_showcase.py",
        "build_showcase_replacement_update",
    )

    assert "public = vocab.DECOMPOSED_GRAPH_IRI" in publication_update
    assert "GRAPH <{public}>" in publication_update
    assert "GRAPH <{SHOWCASE_GRAPH_IRI}>" in showcase_update
    for update in (publication_update, showcase_update):
        assert "STATED_GRAPH_IRI" not in update
        assert stated_iri not in update

    graph_agnostic_writer = _read("ontolib/src/ontolib/decomposition/legacy_writer.py")
    xref_writer_modules = (
        "ontolib/src/ontolib/repositories/xref/publication.py",
        "ontolib/src/ontolib/repositories/xref/ttl_writer.py",
    )
    for path, source in (
        ("legacy_writer.py", graph_agnostic_writer),
        *((path, _read(path)) for path in xref_writer_modules),
    ):
        assert "STATED_GRAPH_IRI" not in source, path
        assert stated_iri not in source, path
