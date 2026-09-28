"""Behavioral contracts for the reviewed morphology qualifier policy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ontolib.decomposition.morphology_qualifier_policy import (
    MORPHOLOGY_QUALIFIER_LIST_VERSION,
    _parse_policy,
    load_packaged_morphology_qualifier_policy,
)

pytestmark = pytest.mark.unit

_P334_HISTOLOGY_EXCLUSIONS = {
    "C4219",
    "C4220",
    "C4536",
    "C48314",
    "C8423",
    "C8559",
}
_P334_QUALIFIER_REENTRIES = {"C3261", "C3482", "C4104", "C4124", "C66717"}


def _packaged_document() -> dict[str, object]:
    policy = load_packaged_morphology_qualifier_policy()
    return {
        "schema_version": 1,
        "list_version": policy.list_version,
        "ncit_version": policy.ncit_version,
        "label_source": policy.label_source,
        "entries": [{"code": code} for code in sorted(policy.codes)],
    }


def test_packaged_policy_is_the_reviewed_ncit_2607d_list() -> None:
    policy = load_packaged_morphology_qualifier_policy()

    assert policy.list_version == MORPHOLOGY_QUALIFIER_LIST_VERSION
    assert policy.list_version == "ncit-26.07d-morphology-qualifier-genus-v2"
    assert policy.ncit_version == "26.07d"
    assert len(policy.codes) == 2_868
    assert {
        "C141041",
        "C120186",
        "C3640",
        "C7853",
        "C9049",
        *_P334_QUALIFIER_REENTRIES,
    } <= policy.codes
    assert _P334_HISTOLOGY_EXCLUSIONS.isdisjoint(policy.codes)
    assert {"C3879", "C4917"}.isdisjoint(policy.codes)


def test_reviewed_policy_excludes_every_adjudicated_oracle_filler() -> None:
    oracle_path = Path(__file__).parent / "golden/neoplasm-adjudicated.json"
    document = json.loads(oracle_path.read_text())
    expected_fillers = {
        constituent["filler"]
        for concept in document["concepts"]
        for constituent in concept["expected"]["constituents"]
    }

    policy = load_packaged_morphology_qualifier_policy()

    assert expected_fillers.isdisjoint(policy.codes)


def test_runtime_policy_uses_codes_without_validating_review_labels() -> None:
    document = _packaged_document()
    entries = document["entries"]
    assert isinstance(entries, list)
    first = entries[0]
    assert isinstance(first, dict)

    policy = _parse_policy(document)

    assert first["code"] in policy.codes


@pytest.mark.parametrize(
    ("case", "message"),
    [
        pytest.param("schema", "schema version", id="schema"),
        pytest.param("list-version", "list version", id="list-version"),
        pytest.param("ncit-version", "NCIt version", id="ncit-version"),
        pytest.param("entries", "entries", id="entries"),
        pytest.param("entry", "entry", id="entry"),
        pytest.param("code", "code is invalid", id="code"),
        pytest.param("count", "code set", id="count"),
        pytest.param("code-format", "invalid NCIt code", id="code-format"),
    ],
)
def test_runtime_policy_rejects_malformed_code_contracts(
    case: str,
    message: str,
) -> None:
    document = _packaged_document()
    entries = document["entries"]
    assert isinstance(entries, list)
    if case == "schema":
        document["schema_version"] = 2
    elif case == "list-version":
        document["list_version"] = "other"
    elif case == "ncit-version":
        document["ncit_version"] = "other"
    elif case == "entries":
        document["entries"] = None
    elif case == "entry":
        entries[0] = None
    elif case == "code":
        assert isinstance(entries[0], dict)
        entries[0].pop("code")
    elif case == "count":
        entries.pop()
    else:
        assert isinstance(entries[0], dict)
        entries[0]["code"] = "C000001"

    with pytest.raises(ValueError, match=message):
        _parse_policy(document)
