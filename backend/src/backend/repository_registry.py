"""Validation for the tracked language-neutral repository registry."""

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)


class CapabilityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class GridFilter(CapabilityModel):
    kind: Literal["text", "categorical"]
    text_parameter: str = Field(pattern=r"^[a-z_]+$")
    values: dict[str, str]
    multiple: bool = False
    source_domain: str | None = None

    @model_validator(mode="after")
    def valid_domain(self):
        if (self.kind == "categorical") != bool(self.values or self.source_domain):
            raise ValueError("only categorical filters require a value domain")
        if any(not key or not label for key, label in self.values.items()):
            raise ValueError("filter values and labels must be nonempty")
        return self


class GridCapabilities(CapabilityModel):
    sorts: dict[Literal["list", "search"], list[str]]
    filters: dict[str, GridFilter]
    pagination: Literal["offset", "cursor"]
    query_before_results: bool
    metadata: Literal["certified", "remote"]
    graph: Literal["ontology", "source-anchors", "none"]
    links: Literal["mapping", "source-anchors", "references", "none"]

    @field_validator("sorts")
    @classmethod
    def valid_sorts(cls, sorts):
        if set(sorts) != {"list", "search"}:
            raise ValueError("list/search sorts required")
        for values in sorts.values():
            if not values or "" in values or len(values) != len(set(values)):
                raise ValueError("sorts must be nonempty and unique")
        return sorts

    @model_validator(mode="after")
    def valid_controls(self):
        parameters = [f.text_parameter for f in self.filters.values()]
        if len(set(parameters)) != len(parameters) or any(not k for k in self.filters):
            raise ValueError("filter names and text parameters must be distinct")
        return self


class _RepositoryDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    label: str
    path: str
    capabilities: GridCapabilities | None = None

    @model_validator(mode="after")
    def consistent_metadata(self):
        if self.capabilities and (
            (self.capabilities.metadata == "certified")
            != isinstance(self, LocalRepositoryDescriptor)
        ):
            raise ValueError("metadata capability contradicts repository kind")
        return self


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
