"""Contracts for the source-bound Uberon/CL QLever index build."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ontolib.core.data_build_tools import (
    JENA_JRE_IMAGE,
    JENA_RIOT_ARTIFACT,
    QLEVER_IMAGE,
    QLEVER_TOOL,
)
from ontolib.core.download_cache import CacheManifest, DownloadOutcome
from ontolib.terminologies.ncit.sibling_store import (
    QLEVER_INDEX_VERSION,
    DockerQleverRuntime,
    LoaderIdentity,
)
from ontolib.terminologies.uberon.store import (
    UBERON_ARTIFACT_MANIFEST_FILENAME,
    UBERON_INDEX_MANIFEST_FILENAME,
    UBERON_OWNER_MARKER_FILENAME,
    UBERON_VERSION_IRI,
    UberonArtifactError,
    UberonArtifactManifest,
    UberonIndexObservation,
    UberonServingFingerprint,
    build_uberon_index,
    certify_uberon_artifact,
    download_uberon_artifact,
    validate_uberon_artifact,
    validate_uberon_index_manifest,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

pytestmark = pytest.mark.unit

_SOURCE_URL = (
    "https://github.com/obophenotype/uberon/releases/download/v2026-06-23/uberon.owl"
)
_RDF = b"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns:owl="http://www.w3.org/2002/07/owl#"
 xmlns:dc="http://purl.org/dc/elements/1.1/">
 <owl:Ontology rdf:about="http://purl.obolibrary.org/obo/uberon.owl">
  <owl:versionIRI rdf:resource="http://purl.obolibrary.org/obo/uberon/releases/2026-06-19/uberon.owl"/>
  <dc:source rdf:resource="http://purl.obolibrary.org/obo/cl.owl"/>
 </owl:Ontology>
 <owl:Class rdf:about="http://purl.obolibrary.org/obo/UBERON_0002048"/>
 <owl:Class rdf:about="http://purl.obolibrary.org/obo/CL_0000000"/>
</rdf:RDF>
"""


def _loader() -> LoaderIdentity:
    return LoaderIdentity(
        image=QLEVER_IMAGE,
        image_id="sha256:" + "a" * 64,
        cli_version=QLEVER_INDEX_VERSION,
        tool=QLEVER_TOOL,
        converter=JENA_RIOT_ARTIFACT.identity,
        converter_runtime_image=JENA_JRE_IMAGE,
    )


def _artifact(tmp_path: Path) -> UberonArtifactManifest:
    source = tmp_path / "uberon.owl"
    source.write_bytes(_RDF)
    return certify_uberon_artifact(
        source,
        source_url=_SOURCE_URL,
        expected_version_iri=UBERON_VERSION_IRI,
    )


class _Runtime:
    def __init__(self, observation: UberonIndexObservation) -> None:
        self.observation = observation
        self.loaded: tuple[Path, Path, str] | None = None

    def identify_loader(self) -> LoaderIdentity:
        return _loader()

    def load_default_graph(
        self, source_path: Path, candidate_path: Path, owner: str
    ) -> None:
        self.loaded = (source_path, candidate_path, owner)
        (candidate_path / "uberon.index.spo").write_text("index")

    async def observe_default_graph(
        self,
        candidate_path: Path,
        owner: str,
        observer: Callable[[str], Awaitable[UberonIndexObservation]],
    ) -> UberonIndexObservation:
        del candidate_path, owner, observer
        return self.observation


def _observation(**updates: object) -> UberonIndexObservation:
    values: dict[str, object] = {
        "version_iri": UBERON_VERSION_IRI,
        "triples": 1_250_000,
        "has_uberon_lung": True,
        "has_cell_class": True,
        "has_ncit_xref": True,
        "serving": UberonServingFingerprint(
            rows=100,
            sha256="f" * 64,
            uberon_classes=10,
            cl_classes=5,
            uberon_searchable_classes=9,
            cl_searchable_classes=5,
        ),
    }
    values.update(updates)
    return UberonIndexObservation.model_validate(values)


def test_certification_binds_the_completed_artifact_identity(tmp_path: Path) -> None:
    manifest = _artifact(tmp_path)

    assert manifest.source_url == _SOURCE_URL
    assert manifest.version_iri == UBERON_VERSION_IRI
    assert manifest.size_bytes == len(_RDF)
    assert manifest.sha256 == hashlib.sha256(_RDF).hexdigest()
    assert manifest.includes_cell_ontology is True
    assert len(manifest.artifact_identity) == 64
    assert (
        Path(manifest.file_path).parent / UBERON_ARTIFACT_MANIFEST_FILENAME
    ).is_file()


def test_artifact_validation_recomputes_content_identity(tmp_path: Path) -> None:
    manifest = _artifact(tmp_path)
    Path(manifest.file_path).write_bytes(_RDF + b"<!-- changed -->")

    with pytest.raises(UberonArtifactError, match="digest"):
        validate_uberon_artifact(
            Path(manifest.file_path).parent / UBERON_ARTIFACT_MANIFEST_FILENAME
        )


def test_certification_rejects_a_different_release(tmp_path: Path) -> None:
    source = tmp_path / "uberon.owl"
    source.write_bytes(_RDF.replace(b"2026-06-19", b"2026-04-01"))

    with pytest.raises(UberonArtifactError, match="version IRI"):
        certify_uberon_artifact(
            source,
            source_url=_SOURCE_URL,
            expected_version_iri=UBERON_VERSION_IRI,
        )


def test_certification_requires_cell_ontology_content(tmp_path: Path) -> None:
    source = tmp_path / "uberon.owl"
    source.write_bytes(_RDF.replace(b"CL_0000000", b"UBERON_0000000"))

    with pytest.raises(UberonArtifactError, match="Cell Ontology"):
        certify_uberon_artifact(
            source,
            source_url=_SOURCE_URL,
            expected_version_iri=UBERON_VERSION_IRI,
        )


@pytest.mark.asyncio
async def test_download_certifies_only_after_the_complete_transfer(
    tmp_path: Path,
) -> None:
    async def download(
        url: str, destination: Path, *, max_retries: int
    ) -> DownloadOutcome:
        assert url == _SOURCE_URL
        assert max_retries == 4
        destination.write_bytes(_RDF)
        return DownloadOutcome(
            path=str(destination),
            status="downloaded",
            manifest=CacheManifest(
                url=url,
                downloaded_at="2026-08-10T00:00:00+00:00",
                size_bytes=len(_RDF),
            ),
        )

    manifest = await download_uberon_artifact(
        tmp_path,
        source_url=_SOURCE_URL,
        expected_version_iri=UBERON_VERSION_IRI,
        max_retries=4,
        downloader=download,
    )

    assert Path(manifest.file_path).read_bytes() == _RDF
    assert manifest.sha256 == hashlib.sha256(_RDF).hexdigest()


@pytest.mark.asyncio
async def test_download_rejects_a_cache_from_another_source(tmp_path: Path) -> None:
    async def download(
        _url: str, destination: Path, *, max_retries: int
    ) -> DownloadOutcome:
        del max_retries
        destination.write_bytes(_RDF)
        return DownloadOutcome(
            path=str(destination),
            status="offline",
            manifest=CacheManifest(
                url="https://example.invalid/wrong.owl",
                downloaded_at="2026-08-10T00:00:00+00:00",
                size_bytes=len(_RDF),
            ),
        )

    with pytest.raises(UberonArtifactError, match="source URL"):
        await download_uberon_artifact(
            tmp_path,
            source_url=_SOURCE_URL,
            expected_version_iri=UBERON_VERSION_IRI,
            downloader=download,
        )


@pytest.mark.asyncio
async def test_download_rejects_bytes_that_differ_from_publisher_digest(
    tmp_path: Path,
) -> None:
    async def download(
        url: str, destination: Path, *, max_retries: int
    ) -> DownloadOutcome:
        del max_retries
        destination.write_bytes(_RDF)
        return DownloadOutcome(
            path=str(destination),
            status="downloaded",
            manifest=CacheManifest(
                url=url,
                downloaded_at="2026-08-10T00:00:00+00:00",
                size_bytes=len(_RDF),
            ),
        )

    with pytest.raises(UberonArtifactError, match="publisher digest"):
        await download_uberon_artifact(
            tmp_path,
            source_url=_SOURCE_URL,
            expected_version_iri=UBERON_VERSION_IRI,
            expected_sha256="0" * 64,
            downloader=download,
        )


@pytest.mark.asyncio
async def test_build_publishes_only_a_validated_index(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    target = tmp_path / "qlever-uberon"
    runtime = _Runtime(_observation())

    manifest = await build_uberon_index(
        tmp_path / UBERON_ARTIFACT_MANIFEST_FILENAME,
        target,
        runtime=runtime,
    )

    assert target.is_dir()
    assert (target / UBERON_INDEX_MANIFEST_FILENAME).is_file()
    assert manifest.artifact_identity == artifact.artifact_identity
    assert manifest.loader == _loader()
    assert manifest.observation.model_dump() == _observation().model_dump()
    assert runtime.loaded is not None
    assert runtime.loaded[0] == Path(artifact.file_path)
    assert (
        validate_uberon_index_manifest(target / UBERON_INDEX_MANIFEST_FILENAME)
        == manifest
    )


@pytest.mark.asyncio
async def test_index_manifest_validation_rejects_a_forged_source_identity(
    tmp_path: Path,
) -> None:
    _artifact(tmp_path)
    target = tmp_path / "qlever-uberon"
    await build_uberon_index(
        tmp_path / UBERON_ARTIFACT_MANIFEST_FILENAME,
        target,
        runtime=_Runtime(_observation()),
        owner="a" * 32,
    )
    manifest_path = target / UBERON_INDEX_MANIFEST_FILENAME
    payload = json.loads(manifest_path.read_text())
    payload["source_identity"] = "0" * 64
    manifest_path.write_text(json.dumps(payload))

    with pytest.raises(UberonArtifactError, match="source identity"):
        validate_uberon_index_manifest(manifest_path)


@pytest.mark.asyncio
async def test_index_manifest_validation_binds_location_owner_and_artifact(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    target = tmp_path / "qlever-uberon"
    await build_uberon_index(
        tmp_path / UBERON_ARTIFACT_MANIFEST_FILENAME,
        target,
        runtime=_Runtime(_observation()),
        owner="b" * 32,
    )
    manifest_path = target / UBERON_INDEX_MANIFEST_FILENAME

    (target / UBERON_OWNER_MARKER_FILENAME).write_text("c" * 32 + "\n")
    with pytest.raises(UberonArtifactError, match="owner marker"):
        validate_uberon_index_manifest(manifest_path)

    (target / UBERON_OWNER_MARKER_FILENAME).write_text("b" * 32 + "\n")
    artifact_payload = json.loads(
        (tmp_path / UBERON_ARTIFACT_MANIFEST_FILENAME).read_text()
    )
    artifact_payload["artifact_identity"] = "d" * 64
    (tmp_path / UBERON_ARTIFACT_MANIFEST_FILENAME).write_text(
        json.dumps(artifact_payload)
    )
    with pytest.raises(UberonArtifactError, match="artifact identity"):
        validate_uberon_index_manifest(manifest_path)

    assert artifact.artifact_identity != "d" * 64


@pytest.mark.asyncio
async def test_index_manifest_cannot_redefine_the_loader_format_identity(
    tmp_path: Path,
) -> None:
    _artifact(tmp_path)
    target = tmp_path / "qlever-uberon"
    await build_uberon_index(
        tmp_path / UBERON_ARTIFACT_MANIFEST_FILENAME,
        target,
        runtime=_Runtime(_observation()),
        owner="e" * 32,
    )
    manifest_path = target / UBERON_INDEX_MANIFEST_FILENAME
    payload = json.loads(manifest_path.read_text())
    payload["loader"]["store_format_identity"] = "f" * 64
    identity_payload = {
        "schema_version": payload["schema_version"],
        "artifact_identity": payload["artifact_identity"],
        "loader": payload["loader"],
        "observation": payload["observation"],
    }
    payload["source_identity"] = hashlib.sha256(
        json.dumps(
            identity_payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    manifest_path.write_text(json.dumps(payload))

    with pytest.raises(UberonArtifactError, match="loader identity"):
        validate_uberon_index_manifest(manifest_path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("version_iri", "wrong", "version"),
        ("triples", 4, "triple count"),
        ("has_uberon_lung", False, "Uberon"),
        ("has_cell_class", False, "Cell Ontology"),
        ("has_ncit_xref", False, "NCIt cross-reference"),
    ],
)
async def test_build_reject_gate_is_live(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    _artifact(tmp_path)
    target = tmp_path / "qlever-uberon"

    with pytest.raises(UberonArtifactError, match=message):
        await build_uberon_index(
            tmp_path / UBERON_ARTIFACT_MANIFEST_FILENAME,
            target,
            runtime=_Runtime(_observation(**{field: value})),
        )

    assert not target.exists()


@pytest.mark.asyncio
async def test_build_refuses_to_overwrite_an_existing_index(tmp_path: Path) -> None:
    _artifact(tmp_path)
    target = tmp_path / "qlever-uberon"
    target.mkdir()

    with pytest.raises(UberonArtifactError, match="already exists"):
        await build_uberon_index(
            tmp_path / UBERON_ARTIFACT_MANIFEST_FILENAME,
            target,
            runtime=_Runtime(_observation()),
        )


class _DockerDouble:
    def __init__(self, results: list[subprocess.CompletedProcess[str]]) -> None:
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


def _completed(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["docker"], returncode=0, stdout=stdout, stderr=""
    )


def test_real_runtime_stream_converts_and_indexes_one_default_graph(
    tmp_path: Path,
) -> None:
    source = tmp_path / "uberon.owl"
    source.write_bytes(_RDF)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    docker = _DockerDouble([_completed(), _completed()])
    runtime = DockerQleverRuntime(
        docker_run=docker,
        jena_install_dir=tmp_path / "jena",
        index_basename="uberon",
        owner_marker_filename=UBERON_OWNER_MARKER_FILENAME,
        server_memory="2G",
        server_cache="256M",
        server_allocator="256M",
    )

    runtime.load_default_graph(source, candidate, "a" * 32)

    convert, index = (call[0] for call in docker.calls)
    assert f"type=bind,src={source.resolve()},dst=/input.owl,readonly" in convert
    assert convert[-1].endswith("/data/source.nt")
    assert index[index.index("-i") : index.index("-m")] == (
        "-i",
        "uberon",
        "-f",
        "source.nt",
        "-g",
        "-",
        "-F",
        "nt",
        "-p",
        "true",
    )
    assert not (candidate / "source.nt").exists()


@pytest.mark.asyncio
async def test_real_runtime_serves_uberon_index_with_bounded_resources(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    owner = "b" * 32
    (candidate / UBERON_OWNER_MARKER_FILENAME).write_text(owner + "\n")
    container_id = "c" * 64
    details = [
        {
            "Id": container_id,
            "Config": {"Labels": {"org.ontoprism.candidate-owner": owner}},
            "Mounts": [{"Source": str(candidate.resolve()), "Destination": "/data"}],
        }
    ]
    docker = _DockerDouble(
        [
            _completed(container_id),
            _completed("127.0.0.1:49160"),
            _completed(json.dumps(details)),
            _completed(),
        ]
    )

    async def ready(_url: str) -> None:
        return None

    async def observer(_url: str) -> UberonIndexObservation:
        return _observation()

    runtime = DockerQleverRuntime(
        docker_run=docker,
        wait_until_ready=ready,
        index_basename="uberon",
        owner_marker_filename=UBERON_OWNER_MARKER_FILENAME,
        server_memory="2G",
        server_cache="256M",
        server_allocator="256M",
    )

    result = await runtime.observe_default_graph(candidate, owner, observer)

    assert result == _observation()
    start = docker.calls[0][0]
    assert start[start.index("-i") + 1] == "uberon"
    assert start[start.index("-m") + 1] == "2G"
    assert start[start.index("-c") + 1] == "256M"
    assert start[start.index("-e") + 1] == "256M"
