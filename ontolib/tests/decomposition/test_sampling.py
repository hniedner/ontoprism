from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ontolib.decomposition.sampling import (
    REQUIRED_SAMPLE_STRATA,
    DecompositionSampleManifest,
    SampleConcept,
    load_sample_manifest,
)

_CANONICAL_SAMPLE = (
    Path(__file__).resolve().parents[3] / "samples" / "ncit-26.07d-m1-review.json"
)
_SME_SAMPLE = (
    Path(__file__).resolve().parents[3] / "samples" / "ncit-26.07d-m1-sme-review.json"
)


def _concept(
    code: str,
    *strata: str,
    rationale: str = "Exercises a production-shaped review stratum.",
) -> SampleConcept:
    return SampleConcept(
        code=code,
        strata=tuple(strata),
        rationale=rationale,
    )


def _manifest(**updates: object) -> DecompositionSampleManifest:
    required = tuple(sorted(REQUIRED_SAMPLE_STRATA))
    values: dict[str, object] = {
        "schema_version": 1,
        "name": "ncit-26.07d-m1-review",
        "branch": "neoplasm",
        "scope_root": "C3262",
        "scope_version": "stated-genus-subclass-v1",
        "source_identity": "a" * 64,
        "ontology_version": "26.07d",
        "selection_method": "explicit-stratified",
        "seed": None,
        "concepts": (
            _concept(
                "C27262",
                *required,
                rationale="Known nested-definition hard case.",
            ),
            _concept("C12400", "atomic-no-op", rationale="Atomic negative control."),
        ),
    }
    values.update(updates)
    return DecompositionSampleManifest.model_validate(values)


@pytest.mark.unit
def test_sample_manifest_identity_binds_exact_order_and_rationale() -> None:
    original = _manifest()
    equivalent = DecompositionSampleManifest.model_validate_json(
        original.model_dump_json()
    )
    reordered = _manifest(concepts=tuple(reversed(original.concepts)))
    revised = _manifest(
        concepts=(
            original.concepts[0].model_copy(
                update={"rationale": "A materially revised review rationale."}
            ),
            original.concepts[1],
        )
    )

    assert equivalent.identity == original.identity
    assert len(original.identity) == 64
    assert original.codes == ("C27262", "C12400")
    assert reordered.identity != original.identity
    assert revised.identity != original.identity


@pytest.mark.unit
def test_sample_manifest_file_round_trips_canonical_review_definition(
    tmp_path: Path,
) -> None:
    expected = _manifest()
    path = tmp_path / "sample.json"
    path.write_text(expected.model_dump_json(indent=2), encoding="utf-8")

    actual = load_sample_manifest(path)

    assert actual == expected
    assert actual.identity == expected.identity
    assert actual.covered_strata == REQUIRED_SAMPLE_STRATA
    assert json.loads(path.read_text(encoding="utf-8"))["seed"] is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "updates",
    [
        {"branch": "disease"},
        {"scope_root": "C2991"},
        {"source_identity": "not-a-digest"},
        {"ontology_version": ""},
        {"selection_method": "random"},
        {"seed": "opaque"},
        {"unknown_field": "not-identity-free"},
        {
            "concepts": (
                _concept("C1", *tuple(sorted(REQUIRED_SAMPLE_STRATA))),
                _concept("C1", "atomic-no-op"),
            )
        },
        {
            "concepts": (
                {
                    "code": "not-a-code",
                    "strata": tuple(sorted(REQUIRED_SAMPLE_STRATA)),
                    "rationale": "Invalid code.",
                },
            )
        },
        {
            "concepts": (
                {
                    "code": "C1",
                    "strata": ("atomic-no-op", "atomic-no-op"),
                    "rationale": "Duplicate tag.",
                },
            )
        },
        {
            "concepts": (
                {
                    "code": "C1",
                    "strata": ("unknown-stratum",),
                    "rationale": "Unknown tag.",
                },
            )
        },
        {
            "concepts": (
                {
                    "code": "C1",
                    "strata": tuple(sorted(REQUIRED_SAMPLE_STRATA)),
                    "rationale": "",
                },
            )
        },
        {"concepts": (_concept("C1", "atomic-no-op"),)},
    ],
)
def test_sample_manifest_rejects_ambiguous_or_incomplete_review_definitions(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _manifest(**updates)


@pytest.mark.unit
def test_load_sample_manifest_rejects_non_json_and_missing_files(
    tmp_path: Path,
) -> None:
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="valid sample manifest"):
        load_sample_manifest(malformed)
    with pytest.raises(ValueError, match="valid sample manifest"):
        load_sample_manifest(tmp_path / "missing.json")


@pytest.mark.unit
def test_tracked_m1_sample_is_source_bound_and_covers_every_review_stratum() -> None:
    sample = load_sample_manifest(_CANONICAL_SAMPLE)

    assert sample.name == "ncit-26.07d-m1-review"
    assert sample.branch == "neoplasm"
    assert sample.scope_root == "C3262"
    assert sample.source_identity == (
        "f54dd2910a31245a30cea094dc72ce6a5c8d7b5a9c4e484007a35a1c343624c8"
    )
    assert sample.ontology_version == "26.07d"
    assert sample.seed is None
    assert sample.covered_strata == REQUIRED_SAMPLE_STRATA
    assert sample.codes == (
        "C27262",
        "C102870",
        "C162770",
        "C102883",
        "C115057",
        "C101539",
        "C132677",
        "C181564",
        "C186620",
        "C162226",
        "C206219",
        "C198031",
        "C100054",
        "C100051",
        "C6135",
    )


@pytest.mark.unit
def test_tracked_sme_sample_extends_m1_sample_with_required_review_seeds() -> None:
    comparison = load_sample_manifest(_CANONICAL_SAMPLE)
    adjudication = load_sample_manifest(_SME_SAMPLE)

    assert adjudication.codes[: len(comparison.codes)] == comparison.codes
    assert adjudication.codes[-5:] == (
        "C4791",
        "C35756",
        "C89995",
        "C27787",
        "C115118",
    )
    assert adjudication.identity == (
        "9c32b36c482879f030aca0ec8f2bd84542a3f53fe541c9a4372cfe050b94b87c"
    )
