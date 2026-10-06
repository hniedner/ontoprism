"""Resume identity follows explicit semantics and real packaged policy inputs."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from ontolib.decomposition import semantic_identity

pytestmark = pytest.mark.unit


def test_engine_stamp_changes_resume_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    before = semantic_identity.routing_implementation_identity()
    monkeypatch.setattr(semantic_identity, "ENGINE_VERSION", "decomposition-engine-v2")
    assert semantic_identity.routing_implementation_identity() != before


def test_rules_stamp_changes_resume_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    before = semantic_identity.routing_implementation_identity()
    monkeypatch.setattr(semantic_identity, "RULES_VERSION", "decomposition-rules-v2")
    assert semantic_identity.routing_implementation_identity() != before


def test_group_policy_input_changes_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    before = semantic_identity.routing_implementation_identity()
    monkeypatch.setattr(
        semantic_identity,
        "load_packaged_normalized_group_policy",
        lambda: SimpleNamespace(policy_identity="a" * 64),
    )
    assert semantic_identity.routing_implementation_identity() != before


def test_qualifier_policy_bytes_change_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resource = tmp_path / "data/morphology-qualifier-genera-26.07d.json"
    resource.parent.mkdir()
    resource.write_text('{"entries": ["C1"]}')
    monkeypatch.setattr(semantic_identity, "files", lambda package: tmp_path)
    before = semantic_identity.routing_implementation_identity()
    resource.write_text('{"entries": ["C2"]}')
    assert semantic_identity.routing_implementation_identity() != before


def test_missing_policy_is_not_replaced_with_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(semantic_identity, "files", lambda package: tmp_path)
    with pytest.raises(FileNotFoundError):
        semantic_identity.routing_implementation_identity()
