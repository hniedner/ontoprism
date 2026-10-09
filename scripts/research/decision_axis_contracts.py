"""Contracts in the coordinates of retained human decision records.

#467 changes the name, not the genus members or modality. This catalogue is for
reading those records; the engine/API catalogue emits only ToldGenus.
"""

from dataclasses import replace
from types import MappingProxyType

from ontolib.decomposition.axis_contracts import AXIS_CONTRACTS
from ontolib.decomposition.axis_diagnostics import (
    AxisDiagnosticSource,
    AxisRangeEvidence,
)

DECISION_AXIS_CONTRACTS = MappingProxyType(
    {
        **AXIS_CONTRACTS,
        "op:Morphology": AXIS_CONTRACTS["op:ToldGenus"].model_copy(
            update={"axis": "op:Morphology"}
        ),
    }
)


def classify_decision_axis(
    source: AxisDiagnosticSource, axis: str, filler: str
) -> AxisRangeEvidence:
    current = "op:ToldGenus" if axis == "op:Morphology" else axis
    return replace(source.classify(axis=current, filler_code=filler), axis=axis)
