"""Behavioral contracts for governed obsolete-artifact consolidation."""

from __future__ import annotations

import json
import os
import subprocess
from typing import TYPE_CHECKING

import pytest
import scripts.validation.run_agent_replay as replay
from scripts.validation.run_agent_replay import AgentReplayInputError, run_agent_replay

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

_GIT = "/usr/bin/git"


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed executable and fixture-owned arguments
        [_GIT, *arguments], cwd=root, check=True, capture_output=True, text=True
    ).stdout


def _repository(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.email", "agent@example.test")
    _git(root, "config", "user.name", "Agent Test")
    (root / ".gitignore").write_text("tmp/\n", encoding="utf-8")
    _git(root, "add", ".gitignore")
    _git(root, "commit", "-m", "test: initialize fixture")


def _write_manifest(
    root: Path,
    sources: Sequence[Mapping[str, object]],
    *,
    name: str = "manifest.json",
) -> tuple[Path, Path, list[str]]:
    plan = root / "tmp/plans/reviewed"
    plan.mkdir(parents=True, exist_ok=True)
    manifest = plan / name
    report = plan / "report.json"
    manifest.write_text(
        json.dumps({"schema_version": 1, "sources": sources}), encoding="utf-8"
    )
    return (
        manifest,
        report,
        [
            "consolidate-obsolete",
            f"tmp/plans/reviewed/{name}",
            "--report",
            "tmp/plans/reviewed/report.json",
        ],
    )


def _ignored_file(root: Path, relative: str, content: bytes = b"payload") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.mark.unit
def test_consolidate_obsolete_moves_files_trees_and_empty_directories_with_report(
    tmp_path: Path,
) -> None:
    _repository(tmp_path)
    plan = tmp_path / "tmp/plans/reviewed"
    plan.mkdir(parents=True)
    (tmp_path / "tmp/root.txt").write_bytes(b"root payload")
    tree = tmp_path / "tmp/nested/tree"
    (tree / "empty").mkdir(parents=True)
    (tree / "payload.bin").write_bytes(b"\x00tree payload")
    manifest = plan / "manifest.json"
    report = plan / "report.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": [
                    {"path": "tmp/root.txt"},
                    {"path": "tmp/nested/tree"},
                ],
            }
        ),
        encoding="utf-8",
    )

    assert (
        run_agent_replay(
            [
                "consolidate-obsolete",
                "tmp/plans/reviewed/manifest.json",
                "--report",
                "tmp/plans/reviewed/report.json",
            ],
            tmp_path,
        )
        == 0
    )

    assert not (tmp_path / "tmp/root.txt").exists()
    assert not tree.exists()
    assert (tmp_path / "tmp/obsolete/root/root.txt").read_bytes() == b"root payload"
    moved_tree = tmp_path / "tmp/obsolete/nested/tree"
    assert (moved_tree / "payload.bin").read_bytes() == b"\x00tree payload"
    assert (moved_tree / "empty").is_dir()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert [mapping["source"] for mapping in payload["mappings"]] == [
        "tmp/root.txt",
        "tmp/nested/tree",
    ]
    assert [mapping["destination"] for mapping in payload["mappings"]] == [
        "tmp/obsolete/root/root.txt",
        "tmp/obsolete/nested/tree",
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("manifest_text", "message"),
    [
        ('{"schema_version":1,"schema_version":1,"sources":[]}', "duplicate JSON"),
        ('{"schema_version":1,"sources":[],"command":"rm"}', "invalid schema"),
        ('{"schema_version":true,"sources":[]}', "invalid schema"),
        ('{"schema_version":1,"sources":"tmp/x"}', "non-empty ordered list"),
        (
            '{"schema_version":1,"sources":[{"path":"tmp/x","destination":"tmp/y"}]}',
            "source 0 has an invalid schema",
        ),
    ],
)
def test_consolidate_obsolete_rejects_non_strict_manifest_shapes(
    tmp_path: Path, manifest_text: str, message: str
) -> None:
    _repository(tmp_path)
    plan = tmp_path / "tmp/plans/reviewed"
    plan.mkdir(parents=True)
    (plan / "manifest.json").write_text(manifest_text, encoding="utf-8")

    with pytest.raises(AgentReplayInputError, match=message):
        run_agent_replay(
            [
                "consolidate-obsolete",
                "tmp/plans/reviewed/manifest.json",
                "--report",
                "tmp/plans/reviewed/report.json",
            ],
            tmp_path,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    ["//absolute", "tmp/../escape", "tmp/*.json", "outside/file"],
)
def test_consolidate_obsolete_rejects_unsafe_or_outside_source_paths(
    tmp_path: Path, source: str
) -> None:
    _repository(tmp_path)
    _manifest, _report, arguments = _write_manifest(tmp_path, [{"path": source}])

    with pytest.raises(AgentReplayInputError):
        run_agent_replay(arguments, tmp_path)


@pytest.mark.unit
def test_consolidate_obsolete_requires_manifest_and_report_in_same_plan_directory(
    tmp_path: Path,
) -> None:
    _repository(tmp_path)
    _ignored_file(tmp_path, "tmp/item")
    _manifest, _report, arguments = _write_manifest(tmp_path, [{"path": "tmp/item"}])

    for unsafe_arguments in (
        ["consolidate-obsolete", "tmp/item"],
        [
            "consolidate-obsolete",
            "tmp/plans/reviewed/manifest.json",
            "--report",
            "tmp/plans/other/report.json",
        ],
        [
            "consolidate-obsolete",
            "tmp/plans/reviewed/manifest.json",
            "--report",
            "tmp/plans/reviewed/manifest.json",
        ],
    ):
        with pytest.raises(AgentReplayInputError):
            run_agent_replay(unsafe_arguments, tmp_path)
    assert (tmp_path / "tmp/item").exists()
    assert arguments


@pytest.mark.unit
@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("tmp/obsolete/already", "already obsolete"),
        ("tmp/plans/reviewed/artifact", "cleanup-plan directory"),
        ("tmp/missing", "missing or has wrong kind"),
    ],
)
def test_consolidate_obsolete_rejects_forbidden_or_missing_sources(
    tmp_path: Path, source: str, message: str
) -> None:
    _repository(tmp_path)
    _manifest, _report, arguments = _write_manifest(tmp_path, [{"path": source}])
    if source != "tmp/missing":
        _ignored_file(tmp_path, source)

    with pytest.raises(AgentReplayInputError, match=message):
        run_agent_replay(arguments, tmp_path)


@pytest.mark.unit
def test_consolidate_obsolete_rejects_wrong_source_kind(tmp_path: Path) -> None:
    _repository(tmp_path)
    (tmp_path / "tmp").mkdir()
    fifo = tmp_path / "tmp/pipe"
    os.mkfifo(fifo)
    _manifest, _report, arguments = _write_manifest(tmp_path, [{"path": "tmp/pipe"}])

    with pytest.raises(AgentReplayInputError, match="wrong kind"):
        run_agent_replay(arguments, tmp_path)

    assert fifo.exists()


@pytest.mark.unit
def test_consolidate_obsolete_rejects_symlink_component_and_symlink_in_tree(
    tmp_path: Path,
) -> None:
    _repository(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp/link").symlink_to(outside, target_is_directory=True)
    _manifest, _report, arguments = _write_manifest(
        tmp_path, [{"path": "tmp/link/item"}]
    )
    with pytest.raises(AgentReplayInputError, match="symlink"):
        run_agent_replay(arguments, tmp_path)

    (tmp_path / "tmp/link").unlink()
    tree = tmp_path / "tmp/tree"
    tree.mkdir()
    (tree / "link").symlink_to(outside, target_is_directory=True)
    _manifest, _report, arguments = _write_manifest(tmp_path, [{"path": "tmp/tree"}])
    with pytest.raises(AgentReplayInputError, match="symlink"):
        run_agent_replay(arguments, tmp_path)


@pytest.mark.unit
def test_consolidate_obsolete_rejects_duplicate_and_overlapping_sources(
    tmp_path: Path,
) -> None:
    _repository(tmp_path)
    _ignored_file(tmp_path, "tmp/tree/child")
    for sources in (
        [{"path": "tmp/tree"}, {"path": "tmp/tree"}],
        [{"path": "tmp/tree"}, {"path": "tmp/tree/child"}],
    ):
        _manifest, _report, arguments = _write_manifest(tmp_path, sources)
        with pytest.raises(AgentReplayInputError, match="duplicate or overlap"):
            run_agent_replay(arguments, tmp_path)
        assert (tmp_path / "tmp/tree/child").exists()


@pytest.mark.unit
def test_consolidate_obsolete_preflights_every_destination_before_moving(
    tmp_path: Path,
) -> None:
    _repository(tmp_path)
    first = _ignored_file(tmp_path, "tmp/first")
    second = _ignored_file(tmp_path, "tmp/nested/second")
    _ignored_file(tmp_path, "tmp/obsolete/nested/second", b"conflict")
    _manifest, _report, arguments = _write_manifest(
        tmp_path, [{"path": "tmp/first"}, {"path": "tmp/nested/second"}]
    )

    with pytest.raises(AgentReplayInputError, match="destination already exists"):
        run_agent_replay(arguments, tmp_path)

    assert first.exists()
    assert second.exists()
    assert not (tmp_path / "tmp/obsolete/root/first").exists()


@pytest.mark.unit
def test_consolidate_obsolete_refuses_cross_device_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repository(tmp_path)
    source = _ignored_file(tmp_path, "tmp/item")
    _manifest, _report, arguments = _write_manifest(tmp_path, [{"path": "tmp/item"}])
    monkeypatch.setattr(replay, "_same_filesystem", lambda *_paths: False)

    with pytest.raises(AgentReplayInputError, match="cross-device"):
        run_agent_replay(arguments, tmp_path)

    assert source.exists()


@pytest.mark.unit
def test_consolidate_obsolete_rejects_source_destination_overlap(
    tmp_path: Path,
) -> None:
    _repository(tmp_path)
    _ignored_file(tmp_path, "tmp/item")
    _manifest, _report, arguments = _write_manifest(tmp_path, [{"path": "tmp"}])

    with pytest.raises(AgentReplayInputError, match="source and destination overlap"):
        run_agent_replay(arguments, tmp_path)

    assert (tmp_path / "tmp/item").exists()


@pytest.mark.unit
def test_consolidate_obsolete_requires_untracked_and_git_ignored_sources(
    tmp_path: Path,
) -> None:
    _repository(tmp_path)
    nonignored = _ignored_file(tmp_path, "local/item")
    _manifest, _report, arguments = _write_manifest(tmp_path, [{"path": "local/item"}])
    with pytest.raises(AgentReplayInputError, match="under tmp"):
        run_agent_replay(arguments, tmp_path)
    assert nonignored.exists()

    (tmp_path / ".gitignore").write_text("tmp/plans/\n", encoding="utf-8")
    unignored = _ignored_file(tmp_path, "tmp/unignored")
    _manifest, _report, arguments = _write_manifest(
        tmp_path, [{"path": "tmp/unignored"}]
    )
    with pytest.raises(AgentReplayInputError, match="not ignored"):
        run_agent_replay(arguments, tmp_path)
    assert unignored.exists()

    (tmp_path / ".gitignore").write_text("tmp/\n", encoding="utf-8")
    tracked = _ignored_file(tmp_path, "tmp/tracked")
    _git(tmp_path, "add", "-f", "tmp/tracked")
    _git(tmp_path, "commit", "-m", "test: track source")
    _manifest, _report, arguments = _write_manifest(tmp_path, [{"path": "tmp/tracked"}])
    with pytest.raises(AgentReplayInputError, match="tracked by Git"):
        run_agent_replay(arguments, tmp_path)
    assert tracked.exists()


@pytest.mark.unit
def test_consolidate_obsolete_requires_duplicate_of_byte_tree_equality(
    tmp_path: Path,
) -> None:
    _repository(tmp_path)
    source = _ignored_file(tmp_path, "tmp/copy/tree/file", b"same")
    canonical = _ignored_file(tmp_path, ".tools/canonical/file", b"same")
    _manifest, _report, arguments = _write_manifest(
        tmp_path,
        [{"path": "tmp/copy/tree", "duplicate_of": ".tools/canonical"}],
    )
    assert run_agent_replay(arguments, tmp_path) == 0
    assert not source.exists()
    assert canonical.exists()

    report = tmp_path / "tmp/plans/reviewed/report.json"
    report.unlink()
    moved = tmp_path / "tmp/obsolete/copy/tree"
    moved.rename(tmp_path / "tmp/copy/tree")
    canonical.write_bytes(b"different")
    with pytest.raises(AgentReplayInputError, match="duplicate_of differs"):
        run_agent_replay(arguments, tmp_path)
    assert (tmp_path / "tmp/copy/tree/file").exists()


@pytest.mark.unit
def test_consolidate_obsolete_rolls_back_moves_and_created_parents_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repository(tmp_path)
    first = _ignored_file(tmp_path, "tmp/a/first")
    second = _ignored_file(tmp_path, "tmp/b/second")
    _manifest, _report, arguments = _write_manifest(
        tmp_path, [{"path": "tmp/a/first"}, {"path": "tmp/b/second"}]
    )
    real_rename = replay._rename_artifact
    calls = 0

    def fail_second_move(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected move failure")
        real_rename(source, destination)

    monkeypatch.setattr(replay, "_rename_artifact", fail_second_move)
    with pytest.raises(OSError, match="injected move failure"):
        run_agent_replay(arguments, tmp_path)

    assert first.exists()
    assert second.exists()
    assert not (tmp_path / "tmp/obsolete").exists()


@pytest.mark.unit
def test_consolidate_obsolete_reports_incomplete_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repository(tmp_path)
    _ignored_file(tmp_path, "tmp/first")
    _ignored_file(tmp_path, "tmp/second")
    _manifest, _report, arguments = _write_manifest(
        tmp_path, [{"path": "tmp/first"}, {"path": "tmp/second"}]
    )
    real_rename = replay._rename_artifact
    calls = 0

    def fail_move_and_rollback(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise OSError(f"injected rename failure {calls}")
        real_rename(source, destination)

    monkeypatch.setattr(replay, "_rename_artifact", fail_move_and_rollback)
    with pytest.raises(OSError, match="injected rename failure") as raised:
        run_agent_replay(arguments, tmp_path)

    assert any("rollback incomplete" in note for note in raised.value.__notes__)


@pytest.mark.unit
def test_consolidate_obsolete_refuses_manifest_drift_before_first_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repository(tmp_path)
    source = _ignored_file(tmp_path, "tmp/item")
    manifest, _report, arguments = _write_manifest(tmp_path, [{"path": "tmp/item"}])
    original = replay._read_manifest_bytes
    reads = 0

    def drift_on_second_read(path: Path) -> bytes:
        nonlocal reads
        reads += 1
        if reads == 2:
            path.write_bytes(path.read_bytes() + b"\n")
        return original(path)

    monkeypatch.setattr(replay, "_read_manifest_bytes", drift_on_second_read)
    with pytest.raises(AgentReplayInputError, match="manifest changed"):
        run_agent_replay(arguments, tmp_path)

    assert source.exists()
    assert manifest.read_bytes().endswith(b"\n")


@pytest.mark.unit
def test_consolidate_obsolete_report_binds_identity_bytes_head_and_is_idempotent(
    tmp_path: Path,
) -> None:
    _repository(tmp_path)
    _ignored_file(tmp_path, "tmp/item", b"identity")
    manifest, report, arguments = _write_manifest(tmp_path, [{"path": "tmp/item"}])

    assert run_agent_replay(arguments, tmp_path) == 0
    first_report = report.read_bytes()
    payload = json.loads(first_report)
    mapping = payload["mappings"][0]
    assert payload["git_head"] == _git(tmp_path, "rev-parse", "HEAD").strip()
    assert payload["manifest"]["sha256"]
    assert mapping["pre"] == mapping["post"]
    assert mapping["pre"]["logical_bytes"] == len(b"identity")
    assert mapping["pre"]["entries"][0]["sha256"]
    assert payload["totals"]["logical_bytes"] == len(b"identity")

    assert run_agent_replay(arguments, tmp_path) == 0
    assert report.read_bytes() == first_report
    assert manifest.exists()


@pytest.mark.unit
def test_consolidate_obsolete_refuses_existing_report_or_changed_completed_destination(
    tmp_path: Path,
) -> None:
    _repository(tmp_path)
    source = _ignored_file(tmp_path, "tmp/item", b"original")
    _manifest, report, arguments = _write_manifest(tmp_path, [{"path": "tmp/item"}])
    report.write_text("{}", encoding="utf-8")
    with pytest.raises(AgentReplayInputError, match="invalid schema"):
        run_agent_replay(arguments, tmp_path)
    assert source.exists()

    report.unlink()
    assert run_agent_replay(arguments, tmp_path) == 0
    destination = tmp_path / "tmp/obsolete/root/item"
    destination.write_bytes(b"changed")
    with pytest.raises(AgentReplayInputError, match="differs from report"):
        run_agent_replay(arguments, tmp_path)


@pytest.mark.unit
def test_consolidate_obsolete_cli_help_and_main_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run_agent_replay(["consolidate-obsolete", "--help"], tmp_path) == 0
    help_text = capsys.readouterr().out
    assert "manual deletion" in help_text
    assert "does not delete" in help_text

    calls: list[tuple[list[str], Path]] = []

    def dispatched(arguments: list[str], root: Path) -> int:
        calls.append((arguments, root))
        return 0

    monkeypatch.setattr(replay, "run_agent_replay", dispatched)
    monkeypatch.setattr(
        replay.sys,
        "argv",
        [
            "run_agent_replay.py",
            "consolidate-obsolete",
            "tmp/plans/x/manifest.json",
            "--report",
            "tmp/plans/x/report.json",
        ],
    )
    assert replay.main() == 0
    assert calls[0][0][0] == "consolidate-obsolete"
