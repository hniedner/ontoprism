from __future__ import annotations

import hashlib
import json
import subprocess
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, get_args

import pytest
from pydantic import ValidationError
from rdflib import RDF

from ontolib.decomposition import vocab
from ontolib.decomposition.provenance_models import RunSummary
from ontolib.decomposition.publication import PublicationMarker
from ontolib.decomposition.vocab import DECOMPOSED_GRAPH_IRI
from ontolib.terminologies.ncit.activation import (
    EXPECTED_C27262_DEFINITION_IDENTITY,
    QLEVER_REQUIRED_STORE_FILES,
    ActivationHealthError,
    ActivationJournal,
    ActivationJournalError,
    ActivationPhase,
    ActivationPreflightError,
    ActivationProjectionError,
    ActivationRolledBackError,
    ActivationServiceError,
    ActivationStoreProof,
    ActivationTransitionError,
    DockerComposeNcitService,
    ProjectionPlan,
    QleverServiceContract,
    activate_candidate_store,
    cleanup_rollback_store,
    preflight_activation,
    prepare_activation_journal,
    projection_plan_from_run,
    read_activation_journal,
    reconcile_projection_with_client,
    restore_rollback_store,
    run_journaled_activation,
    stage_active_store_for_rollback,
    transition_activation_journal,
    validate_activation_health,
    validate_projection_artifact,
    validate_projection_health,
    write_activation_journal,
)
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.ncit.sibling_store import (
    CANDIDATE_MANIFEST_FILENAME,
    OWNER_MARKER_FILENAME,
    QLEVER_IMAGE,
    QLEVER_INDEX_VERSION,
    CandidateGraph,
    CandidateObservation,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Collection

_PHASES = (
    "preflight",
    "publication-paused",
    "service-stopped",
    "rollback-staged",
    "candidate-activated",
    "service-restarted",
    "health-validated",
    "rollback-cleaned",
    "publication-resumed",
    "complete",
    "rolled-back",
)


def _journal(tmp_path: Path, **changes: object) -> ActivationJournal:
    values: dict[str, object] = {
        "phase": "preflight",
        "active_path": str((tmp_path / "qlever-ncit").resolve()),
        "candidate_path": str((tmp_path / ".qlever-ncit.candidate-a").resolve()),
        "rollback_path": str((tmp_path / ".qlever-ncit.rollback-a").resolve()),
        "candidate_manifest_path": str(
            (tmp_path / ".qlever-ncit.candidate-a" / "manifest.json").resolve()
        ),
        "candidate_manifest_sha256": "1" * 64,
        "candidate_owner": "a" * 32,
        "active_owner": "b" * 32,
        "candidate_source_identity": "2" * 64,
        "active_source_identity": "3" * 64,
        "store_format_identity": "4" * 64,
        "qlever_image": QLEVER_IMAGE,
        "qlever_image_id": "sha256:" + QLEVER_IMAGE.rsplit("@sha256:", 1)[1],
        "qlever_index_version": QLEVER_INDEX_VERSION,
        "qlever_index_basename": "ncit",
    }
    values.update(changes)
    return ActivationJournal.model_validate(values)


@pytest.mark.unit
def test_activation_phase_and_journal_paths_are_closed_and_exact(
    tmp_path: Path,
) -> None:
    assert get_args(ActivationPhase) == _PHASES
    journal = _journal(tmp_path)
    assert journal.phase == "preflight"
    assert journal.active_path == str((tmp_path / "qlever-ncit").resolve())
    assert journal.candidate_path != journal.rollback_path
    assert journal.qlever_image == QLEVER_IMAGE
    assert journal.qlever_index_version == QLEVER_INDEX_VERSION
    assert journal.qlever_index_basename == "ncit"

    with pytest.raises(ValidationError):
        _journal(tmp_path, phase="unknown")
    with pytest.raises(ValidationError):
        _journal(tmp_path, active_path="relative/store")
    with pytest.raises(ValidationError):
        _journal(tmp_path, unexpected="not allowed")


@pytest.mark.unit
def test_every_forward_journal_transition_is_durable_and_skips_are_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "activation.json"
    journal = _journal(tmp_path)
    write_activation_journal(path, journal)

    for phase in _PHASES[1:-1]:
        journal = transition_activation_journal(path, journal, phase)
        assert read_activation_journal(path) == journal

    with pytest.raises(ActivationTransitionError, match=r"complete.*service-stopped"):
        transition_activation_journal(path, journal, "service-stopped")
    assert read_activation_journal(path).phase == "complete"


@pytest.mark.unit
def test_health_validation_transition_records_durable_activation_time(
    tmp_path: Path,
) -> None:
    path = tmp_path / "activation.json"
    journal = _journal(tmp_path)
    write_activation_journal(path, journal)
    for phase in _PHASES[1:7]:
        journal = transition_activation_journal(path, journal, phase)

    assert journal.phase == "health-validated"
    assert journal.activated_at is not None
    assert journal.activated_at.tzinfo is not None
    assert read_activation_journal(path).activated_at == journal.activated_at


@pytest.mark.unit
def test_journal_rejects_activation_time_that_contradicts_its_phase(
    tmp_path: Path,
) -> None:
    stamped = datetime(2026, 8, 10, tzinfo=UTC)

    # A phase at or after health-validation must carry an activation time.
    for activated_phase in ("health-validated", "complete"):
        with pytest.raises(ValidationError, match="activated_at is required"):
            _journal(tmp_path, phase=activated_phase, activated_at=None)

    # A phase before health-validation must not carry one.
    for pending_phase in ("preflight", "service-restarted"):
        with pytest.raises(ValidationError, match="activated_at must be unset"):
            _journal(tmp_path, phase=pending_phase, activated_at=stamped)

    # ``rolled-back`` is terminal from either side and constrains neither.
    assert _journal(tmp_path, phase="rolled-back", activated_at=None).phase == (
        "rolled-back"
    )
    assert (
        _journal(tmp_path, phase="rolled-back", activated_at=stamped).activated_at
        == stamped
    )


@pytest.mark.unit
def test_failed_fsync_does_not_report_a_journal_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "activation.json"
    journal = _journal(tmp_path)
    write_activation_journal(path, journal)

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("fsync failed")

    monkeypatch.setattr("ontolib.terminologies.ncit.activation.os.fsync", fail_fsync)
    with pytest.raises(OSError, match="fsync failed"):
        transition_activation_journal(path, journal, "publication-paused")

    assert read_activation_journal(path) == journal


@pytest.mark.unit
def test_read_refuses_a_symlinked_activation_journal(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    link = tmp_path / "activation.json"
    write_activation_journal(target, _journal(tmp_path))
    link.symlink_to(target)

    with pytest.raises(ActivationJournalError, match="exact regular file"):
        read_activation_journal(link)


@pytest.mark.unit
def test_write_refuses_a_symlinked_activation_journal(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    link = tmp_path / "activation.json"
    original = _journal(tmp_path)
    write_activation_journal(target, original)
    link.symlink_to(target)

    with pytest.raises(ActivationJournalError, match="symlink"):
        write_activation_journal(
            link,
            original.model_copy(update={"phase": "publication-paused"}),
        )

    assert read_activation_journal(target) == original


def _write_qlever_store(path: Path, owner: str) -> None:
    path.mkdir()
    for filename in QLEVER_REQUIRED_STORE_FILES:
        (path / filename).write_text(filename)
    (path / OWNER_MARKER_FILENAME).write_text(owner + "\n")
    (path / CANDIDATE_MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "candidate_path": str(path.resolve()),
                "active_store_path": str(path.resolve()),
            }
        )
        + "\n"
    )


def _proof(
    path: Path, owner: str, source: str, store_format: str
) -> ActivationStoreProof:
    return ActivationStoreProof(
        path=str(path.resolve()),
        owner=owner,
        source_identity=source,
        store_format_identity=store_format,
        qlever_image=QLEVER_IMAGE,
        qlever_image_id="sha256:" + QLEVER_IMAGE.rsplit("@sha256:", 1)[1],
        qlever_index_version=QLEVER_INDEX_VERSION,
        qlever_index_basename="ncit",
    )


def _observation(**changes: object) -> CandidateObservation:
    values: dict[str, object] = {
        "default_triples": 12_500_000,
        "stated_triples": 10_500_000,
        "named_graphs": (
            CandidateGraph(graph_iri=STATED_GRAPH_IRI, triples=10_500_000),
        ),
        "default_version": "26.test",
        "stated_version": "26.test",
        "restriction_count": 150_000,
        "has_required_restriction": True,
        "default_has_stated_only_sentinel": False,
        "stated_has_stated_only_sentinel": True,
    }
    values.update(changes)
    return CandidateObservation.model_validate(values)


@pytest.mark.unit
def test_preflight_binds_exact_owned_same_filesystem_qlever_paths(
    tmp_path: Path,
) -> None:
    active = tmp_path / "qlever-ncit"
    candidate = tmp_path / f".qlever-ncit.candidate-{'a' * 32}"
    _write_qlever_store(active, "b" * 32)
    _write_qlever_store(candidate, "a" * 32)
    manifest_path = candidate / CANDIDATE_MANIFEST_FILENAME

    path, journal = preflight_activation(
        candidate_manifest_path=manifest_path,
        candidate=_proof(candidate, "a" * 32, "1" * 64, "3" * 64),
        active=_proof(active, "b" * 32, "2" * 64, "3" * 64),
        expected_active_path=active,
        minimum_free_bytes=0,
    )

    assert path == tmp_path / ".qlever-ncit.activation.json"
    assert read_activation_journal(path) == journal
    assert journal.phase == "preflight"
    assert journal.rollback_path == str(
        (tmp_path / f".qlever-ncit.rollback-{'a' * 32}").resolve()
    )
    assert (
        journal.candidate_manifest_sha256
        == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("owner", "owner marker"),
        ("sidecar", "unexpected QLever store entry"),
        ("format", "store format"),
        ("active-path", "configured active"),
        ("loader", "executable/index identities"),
    ],
)
def test_preflight_refuses_unowned_sidecar_or_wrong_format_paths(
    tmp_path: Path, mutation: str, message: str
) -> None:
    active = tmp_path / "qlever-ncit"
    candidate = tmp_path / f".qlever-ncit.candidate-{'a' * 32}"
    _write_qlever_store(active, "b" * 32)
    _write_qlever_store(candidate, "a" * 32)
    candidate_proof = _proof(candidate, "a" * 32, "1" * 64, "3" * 64)
    active_proof = _proof(active, "b" * 32, "2" * 64, "3" * 64)
    expected_active = active
    if mutation == "owner":
        (candidate / OWNER_MARKER_FILENAME).write_text("f" * 32 + "\n")
    elif mutation == "sidecar":
        (candidate / "ncit.index.unknown-sidecar").write_text("foreign")
    elif mutation == "format":
        candidate_proof = candidate_proof.model_copy(
            update={"store_format_identity": "9" * 64}
        )
    elif mutation == "active-path":
        expected_active = tmp_path / "somewhere-else"
    else:
        candidate_proof = candidate_proof.model_copy(
            update={"qlever_index_version": "qlever-index changed"}
        )

    with pytest.raises(ActivationPreflightError, match=message):
        preflight_activation(
            candidate_manifest_path=candidate / CANDIDATE_MANIFEST_FILENAME,
            candidate=candidate_proof,
            active=active_proof,
            expected_active_path=expected_active,
            minimum_free_bytes=0,
        )


@pytest.mark.unit
def test_exact_same_filesystem_swap_and_rollback_restore_both_owned_stores(
    tmp_path: Path,
) -> None:
    active = tmp_path / "qlever-ncit"
    candidate = tmp_path / f".qlever-ncit.candidate-{'a' * 32}"
    _write_qlever_store(active, "b" * 32)
    _write_qlever_store(candidate, "a" * 32)
    journal_path, journal = preflight_activation(
        candidate_manifest_path=candidate / CANDIDATE_MANIFEST_FILENAME,
        candidate=_proof(candidate, "a" * 32, "1" * 64, "3" * 64),
        active=_proof(active, "b" * 32, "2" * 64, "3" * 64),
        expected_active_path=active,
        minimum_free_bytes=0,
    )
    journal = transition_activation_journal(
        journal_path,
        transition_activation_journal(journal_path, journal, "publication-paused"),
        "service-stopped",
    )

    journal = stage_active_store_for_rollback(journal_path, journal)
    assert not active.exists()
    assert (
        Path(journal.rollback_path, OWNER_MARKER_FILENAME).read_text().strip()
        == "b" * 32
    )
    journal = activate_candidate_store(journal_path, journal)
    assert not candidate.exists()
    assert Path(active, OWNER_MARKER_FILENAME).read_text().strip() == "a" * 32
    assert json.loads(Path(active, CANDIDATE_MANIFEST_FILENAME).read_text())[
        "candidate_path"
    ] == str(active.resolve())

    journal = restore_rollback_store(journal_path, journal)

    assert journal.phase == "rolled-back"
    assert Path(active, OWNER_MARKER_FILENAME).read_text().strip() == "b" * 32
    assert Path(candidate, OWNER_MARKER_FILENAME).read_text().strip() == "a" * 32
    assert json.loads(Path(candidate, CANDIDATE_MANIFEST_FILENAME).read_text())[
        "candidate_path"
    ] == str(candidate.resolve())


@pytest.mark.unit
def test_cleanup_removes_only_the_exact_owner_verified_rollback(tmp_path: Path) -> None:
    active = tmp_path / "qlever-ncit"
    candidate = tmp_path / f".qlever-ncit.candidate-{'a' * 32}"
    _write_qlever_store(active, "b" * 32)
    _write_qlever_store(candidate, "a" * 32)
    journal_path, journal = preflight_activation(
        candidate_manifest_path=candidate / CANDIDATE_MANIFEST_FILENAME,
        candidate=_proof(candidate, "a" * 32, "1" * 64, "3" * 64),
        active=_proof(active, "b" * 32, "2" * 64, "3" * 64),
        expected_active_path=active,
        minimum_free_bytes=0,
    )
    for phase in (
        "publication-paused",
        "service-stopped",
    ):
        journal = transition_activation_journal(journal_path, journal, phase)
    journal = stage_active_store_for_rollback(journal_path, journal)
    journal = activate_candidate_store(journal_path, journal)
    journal = transition_activation_journal(journal_path, journal, "service-restarted")
    journal = transition_activation_journal(journal_path, journal, "health-validated")
    decoy = tmp_path / f".qlever-ncit.rollback-{'c' * 32}"
    decoy.mkdir()

    journal = cleanup_rollback_store(journal_path, journal)

    assert journal.phase == "rollback-cleaned"
    assert not Path(journal.rollback_path).exists()
    assert decoy.is_dir()


@pytest.mark.unit
def test_cleanup_refuses_a_rollback_whose_owner_marker_changed(tmp_path: Path) -> None:
    active = tmp_path / "qlever-ncit"
    candidate = tmp_path / f".qlever-ncit.candidate-{'a' * 32}"
    _write_qlever_store(active, "b" * 32)
    _write_qlever_store(candidate, "a" * 32)
    journal_path, journal = preflight_activation(
        candidate_manifest_path=candidate / CANDIDATE_MANIFEST_FILENAME,
        candidate=_proof(candidate, "a" * 32, "1" * 64, "3" * 64),
        active=_proof(active, "b" * 32, "2" * 64, "3" * 64),
        expected_active_path=active,
        minimum_free_bytes=0,
    )
    for phase in ("publication-paused", "service-stopped"):
        journal = transition_activation_journal(journal_path, journal, phase)
    journal = stage_active_store_for_rollback(journal_path, journal)
    journal = activate_candidate_store(journal_path, journal)
    journal = transition_activation_journal(journal_path, journal, "service-restarted")
    journal = transition_activation_journal(journal_path, journal, "health-validated")
    Path(journal.rollback_path, OWNER_MARKER_FILENAME).write_text("f" * 32 + "\n")

    with pytest.raises(ActivationPreflightError, match="rollback owner marker"):
        cleanup_rollback_store(journal_path, journal)

    assert Path(journal.rollback_path).is_dir()
    assert read_activation_journal(journal_path).phase == "health-validated"


@pytest.mark.unit
def test_ambiguous_successful_renames_and_cleanup_resume_forward(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal_path, journal = _activation_attempt(tmp_path)
    for phase in ("publication-paused", "service-stopped"):
        journal = transition_activation_journal(journal_path, journal, phase)
    real_transition = transition_activation_journal

    def fail_transition(
        _path: Path,
        _journal_value: ActivationJournal,
        _phase: ActivationPhase,
    ) -> ActivationJournal:
        raise OSError("crash after irreversible boundary")

    with monkeypatch.context() as crash:
        crash.setattr(
            "ontolib.terminologies.ncit.activation.transition_activation_journal",
            fail_transition,
        )
        with pytest.raises(OSError, match="irreversible boundary"):
            stage_active_store_for_rollback(journal_path, journal)
    assert read_activation_journal(journal_path).phase == "service-stopped"
    journal = stage_active_store_for_rollback(journal_path, journal)
    assert journal.phase == "rollback-staged"

    with monkeypatch.context() as crash:
        crash.setattr(
            "ontolib.terminologies.ncit.activation.transition_activation_journal",
            fail_transition,
        )
        with pytest.raises(OSError, match="irreversible boundary"):
            activate_candidate_store(journal_path, journal)
    assert read_activation_journal(journal_path).phase == "rollback-staged"
    journal = activate_candidate_store(journal_path, journal)
    assert journal.phase == "candidate-activated"

    for phase in ("service-restarted", "health-validated"):
        journal = real_transition(journal_path, journal, phase)
    with monkeypatch.context() as crash:
        crash.setattr(
            "ontolib.terminologies.ncit.activation.transition_activation_journal",
            fail_transition,
        )
        with pytest.raises(OSError, match="irreversible boundary"):
            cleanup_rollback_store(journal_path, journal)
    assert read_activation_journal(journal_path).phase == "health-validated"
    journal = cleanup_rollback_store(journal_path, journal)
    assert journal.phase == "rollback-cleaned"


def _container_inspection(
    active: Path,
    *,
    running: bool,
    healthy: bool,
    container_id: str,
    image: str | None = None,
    contract: QleverServiceContract | None = None,
) -> str:
    contract = contract or QleverServiceContract.production()
    if image is None:
        image = contract.image_id
    return json.dumps(
        [
            {
                "Id": container_id,
                "Name": f"/{contract.container_name}",
                "Image": image,
                "State": {
                    "Running": running,
                    "Status": "running" if running else "exited",
                    "Health": {"Status": "healthy" if healthy else "starting"},
                },
                "Mounts": [
                    {
                        "Type": "bind",
                        "Source": str(active.resolve()),
                        "Destination": "/data",
                        "RW": True,
                    }
                ],
                "Config": {
                    "Image": contract.image,
                    "Cmd": [f"exec qlever-server -i {contract.index_basename} -p 7001"],
                    "Labels": {"com.docker.compose.service": contract.service_name},
                },
            }
        ]
    )


class _DockerServiceRun:
    def __init__(
        self,
        active: Path,
        contract: QleverServiceContract | None = None,
    ) -> None:
        self.active = active
        self.contract = contract or QleverServiceContract.production()
        self.running = True
        self.healthy = True
        self.container_id = "1" * 64
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        del check
        self.calls.append(args)
        if args == ("inspect", self.contract.container_name):
            return subprocess.CompletedProcess(
                args,
                0,
                _container_inspection(
                    self.active,
                    running=self.running,
                    healthy=self.healthy,
                    container_id=self.container_id,
                    contract=self.contract,
                ),
                "",
            )
        if args[-2:] == ("stop", self.contract.service_name):
            self.running = False
            self.healthy = False
        elif args[-1:] == (self.contract.service_name,) and "up" in args:
            self.running = True
            self.healthy = True
            self.container_id = "2" * 64
        return subprocess.CompletedProcess(args, 0, "", "")


@pytest.mark.unit
def test_qlever_service_stop_and_recreate_validate_exact_real_contract(
    tmp_path: Path,
) -> None:
    active = tmp_path / "qlever-ncit"
    active.mkdir()
    docker = _DockerServiceRun(active)
    service = DockerComposeNcitService(
        project_directory=tmp_path,
        docker_run=docker,
    )

    service.stop(active)
    service.restart(active)

    assert docker.calls == [
        ("inspect", "ontoprism-qlever-ncit"),
        (
            "compose",
            "--project-directory",
            str(tmp_path.resolve()),
            "stop",
            "qlever-ncit",
        ),
        ("inspect", "ontoprism-qlever-ncit"),
        (
            "compose",
            "--project-directory",
            str(tmp_path.resolve()),
            "up",
            "-d",
            "--force-recreate",
            "--no-deps",
            "qlever-ncit",
        ),
        ("inspect", "ontoprism-qlever-ncit"),
    ]


@pytest.mark.unit
def test_qlever_service_can_apply_the_same_contract_to_a_disposable_service(
    tmp_path: Path,
) -> None:
    active = tmp_path / "qlever-ncit"
    active.mkdir()
    contract = QleverServiceContract(
        service_name="qlever-ncit-disposable",
        container_name="ontoprism-qlever-ncit-disposable",
        image=QLEVER_IMAGE,
        image_id="sha256:" + QLEVER_IMAGE.rsplit("@sha256:", 1)[1],
        index_version=QLEVER_INDEX_VERSION,
        index_basename="ncit",
    )
    docker = _DockerServiceRun(active, contract)
    service = DockerComposeNcitService(
        project_directory=tmp_path,
        contract=contract,
        docker_run=docker,
    )

    service.stop(active)
    service.restart(active)

    assert docker.calls[0] == ("inspect", contract.container_name)
    assert docker.calls[1][-2:] == ("stop", contract.service_name)
    assert docker.calls[-2][-1] == contract.service_name


@pytest.mark.unit
def test_qlever_service_refuses_wrong_image_or_mount_before_stop(
    tmp_path: Path,
) -> None:
    active = tmp_path / "qlever-ncit"
    active.mkdir()
    docker = _DockerServiceRun(active)
    service = DockerComposeNcitService(
        project_directory=tmp_path,
        docker_run=docker,
    )
    original = docker.__call__

    def wrong_image(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        if args == ("inspect", "ontoprism-qlever-ncit"):
            return subprocess.CompletedProcess(
                args,
                0,
                _container_inspection(
                    active,
                    running=True,
                    healthy=True,
                    container_id="1" * 64,
                    image="sha256:" + "f" * 64,
                ),
                "",
            )
        return original(*args, check=check)

    service = DockerComposeNcitService(
        project_directory=tmp_path,
        docker_run=wrong_image,
    )
    with pytest.raises(ActivationServiceError, match="image identity"):
        service.stop(active)

    assert all("stop" not in call for call in docker.calls)


@pytest.mark.unit
def test_health_accepts_exact_base_with_the_known_additive_projection_graph() -> None:
    expected = _observation()
    observed = _observation(
        named_graphs=(
            CandidateGraph(graph_iri=STATED_GRAPH_IRI, triples=10_500_000),
            CandidateGraph(graph_iri=DECOMPOSED_GRAPH_IRI, triples=9),
        )
    )

    validate_activation_health(
        expected=expected,
        observed=observed,
        complete_definition_identity=EXPECTED_C27262_DEFINITION_IDENTITY,
        browse_codes=("C1000",),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("count", "base observation"),
        ("definition", "C27262"),
        ("browse", "browse"),
        ("graph", "unexpected named graph"),
    ],
)
def test_health_rejects_wrong_base_definition_browse_or_graph(
    change: str,
    message: str,
) -> None:
    expected = _observation()
    observed = expected
    definition = EXPECTED_C27262_DEFINITION_IDENTITY
    browse = ("C1000",)
    if change == "count":
        observed = _observation(default_triples=12_500_001)
    elif change == "definition":
        definition = "f" * 64
    elif change == "browse":
        browse = ()
    else:
        observed = _observation(
            named_graphs=(
                CandidateGraph(graph_iri=STATED_GRAPH_IRI, triples=10_500_000),
                CandidateGraph(graph_iri="https://example.test/foreign", triples=1),
            )
        )

    with pytest.raises(ActivationHealthError, match=message):
        validate_activation_health(
            expected=expected,
            observed=observed,
            complete_definition_identity=definition,
            browse_codes=browse,
        )


class _ActivationService:
    def __init__(self) -> None:
        self.events: list[str] = []

    def stop(self, _active_path: Path) -> None:
        self.events.append("service-stop")

    def restart(self, _active_path: Path) -> None:
        self.events.append("service-restart")


def _activation_attempt(tmp_path: Path) -> tuple[Path, ActivationJournal]:
    active = tmp_path / "qlever-ncit"
    candidate = tmp_path / f".qlever-ncit.candidate-{'a' * 32}"
    _write_qlever_store(active, "b" * 32)
    _write_qlever_store(candidate, "a" * 32)
    return preflight_activation(
        candidate_manifest_path=candidate / CANDIDATE_MANIFEST_FILENAME,
        candidate=_proof(candidate, "a" * 32, "1" * 64, "3" * 64),
        active=_proof(active, "b" * 32, "2" * 64, "3" * 64),
        expected_active_path=active,
        minimum_free_bytes=0,
    )


@pytest.mark.unit
async def test_journaled_activation_pauses_recreates_validates_cleans_and_resumes(
    tmp_path: Path,
) -> None:
    journal_path, _journal_value = _activation_attempt(tmp_path)
    service = _ActivationService()
    events = service.events

    @asynccontextmanager
    async def pause_publication() -> AsyncIterator[None]:
        events.append("publication-enter")
        yield
        events.append("publication-exit")

    async def reconcile(_journal: ActivationJournal) -> None:
        events.append("projection-reconciled")

    async def health(_journal: ActivationJournal) -> None:
        events.append("health-validated")

    result = await run_journaled_activation(
        journal_path,
        service=service,
        pause_publication=pause_publication,
        reconcile_projection=reconcile,
        validate_health=health,
        validate_rollback_health=_no_op_step,
    )

    assert result.phase == "complete"
    assert read_activation_journal(journal_path) == result
    assert events == [
        "publication-enter",
        "service-stop",
        "service-restart",
        "projection-reconciled",
        "health-validated",
        "publication-exit",
    ]
    assert (
        Path(result.active_path, OWNER_MARKER_FILENAME).read_text().strip() == "a" * 32
    )
    assert not Path(result.rollback_path).exists()


@pytest.mark.unit
async def test_unhealthy_restarted_service_restores_old_store_before_resuming(
    tmp_path: Path,
) -> None:
    journal_path, _journal_value = _activation_attempt(tmp_path)
    service = _ActivationService()
    events = service.events

    @asynccontextmanager
    async def pause_publication() -> AsyncIterator[None]:
        events.append("publication-enter")
        yield
        events.append("publication-exit")

    async def reconcile(_journal: ActivationJournal) -> None:
        events.append("projection-reconciled")

    async def unhealthy(_journal: ActivationJournal) -> None:
        events.append("health-rejected")
        raise ActivationHealthError("forced unhealthy service")

    async def rollback_health(_journal: ActivationJournal) -> None:
        events.append("rollback-health-validated")

    with pytest.raises(ActivationRolledBackError, match="forced unhealthy"):
        await run_journaled_activation(
            journal_path,
            service=service,
            pause_publication=pause_publication,
            reconcile_projection=reconcile,
            validate_health=unhealthy,
            validate_rollback_health=rollback_health,
        )

    result = read_activation_journal(journal_path)
    assert result.phase == "rolled-back"
    assert (
        Path(result.active_path, OWNER_MARKER_FILENAME).read_text().strip() == "b" * 32
    )
    assert (
        Path(result.candidate_path, OWNER_MARKER_FILENAME).read_text().strip()
        == "a" * 32
    )
    assert events == [
        "publication-enter",
        "service-stop",
        "service-restart",
        "projection-reconciled",
        "health-rejected",
        "service-stop",
        "service-restart",
        "rollback-health-validated",
        "publication-exit",
    ]


class _FailFirstRestartService(_ActivationService):
    def __init__(self) -> None:
        super().__init__()
        self._failed = False

    def restart(self, _active_path: Path) -> None:
        self.events.append("service-restart")
        if not self._failed:
            self._failed = True
            raise ActivationServiceError("forced server start failure")


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["server-start", "projection"])
async def test_server_start_or_projection_failure_restores_the_previous_store(
    tmp_path: Path,
    failure: str,
) -> None:
    journal_path, _journal_value = _activation_attempt(tmp_path)
    service: _ActivationService = (
        _FailFirstRestartService()
        if failure == "server-start"
        else _ActivationService()
    )

    async def projection(_journal: ActivationJournal) -> None:
        if failure == "projection":
            raise ActivationProjectionError("forced projection failure")

    with pytest.raises(ActivationRolledBackError, match="forced"):
        await run_journaled_activation(
            journal_path,
            service=service,
            pause_publication=_empty_pause,
            reconcile_projection=projection,
            validate_health=_no_op_step,
            validate_rollback_health=_no_op_step,
        )

    result = read_activation_journal(journal_path)
    assert result.phase == "rolled-back"
    assert (
        Path(result.active_path, OWNER_MARKER_FILENAME).read_text().strip() == "b" * 32
    )
    assert (
        Path(result.candidate_path, OWNER_MARKER_FILENAME).read_text().strip()
        == "a" * 32
    )


@pytest.mark.unit
@pytest.mark.parametrize("boundary", ["candidate-preserved", "old-active-restored"])
def test_interrupted_rollback_resumes_from_either_ambiguous_rename(
    tmp_path: Path,
    boundary: str,
) -> None:
    journal_path, journal = _activation_attempt(tmp_path)
    journal = _persist_phase(journal_path, journal, "service-restarted")
    active = Path(journal.active_path)
    candidate = Path(journal.candidate_path)
    rollback = Path(journal.rollback_path)
    active.replace(candidate)
    if boundary == "old-active-restored":
        rollback.replace(active)

    result = restore_rollback_store(journal_path, journal)

    assert result.phase == "rolled-back"
    assert (
        Path(result.active_path, OWNER_MARKER_FILENAME).read_text().strip() == "b" * 32
    )
    assert (
        Path(result.candidate_path, OWNER_MARKER_FILENAME).read_text().strip()
        == "a" * 32
    )


def _persist_phase(
    journal_path: Path,
    journal: ActivationJournal,
    target: ActivationPhase,
) -> ActivationJournal:
    if target in {"preflight", "rolled-back"}:
        if target == "rolled-back":
            journal = transition_activation_journal(
                journal_path, journal, "publication-paused"
            )
            journal = transition_activation_journal(
                journal_path, journal, "service-stopped"
            )
            journal = stage_active_store_for_rollback(journal_path, journal)
            journal = activate_candidate_store(journal_path, journal)
            return restore_rollback_store(journal_path, journal)
        return journal
    for phase in (
        "publication-paused",
        "service-stopped",
        "rollback-staged",
        "candidate-activated",
        "service-restarted",
        "health-validated",
        "rollback-cleaned",
        "publication-resumed",
        "complete",
    ):
        if phase == "rollback-staged":
            journal = stage_active_store_for_rollback(journal_path, journal)
        elif phase == "candidate-activated":
            journal = activate_candidate_store(journal_path, journal)
        elif phase == "rollback-cleaned":
            journal = cleanup_rollback_store(journal_path, journal)
        else:
            journal = transition_activation_journal(journal_path, journal, phase)
        if phase == target:
            return journal
    raise AssertionError(f"unsupported test phase: {target}")


@pytest.mark.unit
@pytest.mark.parametrize("phase", _PHASES)
async def test_activation_restarts_deterministically_from_every_persisted_phase(
    tmp_path: Path,
    phase: ActivationPhase,
) -> None:
    journal_path, journal = _activation_attempt(tmp_path)
    journal = _persist_phase(journal_path, journal, phase)
    if phase == "rolled-back":
        assert (
            await run_journaled_activation(
                journal_path,
                service=_ActivationService(),
                pause_publication=_empty_pause,
                reconcile_projection=_no_op_step,
                validate_health=_no_op_step,
                validate_rollback_health=_no_op_step,
            )
        ).phase == "rolled-back"
        return

    result = await run_journaled_activation(
        journal_path,
        service=_ActivationService(),
        pause_publication=_empty_pause,
        reconcile_projection=_no_op_step,
        validate_health=_no_op_step,
        validate_rollback_health=_no_op_step,
    )
    assert result.phase == "complete"
    assert (
        Path(result.active_path, OWNER_MARKER_FILENAME).read_text().strip() == "a" * 32
    )


@asynccontextmanager
async def _empty_pause() -> AsyncIterator[None]:
    yield


async def _no_op_step(_journal: ActivationJournal) -> None:
    return None


@pytest.mark.unit
def test_projection_plan_reads_only_the_postgres_bound_artifact_identity(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "published.ttl"
    artifact.write_text("<urn:s> <urn:p> <urn:o> .\n")
    identity = hashlib.sha256(artifact.read_bytes()).hexdigest()
    plan = ProjectionPlan(
        run_id="run-1",
        source_identity="1" * 64,
        representation_identity=identity,
        artifact_path=str(artifact.resolve()),
        built_at=datetime(2026, 8, 10, tzinfo=UTC),
    )

    assert validate_projection_artifact(plan) == artifact.read_bytes()

    artifact.write_text("<urn:s> <urn:p> <urn:changed> .\n")
    with pytest.raises(ActivationProjectionError, match="identity"):
        validate_projection_artifact(plan)


class _ProjectionClient:
    def __init__(self, plan: ProjectionPlan) -> None:
        self.plan = plan
        self.loaded: tuple[bytes, str, str | None, bool] | None = None
        self.update_text: str | None = None

    async def load(
        self,
        data: bytes,
        *,
        content_type: str,
        graph_iri: str | None = None,
        replace: bool = True,
    ) -> None:
        self.loaded = (data, content_type, graph_iri, replace)

    async def update(self, update: str) -> None:
        self.update_text = update

    async def select_once(
        self, _query: str, *, required_variables: Collection[str] = ()
    ) -> list[dict[str, str]]:
        assert required_variables == {"predicate", "value"}
        return [
            {"predicate": str(RDF.type), "value": vocab.PUBLICATION_CLASS},
            {"predicate": vocab.PUBLICATION_RUN, "value": self.plan.run_id},
            {
                "predicate": vocab.PUBLICATION_SOURCE_IDENTITY,
                "value": self.plan.source_identity,
            },
            {
                "predicate": vocab.PUBLICATION_REPRESENTATION_IDENTITY,
                "value": self.plan.representation_identity,
            },
            {
                "predicate": vocab.PUBLICATION_BUILT_AT,
                "value": self.plan.built_at.isoformat().replace("+00:00", "Z"),
            },
        ]


@pytest.mark.unit
async def test_projection_reconciliation_replays_artifact_and_exact_marker(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "published.ttl"
    artifact.write_text("<urn:s> <urn:p> <urn:o> .\n")
    plan = ProjectionPlan(
        run_id="run-1",
        source_identity="1" * 64,
        representation_identity=hashlib.sha256(artifact.read_bytes()).hexdigest(),
        artifact_path=str(artifact.resolve()),
        built_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    client = _ProjectionClient(plan)

    await reconcile_projection_with_client(plan, client)

    assert client.loaded is not None
    payload, content_type, graph_iri, replace = client.loaded
    assert payload == artifact.read_bytes()
    assert content_type == "text/turtle"
    assert graph_iri is not None
    assert graph_iri.endswith(hashlib.sha256(b"run-1").hexdigest())
    assert replace is True
    assert client.update_text is not None
    assert vocab.DECOMPOSED_GRAPH_IRI in client.update_text


@pytest.mark.unit
def test_projection_plan_requires_the_exact_published_postgres_run(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "published.ttl"
    artifact.write_text("<urn:s> <urn:p> <urn:o> .\n")
    identity = hashlib.sha256(artifact.read_bytes()).hexdigest()
    built_at = datetime(2026, 8, 10, tzinfo=UTC)
    marker = PublicationMarker(
        run_id="run-1",
        source_identity="1" * 64,
        representation_identity=identity,
        built_at=built_at,
    )
    run = RunSummary(
        id="run-1",
        branch="neoplasm",
        status="complete",
        ncit_version="26.test",
        started_at=built_at,
        finished_at=built_at,
        source_identity="1" * 64,
        publication_state="published",
        representation_identity=identity,
        publication_artifact_path=str(artifact.resolve()),
        publication_built_at=built_at,
    )

    assert projection_plan_from_run(marker, run).run_id == "run-1"

    with pytest.raises(ActivationProjectionError, match="source identity"):
        projection_plan_from_run(
            marker.model_copy(update={"source_identity": "2" * 64}),
            run,
        )


@pytest.mark.unit
def test_projection_health_binds_the_composed_marker_to_the_journal(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "published.ttl"
    artifact.write_text("<urn:s> <urn:p> <urn:o> .\n")
    plan = ProjectionPlan(
        run_id="run-1",
        source_identity="1" * 64,
        representation_identity=hashlib.sha256(artifact.read_bytes()).hexdigest(),
        artifact_path=str(artifact.resolve()),
        built_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    marker = PublicationMarker(
        run_id=plan.run_id,
        source_identity=plan.source_identity,
        representation_identity=plan.representation_identity,
        built_at=plan.built_at,
    )

    validate_projection_health(plan, marker)
    validate_projection_health(None, None)
    with pytest.raises(ActivationHealthError, match="projection marker"):
        validate_projection_health(plan, None)
    with pytest.raises(ActivationHealthError, match="projection marker"):
        validate_projection_health(None, marker)
    with pytest.raises(ActivationHealthError, match="projection marker"):
        validate_projection_health(
            plan,
            marker.model_copy(update={"source_identity": "2" * 64}),
        )


@pytest.mark.unit
def test_prepare_resumes_only_the_exact_candidate_manifest(tmp_path: Path) -> None:
    journal_path, journal = _activation_attempt(tmp_path)
    candidate_manifest = Path(journal.candidate_manifest_path)

    assert prepare_activation_journal(
        candidate_manifest,
        expected_active_path=Path(journal.active_path),
        minimum_free_bytes=0,
    ) == (journal_path, journal)

    with pytest.raises(ActivationPreflightError, match="different candidate manifest"):
        prepare_activation_journal(
            tmp_path / "foreign" / CANDIDATE_MANIFEST_FILENAME,
            expected_active_path=Path(journal.active_path),
            minimum_free_bytes=0,
        )


@pytest.mark.unit
def test_prepare_archives_an_exact_terminal_journal_before_a_new_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal_path, journal = _activation_attempt(tmp_path)
    completed = _persist_phase(journal_path, journal, "complete")
    active = Path(completed.active_path)
    candidate = tmp_path / f".qlever-ncit.candidate-{'c' * 32}"
    _write_qlever_store(candidate, "c" * 32)
    manifest_path = candidate / CANDIDATE_MANIFEST_FILENAME

    def manifest_for(
        path: Path,
        *,
        expected_policy: object = None,
    ):
        del expected_policy
        owner = "c" * 32 if path.parent == candidate else "a" * 32
        source = "5" * 64 if path.parent == candidate else "1" * 64
        return SimpleNamespace(
            candidate_path=str(path.parent.resolve()),
            active_store_path=str(active.resolve()),
            owner=owner,
            source_identity=source,
            loader=SimpleNamespace(
                store_format_identity="3" * 64,
                image=QLEVER_IMAGE,
                image_id="sha256:" + QLEVER_IMAGE.rsplit("@sha256:", 1)[1],
                cli_version=QLEVER_INDEX_VERSION,
            ),
        )

    monkeypatch.setattr(
        "ontolib.terminologies.ncit.activation.validate_ncit_sibling_manifest",
        manifest_for,
    )

    new_path, new_journal = prepare_activation_journal(
        manifest_path,
        expected_active_path=active,
        minimum_free_bytes=0,
    )

    history = tmp_path / (f".qlever-ncit.activation-{'a' * 32}-complete.json")
    assert new_path == journal_path
    assert new_journal.phase == "preflight"
    assert new_journal.candidate_owner == "c" * 32
    assert read_activation_journal(history) == completed


@pytest.mark.unit
def test_prepare_preserves_terminal_journal_when_new_candidate_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal_path, journal = _activation_attempt(tmp_path)
    completed = _persist_phase(journal_path, journal, "complete")
    invalid_manifest = tmp_path / "invalid" / CANDIDATE_MANIFEST_FILENAME

    def reject_manifest(
        _path: Path,
        *,
        expected_policy: object = None,
    ) -> object:
        del expected_policy
        raise RuntimeError("invalid replacement candidate")

    monkeypatch.setattr(
        "ontolib.terminologies.ncit.activation.validate_ncit_sibling_manifest",
        reject_manifest,
    )

    with pytest.raises(RuntimeError, match="invalid replacement candidate"):
        prepare_activation_journal(
            invalid_manifest,
            expected_active_path=Path(completed.active_path),
            minimum_free_bytes=0,
        )

    assert read_activation_journal(journal_path) == completed
