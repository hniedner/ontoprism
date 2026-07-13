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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ontolib.repositories.xref.vocab import ALLOWED_PREDICATES

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ontolib.repositories.xref.models import SSSOMRecord

# ── evidence kinds ─────────────────────────────────────────────────────

# NCIt label agrees with an upstream label (case-folded).
LABEL_AGREEMENT = "label_agreement"
# The upstream project independently asserts an `NCI:<code>` xref on the object.
XREF_ASSERTION = "xref_assertion"
# The upstream object sits under the upstream image of an NCIt ancestor of the
# subject, established through a *separately validated* anchor bridge.
STRUCTURAL_CORROBORATION = "structural_corroboration"
# The pair is in the curated (SME-signed) mapping set.
SME_CURATION = "sme_curation"

EVIDENCE_KINDS = frozenset(
    {LABEL_AGREEMENT, XREF_ASSERTION, STRUCTURAL_CORROBORATION, SME_CURATION}
)

# The signal each mapping_justification is derived from — that signal is the
# candidate's *origin*, so it can never also be its evidence.
_GENERATING_SIGNAL: dict[str, str] = {
    "semapv:LexicalMatching": LABEL_AGREEMENT,
    "semapv:DatabaseCrossReference": XREF_ASSERTION,
}

# Absent human curation, a bridge needs corroboration from two *different* signals.
_MIN_INDEPENDENT_SIGNALS = 2


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
        if self.source in ALLOWED_PREDICATES or self.detail in ALLOWED_PREDICATES:
            raise ValueError(
                "a SKOS mapping annotation may never serve as evidence for the "
                f"bridge it annotates (D28): {self.source or self.detail}"
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
    pair set, and whether the reasoner corroborated the pair through a separate
    anchor (computed over a merge that excludes this candidate's bridge).
    """
    origin = _GENERATING_SIGNAL.get(record.mapping_justification)
    candidates = [
        _label_agreement(subject_labels, object_labels),
        _xref_assertion(record.subject_id, object_xref_codes),
        _sme_curation(record, curated_pairs),
        _corroboration(structurally_corroborated),
    ]
    return [e for e in candidates if e is not None and e.kind != origin]


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
        detail=f"NCI:{subject_id}",
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
    return Evidence(kind=STRUCTURAL_CORROBORATION, source="elk:anchored-ancestor")


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
