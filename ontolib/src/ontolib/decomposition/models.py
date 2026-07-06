"""Pure data models for the NCIt decomposition engine (Issue #4 / M5).

No FastAPI or DB coupling — these are the deterministic value objects the detector,
filler-selection, and (later) writer/provenance layers exchange. See
``docs/design/ncit-decomposition-engine.md`` §4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# How an axis/constituent was recovered — the ``op:axisSource`` provenance value.
AxisSource = Literal["role", "nlp", "parent"]


@dataclass(frozen=True, slots=True)
class RoleRestriction:
    """One OWL ``someValuesFrom`` role restriction read from the stated graph.

    ``role_code`` is the NCIt property code (e.g. ``R105``); ``role_label`` is its
    human-readable name (e.g. ``Disease_Has_Abnormal_Cell``) when resolvable — the
    label is what the ``Excludes_*`` / defining classification keys on.
    """

    role_code: str
    filler_code: str
    role_label: str | None = None


@dataclass(frozen=True, slots=True)
class Constituent:
    """A single decomposed constituent: an axis and the concept that fills it.

    ``axis`` is the NCIt role code (reused as the axis identifier) or an ``op:`` axis
    such as ``op:Morphology``. ``most_specific`` records that the filler was chosen as
    the hierarchy leaf over its ancestors; ``needs_review`` flags an ambiguous pick for
    curation rather than silently resolving it.
    """

    axis: str
    filler_code: str
    axis_source: AxisSource
    most_specific: bool = False
    needs_review: bool = False


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """The detector's verdict for one concept."""

    code: str
    is_precoordinated: bool
    defining_role_count: int
    semantic_type: str | None
    label_multi_aspect: bool = False


@dataclass(frozen=True, slots=True)
class Decomposition:
    """A decomposed concept: its source code and its constituents (roles-first)."""

    code: str
    semantic_type: str | None
    constituents: list[Constituent] = field(default_factory=list)

    @property
    def axes(self) -> set[str]:
        """The distinct axes covered by this decomposition."""
        return {c.axis for c in self.constituents}
