"""Presentation helpers for mappings stored in subject-to-object direction."""

from ontolib.repositories.xref.models import EndpointIdentity, MappingResult
from ontolib.repositories.xref.vocab import (
    BROAD_MATCH,
    NARROW_MATCH,
    MappingPredicate,
)

_INVERSE_PREDICATE: dict[MappingPredicate, MappingPredicate] = {
    BROAD_MATCH: NARROW_MATCH,
    NARROW_MATCH: BROAD_MATCH,
}


def mapping_relative_to(
    row: MappingResult, requested_identifier: str
) -> tuple[EndpointIdentity, MappingPredicate]:
    """Return the target and predicate from the requested endpoint's perspective."""
    if row.subject.identifier == requested_identifier:
        return row.object, row.predicate
    if row.object.identifier == requested_identifier:
        return row.subject, _INVERSE_PREDICATE.get(row.predicate, row.predicate)
    raise ValueError(
        f"Mapping row does not contain requested identifier: {requested_identifier}"
    )
