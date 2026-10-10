"""NCIt P334 histology anchors over the shared, validated told hierarchy."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from ontolib.decomposition.axis_diagnostics import AxisHierarchyEvidence
from ontolib.decomposition.models import Constituent
from ontolib.decomposition.scope import ScopeSelectClient
from ontolib.decomposition.source_support import ConstituentEvidence, P334Evidence
from ontolib.repositories.xref.p334_alignment import build_p334_assertions_query
from ontolib.terminologies.namespaces import NCIT_NS


@dataclass(frozen=True, slots=True)
class HistologyAnchors:
    """All raw P334 values retained; only /0-/3 qualify their NCIt carriers."""

    source: AxisHierarchyEvidence
    values: Mapping[str, tuple[str, ...]]
    carriers: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))
        object.__setattr__(
            self,
            "carriers",
            frozenset(
                code
                for code, codes in self.values.items()
                if any(re.fullmatch(r"[0-9]{4}/[0-3]", value) for value in codes)
            ),
        )

    def for_concept(self, code: str) -> tuple[str, ...]:
        candidates = (set(self.source.ancestor_paths(code)) | {code}) & self.carriers
        return tuple(
            sorted(
                candidate
                for candidate in candidates
                if not any(
                    candidate in self.source.ancestor_paths(other)
                    for other in candidates
                    if other != candidate
                )
            )
        )

    def constituents(self, code: str) -> tuple[Constituent, ...]:
        """Emit all minima; multiplicity is unresolved rather than tie-broken."""
        anchors = self.for_concept(code)
        return tuple(
            Constituent(
                axis="op:HistologyAnchor",
                filler_code=anchor,
                axis_source="p334",
                needs_review=len(anchors) > 1,
                axis_ambiguous=len(anchors) > 1,
            )
            for anchor in anchors
        )

    def support(self, evidence: ConstituentEvidence) -> ConstituentEvidence:
        """Fail closed when a stored anchor cannot be corroborated by this source."""
        code, filler = evidence.concept_code, evidence.filler_code
        if filler not in self.for_concept(code):
            raise ValueError("stored histology anchor is not a current P334 minimum")
        values = self.values[filler]
        eligible = tuple(v for v in values if re.fullmatch(r"[0-9]{4}/[0-3]", v))
        annotation = P334Evidence(
            eligible_values=eligible,
            other_values=tuple(v for v in values if v not in eligible),
            path=(
                (code,) if code == filler else self.source.ancestor_paths(code)[filler]
            ),
        )
        payload = evidence.model_dump(exclude_computed_fields=True)
        payload["p334"] = annotation
        return ConstituentEvidence.model_validate(payload)


async def read_p334_values(client: ScopeSelectClient) -> Mapping[str, tuple[str, ...]]:
    """Read every assertion, including malformed/other-behavior values for evidence."""
    rows = await client.select_once(
        build_p334_assertions_query(), required_variables={"concept", "value"}
    )
    values: dict[str, set[str]] = {}
    for row in rows:
        iri, value = row["concept"], row["value"]
        if (
            iri is None
            or not iri.startswith(NCIT_NS)
            or not re.fullmatch(r"C[0-9]+", iri.removeprefix(NCIT_NS))
        ):
            raise ValueError("P334 carrier is not a named NCIt concept")
        if not value:
            raise ValueError("P334 asserted value is missing")
        values.setdefault(iri.removeprefix(NCIT_NS), set()).add(value)
    return _require_inventory(values)


def _require_inventory(values: dict[str, set[str]]) -> Mapping[str, tuple[str, ...]]:
    if not values:
        raise ValueError("P334 source inventory is empty; cannot assess anchor absence")
    return MappingProxyType(
        {code: tuple(sorted(items)) for code, items in values.items()}
    )
