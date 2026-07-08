"""Resolve NLP-fallback aspects to existing concepts, or mint a proposal (design §7.2).

The NLP fallback (:mod:`ontolib.decomposition.nlp_fallback`) recovers an axis and a
*surface form* from a concept's label — not a concept code. This module is the
resolution step design §3 calls ``constituent_index``: for each recovered aspect, look
up whether an existing NCIt concept's label matches; if so the aspect resolves to that
concept, otherwise it is minted as a proposal (never a silent create — design §7.2).

Negation is never resolved against an existing concept: "without Pleural Effusion" does
not mean the *Pleural Effusion* concept, it means its *absence*, so a negative-polarity
aspect always mints the full negated phrase as the label (matching the design's own
``"Without Pleural Effusion"`` worked example in §4.5 — the general negation-minting
rule is §7.2). Positive aspects resolve normally and only mint when no matching concept
exists.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from ontolib.decomposition.minting import MintedConcept
from ontolib.decomposition.models import Constituent

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ontolib.decomposition.nlp_fallback import AspectRecord

# Resolve a surface form to an existing concept's code, or None if no match.
LabelLookup = Callable[[str], Awaitable["str | None"]]


def _mint_label(aspect: AspectRecord) -> str:
    """The label to mint for *aspect* — negated phrasing for negative polarity."""
    if aspect.polarity == "negative":
        return f"Without {aspect.surface_form}"
    return aspect.surface_form


async def resolve_aspects(
    aspects: Sequence[AspectRecord],
    label_lookup: LabelLookup,
) -> tuple[list[Constituent], list[MintedConcept]]:
    """Resolve each aspect to an existing concept or a minted proposal.

    Every aspect yields exactly one :class:`Constituent` (``axis_source="nlp"``),
    whose ``filler_code`` is either an existing concept code or a freshly minted
    proposal's id. Negative-polarity aspects always mint (never resolved) — see the
    module docstring.
    """
    constituents: list[Constituent] = []
    minted: list[MintedConcept] = []

    for aspect in aspects:
        code = (
            None
            if aspect.polarity == "negative"
            else await label_lookup(aspect.surface_form)
        )
        if code is not None:
            constituents.append(
                Constituent(axis=aspect.axis, filler_code=code, axis_source="nlp")
            )
            continue

        proposal = MintedConcept(
            axis=aspect.axis,
            label=_mint_label(aspect),
            source_signal=aspect.surface_form,
        )
        minted.append(proposal)
        constituents.append(
            Constituent(axis=aspect.axis, filler_code=proposal.id, axis_source="nlp")
        )

    return constituents, minted
