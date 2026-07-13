"""Contract tests: what ROBOT/ELK *actually does*, and whether our double agrees.

**Why this file exists.** Every bug in the #73 work was a false assumption about an
external system (ROBOT's CLI, ELK's output shape, OWL semantics, the real Uberon data) —
not a logic error inside our code. Strict TDD cannot catch that class of bug: the test
and
the code are written from the same mental model, so the hand-made double encodes the
same
false belief as the implementation, the two agree with each other, and both are wrong.
The
suite stays green while the system is broken. Three of the worst bugs here were
*certified*
by a double that implemented a rule the real reasoner does not.

So this file tests things a double can never tell us:

1. **The tool's contract.** Assertions about ROBOT/ELK itself. If a ROBOT upgrade
   changes
   a default or renames a subcommand, these fail loudly and name the broken assumption —
   instead of the change surfacing as "no candidate qualified" six months later.
2. **Double fidelity.** The same merge through `_ElkLikeReasoner` and the real
   `elk_reasoner`, asserting they reach the same verdict. A double that is *stronger*
   than
   the real reasoner is how the same-subject guard got certified without existing.
3. **Gate liveness.** Each gate must be demonstrably able to *fire* on production-shaped
   input. The satisfiability gate was vacuous for a whole round — its reject branch was
   unreachable — and every happy-path test still passed.

These are integration tests: they need `robot` on PATH (CI installs it; see ci.yml).
"""

from __future__ import annotations

import shutil
from pathlib import Path  # noqa: TC003 — needed at runtime by pytest fixtures

import pytest

from ontolib.repositories.xref.promotion import elk_reasoner, parse_inferred_subclasses
from ontolib.repositories.xref.validation import classify, to_el_profile_and_check

_A = "http://example.org/A"
_B = "http://example.org/B"
_C = "http://example.org/C"
_D = "http://example.org/D"

pytestmark = pytest.mark.integration


def _skip_without_robot() -> None:
    if shutil.which("robot") is None:
        pytest.skip("robot not on PATH")


def _ttl(body: str) -> str:
    return f"""\
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
<http://example.org/o> a owl:Ontology .
{body}
"""


# ── 1. the tool's contract ─────────────────────────────────────────────


def test_robot_reason_does_not_materialize_the_transitive_closure(
    tmp_path: Path,
) -> None:
    """ROBOT emits DIRECT subsumptions; it does not state A ⊑ C for A ⊑ B ⊑ C.

    Assuming it did is what made corroboration miss every anchor above a direct parent.
    Our code must therefore WALK the hierarchy, never test it for membership.
    """
    _skip_without_robot()
    src = tmp_path / "chain.ttl"
    src.write_text(
        _ttl(
            f"""<{_A}> a owl:Class ; rdfs:subClassOf <{_B}> .
<{_B}> a owl:Class ; rdfs:subClassOf <{_C}> .
<{_C}> a owl:Class ."""
        )
    )

    out = classify(str(src))
    assert out is not None
    edges = parse_inferred_subclasses(out)

    assert (_A, _B) in edges
    assert (_B, _C) in edges
    assert (_A, _C) not in edges, (
        "ROBOT materialized the closure — the walk in _reachable_ancestors could be "
        "replaced by a membership test, and this contract test should be deleted."
    )


def test_robot_reason_keeps_our_asserted_axioms(tmp_path: Path) -> None:
    """With `--remove-redundant-subclass-axioms false`, the output is a SUPERSET of the
    input.

    Without the flag ROBOT deletes an asserted `A ⊑ B` once it infers `A ≡ B`, and its
    default axiom-generators never write the equivalence back — so A and B end up
    connected by nothing, and `elk_reasoner`'s "did ROBOT classify this?" check
    false-alarms on a perfectly sound run.
    """
    _skip_without_robot()
    src = tmp_path / "cycle.ttl"
    # A ⊑ B and B ⊑ A: the merge entails A ≡ B, which is exactly the collapse that
    # triggered the false alarm.
    src.write_text(
        _ttl(
            f"""<{_A}> a owl:Class ; rdfs:subClassOf <{_B}> .
<{_B}> a owl:Class ; rdfs:subClassOf <{_A}> ."""
        )
    )

    out = classify(str(src))
    assert out is not None
    edges = parse_inferred_subclasses(out)

    assert (_A, _B) in edges
    assert (_B, _A) in edges


def test_robot_refuses_an_unsatisfiable_merge_and_says_so(tmp_path: Path) -> None:
    """The satisfiability gate rests on ROBOT exiting non-zero AND its output carrying a
    marker we match (`_UNSATISFIABLE_MARKERS`).  If ROBOT reworded that message, the
    marker match would fail and we would raise ReasonerUnavailableError instead of
    recording a refutation — this test pins the wording."""
    _skip_without_robot()
    src = tmp_path / "unsat.ttl"
    src.write_text(
        _ttl(
            f"""<{_C}> a owl:Class ; owl:disjointWith <{_D}> .
<{_D}> a owl:Class .
<{_A}> a owl:Class ; rdfs:subClassOf <{_C}> , <{_D}> ."""
        )
    )

    assert to_el_profile_and_check(str(src)) is True  # disjointness is EL-legal
    assert classify(str(src)) is None  # refuted, NOT an exception


def test_robot_validate_profile_rejects_a_non_el_ontology(tmp_path: Path) -> None:
    """The EL gate's False branch must be reachable, and reached via a *reported
    violation* rather than an environment failure (which raises).

    `owl:unionOf` is OWL 2 DL but not EL.
    """
    _skip_without_robot()
    src = tmp_path / "non-el.ttl"
    src.write_text(
        _ttl(
            f"""<{_A}> a owl:Class ; owl:equivalentClass [
    a owl:Class ; owl:unionOf ( <{_B}> <{_C}> )
] .
<{_B}> a owl:Class .
<{_C}> a owl:Class ."""
        )
    )

    assert to_el_profile_and_check(str(src)) is False


# ── 2. double fidelity ─────────────────────────────────────────────────


def _merges() -> list[tuple[str, str]]:
    """(name, turtle) — merges shaped like the ones promotion actually builds."""
    return [
        (
            "plain chain",
            _ttl(
                f"""<{_A}> a owl:Class ; rdfs:subClassOf <{_B}> .
<{_B}> a owl:Class ; rdfs:subClassOf <{_C}> .
<{_C}> a owl:Class ."""
            ),
        ),
        (
            "anchor equivalence",
            _ttl(
                f"""<{_A}> a owl:Class ; rdfs:subClassOf <{_B}> .
<{_B}> a owl:Class ; owl:equivalentClass <{_C}> .
<{_C}> a owl:Class ."""
            ),
        ),
        (
            "unsatisfiable via disjointness",
            _ttl(
                f"""<{_C}> a owl:Class ; owl:disjointWith <{_D}> .
<{_D}> a owl:Class .
<{_A}> a owl:Class ; rdfs:subClassOf <{_C}> , <{_D}> ."""
            ),
        ),
        (
            "two equivalents, NOT disjoint (a refutation-only oracle is powerless)",
            _ttl(
                f"""<{_A}> a owl:Class ; owl:equivalentClass <{_B}> , <{_C}> .
<{_B}> a owl:Class .
<{_C}> a owl:Class ."""
            ),
        ),
    ]


@pytest.mark.parametrize(("name", "ttl"), _merges(), ids=lambda v: str(v)[:40])
def test_the_unit_double_agrees_with_the_real_reasoner(name: str, ttl: str) -> None:
    """The unit double must reach the SAME verdict as real ELK on the same merge.

    This is the test that would have caught three of this PR's bugs on day one. A double
    that is *stronger* than the reasoner (refuting things ELK accepts) certifies guards
    that do not exist; a double that is *weaker* hides gates that cannot fire. Either
    way
    the unit suite is green and the system is wrong.

    Imported lazily from the test module that owns it, so the double under test is
    literally the one the unit tests use — not a copy that can drift.
    """
    _skip_without_robot()
    from .test_promotion import _SatisfiabilityHonestReasoner  # noqa: PLC0415

    double_verdict = _SatisfiabilityHonestReasoner()(ttl)
    real_verdict = elk_reasoner(ttl)

    # Agreement on the only thing the caller branches on: refuted (None) vs classified.
    assert (double_verdict is None) == (real_verdict is None), (
        f"the double and real ELK disagree on '{name}': "
        f"double={'refuted' if double_verdict is None else 'accepted'}, "
        f"real={'refuted' if real_verdict is None else 'accepted'}"
    )
