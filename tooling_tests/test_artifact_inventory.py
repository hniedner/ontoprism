from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from scripts import artifacts
from scripts.artifacts import (
    COMPOSE_PROJECT,
    apply_cleanup_plan,
    build_cleanup_plan,
    inventory_repository,
    load_retention_policy,
    parser,
    write_cleanup_plan,
)

from ontolib.decomposition import run_artifacts as artifact_publication
from ontolib.decomposition.run_artifacts import (
    GeneratorBinding,
    ParentManifestBinding,
    RetentionBinding,
    SourceIdentity,
    publish_generation,
)


def _policy(root: Path) -> Path:
    path = root / "artifact-retention.toml"
    path.write_text(
        """schema_version = 1
[managed]
generation_root = "tmp/artifacts/v1/generations"

[classes.unreferenced-bounded-derived]
owner = "artifact-review"
expiry = "manifest-expires-at"
cleanup_eligible = true

[classes.cleanup-plan]
owner = "artifact-review"
expiry = "superseded-or-session-end"

[classes.superseded-unavailable-audit]
owner = "artifact-review"
expiry = "none"

[classes.referenced-bounded-run]
owner = "decomposition"
expiry = "while-referenced"

[classes.critical-full-corpus]
owner = "decomposition"
expiry = "none"

[classes.licensed-source]
owner = "data-build"
expiry = "after-repository-certification"
""",
        encoding="utf-8",
    )
    return path


def _publish(
    root: Path,
    generation: str,
    *,
    family: str = "bounded-derived",
    retention_class: str = "unreferenced-bounded-derived",
    owner: str = "artifact-review",
    parents: tuple[ParentManifestBinding, ...] = (),
    content: bytes = b"payload",
    expires_at: str | None = "2000-01-01T00:00:00Z",
) -> tuple[Path, str]:
    source = root / f"{generation}.source"
    source.write_bytes(content)
    manifest = publish_generation(
        artifacts_root=root / "tmp/artifacts/v1/generations",
        family=family,
        generation_id=generation,
        run_id=None,
        artifact_sources={"artifacts/result.bin": source},
        parents=parents,
        generator=GeneratorBinding(identity="git:abc", command=("generate",)),
        sources=(SourceIdentity(name="fixture", identity="exact"),),
        retention=RetentionBinding(
            retention_class=retention_class, owner=owner, expires_at=expires_at
        ),
    )
    directory = root / f"tmp/artifacts/v1/generations/{family}/{generation}"
    return directory, manifest.manifest_identity


@pytest.mark.unit
def test_inventory_summarizes_unmanaged_roots_with_exact_bounded_drilldown(
    tmp_path: Path,
) -> None:
    _policy(tmp_path)
    unmanaged = tmp_path / "tmp/unmanaged"
    unmanaged.mkdir(parents=True)
    for index, size in enumerate((1, 2, 3, 4, 5, 6)):
        (unmanaged / f"item-{index}.bin").write_bytes(b"x" * size)
    (tmp_path / "data").mkdir()
    (tmp_path / "data/index.meta").write_bytes(b"index")

    report = inventory_repository(
        tmp_path,
        git_worktrees={"status": "empty", "entries": []},
        compose_resources={"status": "empty", "project": COMPOSE_PROJECT, "items": []},
        top_n=3,
    )

    summaries = report["unmanaged_root_summaries"]
    assert len(summaries) == 2
    tmp_summary = next(item for item in summaries if item["path"] == "tmp/unmanaged")
    assert tmp_summary["files"] == 6
    assert tmp_summary["logical_bytes"] == 21
    assert len(tmp_summary["top_files"]) == 3
    assert tmp_summary["top_files_truncated"] is True
    assert tmp_summary["retention_class"] == "unknown"
    assert tmp_summary["owner"] == "unknown"
    assert tmp_summary["reference_status"] == "unknown"
    assert "unknown_unmanaged_paths" not in report
    assert report["budgets"] == {"tmp": "not-declared", "data": "not-declared"}
    assert report["summary_counts"] == {
        "managed_records": 0,
        "partial_generations": 0,
        "unavailable_records": 0,
        "unmanaged_roots": 2,
        "top_n_per_root": 3,
    }


@pytest.mark.unit
def test_inventory_counts_a_top_level_unmanaged_file_exactly(tmp_path: Path) -> None:
    _policy(tmp_path)
    (tmp_path / "tmp").mkdir()
    unmanaged = tmp_path / "tmp/unmanaged.bin"
    unmanaged.write_bytes(b"seven!!")

    report = inventory_repository(
        tmp_path,
        git_worktrees={"status": "empty", "entries": []},
        compose_resources={
            "status": "empty",
            "project": COMPOSE_PROJECT,
            "items": [],
        },
    )

    assert report["unmanaged_root_summaries"] == [
        {
            "path": "tmp/[top-level-files]",
            "available": True,
            "logical_bytes": 7,
            "allocated_bytes": unmanaged.stat().st_blocks * 512,
            "files": 1,
            "top_files": [{"path": "unmanaged.bin", "logical_bytes": 7}],
            "top_files_truncated": False,
            "retention_class": "unknown",
            "owner": "unknown",
            "reference_status": "unknown",
        }
    ]


@pytest.mark.unit
def test_inventory_never_repeats_managed_registry_as_unmanaged(tmp_path: Path) -> None:
    _policy(tmp_path)
    _publish(tmp_path, "managed")

    report = inventory_repository(
        tmp_path,
        git_worktrees={"status": "empty", "entries": []},
        compose_resources={
            "status": "empty",
            "project": COMPOSE_PROJECT,
            "items": [],
        },
    )

    assert all(
        summary["path"] != "tmp/artifacts"
        for summary in report["unmanaged_root_summaries"]
    )


@pytest.mark.unit
def test_inventory_large_file_shape_is_output_bounded_and_totals_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _policy(tmp_path)
    (tmp_path / "tmp/million-shaped").mkdir(parents=True)

    def shaped_usage(path: Path, *, top_n: int = 5) -> dict[str, object]:
        assert path.name == "million-shaped"
        return {
            "available": True,
            "logical_bytes": 1_000_000,
            "allocated_bytes": 4_096_000_000,
            "files": 1_000_000,
            "top_files": [
                {"path": f"item-{index}", "logical_bytes": 1} for index in range(top_n)
            ],
            "top_files_truncated": True,
        }

    monkeypatch.setattr(artifacts, "_tree_summary", shaped_usage)
    report = inventory_repository(
        tmp_path,
        git_worktrees={"status": "empty", "entries": []},
        compose_resources={"status": "empty", "project": COMPOSE_PROJECT, "items": []},
        top_n=4,
    )
    summary = report["unmanaged_root_summaries"][0]
    assert (summary["files"], summary["logical_bytes"], len(summary["top_files"])) == (
        1_000_000,
        1_000_000,
        4,
    )
    assert len(json.dumps(report)) < 5000


@pytest.mark.unit
def test_inventory_distinguishes_missing_and_failed_tools_from_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _policy(tmp_path)
    monkeypatch.setattr(artifacts.shutil, "which", lambda _name: None)
    missing = inventory_repository(tmp_path)
    assert missing["worktrees"]["status"] == "unavailable"
    assert missing["compose"]["status"] == "unavailable"

    monkeypatch.setattr(artifacts.shutil, "which", lambda name: f"/usr/bin/{name}")

    def failed(*_args: object, **_kwargs: object) -> object:
        return type("Result", (), {"returncode": 1, "stdout": "", "stderr": "boom"})()

    monkeypatch.setattr(artifacts.subprocess, "run", failed)
    failed_report = inventory_repository(tmp_path)
    assert failed_report["worktrees"]["status"] == "error"
    assert failed_report["compose"]["status"] == "error"


@pytest.mark.unit
def test_compose_inventory_queries_the_pinned_volume_by_exact_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _policy(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(artifacts.shutil, "which", lambda name: f"/usr/bin/{name}")

    def run(command: list[str], *_args: object, **_kwargs: object) -> object:
        calls.append(command)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(artifacts.subprocess, "run", run)

    inventory_repository(tmp_path)

    assert [
        "/usr/bin/docker",
        "volume",
        "inspect",
        "ontoprism-podman-poc_ontoprism_pg_data",
        "--format",
        "{{.Name}}|{{.Scope}}",
    ] in calls


@pytest.mark.unit
def test_worktree_and_compose_audit_are_operator_only_and_protect_active_resources(
    tmp_path: Path,
) -> None:
    _policy(tmp_path)
    worktrees = {
        "status": "ok",
        "entries": [
            {
                "path": "/repo/Fallow/worktree",
                "common_git_dir": "/repo/.git",
                "head": "a" * 40,
                "branch": "refs/heads/fallow",
                "state": "branch",
                "dirty": 0,
                "untracked": 0,
                "ignored": 3,
                "unique_commit_status": "unknown",
                "resolution": "operator-action-required",
            },
            {
                "path": "/repo/recovery-274",
                "common_git_dir": "/repo/.git",
                "head": "b" * 40,
                "branch": None,
                "state": "detached",
                "dirty": 1,
                "untracked": 2,
                "ignored": 0,
                "unique_commit_status": "unique",
                "resolution": "operator-action-required",
            },
        ],
    }
    compose = {
        "status": "ok",
        "project": COMPOSE_PROJECT,
        "items": [
            {"kind": "container", "name": "api", "state": "running", "protected": True},
            {"kind": "volume", "name": "pg", "state": "active", "protected": True},
        ],
    }
    report = inventory_repository(
        tmp_path, git_worktrees=worktrees, compose_resources=compose
    )
    assert all(
        entry["resolution"] == "operator-action-required"
        for entry in report["worktrees"]["entries"]
    )
    assert all(item["protected"] for item in report["compose"]["items"])
    source = Path(artifacts.__file__).read_text(encoding="utf-8")
    assert "worktree remove" not in source
    assert "worktree prune" not in source
    assert "down -v" not in source
    assert "--volumes" not in source


@pytest.mark.unit
def test_policy_parses_managed_root_and_optional_budgets(tmp_path: Path) -> None:
    policy = load_retention_policy(_policy(tmp_path))
    assert policy.generation_root == "tmp/artifacts/v1/generations"
    assert policy.budgets == {"tmp": "not-declared", "data": "not-declared"}
    assert policy.classes["unreferenced-bounded-derived"].cleanup_eligible is True


@pytest.mark.unit
def test_cleanup_plan_is_deterministic_managed_only_and_excludes_references(
    tmp_path: Path,
) -> None:
    _policy(tmp_path)
    eligible, identity = _publish(tmp_path, "eligible", content=b"eligible")
    parent, parent_identity = _publish(tmp_path, "parent", content=b"parent")
    parent_manifest = parent / "manifest.json"
    parent_record = ParentManifestBinding(
        family="bounded-derived",
        generation_id="parent",
        manifest_path=parent_manifest.relative_to(tmp_path).as_posix(),
        manifest_identity=parent_identity,
    )
    _publish(tmp_path, "child", parents=(parent_record,), content=b"child")
    (tmp_path / "tmp/unmanaged").write_bytes(b"never eligible")

    first = build_cleanup_plan(tmp_path)
    second = build_cleanup_plan(tmp_path)

    assert first == second
    assert first["plan_identity"] == second["plan_identity"]
    assert [action["path"] for action in first["actions"]] == [
        eligible.relative_to(tmp_path).as_posix()
    ]
    action = first["actions"][0]
    assert action["manifest_identity"] == identity
    assert action["owner"] == "artifact-review"
    assert action["references"] == {
        "coverage": "all-managed-record-roots",
        "incoming_generation_parents": [],
    }
    assert action["logical_bytes"] > len(b"eligible")
    assert action["reason"] == "retention-expired-and-no-incoming-generation-parent"
    assert "unmanaged" not in json.dumps(first)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("retention_class", "owner", "message"),
    [
        ("licensed-source", "data-build", "#335"),
        ("critical-full-corpus", "decomposition", "protected"),
        ("unknown-class", "artifact-review", "unknown retention"),
        ("unreferenced-bounded-derived", "unknown-owner", "owner"),
    ],
)
def test_cleanup_plan_refuses_protected_or_unknown_manifest_contracts(
    tmp_path: Path, retention_class: str, owner: str, message: str
) -> None:
    _policy(tmp_path)
    _publish(
        tmp_path,
        "candidate",
        retention_class=retention_class,
        owner=owner,
        expires_at=None,
    )
    with pytest.raises(ValueError, match=message):
        build_cleanup_plan(tmp_path)


@pytest.mark.unit
def test_cleanup_apply_revalidates_all_actions_before_first_mutation(
    tmp_path: Path,
) -> None:
    _policy(tmp_path)
    first, _identity = _publish(tmp_path, "first", content=b"first")
    second, _identity2 = _publish(tmp_path, "second", content=b"second")
    plan = build_cleanup_plan(tmp_path)
    plan_path = write_cleanup_plan(tmp_path, plan)
    (second / "artifacts/result.bin").write_bytes(b"drifted")

    with pytest.raises(ValueError, match="drift"):
        apply_cleanup_plan(tmp_path, plan_path, plan["plan_identity"])

    assert first.is_dir()
    assert second.is_dir()


@pytest.mark.unit
def test_cleanup_apply_rejects_identity_symlink_overlap_and_parent_reference(
    tmp_path: Path,
) -> None:
    _policy(tmp_path)
    directory, _identity = _publish(tmp_path, "candidate")
    plan = build_cleanup_plan(tmp_path)
    plan_path = write_cleanup_plan(tmp_path, plan)
    with pytest.raises(ValueError, match="identity"):
        apply_cleanup_plan(tmp_path, plan_path, "f" * 64)

    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    payload["actions"].append(
        {**payload["actions"][0], "path": payload["actions"][0]["path"] + "/artifacts"}
    )
    payload.pop("plan_identity")
    overlap = artifacts.bind_plan_identity(payload)
    overlap_path = write_cleanup_plan(tmp_path, overlap)
    with pytest.raises(ValueError, match="overlap"):
        apply_cleanup_plan(tmp_path, overlap_path, overlap["plan_identity"])

    (directory / "artifacts/result.bin").unlink()
    (directory / "artifacts/result.bin").symlink_to(tmp_path / "candidate.source")
    with pytest.raises(ValueError, match=r"symlink|drift"):
        apply_cleanup_plan(tmp_path, plan_path, plan["plan_identity"])


@pytest.mark.unit
def test_cleanup_apply_succeeds_for_temp_generation_with_exact_reclaimed_bytes(
    tmp_path: Path,
) -> None:
    _policy(tmp_path)
    directory, _identity = _publish(tmp_path, "candidate", content=b"reclaim me")
    plan = build_cleanup_plan(tmp_path)
    expected = plan["actions"][0]["logical_bytes"]
    plan_path = write_cleanup_plan(tmp_path, plan)

    report = apply_cleanup_plan(tmp_path, plan_path, plan["plan_identity"])

    assert report["status"] == "complete"
    assert report["reclaimed_logical_bytes"] == expected
    assert report["actions"][0]["status"] == "removed"
    assert not directory.exists()


@pytest.mark.unit
def test_cleanup_partial_failure_never_reports_false_completion(
    tmp_path: Path,
) -> None:
    _policy(tmp_path)
    first, _identity = _publish(tmp_path, "first")
    second, _identity2 = _publish(tmp_path, "second")
    plan = build_cleanup_plan(tmp_path)
    plan_path = write_cleanup_plan(tmp_path, plan)

    def fail_second(path: Path, managed_root: Path) -> None:
        if path == second:
            raise OSError("injected removal failure")
        artifacts._remove_generation_tree(path, managed_root)

    report = apply_cleanup_plan(
        tmp_path, plan_path, plan["plan_identity"], remover=fail_second
    )
    assert report["status"] == "partial-failure"
    assert [item["status"] for item in report["actions"]] == ["removed", "failed"]
    assert not first.exists()
    assert second.exists()


@pytest.mark.unit
def test_cleanup_safety_refusal_stops_later_actions_and_preserves_prior_results(
    tmp_path: Path,
) -> None:
    _policy(tmp_path)
    first, _identity = _publish(tmp_path, "first")
    second, _identity2 = _publish(tmp_path, "second")
    third, _identity3 = _publish(tmp_path, "third")
    plan = build_cleanup_plan(tmp_path)
    plan_path = write_cleanup_plan(tmp_path, plan)

    def refuse_second(path: Path, managed_root: Path) -> None:
        if path == second:
            raise ValueError("unsafe path refuses cleanup")
        artifacts._remove_generation_tree(path, managed_root)

    report = apply_cleanup_plan(
        tmp_path, plan_path, plan["plan_identity"], remover=refuse_second
    )

    assert report["status"] == "partial-failure"
    assert [item["status"] for item in report["actions"]] == [
        "removed",
        "refused",
        "not-attempted",
    ]
    assert report["actions"][1]["error"] == "unsafe path refuses cleanup"
    assert not first.exists()
    assert second.is_dir()
    assert third.is_dir()
    assert report["reclaimed_logical_bytes"] == plan["actions"][0]["logical_bytes"]


@pytest.mark.unit
def test_cleanup_remainder_measurement_never_masks_safety_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _policy(tmp_path)
    _publish(tmp_path, "candidate")
    plan = build_cleanup_plan(tmp_path)
    plan_path = write_cleanup_plan(tmp_path, plan)

    def refuse(_path: Path, _managed_root: Path) -> None:
        raise ValueError("primary safety refusal")

    original_measure = artifacts._directory_logical_bytes
    calls = 0

    def fail_second_measurement(path: Path) -> int:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise OSError("measurement failed")
        return original_measure(path)

    monkeypatch.setattr(
        artifacts,
        "_directory_logical_bytes",
        fail_second_measurement,
    )
    report = apply_cleanup_plan(
        tmp_path, plan_path, plan["plan_identity"], remover=refuse
    )

    assert report["status"] == "partial-failure"
    assert report["actions"][0]["status"] == "refused"
    assert report["actions"][0]["error"] == "primary safety refusal"
    assert report["actions"][0]["measurement_error"] == "measurement failed"


@pytest.mark.unit
def test_cleanup_partial_child_deletion_removes_completion_first_and_measures_remainder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _policy(tmp_path)
    directory, _identity = _publish(tmp_path, "candidate", content=b"payload")
    plan = build_cleanup_plan(tmp_path)
    plan_path = write_cleanup_plan(tmp_path, plan)
    original_unlink = Path.unlink
    removed_child = False

    def fail_after_child(path: Path, missing_ok: bool = False) -> None:
        nonlocal removed_child
        if path.name == "result.bin":
            original_unlink(path, missing_ok=missing_ok)
            removed_child = True
            return
        if removed_child and path.name == "manifest.json":
            raise OSError("injected after child")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_after_child)
    report = apply_cleanup_plan(tmp_path, plan_path, plan["plan_identity"])

    action = report["actions"][0]
    assert action["status"] == "partially-removed"
    assert not (directory / ".complete").exists()
    assert action["reclaimed_logical_bytes"] > 0
    assert action["remaining_logical_bytes"] > 0
    assert (
        action["reclaimed_logical_bytes"] + action["remaining_logical_bytes"]
        == plan["actions"][0]["logical_bytes"]
    )
    assert report["reclaimed_logical_bytes"] == action["reclaimed_logical_bytes"]


@pytest.mark.unit
def test_cleanup_rejects_rebound_outside_actions_before_mutation(
    tmp_path: Path,
) -> None:
    _policy(tmp_path)
    _publish(tmp_path, "candidate")
    plan = build_cleanup_plan(tmp_path)
    for malicious in ("data", "../outside"):
        payload = {**plan, "actions": [{**plan["actions"][0], "path": malicious}]}
        payload.pop("plan_identity")
        rebound = artifacts.bind_plan_identity(payload)
        path = write_cleanup_plan(tmp_path, rebound)
        called = False

        def remover(_path: Path, _root: Path) -> None:
            nonlocal called
            called = True

        with pytest.raises(ValueError, match=r"managed generation|drift|path"):
            apply_cleanup_plan(
                tmp_path, path, rebound["plan_identity"], remover=remover
            )
        assert called is False


@pytest.mark.unit
def test_generation_remover_rechecks_top_level_symlink_and_exact_root(
    tmp_path: Path,
) -> None:
    managed = tmp_path / "tmp/artifacts/v1/generations"
    outside = tmp_path / "outside"
    outside.mkdir()
    payload = outside / "keep.bin"
    payload.write_bytes(b"keep")
    family = managed / "family"
    family.mkdir(parents=True)
    link = family / "generation"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        artifacts._remove_generation_tree(link, managed)
    with pytest.raises(ValueError, match="managed generation"):
        artifacts._remove_generation_tree(outside, managed)
    assert payload.read_bytes() == b"keep"


@pytest.mark.unit
def test_cleanup_symlink_swap_after_global_preflight_refuses_before_tree_walk(
    tmp_path: Path,
) -> None:
    _policy(tmp_path)
    directory, _identity = _publish(tmp_path, "candidate")
    plan = build_cleanup_plan(tmp_path)
    plan_path = write_cleanup_plan(tmp_path, plan)
    outside = tmp_path / "outside"
    outside.mkdir()
    keep = outside / "keep"
    keep.write_bytes(b"keep")

    def swap_then_remove(path: Path, managed_root: Path) -> None:
        os.rename(path, path.with_name("original"))
        path.symlink_to(outside, target_is_directory=True)
        artifacts._remove_generation_tree(path, managed_root)

    report = apply_cleanup_plan(
        tmp_path, plan_path, plan["plan_identity"], remover=swap_then_remove
    )
    assert report["status"] == "partial-failure"
    assert report["actions"][0]["status"] == "refused"
    assert "symlink" in report["actions"][0]["error"]
    assert report["actions"][0]["remaining_logical_bytes"] is None
    assert keep.read_bytes() == b"keep"
    assert directory.is_symlink()


@pytest.mark.unit
def test_cleanup_requires_reached_manifest_expiry_and_blocks_no_expiry(
    tmp_path: Path,
) -> None:
    _policy(tmp_path)
    _publish(tmp_path, "past", expires_at="2000-01-01T00:00:00Z")
    _publish(tmp_path, "future", expires_at="2999-01-01T00:00:00Z")
    _publish(
        tmp_path,
        "referenced",
        retention_class="referenced-bounded-run",
        owner="decomposition",
        expires_at=None,
    )

    plan = build_cleanup_plan(tmp_path, now=datetime(2026, 1, 1, tzinfo=UTC))

    assert [action["path"].rsplit("/", 1)[-1] for action in plan["actions"]] == ["past"]

    other_root = tmp_path / "invalid"
    other_root.mkdir()
    _policy(other_root)
    _publish(other_root, "no-expiry", expires_at=None)
    with pytest.raises(ValueError, match="expiry differs"):
        build_cleanup_plan(other_root, now=datetime(2026, 1, 1, tzinfo=UTC))


@pytest.mark.unit
def test_inventory_reports_bounded_scan_errors_for_vanishing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _policy(tmp_path)
    root = tmp_path / "tmp/vanishing"
    root.mkdir(parents=True)
    vanished = root / "gone.bin"
    vanished.write_bytes(b"gone")
    path_type = type(vanished)
    original_lstat = path_type.lstat

    def unreliable_lstat(path: Path) -> os.stat_result:
        if path.name == vanished.name:
            raise FileNotFoundError("vanished during scan")
        return original_lstat(path)

    monkeypatch.setattr(path_type, "lstat", unreliable_lstat)
    report = inventory_repository(
        tmp_path,
        git_worktrees={"status": "empty", "entries": []},
        compose_resources={"status": "empty", "project": COMPOSE_PROJECT, "items": []},
    )
    summary = next(
        item
        for item in report["unmanaged_root_summaries"]
        if item["path"] == "tmp/vanishing"
    )
    assert summary["status"] == "error"
    assert summary["scan_errors"]["count"] == 1
    assert len(summary["scan_errors"]["entries"]) == 1
    assert report["ignored_usage"]["tmp"]["status"] == "error"


@pytest.mark.unit
def test_inventory_summarizes_cleanup_plan_registry_with_retention(
    tmp_path: Path,
) -> None:
    _policy(tmp_path)
    _publish(tmp_path, "candidate")
    written = write_cleanup_plan(tmp_path, build_cleanup_plan(tmp_path))

    report = inventory_repository(
        tmp_path,
        git_worktrees={"status": "empty", "entries": []},
        compose_resources={"status": "empty", "project": COMPOSE_PROJECT, "items": []},
    )
    entry = next(
        item
        for item in report["managed_records"]
        if item.get("record_type") == "managed-cleanup-plan-root"
    )
    assert entry["path"] == "tmp/artifacts/v1/cleanup-plans"
    assert entry["retention_class"] == "cleanup-plan"
    assert entry["owner"] == "artifact-review"
    assert entry["expiry_policy"] == "superseded-or-session-end"
    assert entry["files"] == 1
    assert entry["logical_bytes"] == written.stat().st_size


@pytest.mark.unit
def test_inventory_manages_superseded_unavailable_audit_without_expiry(
    tmp_path: Path,
) -> None:
    policy = load_retention_policy(_policy(tmp_path))
    audit = tmp_path / "tmp/artifacts/v1/unavailable/superseded/stale.json"
    audit.parent.mkdir(parents=True)
    audit.write_bytes(b"immutable stale record\n")

    report = inventory_repository(
        tmp_path,
        git_worktrees={"status": "empty", "entries": []},
        compose_resources={"status": "empty", "project": COMPOSE_PROJECT, "items": []},
    )

    entry = next(
        item
        for item in report["managed_records"]
        if item.get("record_type") == "managed-superseded-unavailable-audit-root"
    )
    assert entry["path"] == "tmp/artifacts/v1/unavailable/superseded"
    assert entry["retention_class"] == "superseded-unavailable-audit"
    assert entry["owner"] == "artifact-review"
    assert entry["expiry_policy"] == "none"
    assert entry["cleanup_eligible"] is False
    assert entry["files"] == 1
    assert entry["logical_bytes"] == audit.stat().st_size
    assert policy.classes["superseded-unavailable-audit"].cleanup_eligible is False
    assert build_cleanup_plan(tmp_path)["actions"] == []


@pytest.mark.unit
def test_cli_has_no_arbitrary_cleanup_target_and_requires_plan_identity() -> None:
    assert parser().parse_args(["inventory"]).command == "inventory"
    assert parser().parse_args(["plan"]).command == "plan"
    apply = parser().parse_args(
        ["apply", "--plan", "plan.json", "--identity", "a" * 64]
    )
    assert apply.plan == "plan.json"
    with pytest.raises(SystemExit):
        parser().parse_args(["apply", "tmp/unmanaged"])


@pytest.mark.unit
def test_artifact_contract_has_no_mutable_current_reference_or_cleanup_promotion() -> (
    None
):
    documentation = Path("docs/design/immutable-run-artifacts.md").read_text(
        encoding="utf-8"
    )
    normalized_documentation = " ".join(documentation.split())
    assert "There is no mutable `current` or `latest` reference" in documentation
    assert "exact parent manifest path and identity" in documentation
    assert "operator-action-required" in documentation
    assert "#335" in documentation
    assert "#274" in documentation
    assert "must call `publish_generation`" in normalized_documentation
    assert "nonempty tuple of exact `ParentManifestBinding`" in normalized_documentation
    assert not hasattr(artifact_publication, "publish_parent_bound_generation")
    with pytest.raises(SystemExit):
        parser().parse_args(["promote"])
    with pytest.raises(SystemExit):
        parser().parse_args(["prune"])
