"""Independent-evidence policy for candidate promotion (design §4.4, D28).

D28 makes non-circularity an invariant: **the evidence for an `owl:equivalentClass`
bridge may never be the mapping itself.**  Two teeth:

1. A ``skos:*Match`` annotation is not admissible evidence at all — it carries no
   logical semantics, and volume of xrefs is not evidence of correctness.  This is
   enforced structurally: :class:`Evidence` refuses to hold one.
2. The signal that *generated* a candidate may not be recycled as the evidence that
   promotes it.  An xref-derived candidate is not corroborated by that same xref; a
   lexically-derived candidate is not corroborated by that same label match.
   :func:`gather_evidence` drops the generating signal, keyed on the record's
   ``mapping_justification``.

Promotion then requires *independent* evidence (:func:`is_independent`): either human
curation, or at least two distinct corroborating signals.

**Two passes can generate the same pair (D34).**  Ingest runs the xref pass and the
lexical pass over every filler, and where both produce the same ``(subject, object)`` it
mints a single ``semapv:CompositeMatching`` candidate.  For that record tooth 2 drops
**nothing**, and this is not a loophole but the only reading that keeps the rule
coherent: two independent processes produced the pair, so the label match corroborates
the xref-derived candidate and the xref corroborates the lexically-derived one — neither
is its own evidence.  (Formally: the evidence for a pair is the union, over its
candidate records, of each record's evidence-minus-its-origin — and that union drops
exactly the *intersection* of the origins, which is empty when the two passes differ.)
Dropping both
would instead make the strongest candidates — an independent OBO curator asserted the
cross-reference *and* the names agree — the only ones that can never promote.

The justification is never taken on trust: every signal is re-derived here from the
store's own facts, so a composite record whose labels have since diverged gathers one
signal, not two, and stops promoting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ontolib.repositories.xref.vocab import (
    ALLOWED_PREDICATES,
    COMPOSITE_MATCHING,
    DATABASE_CROSS_REFERENCE,
    LEXICAL_MATCHING,
    SKOS_NS,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ontolib.repositories.xref.models import SSSOMRecord

# -- module constants ---------------------------------------------------

_NCIT_CODE_PREFIX = "NCIT:"

# ── evidence kinds ─────────────────────────────────────────────────────

# NCIt label agrees with an upstream label (case-folded).
LABEL_AGREEMENT = "label_agreement"
# The upstream project independently asserts an `NCIT:<code>` xref on the object.
XREF_ASSERTION = "xref_assertion"
# The upstream object sits under the upstream image of an NCIt ancestor of the
# subject (reachable via subClassOf OR part_of), established through a *separately
# validated* anchor bridge.
STRUCTURAL_CORROBORATION = "structural_corroboration"
# The pair is in the curated (SME-signed) mapping set.
SME_CURATION = "sme_curation"

EVIDENCE_KINDS = frozenset(
    {LABEL_AGREEMENT, XREF_ASSERTION, STRUCTURAL_CORROBORATION, SME_CURATION}
)

# The signals each mapping_justification was derived from — a candidate's *origins* can
# never also be its evidence.  A composite candidate has NO sole origin: two independent
# passes produced it, and each corroborates the candidate the other generated (D34).
_GENERATING_SIGNALS: dict[str, frozenset[str]] = {
    LEXICAL_MATCHING: frozenset({LABEL_AGREEMENT}),
    DATABASE_CROSS_REFERENCE: frozenset({XREF_ASSERTION}),
    COMPOSITE_MATCHING: frozenset(),
}

# Absent human curation, a bridge needs corroboration from two *different* signals.
_MIN_INDEPENDENT_SIGNALS = 2


def _is_skos_mapping(value: str) -> bool:
    """Is *value* a SKOS mapping property, in **either** spelling?

    Checking only the full IRIs (``ALLOWED_PREDICATES``) would be near-vacuous: every
    ``source`` this module actually mints is CURIE-form (``rdfs:label``,
    ``oboInOwl:hasDbXref``), so a caller writing ``skos:exactMatch`` — the natural
    spelling, and precisely the thing D28 forbids as evidence — would sail through a
    guard that only knows ``http://www.w3.org/2004/02/skos/core#exactMatch``.  A guard
    that rejects only a form nobody writes is worse than no guard: it reads as
    protection.
    """
    if value in ALLOWED_PREDICATES:
        return True
    local = value.split(":", 1)[-1] if value.startswith("skos:") else ""
    return bool(local) and f"{SKOS_NS}{local}" in ALLOWED_PREDICATES


@dataclass(frozen=True)
class Evidence:
    """One independent corroborating signal, with its provenance."""

    kind: str
    source: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.kind not in EVIDENCE_KINDS:
            raise ValueError(f"unknown evidence kind: {self.kind}")
        if not self.source:
            raise ValueError("evidence source must be non-empty")
        for field in (self.source, self.detail):
            if _is_skos_mapping(field):
                raise ValueError(
                    "a SKOS mapping annotation may never serve as evidence for the "
                    f"bridge it annotates (D28): {field}"
                )


def gather_evidence(
    record: SSSOMRecord,
    *,
    subject_labels: set[str],
    object_labels: set[str],
    object_xref_codes: set[str],
    curated_pairs: frozenset[tuple[str, str]],
    structurally_corroborated: bool,
) -> list[Evidence]:
    """Collect the signals that corroborate *record*, minus its generating signal.

    All inputs are facts already fetched about the two endpoints: the labels of the
    subject and object, the NCIt codes the upstream object itself xrefs, the curated
    pair set, and whether the pair was structurally corroborated (ELK's ``subClassOf``
    entailments together with stated ``part_of`` edges) through a separate anchor,
    computed over a merge that excludes this candidate's bridge.
    """
    if record.mapping_justification not in _GENERATING_SIGNALS:
        # Fail closed. With an unrecognised justification we cannot know which signal
        # produced this candidate, so we cannot drop it — and the xref that generated
        # the mapping would be counted as independent evidence *for* that mapping,
        # which is exactly the circularity this module exists to prevent (D28).
        raise ValueError(
            "cannot establish the generating signal for mapping_justification "
            f"{record.mapping_justification!r}: refusing to gather evidence, because "
            "the signal that produced a candidate may never also justify it (D28). "
            f"Known justifications: {sorted(_GENERATING_SIGNALS)}"
        )

    origins = _GENERATING_SIGNALS[record.mapping_justification]
    candidates = [
        _label_agreement(subject_labels, object_labels),
        _xref_assertion(record.subject_id, object_xref_codes),
        _sme_curation(record, curated_pairs),
        _corroboration(structurally_corroborated),
    ]
    return [e for e in candidates if e is not None and e.kind not in origins]


def _label_agreement(
    subject_labels: set[str], object_labels: set[str]
) -> Evidence | None:
    shared = {label.casefold() for label in subject_labels} & {
        label.casefold() for label in object_labels
    }
    if not shared:
        return None
    return Evidence(kind=LABEL_AGREEMENT, source="rdfs:label", detail=sorted(shared)[0])


def _xref_assertion(subject_id: str, object_xref_codes: set[str]) -> Evidence | None:
    if subject_id not in object_xref_codes:
        return None
    return Evidence(
        kind=XREF_ASSERTION,
        source="oboInOwl:hasDbXref",
        detail=f"{_NCIT_CODE_PREFIX}{subject_id}",
    )


def _sme_curation(
    record: SSSOMRecord, curated_pairs: frozenset[tuple[str, str]]
) -> Evidence | None:
    if (record.subject_id, record.object_id) not in curated_pairs:
        return None
    return Evidence(kind=SME_CURATION, source="curated-mapping-set")


def _corroboration(structurally_corroborated: bool) -> Evidence | None:
    if not structurally_corroborated:
        return None
    # Not "elk:": the verdict can turn on a stated part_of edge that never passes
    # through the reasoner (ELK supplies only the subClassOf leg). See D32.
    return Evidence(
        kind=STRUCTURAL_CORROBORATION, source="structural:anchored-ancestor"
    )


def is_independent(evidence: Sequence[Evidence]) -> bool:
    """Is *evidence* independent enough to justify a logical bridge (D28)?

    Human curation stands alone.  Otherwise at least two *distinct kinds* of signal
    are required — the same signal twice (two lexical hits, say) is one signal, not
    two, and D28 is explicit that a single annotation is never sufficient.
    """
    kinds = {e.kind for e in evidence}
    if SME_CURATION in kinds:
        return True
    return len(kinds) >= _MIN_INDEPENDENT_SIGNALS
