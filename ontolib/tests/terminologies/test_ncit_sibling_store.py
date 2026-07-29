from __future__ import annotations

import hashlib
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ontolib.core.exceptions import StorageError
from ontolib.terminologies.ncit.owl_download import (
    OwlContentError,
    validate_ncit_owl_pair,
)
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.ncit.sibling_store import (
    CANDIDATE_MANIFEST_FILENAME,
    OWNER_MARKER_FILENAME,
    OXIGRAPH_IMAGE,
    REJECTED_CANDIDATE_FILENAME,
    CandidateGraph,
    CandidateObservation,
    CandidateValidationPolicy,
    DockerOxigraphRuntime,
    LoaderIdentity,
    SiblingStoreValidationError,
    _select_int,
    _select_version,
    _wait_until_ready,
    build_ncit_sibling_store,
    run_docker,
    validate_ncit_sibling_manifest,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection, Iterator

    from ontolib.terminologies.ncit.owl_download import OwlArtifactPairManifest

_ONTOLOGY_IRI = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl"
_VERSION = "26.test"
_OWL = f"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:owl="http://www.w3.org/2002/07/owl#">
  <owl:Ontology rdf:about="{_ONTOLOGY_IRI}">
    <owl:versionInfo>{_VERSION}</owl:versionInfo>
  </owl:Ontology>
</rdf:RDF>
""".encode()


def _identity(payload: object) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _write_pair(root: Path) -> Path:
    root.mkdir(parents=True)
    artifacts: dict[str, dict[str, object]] = {}
    for variant in ("stated", "inferred"):
        archive = root / f"{variant}.zip"
        owl = root / f"{variant}.owl"
        archive.write_bytes(f"{variant}-archive".encode())
        owl.write_bytes(_OWL)
        record: dict[str, object] = {
            "variant": variant,
            "source_url": f"https://example.test/{variant}.zip",
            "archive_path": str(archive.resolve()),
            "file_path": str(owl.resolve()),
            "archive_size_bytes": archive.stat().st_size,
            "size_bytes": owl.stat().st_size,
            "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "owl_sha256": hashlib.sha256(_OWL).hexdigest(),
            "ontology_version": _VERSION,
            "ontology_iri": _ONTOLOGY_IRI,
        }
        record["artifact_identity"] = _identity(
            {
                "variant": variant,
                "source_url": record["source_url"],
                "archive_sha256": record["archive_sha256"],
                "owl_sha256": record["owl_sha256"],
                "ontology_version": _VERSION,
                "ontology_iri": _ONTOLOGY_IRI,
            }
        )
        artifacts[variant] = record
    manifest = {
        "schema_version": 1,
        "manifest_identity": _identity(
            {
                "schema_version": 1,
                "stated": artifacts["stated"]["artifact_identity"],
                "inferred": artifacts["inferred"]["artifact_identity"],
                "ontology_version": _VERSION,
                "ontology_iri": _ONTOLOGY_IRI,
            }
        ),
        "ontology_version": _VERSION,
        "ontology_iri": _ONTOLOGY_IRI,
        **artifacts,
    }
    path = root / "ncit-artifact-pair.json"
    path.write_text(json.dumps(manifest))
    return path


def _observation(**changes: object) -> CandidateObservation:
    base = CandidateObservation(
        default_triples=120,
        stated_triples=100,
        named_graphs=(CandidateGraph(graph_iri=STATED_GRAPH_IRI, triples=100),),
        default_version=_VERSION,
        stated_version=_VERSION,
        restriction_count=12,
        has_required_restriction=True,
        default_has_stated_only_sentinel=False,
        stated_has_stated_only_sentinel=True,
    )
    return CandidateObservation.model_validate({**base.model_dump(), **changes})


def _policy() -> CandidateValidationPolicy:
    return CandidateValidationPolicy(
        min_default_triples=100,
        max_default_triples=140,
        min_stated_triples=80,
        max_stated_triples=120,
        min_restrictions=10,
        max_restrictions=20,
    )


class _Runtime:
    def __init__(self, observation: CandidateObservation | None = None) -> None:
        self.observation = observation or _observation()
        self.calls: list[tuple[str, Path]] = []

    def identify_loader(self) -> LoaderIdentity:
        return LoaderIdentity(
            image=(
                "ghcr.io/oxigraph/oxigraph@sha256:"
                "cc943499d4724fbb348c75c623335c69a047de71c59852413b0d0467d3caebe3"
            ),
            image_id="sha256:" + "1" * 64,
            cli_version="oxigraph 0.5.3",
        )

    def load(
        self,
        pair: OwlArtifactPairManifest,
        candidate_path: Path,
        owner: str,
    ) -> None:
        del pair, owner
        self.calls.append(("load", candidate_path))

    async def observe(
        self,
        candidate_path: Path,
        owner: str,
        observer: Callable[[str], Awaitable[CandidateObservation]],
    ) -> CandidateObservation:
        del owner, observer
        self.calls.append(("observe", candidate_path))
        return self.observation


@pytest.mark.unit
async def test_build_persists_owned_validated_candidate_without_touching_active(
    tmp_path: Path,
) -> None:
    pair_path = _write_pair(tmp_path / "pair")
    active = tmp_path / "stores" / "oxigraph-ncit"
    active.mkdir(parents=True)
    active_sentinel = active / "active"
    active_sentinel.write_text("unchanged")
    runtime = _Runtime()

    manifest = await build_ncit_sibling_store(
        pair_path,
        active_store_path=active,
        owner="a" * 32,
        policy=_policy(),
        runtime=runtime,
    )

    candidate = active.parent / f".{active.name}.candidate-{'a' * 32}"
    assert manifest.candidate_path == str(candidate.resolve())
    assert manifest.active_store_path == str(active.resolve())
    assert manifest.owner == "a" * 32
    assert manifest.ontology_version == _VERSION
    assert manifest.graph_layout.stated_graph_iri == STATED_GRAPH_IRI
    assert manifest.observation == _observation()
    assert manifest.inferred_artifact.variant == "inferred"
    assert manifest.stated_artifact.variant == "stated"
    assert len(manifest.inferred_artifact.sha256) == 64
    assert len(manifest.stated_artifact.sha256) == 64
    assert manifest.loader.store_format_identity
    assert (candidate / CANDIDATE_MANIFEST_FILENAME).exists()
    assert (
        validate_ncit_sibling_manifest(candidate / CANDIDATE_MANIFEST_FILENAME)
        == manifest
    )
    assert not (candidate / REJECTED_CANDIDATE_FILENAME).exists()
    assert active_sentinel.read_text() == "unchanged"
    assert runtime.calls == [("load", candidate), ("observe", candidate)]


@pytest.mark.unit
async def test_source_identity_excludes_candidate_owner_and_paths(
    tmp_path: Path,
) -> None:
    identities = []
    for suffix, owner in (("one", "1" * 32), ("two", "2" * 32)):
        pair_path = _write_pair(tmp_path / suffix / "pair")
        active = tmp_path / suffix / "stores" / "oxigraph-ncit"
        active.mkdir(parents=True)
        result = await build_ncit_sibling_store(
            pair_path,
            active_store_path=active,
            owner=owner,
            policy=_policy(),
            runtime=_Runtime(),
        )
        identities.append(result.source_identity)

    assert identities[0] == identities[1]


@pytest.mark.unit
async def test_candidate_manifest_revalidation_rejects_identity_or_owner_drift(
    tmp_path: Path,
) -> None:
    pair_path = _write_pair(tmp_path / "pair")
    active = tmp_path / "stores" / "oxigraph-ncit"
    active.mkdir(parents=True)
    owner = "4" * 32
    manifest = await build_ncit_sibling_store(
        pair_path,
        active_store_path=active,
        owner=owner,
        policy=_policy(),
        runtime=_Runtime(),
    )
    path = Path(manifest.candidate_path) / CANDIDATE_MANIFEST_FILENAME
    document = json.loads(path.read_text())
    document["source_identity"] = "0" * 64
    path.write_text(json.dumps(document))
    with pytest.raises(SiblingStoreValidationError, match="source identity"):
        validate_ncit_sibling_manifest(path)

    document["source_identity"] = manifest.source_identity
    path.write_text(json.dumps(document))
    (path.parent / ".ontoprism-ncit-owner").write_text("5" * 32)
    with pytest.raises(SiblingStoreValidationError, match="owner marker"):
        validate_ncit_sibling_manifest(path)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("json", "unreadable"),
        ("schema", "unsupported"),
        ("path", "manifest path"),
        ("marker", "owner marker"),
        ("loader", "loader identity"),
        ("layout", "graph layout"),
    ],
)
async def test_candidate_manifest_revalidation_rejects_proof_drift(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    pair_path = _write_pair(tmp_path / "pair")
    active = tmp_path / "stores" / "oxigraph-ncit"
    active.mkdir(parents=True)
    manifest = await build_ncit_sibling_store(
        pair_path,
        active_store_path=active,
        owner="6" * 32,
        policy=_policy(),
        runtime=_Runtime(),
    )
    path = Path(manifest.candidate_path) / CANDIDATE_MANIFEST_FILENAME
    document = json.loads(path.read_text())
    if mutation == "json":
        path.write_text("{")
    elif mutation == "schema":
        document["schema_version"] = 99
        path.write_text(json.dumps(document))
    elif mutation == "path":
        document["candidate_path"] = str(tmp_path / "other")
        path.write_text(json.dumps(document))
    elif mutation == "marker":
        (path.parent / OWNER_MARKER_FILENAME).unlink()
    elif mutation == "loader":
        document["loader"]["image"] = "example.invalid/oxigraph@sha256:" + "0" * 64
        path.write_text(json.dumps(document))
    else:
        document["graph_layout"]["default_graph"] = "stated"
        path.write_text(json.dumps(document))

    with pytest.raises(SiblingStoreValidationError, match=message):
        validate_ncit_sibling_manifest(path)


@pytest.mark.unit
@pytest.mark.parametrize("mutation", ["unknown-field", "coerced-count"])
async def test_candidate_manifest_revalidation_rejects_malformed_shapes(
    tmp_path: Path,
    mutation: str,
) -> None:
    pair_path = _write_pair(tmp_path / "pair")
    active = tmp_path / "stores" / "oxigraph-ncit"
    active.mkdir(parents=True)
    manifest = await build_ncit_sibling_store(
        pair_path,
        active_store_path=active,
        owner="7" * 32,
        policy=_policy(),
        runtime=_Runtime(),
    )
    path = Path(manifest.candidate_path) / CANDIDATE_MANIFEST_FILENAME
    document = json.loads(path.read_text())
    if mutation == "unknown-field":
        document["unrecognized_proof"] = "must not be ignored"
    else:
        document["observation"]["default_triples"] = str(
            document["observation"]["default_triples"]
        )
    path.write_text(json.dumps(document))

    with pytest.raises(SiblingStoreValidationError, match="unreadable"):
        validate_ncit_sibling_manifest(path)


@pytest.mark.unit
async def test_pair_is_revalidated_before_candidate_or_runtime_effects(
    tmp_path: Path,
) -> None:
    pair_path = _write_pair(tmp_path / "pair")
    pair = json.loads(pair_path.read_text())
    Path(pair["stated"]["file_path"]).write_bytes(b"corrupt")
    active = tmp_path / "stores" / "oxigraph-ncit"
    active.mkdir(parents=True)
    runtime = _Runtime()

    with pytest.raises(OwlContentError, match="SHA-256"):
        await build_ncit_sibling_store(
            pair_path,
            active_store_path=active,
            owner="b" * 32,
            policy=_policy(),
            runtime=runtime,
        )

    assert runtime.calls == []
    assert list(active.parent.iterdir()) == [active]


@pytest.mark.unit
async def test_invalid_owner_is_rejected_before_runtime_effects(
    tmp_path: Path,
) -> None:
    pair_path = _write_pair(tmp_path / "pair")
    active = tmp_path / "stores" / "oxigraph-ncit"
    active.mkdir(parents=True)
    runtime = _Runtime()

    with pytest.raises(SiblingStoreValidationError, match="candidate owner"):
        await build_ncit_sibling_store(
            pair_path,
            active_store_path=active,
            owner="../not-owned",
            policy=_policy(),
            runtime=runtime,
        )

    assert runtime.calls == []


_INVALID_OBSERVATIONS = (
    (
        "default version",
        _observation(default_version="other"),
        "default graph ontology version",
    ),
    (
        "stated version",
        _observation(stated_version="other"),
        "stated graph ontology version",
    ),
    (
        "extra graph",
        _observation(
            named_graphs=(
                CandidateGraph(graph_iri=STATED_GRAPH_IRI, triples=100),
                CandidateGraph(graph_iri="urn:unexpected", triples=1),
            )
        ),
        "named-graph layout",
    ),
    (
        "graph count",
        _observation(
            named_graphs=(CandidateGraph(graph_iri=STATED_GRAPH_IRI, triples=99),)
        ),
        "stated graph count",
    ),
    (
        "default too small",
        _observation(default_triples=99),
        "default graph triple count",
    ),
    (
        "stated too large",
        _observation(
            stated_triples=121,
            named_graphs=(CandidateGraph(graph_iri=STATED_GRAPH_IRI, triples=121),),
        ),
        "stated graph triple count",
    ),
    (
        "restrictions",
        _observation(restriction_count=9),
        "restriction count",
    ),
    (
        "required restriction",
        _observation(has_required_restriction=False),
        "required C6135 restriction",
    ),
    (
        "default sentinel",
        _observation(default_has_stated_only_sentinel=True),
        "default graph unexpectedly contains",
    ),
    (
        "stated sentinel",
        _observation(stated_has_stated_only_sentinel=False),
        "stated graph lacks",
    ),
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("_case", "observation", "message"),
    _INVALID_OBSERVATIONS,
)
async def test_every_candidate_invariant_rejects_and_marks_inactive(
    tmp_path: Path,
    _case: str,
    observation: CandidateObservation,
    message: str,
) -> None:
    pair_path = _write_pair(tmp_path / "pair")
    active = tmp_path / "stores" / "oxigraph-ncit"
    active.mkdir(parents=True)
    owner = "c" * 32
    candidate = active.parent / f".{active.name}.candidate-{owner}"

    with pytest.raises(SiblingStoreValidationError, match=message):
        await build_ncit_sibling_store(
            pair_path,
            active_store_path=active,
            owner=owner,
            policy=_policy(),
            runtime=_Runtime(observation),
        )

    assert (candidate / REJECTED_CANDIDATE_FILENAME).exists()
    assert not (candidate / CANDIDATE_MANIFEST_FILENAME).exists()
    assert list(active.iterdir()) == []


@pytest.mark.unit
async def test_candidate_path_cannot_preexist_or_target_missing_active(
    tmp_path: Path,
) -> None:
    pair_path = _write_pair(tmp_path / "pair")
    missing = tmp_path / "stores" / "missing"
    with pytest.raises(SiblingStoreValidationError, match="active store"):
        await build_ncit_sibling_store(
            pair_path,
            active_store_path=missing,
            owner="d" * 32,
            policy=_policy(),
            runtime=_Runtime(),
        )

    active = tmp_path / "stores" / "oxigraph-ncit"
    active.mkdir(parents=True)
    candidate = active.parent / f".{active.name}.candidate-{'d' * 32}"
    candidate.mkdir()
    with pytest.raises(SiblingStoreValidationError, match="already exists"):
        await build_ncit_sibling_store(
            pair_path,
            active_store_path=active,
            owner="d" * 32,
            policy=_policy(),
            runtime=_Runtime(),
        )


class _DockerDouble:
    def __init__(
        self,
        results: list[subprocess.CompletedProcess[str]],
    ) -> None:
        self.results = results
        self.calls: list[tuple[tuple[str, ...], bool]] = []

    def __call__(
        self, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, check))
        result = self.results.pop(0)
        if check and result.returncode:
            raise subprocess.CalledProcessError(
                result.returncode,
                result.args,
                output=result.stdout,
                stderr=result.stderr,
            )
        return result


def _completed(
    stdout: str = "", *, returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["docker"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class _RowsClient:
    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = rows

    async def select_once(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str]]:
        del query, required_variables
        return self.rows


@pytest.mark.unit
async def test_candidate_scalar_queries_reject_malformed_results() -> None:
    with pytest.raises(StorageError, match="no unique"):
        await _select_int(_RowsClient([]), "query", "count")
    with pytest.raises(StorageError, match="non-integer"):
        await _select_int(
            _RowsClient([{"count": "not-an-int"}]),
            "query",
            "count",
        )
    assert await _select_version(_RowsClient([]), None) is None
    with pytest.raises(StorageError, match="unique ontology version"):
        await _select_version(
            _RowsClient([{"version": "one"}, {"version": "two"}]),
            STATED_GRAPH_IRI,
        )


@pytest.mark.unit
async def test_default_readiness_probe_times_out_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _UnavailableClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _UnavailableClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def ask(self, _query: str) -> bool:
            raise StorageError("not ready")

    monkeypatch.setattr(
        "ontolib.terminologies.ncit.sibling_store.OxigraphHttpClient",
        _UnavailableClient,
    )
    with pytest.raises(SiblingStoreValidationError, match="did not become ready"):
        await _wait_until_ready(
            "http://127.0.0.1:9",
            timeout_seconds=0.001,
            retry_delay_seconds=0,
        )


@pytest.mark.unit
def test_run_docker_requires_installed_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(SiblingStoreValidationError, match="Docker is required"):
        run_docker("version")


@pytest.mark.unit
def test_runtime_requires_exact_pinned_image_and_cli_identity() -> None:
    image_id = "sha256:" + "9" * 64
    docker = _DockerDouble(
        [
            _completed(
                json.dumps(
                    [
                        {
                            "Id": image_id,
                            "RepoDigests": [OXIGRAPH_IMAGE],
                        }
                    ]
                )
            ),
            _completed("oxigraph 0.5.3\n"),
        ]
    )

    identity = DockerOxigraphRuntime(docker_run=docker).identify_loader()

    assert identity == LoaderIdentity(
        image=OXIGRAPH_IMAGE,
        image_id=image_id,
        cli_version="oxigraph 0.5.3",
    )
    assert docker.calls == [
        (("image", "inspect", OXIGRAPH_IMAGE), True),
        (("run", "--rm", OXIGRAPH_IMAGE, "--version"), True),
    ]


@pytest.mark.unit
def test_runtime_rejects_malformed_image_inspection() -> None:
    docker = _DockerDouble([_completed("{}")])
    with pytest.raises(SiblingStoreValidationError, match="malformed"):
        DockerOxigraphRuntime(docker_run=docker).identify_loader()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("details", "version", "message"),
    [
        (
            [{"Id": "sha256:" + "9" * 64, "RepoDigests": ["example/wrong@sha256:1"]}],
            "oxigraph 0.5.3",
            "pinned digest",
        ),
        (
            [{"Id": "sha256:" + "9" * 64, "RepoDigests": [OXIGRAPH_IMAGE]}],
            "oxigraph 0.6.0",
            "CLI version",
        ),
    ],
)
def test_runtime_rejects_loader_identity_drift(
    details: list[dict[str, object]],
    version: str,
    message: str,
) -> None:
    docker = _DockerDouble(
        [_completed(json.dumps(details)), _completed(version + "\n")]
    )

    with pytest.raises(SiblingStoreValidationError, match=message):
        DockerOxigraphRuntime(docker_run=docker).identify_loader()


@pytest.mark.unit
def test_runtime_loads_inferred_default_then_stated_named_offline(
    tmp_path: Path,
) -> None:
    pair_path = _write_pair(tmp_path / "pair")
    pair = validate_ncit_owl_pair(pair_path)
    candidate = tmp_path / "stores" / f".oxigraph-ncit.candidate-{'e' * 32}"
    candidate.mkdir(parents=True)
    docker = _DockerDouble([_completed(), _completed()])

    DockerOxigraphRuntime(docker_run=docker).load(pair, candidate, "e" * 32)

    inferred, stated = (call[0] for call in docker.calls)
    for command, artifact in (
        (inferred, Path(pair.inferred.file_path)),
        (stated, Path(pair.stated.file_path)),
    ):
        assert command[:3] == ("run", "--rm", "--label")
        assert f"org.ontoprism.candidate-owner={'e' * 32}" in command
        assert f"type=bind,src={candidate.resolve()},dst=/data" in command
        assert f"type=bind,src={artifact.resolve()},dst=/input.owl,readonly" in command
        load = command.index("load")
        assert command[load : load + 7] == (
            "load",
            "--location",
            "/data",
            "--file",
            "/input.owl",
            "--format",
            "application/rdf+xml",
        )
        assert command[-1] == "--non-atomic"
        assert "--lenient" not in command
    assert "--graph" not in inferred
    assert stated[-3:-1] == ("--graph", STATED_GRAPH_IRI)
    assert stated.index("load") < stated.index("--graph")


@pytest.mark.unit
def test_runtime_surfaces_offline_loader_failure(tmp_path: Path) -> None:
    pair = validate_ncit_owl_pair(_write_pair(tmp_path / "pair"))
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    docker = _DockerDouble([_completed(returncode=2, stderr="RDF parse failed")])

    with pytest.raises(SiblingStoreValidationError, match="RDF parse failed"):
        DockerOxigraphRuntime(docker_run=docker).load(pair, candidate, "e" * 32)


@pytest.mark.unit
async def test_runtime_serves_on_loopback_and_verifies_owner_before_teardown(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    owner = "f" * 32
    (candidate / ".ontoprism-ncit-owner").write_text(owner + "\n")
    container_id = "7" * 64
    details = [
        {
            "Id": container_id,
            "Config": {
                "Labels": {"org.ontoprism.candidate-owner": owner},
            },
            "Mounts": [
                {
                    "Source": str(candidate.resolve()),
                    "Destination": "/data",
                }
            ],
        }
    ]
    docker = _DockerDouble(
        [
            _completed(container_id + "\n"),
            _completed("127.0.0.1:49152\n"),
            _completed(json.dumps(details)),
            _completed(),
        ]
    )
    registered: list[str] = []

    @contextmanager
    def connection_scope(url: str) -> Iterator[None]:
        registered.append(url)
        yield

    async def ready(_url: str) -> None:
        return None

    async def observer(url: str) -> CandidateObservation:
        assert url == "http://127.0.0.1:49152"
        return _observation()

    runtime = DockerOxigraphRuntime(
        docker_run=docker,
        connection_scope=connection_scope,
        wait_until_ready=ready,
    )
    result = await runtime.observe(candidate, owner, observer)

    assert result == _observation()
    assert registered == ["http://127.0.0.1:49152"]
    start = docker.calls[0][0]
    assert start[:4] == (
        "run",
        "--detach",
        "--name",
        f"ontoprism-ncit-candidate-{owner}",
    )
    assert (
        start[start.index("--publish")],
        start[start.index("--publish") + 1],
    ) == ("--publish", "127.0.0.1::7878")
    assert docker.calls[-2:] == [
        (("inspect", container_id), True),
        (("rm", "--force", container_id), True),
    ]


@pytest.mark.unit
async def test_runtime_propagates_observer_failure_after_owned_teardown(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    owner = "8" * 32
    (candidate / ".ontoprism-ncit-owner").write_text(owner + "\n")
    container_id = "6" * 64
    docker = _DockerDouble(
        [
            _completed(container_id),
            _completed("127.0.0.1:49153"),
            _completed(
                json.dumps(
                    [
                        {
                            "Id": container_id,
                            "Config": {
                                "Labels": {
                                    "org.ontoprism.candidate-owner": owner,
                                }
                            },
                            "Mounts": [
                                {
                                    "Source": str(candidate.resolve()),
                                    "Destination": "/data",
                                }
                            ],
                        }
                    ]
                )
            ),
            _completed(),
        ]
    )

    async def ready(_url: str) -> None:
        return None

    async def fail(_url: str) -> CandidateObservation:
        raise StorageError("malformed candidate response")

    runtime = DockerOxigraphRuntime(
        docker_run=docker,
        wait_until_ready=ready,
    )
    with pytest.raises(StorageError, match="malformed candidate"):
        await runtime.observe(candidate, owner, fail)

    assert docker.calls[-1][0] == ("rm", "--force", container_id)


@pytest.mark.unit
async def test_runtime_preserves_primary_failure_when_teardown_also_fails(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    owner = "7" * 32
    (candidate / OWNER_MARKER_FILENAME).write_text(owner)
    container_id = "2" * 64
    docker = _DockerDouble(
        [
            _completed(container_id),
            _completed("127.0.0.1:49156"),
            _completed("{}"),
        ]
    )

    async def ready(_url: str) -> None:
        return None

    async def fail(_url: str) -> CandidateObservation:
        raise StorageError("primary observation failure")

    runtime = DockerOxigraphRuntime(
        docker_run=docker,
        wait_until_ready=ready,
    )
    with pytest.raises(StorageError, match="primary observation failure") as raised:
        await runtime.observe(candidate, owner, fail)

    assert any("teardown also failed" in note for note in raised.value.__notes__)


def _container_details(
    container_id: str,
    candidate: Path,
    owner: str,
) -> dict[str, object]:
    return {
        "Id": container_id,
        "Config": {
            "Labels": {"org.ontoprism.candidate-owner": owner},
        },
        "Mounts": [
            {
                "Source": str(candidate.resolve()),
                "Destination": "/data",
            }
        ],
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("bad_start", "bad_port", "message"),
    [
        ("not-an-id", None, "container ID"),
        (None, "0.0.0.0:7878", "port mapping"),
    ],
)
async def test_runtime_rejects_ambiguous_container_startup_identity(
    tmp_path: Path,
    bad_start: str | None,
    bad_port: str | None,
    message: str,
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    owner = "9" * 32
    (candidate / OWNER_MARKER_FILENAME).write_text(owner)
    container_id = "5" * 64
    results = [_completed(bad_start or container_id)]
    if bad_start is not None:
        results.extend(
            [
                _completed(
                    json.dumps([_container_details(container_id, candidate, owner)])
                ),
                _completed(),
            ]
        )
    else:
        results.extend(
            [
                _completed(bad_port or "127.0.0.1:49154"),
                _completed(
                    json.dumps([_container_details(container_id, candidate, owner)])
                ),
                _completed(),
            ]
        )
    docker = _DockerDouble(results)
    runtime = DockerOxigraphRuntime(docker_run=docker)

    with pytest.raises(SiblingStoreValidationError, match=message):
        await runtime.observe(candidate, owner, lambda _url: _never_observe())

    assert docker.calls[-1][0] == ("rm", "--force", container_id)


async def _never_observe() -> CandidateObservation:
    raise AssertionError("observer must not run")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("malformed", "inspection was malformed"),
        ("id", "identity changed"),
        ("owner", "owner identity"),
        ("mount", "data mount"),
    ],
)
async def test_runtime_refuses_unverifiable_container_teardown(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    owner = "a" * 32
    (candidate / OWNER_MARKER_FILENAME).write_text(owner)
    container_id = "4" * 64
    details = _container_details(container_id, candidate, owner)
    if mutation == "malformed":
        inspection = "{}"
    else:
        if mutation == "id":
            details["Id"] = "3" * 64
        elif mutation == "owner":
            details["Config"] = {"Labels": {"org.ontoprism.candidate-owner": "other"}}
        else:
            details["Mounts"] = [
                {"Source": str(tmp_path / "other"), "Destination": "/data"}
            ]
        inspection = json.dumps([details])
    docker = _DockerDouble(
        [
            _completed(container_id),
            _completed("127.0.0.1:49155"),
            _completed(inspection),
        ]
    )

    async def ready(_url: str) -> None:
        return None

    async def observer(_url: str) -> CandidateObservation:
        return _observation()

    runtime = DockerOxigraphRuntime(
        docker_run=docker,
        wait_until_ready=ready,
    )
    with pytest.raises(SiblingStoreValidationError, match=message):
        await runtime.observe(candidate, owner, observer)

    assert all(call[0][0] != "rm" for call in docker.calls)
