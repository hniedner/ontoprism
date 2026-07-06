"""Detector — is a concept pre-coordinated? (design §5, resolved decision §14.1).

A concept is a decomposition candidate when its semantic type is in scope **and** it
carries at least ``min_decomposable_axes`` decomposable axes. Decomposable axes are
counted as: distinct *defining* role restrictions (``Excludes_*`` negative axioms do
not count) + a morphology-from-parent axis when present + one label-signalled axis when
the label fuses multiple aspects. This axis framing (not a raw role count) is the gate,
so a single-role concept with a morphology-bearing parent still qualifies while a truly
single-axis atomic concept does not.
"""

from __future__ import annotations

from ontolib.decomposition.axes import is_defining_role, is_in_scope
from ontolib.decomposition.models import DetectionResult, RoleRestriction

_DEFAULT_MIN_AXES = 2

# Surface markers that a label fuses several semantic aspects (assessment §3.1): joined
# phrases, staging/grading, and parenthetical qualifiers.
_LABEL_MARKERS = (" with ", " without ", " of the ", "stage", "grade")


def label_multi_aspect(label: str | None) -> bool:
    """True if the *label* lexically fuses multiple aspects (advisory axis signal)."""
    if not label:
        return False
    if "(" in label or ")" in label:
        return True
    lowered = label.lower()
    return any(marker in lowered for marker in _LABEL_MARKERS)


def detect(
    code: str,
    semantic_type: str | None,
    roles: list[RoleRestriction],
    *,
    has_parent_morphology: bool = False,
    label: str | None = None,
    min_decomposable_axes: int = _DEFAULT_MIN_AXES,
) -> DetectionResult:
    """Classify one concept. Pure — all inputs are supplied by the caller.

    ``roles`` may include ``Excludes_*`` negative axioms; they are filtered here so the
    caller can pass a concept's raw stated roles unmodified.
    """
    defining_axes = {r.role_code for r in roles if is_defining_role(r)}
    multi_aspect = label_multi_aspect(label)

    decomposable_axes = len(defining_axes)
    if has_parent_morphology:
        decomposable_axes += 1
    if multi_aspect:
        decomposable_axes += 1

    is_precoordinated = (
        is_in_scope(semantic_type) and decomposable_axes >= min_decomposable_axes
    )
    return DetectionResult(
        code=code,
        is_precoordinated=is_precoordinated,
        defining_role_count=len(defining_axes),
        semantic_type=semantic_type,
        label_multi_aspect=multi_aspect,
    )
