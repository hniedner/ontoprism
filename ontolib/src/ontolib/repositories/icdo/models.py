"""Strict source and serving models for ICD-O editions and axes."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, computed_field, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MorphologyCode32(_StrictModel):
    value: str

    def model_post_init(self, __context: object) -> None:
        if re.fullmatch(r"[0-9]{4}/[0-9]", self.value) is None:
            raise ValueError("ICD-O-3.2 morphology code must have form dddd/b")

    @computed_field
    @property
    def base(self) -> str:
        return self.value[:4]

    @computed_field
    @property
    def behaviour(self) -> str:
        return self.value[-1]


class MorphologyCode40(_StrictModel):
    value: str

    def model_post_init(self, __context: object) -> None:
        if re.fullmatch(r"[0-9]{4}[0-9A-Z]/[0-9]", self.value) is None:
            raise ValueError("ICD-O-4 morphology code must have form ddddx/b")

    @computed_field
    @property
    def base(self) -> str:
        return self.value[:4]

    @computed_field
    @property
    def specificity(self) -> str:
        return self.value[4]

    @computed_field
    @property
    def behaviour(self) -> str:
        return self.value[-1]


class TopographyCode40(_StrictModel):
    value: str

    def model_post_init(self, __context: object) -> None:
        if re.fullmatch(r"C[0-9]{2}(?:\.[0-9])?", self.value) is None:
            raise ValueError("ICD-O-4 topography code must have form Cdd or Cdd.d")

    @computed_field
    @property
    def level(self) -> Literal["category", "leaf"]:
        return "leaf" if "." in self.value else "category"

    @computed_field
    @property
    def parent(self) -> str | None:
        return self.value[:3] if self.level == "leaf" else None


class SourceShape(_StrictModel):
    sheet_names: tuple[str, ...]
    headers: tuple[str, ...]
    merged_ranges: tuple[str, ...]
    trailing_blank_rows: int


class IcdoRecord(_StrictModel):
    code: str
    level: Literal["morphology", "category", "leaf"]
    parent_code: str | None = None
    base_morphology: str | None = None
    specificity: str | None = None
    behaviour: str | None = None
    preferred: str | None = None
    synonyms: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    code_references: tuple[str, ...] = ()
    see_also: tuple[str, ...] = ()
    see_notes: tuple[str, ...] = ()
    includes: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()
    other_text: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_shape(self) -> IcdoRecord:
        if self.level == "morphology":
            if self.parent_code is not None or not re.fullmatch(
                r"[0-9]{4}(?:[0-9A-Z])?/[0-9]", self.code
            ):
                raise ValueError("morphology record has incompatible fields")
        elif not re.fullmatch(r"C[0-9]{2}(?:\.[0-9])?", self.code):
            raise ValueError("topography record has incompatible code")
        return self


class CanonicalDataset(_StrictModel):
    edition: Literal["3.2", "4.0"]
    axis: Literal["morphology", "topography"]
    records: tuple[IcdoRecord, ...]
    source_shape: SourceShape
    source_sha256: str
    archive_sha256: str | None = None
    annex_sha256: str | None = None

    @model_validator(mode="after")
    def validate_edition_axis(self) -> CanonicalDataset:
        if (self.edition, self.axis) == ("3.2", "topography"):
            raise ValueError("ICD-O-3.2 topography is not served")
        expected_level = "morphology" if self.axis == "morphology" else None
        if any(
            (row.level == "morphology") != (expected_level == "morphology")
            for row in self.records
        ):
            raise ValueError("record level does not match dataset axis")
        return self

    @computed_field
    @property
    def term_counts(self) -> dict[str, int]:
        counts = {
            "preferred": sum(
                row.preferred is not None and row.level != "category"
                for row in self.records
            ),
            "synonym": sum(len(row.synonyms) for row in self.records),
            "related": sum(len(row.related) for row in self.records),
        }
        if self.axis == "topography":
            counts["category"] = sum(row.level == "category" for row in self.records)
        return counts

    @computed_field
    @property
    def level_counts(self) -> dict[str, int]:
        return {
            level: sum(row.level == level for row in self.records)
            for level in ("category", "leaf")
            if self.axis == "topography"
        }


class Icdo4Datasets(_StrictModel):
    morphology: CanonicalDataset
    topography: CanonicalDataset

    @model_validator(mode="after")
    def validate_axes(self) -> Icdo4Datasets:
        if (self.morphology.edition, self.morphology.axis) != ("4.0", "morphology"):
            raise ValueError("morphology must be ICD-O-4 morphology")
        if (self.topography.edition, self.topography.axis) != ("4.0", "topography"):
            raise ValueError("topography must be ICD-O-4 topography")
        return self
