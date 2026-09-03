"""Shared strict roots for serialized and externally validated documents."""

from pydantic import BaseModel, ConfigDict


class StrictBoundaryModel(BaseModel):
    """Boundary document that forbids coercion and unknown fields."""

    model_config = ConfigDict(strict=True, extra="forbid")


class StrictFrozenBoundaryModel(StrictBoundaryModel):
    """Immutable boundary document."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
