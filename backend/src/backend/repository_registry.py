"""Validation for the tracked language-neutral repository registry."""

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class _RepositoryDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    label: str
    path: str


class LocalRepositoryDescriptor(_RepositoryDescriptor):
    id: Literal["ncit", "cadsr", "uberon", "icdo"]
    kind: Literal["local-certified-proxy"]


class RemoteRepositoryDescriptor(_RepositoryDescriptor):
    id: Literal["clinicaltrials", "pubmed"]
    kind: Literal["remote-live-service"]


RepositoryDescriptor = Annotated[
    LocalRepositoryDescriptor | RemoteRepositoryDescriptor,
    Field(discriminator="kind"),
]
_REGISTRY = TypeAdapter(list[RepositoryDescriptor])
REPOSITORY_MANIFEST_PATH = Path(__file__).parents[3] / "repository-manifest.json"


def load_repository_registry(path: Path) -> list[RepositoryDescriptor]:
    """Read and strictly validate one repository manifest."""
    return _REGISTRY.validate_python(json.loads(path.read_bytes()))


def local_repository_ids() -> tuple[Literal["ncit", "cadsr", "uberon", "icdo"], ...]:
    """Return the local-certified repository order declared by the tracked manifest."""
    return tuple(
        entry.id
        for entry in load_repository_registry(REPOSITORY_MANIFEST_PATH)
        if isinstance(entry, LocalRepositoryDescriptor)
    )
