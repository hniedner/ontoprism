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


def load_repository_registry(path: Path) -> list[RepositoryDescriptor]:
    """Read and strictly validate one repository manifest."""
    return _REGISTRY.validate_python(json.loads(path.read_bytes()))
