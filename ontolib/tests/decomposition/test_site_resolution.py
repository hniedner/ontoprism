"""Tests for the D23 SME-validated organ-code lookup."""

import pytest

from ontolib.decomposition.site_resolution import (
    MORPHOLOGY_TO_ORGAN,
    organ_for_morphology,
)


@pytest.mark.unit
class TestOrganForMorphologyKnownMappings:
    def test_thyroid_medullary_carcinoma(self) -> None:
        assert organ_for_morphology("C3879") == "C12400"

    def test_anaplastic_thyroid_carcinoma(self) -> None:
        assert organ_for_morphology("C3878") == "C12400"

    def test_gastric_adenocarcinoma(self) -> None:
        assert organ_for_morphology("C2851") == "C12391"

    def test_colorectal_carcinoma(self) -> None:
        assert organ_for_morphology("C2955") == "C19184"

    def test_esophageal_squamous(self) -> None:
        assert organ_for_morphology("C3889") == "C203674"

    def test_esophageal_adenocarcinoma(self) -> None:
        assert organ_for_morphology("C4911") == "C203674"

    def test_cervical_carcinoma(self) -> None:
        assert organ_for_morphology("C4004") == "C12311"

    def test_lung_carcinoma(self) -> None:
        assert organ_for_morphology("C4874") == "C12468"

    def test_breast_carcinoma(self) -> None:
        assert organ_for_morphology("C4017") == "C12971"

    def test_pancreatic_carcinoma(self) -> None:
        assert organ_for_morphology("C3844") == "C12393"

    def test_gallbladder_carcinoma(self) -> None:
        assert organ_for_morphology("C3860") == "C12377"

    def test_prostate_carcinoma(self) -> None:
        assert organ_for_morphology("C4905") == "C12410"


@pytest.mark.unit
class TestOrganForMorphologyEdgeCases:
    def test_none_returns_none(self) -> None:
        assert organ_for_morphology(None) is None

    def test_unknown_morphology_returns_none(self) -> None:
        assert organ_for_morphology("C99999") is None

    def test_empty_string_returns_none(self) -> None:
        assert organ_for_morphology("") is None


@pytest.mark.unit
class TestMappingTable:
    def test_all_mapped_organs_differ_from_their_morphology_code(self) -> None:
        """Sanity check: no morphology maps to itself."""
        for morph, organ in MORPHOLOGY_TO_ORGAN.items():
            assert morph != organ, f"{morph} maps to itself"

    def test_organs_start_with_c(self) -> None:
        """All organ codes in the table should be valid NCIt codes."""
        for organ in MORPHOLOGY_TO_ORGAN.values():
            assert organ.startswith("C"), f"{organ} does not look like an NCIt code"

    def test_all_morphology_codes_start_with_c(self) -> None:
        for morph in MORPHOLOGY_TO_ORGAN:
            assert morph.startswith("C"), f"{morph} does not look like an NCIt code"
