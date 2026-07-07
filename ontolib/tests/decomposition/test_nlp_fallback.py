"""Unit tests for the NLP-fallback label parser (design §7.1)."""

import pytest

from ontolib.decomposition.nlp_fallback import AspectRecord, parse_label_aspects


@pytest.mark.unit
class TestLaterality:
    def test_left_prefix(self) -> None:
        aspects = parse_label_aspects("Left Atrial Myxoma")
        assert any(
            a.axis == "op:Laterality" and a.surface_form == "Left" for a in aspects
        )

    def test_right_prefix(self) -> None:
        aspects = parse_label_aspects("Right Ventricular Hypertrophy")
        assert any(
            a.axis == "op:Laterality" and a.surface_form == "Right" for a in aspects
        )

    def test_bilateral(self) -> None:
        aspects = parse_label_aspects("Bilateral Renal Tumor")
        assert any(
            a.axis == "op:Laterality" and a.surface_form == "Bilateral" for a in aspects
        )

    def test_no_laterality(self) -> None:
        aspects = parse_label_aspects("Pulmonary Squamous Cell Carcinoma")
        assert not any(a.axis == "op:Laterality" for a in aspects)


@pytest.mark.unit
class TestWithWithoutFinding:
    def test_with_finding_positive(self) -> None:
        aspects = parse_label_aspects(
            "Stage IIIB Lung Small Cell Carcinoma with Pleural Effusion AJCC v7"
        )
        assert any(
            a.axis == "op:WithFinding" and a.surface_form == "Pleural Effusion"
            for a in aspects
        )

    def test_without_finding_negative(self) -> None:
        aspects = parse_label_aspects("Lung Carcinoma without Pleural Effusion")
        wf = [a for a in aspects if a.axis == "op:WithFinding"]
        assert any(
            a.surface_form == "Pleural Effusion" and a.polarity == "negative"
            for a in wf
        )

    def test_with_multiple_findings(self) -> None:
        aspects = parse_label_aspects("Neoplasm with Hemorrhage")
        assert any(
            a.axis == "op:WithFinding" and a.surface_form == "Hemorrhage"
            for a in aspects
        )

    def test_no_finding_pattern(self) -> None:
        aspects = parse_label_aspects("Medullary Thyroid Carcinoma")
        assert not any(a.axis == "op:WithFinding" for a in aspects)


@pytest.mark.unit
class TestStagingVersion:
    def test_ajcc_v7(self) -> None:
        aspects = parse_label_aspects("Stage III Colon Carcinoma AJCC v7")
        assert any(
            a.axis == "op:StageSystem" and a.surface_form == "AJCC v7" for a in aspects
        )

    def test_ajcc_v8(self) -> None:
        aspects = parse_label_aspects("Stage II Rectum Adenocarcinoma AJCC v8")
        assert any(
            a.axis == "op:StageSystem" and a.surface_form == "AJCC v8" for a in aspects
        )

    def test_no_stage_version(self) -> None:
        aspects = parse_label_aspects("Early Stage Lung Cancer")
        assert not any(a.axis == "op:StageSystem" for a in aspects)


@pytest.mark.unit
class TestCombinedPatterns:
    def test_left_with_finding(self) -> None:
        aspects = parse_label_aspects("Left Lung Carcinoma with Pleural Effusion")
        lateral = [a for a in aspects if a.axis == "op:Laterality"]
        finding = [a for a in aspects if a.axis == "op:WithFinding"]
        assert len(lateral) == 1
        assert lateral[0].surface_form == "Left"
        assert len(finding) == 1
        assert finding[0].polarity == "positive"

    def test_right_without_finding(self) -> None:
        aspects = parse_label_aspects("Right Kidney Tumor without Calcification")
        lateral = [a for a in aspects if a.axis == "op:Laterality"]
        finding = [a for a in aspects if a.axis == "op:WithFinding"]
        assert lateral[0].surface_form == "Right"
        assert finding[0].polarity == "negative"

    def test_empty_label_returns_no_aspects(self) -> None:
        assert parse_label_aspects("") == []

    def test_none_label_returns_no_aspects(self) -> None:
        assert parse_label_aspects(None) == []

    def test_aspect_record_fields(self) -> None:
        aspect = AspectRecord(
            axis="op:Laterality", surface_form="Left", polarity="positive"
        )
        assert aspect.axis == "op:Laterality"
        assert aspect.surface_form == "Left"
        assert aspect.polarity == "positive"
