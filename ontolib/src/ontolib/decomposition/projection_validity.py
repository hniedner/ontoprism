"""Immutable axis-range assessments; invalid range alone rejects a projection.

Unknown ranges are retained. Atomicity is not a projection-selection criterion;
residual precoordination is measured separately over emitted constituents.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from ontolib.decomposition.axis_diagnostics import AxisRangeEvidence


def freeze_projection_assessments(
    assessments: Iterable[AxisRangeEvidence],
) -> Mapping[tuple[str, str], AxisRangeEvidence]:
    """Build a duplicate-rejecting immutable range assessment map."""
    result: dict[tuple[str, str], AxisRangeEvidence] = {}
    for assessment in assessments:
        key = assessment.axis, assessment.filler_code
        if key in result:
            raise ValueError(f"duplicate projection assessment: {key!r}")
        result[key] = assessment
    return MappingProxyType(result)
