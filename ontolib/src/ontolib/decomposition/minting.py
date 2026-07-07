"""Deterministic minting of missing qualifier concepts (design §7.2).

When an NLP-fallback axis has no existing NCIt concept — e.g. a laterality value not
modelled as a class or a negation ("without Pleural Effusion") — emit a **proposal**
with a stable synthetic ID: `MINT-{sha1(axis|label)[:12]}`.  The same input always
yields the same ID (idempotent reruns, diffable).

A `MintedConcept` is never emitted silently to the graph; it lands in
`minted_concept` with ``status="proposed"`` for curator review.  Approval is
a governance step outside the engine.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class MintedConcept:
    """A proposal for a missing qualifier concept.

     ``id`` is computed from (axis, label) so reruns are deterministic and diffable.
     ``source_signal`` records the label span or rule that produced this proposal.
     Default status is ``"proposed"`` — requires curator approval before entering
    the decomposed graph.
    """

    axis: str
    label: str
    source_signal: str = ""
    status: Literal["proposed", "approved", "rejected"] = "proposed"
    id: str = field(default="", init=False, repr=False)

    def __post_init__(self) -> None:
        normalized = normalize_label(self.label)
        h = hashlib.sha1(f"{self.axis}|{normalized}".encode()).hexdigest()[:12]  # noqa: S324
        object.__setattr__(self, "id", f"MINT-{h}")


def normalize_label(label: str) -> str:
    """Canonical label for minting (lowercased, stripped)."""
    return label.strip().lower()
