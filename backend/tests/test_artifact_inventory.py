from __future__ import annotations

from pathlib import Path

import pytest
from scripts.artifacts import inventory_repository, parser

from ontolib.decomposition.run_artifacts import (
    GeneratorBinding,
    write_legacy_in_place_manifest,
)


@pytest.mark.unit
def test_inventory_is_read_only_and_reports_unmanaged_ignored_and_worktrees(
    tmp_path: Path,
) -> None:
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp/unmanaged.bin").write_bytes(b"unknown")
    legacy = tmp_path / "tmp/critical.ttl"
    run_id = "neoplasm-0b00326b-6a9f-424f-b074-d4f1f8a0304d"
    legacy.write_text(f"<{run_id}> <p> <o> .\n", encoding="utf-8")
    digest = __import__("hashlib").sha256(legacy.read_bytes()).hexdigest()
    write_legacy_in_place_manifest(
        path=tmp_path / "tmp/artifacts/v1/legacy-in-place/critical.manifest.json",
        repository_root=tmp_path,
        artifact_path=legacy,
        run_id=run_id,
        persisted_representation_identity=digest,
        persisted_artifact_path="tmp/critical.ttl",
        source_identity="source",
        generator=GeneratorBinding(identity="git:abc", command=("register",)),
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "data/index.meta").write_bytes(b"index")

    report = inventory_repository(
        tmp_path,
        git_worktrees=(tmp_path, tmp_path.parent / "abandoned-recovery"),
        compose_resources=(
            {
                "kind": "volume",
                "name": "ontoprism-podman-poc_ontoprism_pg_data",
                "project": "ontoprism-podman-poc",
            },
        ),
    )

    assert parser().parse_args(["inventory"]).command == "inventory"
    with pytest.raises(SystemExit):
        parser().parse_args(["delete"])
    assert report["ignored_usage"]["tmp"]["logical_bytes"] >= 7
    assert report["ignored_usage"]["data"]["logical_bytes"] >= 5
    assert "tmp/unmanaged.bin" in report["unknown_unmanaged_paths"]
    assert "tmp/critical.ttl" not in report["unknown_unmanaged_paths"]
    assert report["managed_records"][0]["artifacts"] == [
        {
            "path": "tmp/critical.ttl",
            "availability": "size-matches-manifest",
            "size": legacy.stat().st_size,
        }
    ]
    assert str(tmp_path.parent / "abandoned-recovery") in report["registered_worktrees"]
    assert report["compose_resources"][0]["project"] == "ontoprism-podman-poc"
    assert "delete" not in report
    assert "apply" not in report
