import pytest
from pydantic import ValidationError

from ontolib.repositories.icdo.models import (
    MorphologyCode32,
    MorphologyCode40,
    TopographyCode40,
)

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
