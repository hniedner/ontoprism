"""Non-persisted evidence assessment, separate from unchanged D37."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, get_args

from pydantic import Field

from ontolib.common.boundary_models import StrictFrozenBoundaryModel
from ontolib.decomposition.axis_diagnostics import AxisDiagnosticSource
from ontolib.decomposition.histology_anchor import HistologyAnchors
from ontolib.decomposition.models import AxisSource

Category = Literal[
    "retained-context",
    "source-qualified-anchor",
    "range-valid-endpoint",
    "detector-positive-disease-endpoint",
    "invalid-endpoint",
    "unknown",
]
GENUS_AXES = frozenset({"op:Morphology", "op:ToldGenus"})
DISEASE_AXES = frozenset(
    {"op:ClinicalFinding", "op:WithFinding", "op:AssociatedPriorDisease"}
)


class Endpoint(StrictFrozenBoundaryModel):
    concept_code: str = Field(pattern=r"^C[0-9]+$")
    axis: str = Field(pattern=r"^(op:[A-Za-z][A-Za-z0-9]*|R[0-9]+)$")
    filler_code: str = Field(pattern=r"^(C[0-9]+|MINT-[0-9a-f]{12})$")
    axis_source: AxisSource
    classification: Literal["atomic", "precoordinated", "unknown"] | None
    needs_review: bool = False
    axis_ambiguous: bool = False


@dataclass(frozen=True)
class EndpointAssessment:
    category: Category
    reason: str


def assess_endpoint(
    item: Endpoint, source: AxisDiagnosticSource, anchors: HistologyAnchors
) -> EndpointAssessment:
    if item.axis in GENUS_AXES:
        return EndpointAssessment(
            "retained-context", "told-genus-not-an-atomicity-target"
        )
    if item.filler_code.startswith("MINT-"):
        return EndpointAssessment("unknown", "proposed-filler")
    if item.axis == "op:HistologyAnchor":
        return _anchor_assessment(item, anchors)
    verdict = source.classify(axis=item.axis, filler_code=item.filler_code)
    if verdict.status != "valid":
        category = "invalid-endpoint" if verdict.status == "invalid" else "unknown"
        return EndpointAssessment(category, verdict.reason)
    return _range_valid_assessment(item)


def _anchor_assessment(item: Endpoint, anchors: HistologyAnchors) -> EndpointAssessment:
    if item.axis_source != "p334":
        return EndpointAssessment("unknown", "anchor-provenance-not-p334")
    if item.filler_code not in anchors.for_concept(item.concept_code):
        return EndpointAssessment("unknown", "anchor-not-corroborated")
    return EndpointAssessment(
        "source-qualified-anchor", "p334-told-minimum-not-atomhood"
    )


def _range_valid_assessment(item: Endpoint) -> EndpointAssessment:
    if item.axis in DISEASE_AXES:
        if item.classification == "precoordinated":
            return EndpointAssessment(
                "detector-positive-disease-endpoint", "stored-disease-detector-positive"
            )
        if item.classification != "atomic":
            return EndpointAssessment("unknown", "disease-detector-unavailable")
    return EndpointAssessment(
        "range-valid-endpoint", "range-membership-not-terminality"
    )


def assessment_counts(
    rows: Sequence[Endpoint],
    decomposed: set[str],
    source: AxisDiagnosticSource,
    anchors: HistologyAnchors,
) -> Counter[str]:
    counts = Counter({f"endpoints.category.{c}": 0 for c in get_args(Category)})
    counts["concepts.decomposed"] = len(decomposed)
    noncontext: set[str] = set()
    emitted: Counter[str] = Counter()
    for item in rows:
        result = assess_endpoint(item, source, anchors)
        counts[f"endpoints.category.{result.category}"] += 1
        counts[
            f"axis.{item.axis}.category.{result.category}.reason.{result.reason}"
        ] += 1
        if result.category != "retained-context":
            noncontext.add(item.concept_code)
        if item.axis == "op:HistologyAnchor":
            emitted[item.concept_code] += 1
        _review_counts(item, counts)
    counts["concepts.context-only"] = len(decomposed - noncontext)
    counts.update(_anchor_counts(decomposed, emitted, anchors))
    return counts


def _review_counts(item: Endpoint, counts: Counter[str]) -> None:
    if item.needs_review:
        counts[f"review.needs-review.axis.{item.axis}"] += 1
    if item.axis_ambiguous:
        counts[f"review.axis-ambiguous.axis.{item.axis}"] += 1


def _anchor_counts(
    decomposed: set[str],
    emitted: Counter[str],
    anchors: HistologyAnchors,
) -> Counter[str]:
    counts: Counter[str] = Counter()
    for code in decomposed:
        candidates = anchors.for_concept(code)
        counts["anchors.no-eligible-source-anchor.concepts"] += not candidates
        counts["anchors.incomparable-source-anchors.concepts"] += len(candidates) > 1
        counts["anchors.no-emitted-anchor.concepts"] += emitted[code] == 0
        counts["anchors.multiple-emitted-anchors.concepts"] += emitted[code] > 1
    return counts
