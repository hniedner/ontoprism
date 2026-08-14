import pytest
from pydantic import ValidationError

from ontolib.repositories.icdo.models import (
    CanonicalDataset,
    IcdoRecord,
    MorphologyCode32,
    MorphologyCode40,
    SourceShape,
    TopographyCode40,
)


def test_dataset_refuses_invalid_edition_axis_combination() -> None:
    with pytest.raises(ValueError, match=r"3\.2 topography"):
        CanonicalDataset(
            edition="3.2",
            axis="topography",
            records=(),
            source_shape=SourceShape(
                sheet_names=(), headers=(), merged_ranges=(), trailing_blank_rows=0
            ),
            source_sha256="a" * 64,
        )


def test_record_refuses_cross_axis_fields_but_accepts_publisher_85032_0() -> None:
    with pytest.raises(ValueError, match="morphology record"):
        IcdoRecord(code="C34.9", level="morphology", parent_code="C34")
    record = IcdoRecord(
        code="85032/0",
        level="morphology",
        base_morphology="8503",
        specificity="2",
        behaviour="0",
    )
    assert record.code == "85032/0"


pytestmark = pytest.mark.unit


def test_edition_specific_code_variants_preserve_structure() -> None:
    old = MorphologyCode32(value="8503/0")
    new = MorphologyCode40(value="85032/0")
    category = TopographyCode40(value="C80")
    leaf = TopographyCode40(value="C80.9")

    assert (old.base, old.behaviour) == ("8503", "0")
    assert (new.base, new.specificity, new.behaviour) == ("8503", "2", "0")
    assert (category.level, category.parent) == ("category", None)
    assert (leaf.level, leaf.parent) == ("leaf", "C80")


@pytest.mark.parametrize(
    ("model", "value"),
    [
        (MorphologyCode32, "85032/0"),
        (MorphologyCode40, "8503/0"),
        (MorphologyCode40, "8503_/0"),
        (TopographyCode40, "C8.09"),
    ],
)
def test_code_variants_reject_other_or_placeholder_shapes(
    model: type[MorphologyCode32 | MorphologyCode40 | TopographyCode40],
    value: str,
) -> None:
    with pytest.raises(ValidationError):
        model(value=value)


@pytest.mark.parametrize(
    ("edition", "code", "fields"),
    [
        (
            "3.2",
            "85032/0",
            {"base_morphology": "8503", "specificity": "2", "behaviour": "0"},
        ),
        ("4.0", "8503/0", {"base_morphology": "8503", "behaviour": "0"}),
    ],
)
def test_dataset_binds_morphology_record_shape_to_edition(
    edition: str, code: str, fields: dict[str, str]
) -> None:
    with pytest.raises(ValidationError, match="edition"):
        CanonicalDataset(
            edition=edition,
            axis="morphology",
            records=(IcdoRecord(code=code, level="morphology", **fields),),
            source_shape=SourceShape(
                sheet_names=(), headers=(), merged_ranges=(), trailing_blank_rows=0
            ),
            source_sha256="a" * 64,
        )


def test_topography_refuses_morphology_only_fields() -> None:
    with pytest.raises(ValidationError, match="topography record"):
        IcdoRecord(code="C34.9", level="leaf", behaviour="3")


@pytest.mark.parametrize(
    "fields",
    [
        {"base_morphology": "9999", "specificity": "2", "behaviour": "0"},
        {"base_morphology": "8503", "specificity": "A", "behaviour": "0"},
        {"base_morphology": "8503", "specificity": "2", "behaviour": "3"},
    ],
)
def test_morphology_refuses_derived_fields_that_disagree_with_code(
    fields: dict[str, str],
) -> None:
    with pytest.raises(ValidationError, match="derived fields"):
        IcdoRecord(code="85032/0", level="morphology", **fields)


@pytest.mark.parametrize(
    "fields",
    [
        {"parent_code": "C34"},
        {"parent_code": None, "level": "leaf"},
        {"parent_code": "C35", "level": "leaf"},
    ],
)
def test_topography_enforces_exact_level_and_parent(fields: dict[str, str]) -> None:
    level = fields.pop("level", "category")
    with pytest.raises(ValidationError, match="topography record"):
        IcdoRecord(code="C34.9" if level == "leaf" else "C34", level=level, **fields)


def test_records_require_32_specificity_absent_and_40_specificity_present() -> None:
    shape = SourceShape(
        sheet_names=(), headers=(), merged_ranges=(), trailing_blank_rows=0
    )
    with pytest.raises(ValidationError, match="derived fields"):
        CanonicalDataset(
            edition="3.2",
            axis="morphology",
            records=(
                IcdoRecord(
                    code="8503/0",
                    level="morphology",
                    base_morphology="8503",
                    specificity="3",
                    behaviour="0",
                ),
            ),
            source_shape=shape,
            source_sha256="a" * 64,
        )
    with pytest.raises(ValidationError, match="derived fields"):
        CanonicalDataset(
            edition="4.0",
            axis="morphology",
            records=(
                IcdoRecord(
                    code="85032/0",
                    level="morphology",
                    base_morphology="8503",
                    behaviour="0",
                ),
            ),
            source_shape=shape,
            source_sha256="a" * 64,
        )
