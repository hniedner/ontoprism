"""One source-bound Uberon/CL search publication for API and data build."""

from collections.abc import Awaitable, Callable
from typing import Literal

from backend.dependencies import RepositoryMetadataReader
from backend.repository_metadata import RepositoryUnhealthy, UberonClassCounts
from ontolib.core.exceptions import StorageError
from ontolib.terminologies.uberon.graph_store import UberonGraphStore
from ontolib.terminologies.uberon.search_index import (
    UberonSearchIndex,
    populate_from_store,
)
from ontolib.terminologies.uberon.store import (
    CertifiedUberonIndexObservation,
    UberonIndexObservation,
)


class UberonNotCertifiedError(StorageError):
    def __init__(self, repository: RepositoryUnhealthy[Literal["uberon"]]) -> None:
        self.repository = repository
        super().__init__(f"Uberon repository is not certified: {repository.reason}")


def same_observation(
    left: UberonIndexObservation,
    right: UberonIndexObservation | CertifiedUberonIndexObservation,
) -> bool:
    """Compare certified and live observation fields despite their model classes."""
    return left == UberonIndexObservation.model_validate(right.model_dump())


async def publish_uberon_search(
    store: UberonGraphStore,
    index: UberonSearchIndex,
    metadata: RepositoryMetadataReader,
    endpoint: str,
    *,
    observe: Callable[
        [str], Awaitable[tuple[UberonIndexObservation, UberonClassCounts]]
    ],
) -> int:
    repository = await metadata.uberon(force=True)
    if isinstance(repository, RepositoryUnhealthy):
        raise UberonNotCertifiedError(repository)
    before, counts = await observe(endpoint)
    if (
        not same_observation(before, repository.observation)
        or counts != repository.class_counts
    ):
        raise StorageError("Uberon/CL source changed before search publication")

    async def validate_source() -> None:
        after, after_counts = await observe(endpoint)
        if not same_observation(after, before) or after_counts != counts:
            raise StorageError("Uberon/CL source changed during search publication")

    return await populate_from_store(
        store,
        index,
        source_identity=repository.source_identity,
        source_hash=repository.observation.serving.sha256,
        validate_source=validate_source,
        expected_row_count=counts.uberon_searchable + counts.cl_searchable,
    )
