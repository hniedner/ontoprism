from __future__ import annotations

import pytest

from ontolib.repositories.xref.models import EndpointIdentity, SSSOMRecord
from ontolib.repositories.xref.vocab import CLOSE_MATCH


@pytest.mark.unit
def test_mapping_requires_complete_typed_endpoint_identity() -> None:
    subject = EndpointIdentity(system="ncit", version="26.07d", identifier="C3262")
    obj = EndpointIdentity(
        system="icdo",
        version="3.2-morphology",
        identifier="8240/3",
    )

    record = SSSOMRecord(
        subject_id=subject.identifier,
        subject_system=subject.system,
        predicate_id=CLOSE_MATCH,
        object_id=obj.identifier,
        object_system=obj.system,
        mapping_justification="https://ontoprism.org/vocab#PublisherDatabaseCrossReference",
        confidence=0.9,
        subject_source_version=subject.version,
        object_source_version=obj.version,
    )

    assert record.subject == subject
    assert record.object == obj
    assert record.subject_id == "C3262"
    assert record.subject_source_version == "26.07d"
    assert record.object_id == "8240/3"
    assert record.object_source_version == "3.2-morphology"


@pytest.mark.unit
@pytest.mark.parametrize("field", ["system", "version", "identifier"])
def test_endpoint_identity_rejects_missing_parts(field: str) -> None:
    values = {"system": "ncit", "version": "26.07d", "identifier": "C1"}
    values[field] = ""
    with pytest.raises(ValueError, match=field):
        EndpointIdentity(**values)
