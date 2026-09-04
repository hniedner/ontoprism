"""Closed inventory of served ICD-O edition/axis datasets."""

from enum import Enum
from typing import Literal

type IcdoEdition = Literal["3.2", "4.0"]
type IcdoAxis = Literal["morphology", "topography"]


class ServedIcdoDataset(Enum):
    ICDO_32_MORPHOLOGY = ("3.2", "morphology")
    ICDO_40_MORPHOLOGY = ("4.0", "morphology")
    ICDO_40_TOPOGRAPHY = ("4.0", "topography")

    @property
    def edition(self) -> IcdoEdition:
        return self.value[0]

    @property
    def axis(self) -> IcdoAxis:
        return self.value[1]

    @classmethod
    def parse(cls, edition: str, axis: str) -> ServedIcdoDataset | None:
        return next(
            (
                dataset
                for dataset in cls
                if dataset.edition == edition and dataset.axis == axis
            ),
            None,
        )
