"""Unit tests for deterministic mint ID generation (design §7.2)."""

import pytest

from ontolib.decomposition.minting import MintedConcept, normalize_label


@pytest.mark.unit
class TestNormalizeLabel:
    def test_lower_title_cased(self) -> None:
        assert normalize_label("Pleural Effusion") == "pleural effusion"

    def test_leading_trailing_whitespace_stripped(self) -> None:
        assert normalize_label("  Left  ") == "left"


@pytest.mark.unit
class TestMintedConcept:
    def test_standard_creation(self) -> None:
        mc = MintedConcept(axis="op:Laterality", label="Left")
        assert mc.id.startswith("MINT-")
        assert len(mc.id) == 17  # "MINT-" + 12 hex chars

    def test_id_is_deterministic(self) -> None:
        a = MintedConcept(axis="op:Laterality", label="Left")
        b = MintedConcept(axis="op:Laterality", label="Left")
        assert a.id == b.id

    def test_different_axis_yields_different_id(self) -> None:
        a = MintedConcept(axis="op:Laterality", label="Left")
        b = MintedConcept(axis="op:WithFinding", label="Left")
        # Same surface form, different axis → different ids.
        assert a.id != b.id

    def test_different_label_yields_different_id(self) -> None:
        a = MintedConcept(axis="op:StageSystem", label="UICC v7")
        b = MintedConcept(axis="op:StageSystem", label="UICC v8")
        assert a.id != b.id

    def test_status_defaults_to_proposed(self) -> None:
        mc = MintedConcept(axis="op:Laterality", label="Left")
        assert mc.status == "proposed"

    def test_custom_source_signal_and_status(self) -> None:
        mc = MintedConcept(
            axis="op:WithFinding",
            label="Without Pleural Effusion",
            source_signal="Lung Carcinoma without Pleural Effusion",
            status="approved",
        )
        assert mc.source_signal == "Lung Carcinoma without Pleural Effusion"
        assert mc.status in ("proposed", "approved", "rejected")
