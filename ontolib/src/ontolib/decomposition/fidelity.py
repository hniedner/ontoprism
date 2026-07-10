"""Roundtrip fidelity metric for --emit-equivalence (design §10, D21.3).

Fidelity measures how well the emitted owl:equivalentClass unfolding
reconstructs the original concept's stated definition. Per D21.3, the oracle
is the stated owl:equivalentClass/owl:intersectionOf structure, NOT the
inferred rdfs:subClassOf+ graph.
"""

from __future__ import annotations


def roundtrip_fidelity(
    emitted: set[tuple[str, str]],
    stated: set[tuple[str, str]],
) -> float:
    """Compute fidelity: fraction of stated restrictions covered by emitted.

    Args:
        emitted: Set of (axis/property, filler) pairs from the emitted
            owl:equivalentClass intersection axiom.
        stated: Set of (property, filler) pairs from the stated definition.

    Returns:
        1.0 if emitted covers all stated restrictions.
        Fractional value (0.0-1.0) if some stated restrictions are missing.
        1.0 if stated is empty (nothing to cover).
    """
    if not stated:
        return 1.0
    covered = len(emitted & stated)
    return covered / len(stated)
