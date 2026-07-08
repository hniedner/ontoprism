"""NLP-fallback label parser for axes not modelled as roles (design §7.1).

Recovers laterality, with/without <finding>, and staging-manual version from the
concept's preferred label or synonyms.  Rule-based (not a model) so output is
deterministic, diffable, fully unit-testable, and does not require a store.

Each rule emits `AspectRecord(axis, surface_form, polarity)` — the caller maps
`surface_form` to an existing NCIt concept code or passes it to the minter (§7.2).
"""

import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class AspectRecord:
    """One axis recovered from a label scan (NLP fallback).

    ``axis`` uses an ``op:`` prefix for ontoprism-defined axes.  ``polarity`` is
    ``"positive"`` for "with <finding>" and ``"negative"`` for "without <finding>"".
    Other axes default to ``"positive"``.
    """

    axis: str
    surface_form: str
    polarity: Literal["positive", "negative"] = "positive"


# Laterality markers — must be the leading word in the label so "Left Lung ..." works
# but a mid-word "left" doesn't trigger false positives.
_LATERALITY = frozenset({"Left", "Right", "Bilateral"})

# Stage-system markers captured from NCIt labels (e.g. "AJCC v7", "UICC").
_STAGE_RE = re.compile(r"\b(AJCC\s*v\d+|UICC)\b")

# "with <Finding>" and "without <Finding>" — the finding text is capitalised.
_FINDING_RE = re.compile(r"(?:with|without)\s+([A-Z][a-zA-Z]+(?:\s+[a-zA-Z]+)?)")


def parse_label_aspects(label: str | None) -> list[AspectRecord]:
    """Scan a concept label for NLP-recoverable axes.

    Returns empty list on None or empty label.  Multiple aspects may be emitted
    (laterality + finding + stage-system are all independent).
    """
    if not label:
        return []

    aspects: list[AspectRecord] = []

    # --- laterality --------------------------------------------------------
    first_word = label.split()[0] if label.split() else ""
    if first_word in _LATERALITY:
        aspects.append(AspectRecord(axis="op:Laterality", surface_form=first_word))

    # --- with/without <finding> --------------------------------------------
    for m in _FINDING_RE.finditer(label):
        finding = m.group(1)
        preamble = m.group(0)[: m.start(1) - m.start()].strip().lower()
        polarity: Literal["positive", "negative"] = (
            "negative" if preamble == "without" else "positive"
        )
        aspects.append(
            AspectRecord(axis="op:WithFinding", surface_form=finding, polarity=polarity)
        )

    # --- staging-manual version --------------------------------------------
    for m in _STAGE_RE.finditer(label):
        aspects.append(AspectRecord(axis="op:StageSystem", surface_form=m.group(1)))

    return aspects
