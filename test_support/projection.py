from __future__ import annotations

from ontolib.decomposition.axis_diagnostics import (
    AxisDiagnosticSource,
    AxisHierarchyEvidence,
)


def unknown_axis_diagnostic_source(source_identity: str) -> AxisDiagnosticSource:
    """Return source-bound empty evidence so tests exercise typed unknown retention."""
    return AxisDiagnosticSource(
        AxisHierarchyEvidence(
            source_identity=source_identity,
            edges=(),
            disjoint_pairs=(),
        )
    )
