"""Reviewed NCIt genera that qualify rather than define morphology."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

MORPHOLOGY_QUALIFIER_LIST_VERSION = "ncit-26.07d-morphology-qualifier-genus-v2"
_NCIT_VERSION = "26.07d"
_EXPECTED_COUNT = 2_868
_CODE = re.compile(r"C[1-9][0-9]*\Z")


@dataclass(frozen=True, slots=True)
class MorphologyQualifierPolicy:
    list_version: str
    ncit_version: str
    label_source: str
    codes: frozenset[str]


def _require_string(document: dict[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"morphology qualifier policy {key} is invalid")
    return value


def _require_document(document: object) -> dict[str, Any]:
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("morphology qualifier policy schema version differs")
    return document


def _parse_entry_code(entry: object) -> str:
    if not isinstance(entry, dict):
        raise ValueError("morphology qualifier policy entry is invalid")
    return _require_string(entry, "code")


def _parse_codes(document: dict[str, Any]) -> tuple[str, ...]:
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError("morphology qualifier policy entries are invalid")
    ordered = tuple(_parse_entry_code(entry) for entry in raw_entries)
    if len(ordered) != _EXPECTED_COUNT or ordered != tuple(sorted(set(ordered))):
        raise ValueError("morphology qualifier policy code set differs")
    if any(_CODE.fullmatch(code) is None for code in ordered):
        raise ValueError("morphology qualifier policy contains an invalid NCIt code")
    return ordered


def _parse_policy(document: object) -> MorphologyQualifierPolicy:
    policy_document = _require_document(document)
    list_version = _require_string(policy_document, "list_version")
    ncit_version = _require_string(policy_document, "ncit_version")
    label_source = _require_string(policy_document, "label_source")
    if list_version != MORPHOLOGY_QUALIFIER_LIST_VERSION:
        raise ValueError("morphology qualifier policy list version differs")
    if ncit_version != _NCIT_VERSION:
        raise ValueError("morphology qualifier policy NCIt version differs")
    codes = _parse_codes(policy_document)
    return MorphologyQualifierPolicy(
        list_version=list_version,
        ncit_version=ncit_version,
        label_source=label_source,
        codes=frozenset(codes),
    )


def load_packaged_morphology_qualifier_policy() -> MorphologyQualifierPolicy:
    resource = files("ontolib.decomposition").joinpath(
        "data/morphology-qualifier-genera-26.07d.json"
    )
    return _parse_policy(json.loads(resource.read_text()))


MORPHOLOGY_QUALIFIER_CODES = load_packaged_morphology_qualifier_policy().codes
