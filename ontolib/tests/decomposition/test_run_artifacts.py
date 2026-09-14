from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ontolib.decomposition import run_artifacts
from ontolib.decomposition.run_artifacts import (
    ArtifactConflictError,
    ArtifactManifest,
    ArtifactPathError,
    ArtifactRecord,
    ArtifactUnavailableRecord,
    GeneratorBinding,
    ParentManifestBinding,
    PartialGenerationError,
    RetentionBinding,
    SourceIdentity,
    load_artifact_record,
    publish_generation,
    publish_parent_bound_generation,
    reconcile_missing_unavailable_references,
    resolve_parent_manifest,
    write_legacy_in_place_manifest,
    write_unavailable_record,
)

RUN_ID = "neoplasm-0b00326b-6a9f-424f-b074-d4f1f8a0304d"


def _publish(
    root: Path, generation_id: str, content: bytes = b"one"
) -> ArtifactManifest:
    source = root / f"{generation_id}.ttl"
    source.write_bytes(content)
    return publish_generation(
        artifacts_root=root / "artifacts",
        family="m1-6-current-replay",
        generation_id=generation_id,
        run_id=RUN_ID,
        artifact_sources={"artifacts/decomposition.ttl": source},
        parents=(),
        generator=GeneratorBinding(identity="git:abc", command=("decompose-current",)),
        sources=(SourceIdentity(name="ncit", identity="sha256:" + "a" * 64),),
        retention=RetentionBinding(
            retention_class="referenced-bounded-run",
            owner="decomposition",
            expires_at=None,
        ),
    )


@pytest.mark.unit
def test_bounded_generations_are_create_only_and_coexist(tmp_path: Path) -> None:
    first = _publish(tmp_path, "generation-one")
    first_dir = tmp_path / "artifacts/m1-6-current-replay/generation-one"
    before = (first_dir / "artifacts/decomposition.ttl").read_bytes()

    assert _publish(tmp_path, "generation-one") == first
    second = _publish(tmp_path, "generation-two", b"two")

    assert before == (first_dir / "artifacts/decomposition.ttl").read_bytes()
    assert second.generation_id == "generation-two"
    assert (
        tmp_path / "artifacts/m1-6-current-replay/generation-two/.complete"
    ).is_file()


@pytest.mark.unit
def test_conflicting_generation_refuses_without_mutation(tmp_path: Path) -> None:
    _publish(tmp_path, "fixed", b"original")
    final = tmp_path / "artifacts/m1-6-current-replay/fixed"
    before = {
        path.relative_to(final): path.read_bytes()
        for path in final.rglob("*")
        if path.is_file()
    }

    with pytest.raises(ArtifactConflictError, match="differs"):
        _publish(tmp_path, "fixed", b"replacement")

    after = {
        path.relative_to(final): path.read_bytes()
        for path in final.rglob("*")
        if path.is_file()
    }
    assert after == before


@pytest.mark.unit
def test_parent_manifest_refuses_partial_deletion_and_substitution(
    tmp_path: Path,
) -> None:
    manifest = _publish(tmp_path, "parent")
    directory = tmp_path / "artifacts/m1-6-current-replay/parent"
    marker = directory / ".complete"
    marker.unlink()
    with pytest.raises(PartialGenerationError):
        resolve_parent_manifest(directory / "manifest.json", manifest.manifest_identity)

    marker.write_text(manifest.manifest_identity + "\n", encoding="ascii")
    with pytest.raises(ArtifactConflictError, match="identity"):
        resolve_parent_manifest(directory / "manifest.json", "f" * 64)

    (directory / "manifest.json").unlink()
    with pytest.raises(PartialGenerationError):
        resolve_parent_manifest(directory / "manifest.json", manifest.manifest_identity)


@pytest.mark.unit
def test_interrupted_markerless_generation_is_refused_and_never_adopted(
    tmp_path: Path,
) -> None:
    final = tmp_path / "artifacts/m1-6-current-replay/interrupted"
    artifact = final / "artifacts/decomposition.ttl"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"interrupted bytes")
    before = artifact.read_bytes()

    with pytest.raises(PartialGenerationError, match="incomplete"):
        _publish(tmp_path, "interrupted", b"replacement")

    assert artifact.read_bytes() == before
    assert not (final / ".complete").exists()
    assert not (final / "manifest.json").exists()


@pytest.mark.unit
def test_unavailable_record_is_never_loaded_as_a_manifest(tmp_path: Path) -> None:
    path = tmp_path / "unavailable.json"
    record = ArtifactUnavailableRecord(
        schema_version=1,
        record_type="unavailable-artifact",
        family="m1-6-current-replay",
        run_id="neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997",
        expected_sha256="4febb77cb0e0b91418a22a08c19d9fa05d65529f00af30e85afe53a8d716424d",
        last_known_path="tmp/m1-6-current-replay.ttl",
        reason="overwritten-before-immutable-retention",
        references=("pre274-observation-summary",),
    )
    write_unavailable_record(path, record)

    loaded = load_artifact_record(path)
    assert isinstance(loaded, ArtifactUnavailableRecord)
    assert not isinstance(loaded, ArtifactManifest)
    assert "artifact_records" not in json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.unit
def test_manifest_identity_excludes_only_identity_and_has_no_timestamp(
    tmp_path: Path,
) -> None:
    manifest = _publish(tmp_path, "canonical")
    payload = manifest.to_dict()

    assert "timestamp" not in payload
    assert "manifest_identity" not in manifest.identity_payload()
    assert ArtifactManifest.from_dict(payload) == manifest


@pytest.mark.unit
def test_manifest_binds_parents_and_rejects_identity_run_and_source_drift(
    tmp_path: Path,
) -> None:
    parent = _publish(tmp_path, "bound-parent")
    binding = ParentManifestBinding(
        family=parent.family,
        generation_id=parent.generation_id,
        manifest_path=f"{parent.family}/{parent.generation_id}/manifest.json",
        manifest_identity=parent.manifest_identity,
    )
    child_source = tmp_path / "child.ttl"
    child_source.write_bytes(b"child")
    child = publish_generation(
        artifacts_root=tmp_path / "artifacts",
        family="m1-6-current-replay",
        generation_id="bound-child",
        run_id=RUN_ID,
        artifact_sources={"artifacts/decomposition.ttl": child_source},
        parents=(binding,),
        generator=GeneratorBinding(identity="git:abc", command=("child",)),
        sources=(SourceIdentity(name="ncit", identity="source"),),
        retention=RetentionBinding(
            retention_class="referenced-bounded-run",
            owner="tests",
            expires_at=None,
        ),
    )

    assert ArtifactManifest.from_dict(child.to_dict()).parents == (binding,)
    with pytest.raises(ArtifactConflictError, match="canonical content"):
        ArtifactManifest.from_dict({**child.to_dict(), "family": "changed-family"})
    with pytest.raises(ValueError, match="run ID"):
        ArtifactManifest.create(
            family="m1-6-current-replay",
            generation_id="bad-run",
            run_id="invalid-run",
            parents=(),
            generator=GeneratorBinding(identity="git:abc", command=("test",)),
            sources=(),
            artifact_records=child.artifact_records,
            retention=child.retention,
        )
    with pytest.raises(ValueError, match="unique"):
        ArtifactManifest.create(
            family="m1-6-current-replay",
            generation_id="duplicate-source",
            run_id=RUN_ID,
            parents=(),
            generator=GeneratorBinding(identity="git:abc", command=("test",)),
            sources=(
                SourceIdentity(name="ncit", identity="one"),
                SourceIdentity(name="ncit", identity="two"),
            ),
            artifact_records=child.artifact_records,
            retention=child.retention,
        )


@pytest.mark.unit
@pytest.mark.parametrize("collection", ["parents", "sources", "artifact_records"])
def test_manifest_reader_rejects_non_array_collections(
    tmp_path: Path, collection: str
) -> None:
    payload = _publish(tmp_path, f"invalid-{collection}").to_dict()
    payload[collection] = "not-an-array"

    with pytest.raises(ValueError, match="array"):
        ArtifactManifest.from_dict(payload)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("loader", "payload"),
    [
        (ArtifactRecord.from_dict, []),
        (
            ArtifactRecord.from_dict,
            {"relative_path": "artifact.ttl", "size": "3", "sha256": "a" * 64},
        ),
        (
            ArtifactRecord.from_dict,
            {"relative_path": "artifact.ttl", "size": -1, "sha256": "a" * 64},
        ),
        (ParentManifestBinding.from_dict, []),
        (
            ParentManifestBinding.from_dict,
            {
                "family": "family",
                "generation_id": "generation",
                "manifest_path": "manifest.json",
                "manifest_identity": "invalid",
            },
        ),
        (GeneratorBinding.from_dict, []),
        (GeneratorBinding.from_dict, {"identity": "git:abc", "command": [1]}),
        (SourceIdentity.from_dict, []),
        (SourceIdentity.from_dict, {"name": "ncit", "identity": 1}),
        (RetentionBinding.from_dict, []),
        (
            RetentionBinding.from_dict,
            {
                "class": "critical",
                "owner": "team",
                "no_expiry": "yes",
                "expires_at": None,
            },
        ),
        (
            RetentionBinding.from_dict,
            {
                "class": "critical",
                "owner": "team",
                "no_expiry": False,
                "expires_at": None,
            },
        ),
    ],
)
def test_nested_manifest_records_fail_closed_on_malformed_source_data(
    loader: Callable[[object], object], payload: object
) -> None:
    with pytest.raises(
        ValueError, match=r"object|types|invalid|strings|boolean|disagree"
    ):
        loader(payload)


@pytest.mark.unit
def test_strict_artifact_models_refuse_coercion_and_unknown_fields() -> None:
    with pytest.raises(ValueError, match="valid integer"):
        ArtifactRecord.model_validate(
            {"relative_path": "artifact.ttl", "size": "3", "sha256": "a" * 64}
        )
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        GeneratorBinding.model_validate(
            {"identity": "git:abc", "command": ("generate",), "unexpected": True}
        )


@pytest.mark.unit
def test_record_loader_refuses_malformed_nonobject_and_unknown_documents(
    tmp_path: Path,
) -> None:
    path = tmp_path / "record.json"
    for content, message in (
        ("not-json", "cannot be read"),
        ("[]", "must be an object"),
        ('{"record_type":"unknown"}', "unknown artifact record type"),
    ):
        path.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            load_artifact_record(path)


@pytest.mark.unit
def test_unavailable_record_reader_rejects_invalid_evidence_fields() -> None:
    valid = ArtifactUnavailableRecord(
        schema_version=1,
        record_type="unavailable-artifact",
        family="m1-6-current-replay",
        run_id="neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997",
        expected_sha256="4febb77cb0e0b91418a22a08c19d9fa05d65529f00af30e85afe53a8d716424d",
        last_known_path="tmp/m1-6-current-replay.ttl",
        reason="overwritten-before-immutable-retention",
        references=("pre274-observation-summary",),
    ).to_dict()

    with pytest.raises(ValueError, match="must be an object"):
        ArtifactUnavailableRecord.from_dict([])
    with pytest.raises(ValueError, match="references must be strings"):
        ArtifactUnavailableRecord.from_dict({**valid, "references": [1]})
    with pytest.raises(ValueError, match="run or digest"):
        ArtifactUnavailableRecord.from_dict({**valid, "run_id": "invalid"})
    with pytest.raises(ValueError, match="run or digest"):
        ArtifactUnavailableRecord.from_dict({**valid, "expected_sha256": "invalid"})
    with pytest.raises(ValueError, match="references are invalid"):
        ArtifactUnavailableRecord.from_dict({**valid, "references": [""]})


@pytest.mark.unit
def test_artifact_paths_reject_traversal_controls_duplicates_and_symlinks(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.ttl"
    source.write_bytes(b"safe")
    link = tmp_path / "link.ttl"
    link.symlink_to(source)

    for relative in ("../escape.ttl", "/absolute.ttl", "a/../../escape", "bad\nname"):
        with pytest.raises(ArtifactPathError):
            publish_generation(
                artifacts_root=tmp_path / "artifacts",
                family="m1-6-current-replay",
                generation_id="unsafe",
                run_id=RUN_ID,
                artifact_sources={relative: source},
                parents=(),
                generator=GeneratorBinding(identity="git:abc", command=("test",)),
                sources=(),
                retention=RetentionBinding(
                    retention_class="referenced-bounded-run",
                    owner="tests",
                    expires_at=None,
                ),
            )
    with pytest.raises(ArtifactPathError, match="symlink"):
        publish_generation(
            artifacts_root=tmp_path / "artifacts",
            family="m1-6-current-replay",
            generation_id="symlink",
            run_id=RUN_ID,
            artifact_sources={"artifacts/decomposition.ttl": link},
            parents=(),
            generator=GeneratorBinding(identity="git:abc", command=("test",)),
            sources=(),
            retention=RetentionBinding(
                retention_class="referenced-bounded-run",
                owner="tests",
                expires_at=None,
            ),
        )
    with pytest.raises(ArtifactPathError, match="duplicate"):
        ArtifactManifest.from_dict(
            {
                **_publish(tmp_path, "valid").to_dict(),
                "artifact_records": [
                    {
                        "relative_path": "artifacts/decomposition.ttl",
                        "size": 3,
                        "sha256": "a" * 64,
                    },
                    {
                        "relative_path": "artifacts/decomposition.ttl",
                        "size": 3,
                        "sha256": "a" * 64,
                    },
                ],
            }
        )
    with pytest.raises(ArtifactPathError, match="duplicate"):
        publish_generation(
            artifacts_root=tmp_path / "artifacts",
            family="m1-6-current-replay",
            generation_id="normalized-duplicate",
            run_id=RUN_ID,
            artifact_sources={"a/file.ttl": source, "a//file.ttl": source},
            parents=(),
            generator=GeneratorBinding(identity="git:abc", command=("test",)),
            sources=(),
            retention=RetentionBinding(
                retention_class="referenced-bounded-run",
                owner="tests",
                expires_at=None,
            ),
        )
    directory = tmp_path / "directory-source"
    directory.mkdir()
    with pytest.raises(ArtifactPathError, match="regular file"):
        publish_generation(
            artifacts_root=tmp_path / "artifacts",
            family="m1-6-current-replay",
            generation_id="directory-source",
            run_id=RUN_ID,
            artifact_sources={"artifact.ttl": directory},
            parents=(),
            generator=GeneratorBinding(identity="git:abc", command=("test",)),
            sources=(),
            retention=RetentionBinding(
                retention_class="referenced-bounded-run",
                owner="tests",
                expires_at=None,
            ),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "relative",
    ["manifest.json", ".complete", ".create-only-claim", ".claims/owner"],
)
def test_artifact_paths_reject_generation_control_names_before_publication(
    tmp_path: Path, relative: str
) -> None:
    source = tmp_path / "source.ttl"
    source.write_bytes(b"safe")
    final = tmp_path / "artifacts/m1-6-current-replay/reserved"

    with pytest.raises(ArtifactPathError, match="reserved"):
        publish_generation(
            artifacts_root=tmp_path / "artifacts",
            family="m1-6-current-replay",
            generation_id="reserved",
            run_id=RUN_ID,
            artifact_sources={relative: source},
            parents=(),
            generator=GeneratorBinding(identity="git:abc", command=("test",)),
            sources=(),
            retention=RetentionBinding(
                retention_class="referenced-bounded-run",
                owner="tests",
                expires_at=None,
            ),
        )

    assert not final.exists()


@pytest.mark.unit
def test_source_mutation_before_staging_cannot_publish_mismatched_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "mutable.ttl"
    source.write_bytes(b"before")
    original_stage = run_artifacts._stage_artifacts

    def mutate_then_stage(staging: Path, sources: list[tuple[str, Path]]) -> None:
        source.write_bytes(b"after")
        original_stage(staging, sources)

    monkeypatch.setattr(run_artifacts, "_stage_artifacts", mutate_then_stage)

    manifest = publish_generation(
        artifacts_root=tmp_path / "artifacts",
        family="m1-6-current-replay",
        generation_id="source-mutated",
        run_id=RUN_ID,
        artifact_sources={"artifacts/decomposition.ttl": source},
        parents=(),
        generator=GeneratorBinding(identity="git:abc", command=("test",)),
        sources=(),
        retention=RetentionBinding(
            retention_class="referenced-bounded-run",
            owner="tests",
            expires_at=None,
        ),
    )
    final = tmp_path / "artifacts/m1-6-current-replay/source-mutated"
    resolved = resolve_parent_manifest(
        final / "manifest.json", manifest.manifest_identity
    )

    assert (final / "artifacts/decomposition.ttl").read_bytes() == b"after"
    assert resolved == manifest


@pytest.mark.unit
def test_concurrent_publishers_have_one_create_and_identical_idempotence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "concurrent-source.ttl"
    source.write_bytes(b"one")

    def publish() -> ArtifactManifest:
        return publish_generation(
            artifacts_root=tmp_path / "artifacts",
            family="m1-6-current-replay",
            generation_id="concurrent",
            run_id=RUN_ID,
            artifact_sources={"artifacts/decomposition.ttl": source},
            parents=(),
            generator=GeneratorBinding(
                identity="git:abc", command=("decompose-current",)
            ),
            sources=(SourceIdentity(name="ncit", identity="sha256:" + "a" * 64),),
            retention=RetentionBinding(
                retention_class="referenced-bounded-run",
                owner="decomposition",
                expires_at=None,
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: publish(), range(2)))

    assert results[0] == results[1]
    final = tmp_path / "artifacts/m1-6-current-replay/concurrent"
    assert [path.name for path in final.iterdir()].count("manifest.json") == 1
    assert (final / ".complete").read_text(encoding="ascii").strip() == results[
        0
    ].manifest_identity


@pytest.mark.unit
def test_real_filesystem_publication_is_complete_after_reopen(tmp_path: Path) -> None:
    manifest = _publish(tmp_path, "durable", b"durable bytes")
    manifest_path = tmp_path / "artifacts/m1-6-current-replay/durable/manifest.json"

    reopened = resolve_parent_manifest(manifest_path, manifest.manifest_identity)

    assert reopened.artifact_records[0].size == len(b"durable bytes")
    assert reopened.parents == ()


@pytest.mark.unit
@pytest.mark.parametrize("tamper", ["marker", "artifact", "inventory"])
def test_completed_generation_rejects_persisted_tampering(
    tmp_path: Path, tamper: str
) -> None:
    manifest = _publish(tmp_path, f"tampered-{tamper}", b"durable bytes")
    directory = tmp_path / f"artifacts/m1-6-current-replay/tampered-{tamper}"
    if tamper == "marker":
        (directory / ".complete").write_text("f" * 64 + "\n", encoding="ascii")
    elif tamper == "artifact":
        (directory / "artifacts/decomposition.ttl").write_bytes(b"changed")
    else:
        (directory / "unexpected.txt").write_text("unexpected", encoding="utf-8")

    with pytest.raises(ArtifactConflictError):
        resolve_parent_manifest(directory / "manifest.json", manifest.manifest_identity)


@pytest.mark.unit
def test_unavailable_record_is_create_only_and_conflicts_on_changed_evidence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "unavailable.json"
    record = ArtifactUnavailableRecord(
        schema_version=1,
        record_type="unavailable-artifact",
        family="m1-6-current-replay",
        run_id="neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997",
        expected_sha256="4febb77cb0e0b91418a22a08c19d9fa05d65529f00af30e85afe53a8d716424d",
        last_known_path="tmp/m1-6-current-replay.ttl",
        reason="overwritten-before-immutable-retention",
        references=("pre274-observation-summary",),
    )
    write_unavailable_record(path, record)
    write_unavailable_record(path, record)
    path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ArtifactConflictError, match="differs"):
        write_unavailable_record(path, record)


@pytest.mark.unit
def test_unavailable_reference_reconciliation_is_cas_audited_and_idempotent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "unavailable/run.json"
    stale = ArtifactUnavailableRecord(
        schema_version=1,
        record_type="unavailable-artifact",
        family="m1-6-current-replay",
        run_id="neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997",
        expected_sha256="4febb77cb0e0b91418a22a08c19d9fa05d65529f00af30e85afe53a8d716424d",
        last_known_path="tmp/m1-6-current-replay.ttl",
        reason="overwritten-before-immutable-retention",
        references=(),
    )
    corrected = stale.model_copy(update={"references": ("known-reference",)})
    write_unavailable_record(path, stale)
    stale_bytes = path.read_bytes()

    audit = reconcile_missing_unavailable_references(
        path=path, expected_stale=stale, corrected=corrected
    )
    assert audit is not None
    corrected_bytes = path.read_bytes()
    assert load_artifact_record(path) == corrected
    assert audit.read_bytes() == stale_bytes
    assert audit.name == f"{__import__('hashlib').sha256(stale_bytes).hexdigest()}.json"
    assert (
        reconcile_missing_unavailable_references(
            path=path, expected_stale=stale, corrected=corrected
        )
        == audit
    )
    assert path.read_bytes() == corrected_bytes


@pytest.mark.unit
def test_unavailable_reference_reconciliation_failure_preserves_complete_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "unavailable/run.json"
    stale = ArtifactUnavailableRecord(
        schema_version=1,
        record_type="unavailable-artifact",
        family="m1-6-current-replay",
        run_id="neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997",
        expected_sha256="4febb77cb0e0b91418a22a08c19d9fa05d65529f00af30e85afe53a8d716424d",
        last_known_path="tmp/m1-6-current-replay.ttl",
        reason="overwritten-before-immutable-retention",
        references=(),
    )
    corrected = stale.model_copy(update={"references": ("known-reference",)})
    write_unavailable_record(path, stale)
    stale_bytes = path.read_bytes()
    monkeypatch.setattr(
        run_artifacts,
        "_replace_reconciled_record",
        lambda *_: (_ for _ in ()).throw(OSError("injected replace failure")),
    )

    with pytest.raises(OSError, match="injected"):
        reconcile_missing_unavailable_references(
            path=path, expected_stale=stale, corrected=corrected
        )

    assert path.read_bytes() == stale_bytes
    audits = list((path.parent / "superseded").glob("*.json"))
    assert len(audits) == 1
    assert audits[0].read_bytes() == stale_bytes


@pytest.mark.unit
def test_unavailable_reconciliation_post_replace_failure_keeps_corrected_and_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "unavailable/run.json"
    stale = ArtifactUnavailableRecord(
        schema_version=1,
        record_type="unavailable-artifact",
        family="m1-6-current-replay",
        run_id="neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997",
        expected_sha256="4febb77cb0e0b91418a22a08c19d9fa05d65529f00af30e85afe53a8d716424d",
        last_known_path="tmp/m1-6-current-replay.ttl",
        reason="overwritten-before-immutable-retention",
        references=(),
    )
    corrected = stale.model_copy(update={"references": ("known-reference",)})
    write_unavailable_record(path, stale)
    stale_bytes = path.read_bytes()

    def replace_then_fail(source: Path, destination: Path) -> None:
        run_artifacts.os.replace(source, destination)
        raise OSError("injected after replace")

    monkeypatch.setattr(run_artifacts, "_replace_reconciled_record", replace_then_fail)
    with pytest.raises(OSError, match="after replace"):
        reconcile_missing_unavailable_references(
            path=path, expected_stale=stale, corrected=corrected
        )

    assert load_artifact_record(path) == corrected
    audits = list((path.parent / "superseded").glob("*.json"))
    assert len(audits) == 1
    assert audits[0].read_bytes() == stale_bytes


@pytest.mark.unit
def test_unavailable_reconciliation_refuses_non_cas_and_unsafe_states(
    tmp_path: Path,
) -> None:
    path = tmp_path / "unavailable/run.json"
    stale = ArtifactUnavailableRecord(
        schema_version=1,
        record_type="unavailable-artifact",
        family="m1-6-current-replay",
        run_id="neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997",
        expected_sha256="4febb77cb0e0b91418a22a08c19d9fa05d65529f00af30e85afe53a8d716424d",
        last_known_path="tmp/m1-6-current-replay.ttl",
        reason="overwritten-before-immutable-retention",
        references=(),
    )
    corrected = stale.model_copy(update={"references": ("known-reference",)})
    with pytest.raises(ArtifactConflictError, match="absent"):
        reconcile_missing_unavailable_references(
            path=path, expected_stale=stale, corrected=corrected
        )
    path.parent.mkdir(parents=True)
    path.write_bytes(b"unrecognized bytes\n")
    with pytest.raises(ArtifactConflictError, match="CAS identity"):
        reconcile_missing_unavailable_references(
            path=path, expected_stale=stale, corrected=corrected
        )

    for invalid_stale, invalid_corrected in (
        (corrected, corrected),
        (stale, stale),
        (stale, corrected.model_copy(update={"family": "different"})),
    ):
        with pytest.raises(ArtifactConflictError, match="only add missing references"):
            reconcile_missing_unavailable_references(
                path=path,
                expected_stale=invalid_stale,
                corrected=invalid_corrected,
            )

    path.write_bytes(
        (
            json.dumps(corrected.to_dict(), sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode()
    )
    assert (
        reconcile_missing_unavailable_references(
            path=path, expected_stale=stale, corrected=corrected
        )
        is None
    )
    stale_bytes = (
        json.dumps(stale.to_dict(), sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    audit = (
        path.parent
        / "superseded"
        / (__import__("hashlib").sha256(stale_bytes).hexdigest() + ".json")
    )
    audit.parent.mkdir()
    audit.write_bytes(b"conflicting audit\n")
    with pytest.raises(ArtifactConflictError, match="audit differs"):
        reconcile_missing_unavailable_references(
            path=path, expected_stale=stale, corrected=corrected
        )
    audit.unlink()
    path.unlink()
    path.symlink_to(tmp_path / "target")
    with pytest.raises(ArtifactConflictError, match="regular file"):
        reconcile_missing_unavailable_references(
            path=path, expected_stale=stale, corrected=corrected
        )


@pytest.mark.unit
def test_legacy_sidecar_requires_exact_bytes_run_and_persisted_binding(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "tmp/m1-6-current-full-corpus.ttl"
    artifact.parent.mkdir()
    artifact.write_text(f"<{RUN_ID}> <p> <o> .\n", encoding="utf-8")
    before = artifact.read_bytes()
    digest = __import__("hashlib").sha256(before).hexdigest()
    sidecar = tmp_path / "tmp/artifacts/v1/legacy-in-place/current.json"

    manifest = write_legacy_in_place_manifest(
        path=sidecar,
        repository_root=tmp_path,
        artifact_path=artifact,
        run_id=RUN_ID,
        persisted_representation_identity=digest,
        persisted_artifact_path=str(artifact),
        source_identity="ncit-source",
        generator=GeneratorBinding(identity="git:abc", command=("register",)),
    )

    assert artifact.read_bytes() == before
    assert manifest.retention.retention_class == "critical-full-corpus"
    assert (
        manifest.artifact_records[0].relative_path == "tmp/m1-6-current-full-corpus.ttl"
    )
    with pytest.raises(ArtifactConflictError, match="sidecar differs"):
        write_legacy_in_place_manifest(
            path=sidecar,
            repository_root=tmp_path,
            artifact_path=artifact,
            run_id=RUN_ID,
            persisted_representation_identity=digest,
            persisted_artifact_path=str(artifact),
            source_identity="changed-source",
            generator=GeneratorBinding(identity="git:abc", command=("register",)),
        )
    with pytest.raises(ArtifactPathError, match="outside the repository"):
        write_legacy_in_place_manifest(
            path=tmp_path / "outside.json",
            repository_root=tmp_path / "different-root",
            artifact_path=artifact,
            run_id=RUN_ID,
            persisted_representation_identity=digest,
            persisted_artifact_path=str(artifact),
            source_identity="ncit-source",
            generator=GeneratorBinding(identity="git:abc", command=("register",)),
        )
    with pytest.raises(ArtifactConflictError, match="run membership"):
        write_legacy_in_place_manifest(
            path=tmp_path / "wrong-run.json",
            repository_root=tmp_path,
            artifact_path=artifact,
            run_id="neoplasm-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            persisted_representation_identity=digest,
            persisted_artifact_path=str(artifact),
            source_identity="ncit-source",
            generator=GeneratorBinding(identity="git:abc", command=("register",)),
        )
    with pytest.raises(ArtifactConflictError, match="persisted artifact path"):
        write_legacy_in_place_manifest(
            path=tmp_path / "bad-path.json",
            repository_root=tmp_path,
            artifact_path=artifact,
            run_id=RUN_ID,
            persisted_representation_identity=digest,
            persisted_artifact_path="bad\npath",
            source_identity="ncit-source",
            generator=GeneratorBinding(identity="git:abc", command=("register",)),
        )
    conflicting = tmp_path / "tmp/artifacts/v1/legacy-in-place/conflict.json"
    with pytest.raises(ArtifactConflictError, match="persisted identity"):
        write_legacy_in_place_manifest(
            path=conflicting,
            repository_root=tmp_path,
            artifact_path=artifact,
            run_id=RUN_ID,
            persisted_representation_identity="f" * 64,
            persisted_artifact_path=str(artifact),
            source_identity="ncit-source",
            generator=GeneratorBinding(identity="git:abc", command=("register",)),
        )
    assert not conflicting.exists()


@pytest.mark.unit
def test_complete_legacy_sidecar_rerun_retains_original_generator_identity(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "tmp/full-corpus.ttl"
    artifact.parent.mkdir()
    artifact.write_text(f"<{RUN_ID}> <p> <o> .\n", encoding="utf-8")
    digest = __import__("hashlib").sha256(artifact.read_bytes()).hexdigest()
    sidecar = tmp_path / "tmp/artifacts/v1/legacy-in-place/full.manifest.json"
    original = write_legacy_in_place_manifest(
        path=sidecar,
        repository_root=tmp_path,
        artifact_path=artifact,
        run_id=RUN_ID,
        persisted_representation_identity=digest,
        persisted_artifact_path="tmp/full-corpus.ttl",
        source_identity="source",
        generator=GeneratorBinding(identity="git:old", command=("record",)),
    )
    original_bytes = sidecar.read_bytes()

    rerun = write_legacy_in_place_manifest(
        path=sidecar,
        repository_root=tmp_path,
        artifact_path=artifact,
        run_id=RUN_ID,
        persisted_representation_identity=digest,
        persisted_artifact_path="tmp/full-corpus.ttl",
        source_identity="source",
        generator=GeneratorBinding(identity="git:later", command=("record",)),
    )

    assert rerun == original
    assert rerun.generator.identity == "git:old"
    assert sidecar.read_bytes() == original_bytes


@pytest.mark.unit
def test_parent_bound_publication_contract_blocks_future_274_orphans(
    tmp_path: Path,
) -> None:
    source = tmp_path / "candidate.json"
    source.write_bytes(b"candidate")
    with pytest.raises(ArtifactConflictError, match="parent manifest"):
        publish_parent_bound_generation(
            repository_root=tmp_path,
            artifacts_root=tmp_path / "artifacts",
            family="issue-274-candidate",
            generation_id="candidate",
            run_id=None,
            artifact_sources={"artifacts/candidate.json": source},
            parents=(),
            generator=GeneratorBinding(identity="git:abc", command=("candidate",)),
            sources=(),
            retention=RetentionBinding(
                retention_class="referenced-bounded-run",
                owner="decomposition",
                expires_at=None,
            ),
        )

    parent = _publish(tmp_path, "candidate-parent")
    parent_path = (
        tmp_path / "artifacts/m1-6-current-replay/candidate-parent/manifest.json"
    )
    binding = ParentManifestBinding(
        family=parent.family,
        generation_id=parent.generation_id,
        manifest_path=parent_path.relative_to(tmp_path).as_posix(),
        manifest_identity=parent.manifest_identity,
    )
    published = publish_parent_bound_generation(
        repository_root=tmp_path,
        artifacts_root=tmp_path / "artifacts",
        family="issue-274-candidate",
        generation_id="candidate",
        run_id=None,
        artifact_sources={"artifacts/candidate.json": source},
        parents=(binding,),
        generator=GeneratorBinding(identity="git:abc", command=("candidate",)),
        sources=(),
        retention=RetentionBinding(
            retention_class="referenced-bounded-run",
            owner="decomposition",
            expires_at=None,
        ),
    )
    assert published.parents == (binding,)

    forged = binding.model_copy(update={"manifest_identity": "f" * 64})
    with pytest.raises(ArtifactConflictError, match="parent manifest"):
        publish_parent_bound_generation(
            repository_root=tmp_path,
            artifacts_root=tmp_path / "artifacts",
            family="issue-274-candidate",
            generation_id="forged",
            run_id=None,
            artifact_sources={"artifacts/candidate.json": source},
            parents=(forged,),
            generator=GeneratorBinding(identity="git:abc", command=("candidate",)),
            sources=(),
            retention=RetentionBinding(
                retention_class="referenced-bounded-run",
                owner="decomposition",
                expires_at=None,
            ),
        )

    wrong_locator = binding.model_copy(update={"family": "different-family"})
    with pytest.raises(ArtifactConflictError, match="locator differs"):
        publish_parent_bound_generation(
            repository_root=tmp_path,
            artifacts_root=tmp_path / "artifacts",
            family="issue-274-candidate",
            generation_id="wrong-locator",
            run_id=None,
            artifact_sources={"artifacts/candidate.json": source},
            parents=(wrong_locator,),
            generator=GeneratorBinding(identity="git:abc", command=("candidate",)),
            sources=(),
            retention=RetentionBinding(
                retention_class="referenced-bounded-run",
                owner="decomposition",
                expires_at=None,
            ),
        )

    with pytest.raises(ArtifactConflictError, match="outside the artifact registry"):
        publish_parent_bound_generation(
            repository_root=tmp_path,
            artifacts_root=tmp_path / "different-artifact-registry",
            family="issue-274-candidate",
            generation_id="outside-parent",
            run_id=None,
            artifact_sources={"artifacts/candidate.json": source},
            parents=(binding,),
            generator=GeneratorBinding(identity="git:abc", command=("candidate",)),
            sources=(),
            retention=RetentionBinding(
                retention_class="referenced-bounded-run",
                owner="decomposition",
                expires_at=None,
            ),
        )
