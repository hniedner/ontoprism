"""Test-only setup for PostgreSQL xref generation fixtures."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ontolib.repositories.xref.models import (
    GenerationSourceMetadata,
    UberonCandidateGenerationMetadata,
)
from ontolib.repositories.xref.publication import (
    generation_graph_iri,
    generation_identity,
)

_SOURCE_METADATA = UberonCandidateGenerationMetadata(
    ncit_source_identity="a" * 64,
    uberon_source_identity="b" * 64,
    uberon_serving_identity="c" * 64,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ontolib.repositories.xref.models import SSSOMRecord
    from ontolib.repositories.xref.store import XrefStore


async def activate_records(
    store: XrefStore,
    *,
    source: str,
    run_id: str,
    records: Sequence[SSSOMRecord],
    source_metadata: GenerationSourceMetadata = _SOURCE_METADATA,
) -> bool:
    """Prepare and activate records for tests that have no RDF collaborator."""
    generation_id, content_sha256 = generation_identity(
        source, records, source_metadata
    )
    changed = await store.prepare_generation(
        source=source,
        generation_id=generation_id,
        content_sha256=content_sha256,
        source_metadata=source_metadata,
        graph_iri=generation_graph_iri(source, generation_id),
        run_id=run_id,
        records=records,
    )
    await store.activate_generation(source, generation_id)
    return changed
