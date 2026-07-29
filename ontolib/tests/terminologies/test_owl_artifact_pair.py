"""Behavioral contracts for release-bound NCIt artifact pairs."""

from __future__ import annotations

import io
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from ontolib.terminologies.ncit.owl_download import (
    DEFAULT_OWL_BASE_URL,
    OwlContentError,
    download_ncit_owl_pair,
    validate_ncit_owl_pair,
)

_ONTOLOGY_IRI = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl"


def _owl(version: str) -> bytes:
    return f"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:owl="http://www.w3.org/2002/07/owl#">
  <owl:Ontology rdf:about="{_ONTOLOGY_IRI}">
    <owl:versionInfo>{version}</owl:versionInfo>
  </owl:Ontology>
</rdf:RDF>
""".encode()


def _zip(member: str, payload: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member, payload)
    return buffer.getvalue()


def _serve_pair(
    *, stated_version: str = "26.07d", inferred_version: str = "26.07d"
) -> tuple[ThreadingHTTPServer, str]:
    bodies = {
        "/Thesaurus.OWL.zip": _zip("Thesaurus.owl", _owl(stated_version)),
        "/ThesaurusInf.OWL.zip": _zip("ThesaurusInferred.owl", _owl(inferred_version)),
    }

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = bodies[self.path]
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


@pytest.mark.unit
async def test_pair_persists_distinct_verified_artifacts(tmp_path: Path) -> None:
    server, base_url = _serve_pair()
    try:
        pair = await download_ncit_owl_pair(tmp_path, base_url=base_url, max_retries=0)
    finally:
        server.shutdown()
        server.server_close()

    assert pair.success is True
    assert pair.ontology_version == "26.07d"
    assert pair.ontology_iri == _ONTOLOGY_IRI
    assert pair.manifest_identity
    assert pair.manifest_path
    assert pair.stated is not None
    assert pair.inferred is not None
    assert pair.stated.file_path != pair.inferred.file_path
    assert pair.stated.archive_path != pair.inferred.archive_path
    assert pair.stated.archive_sha256
    assert pair.stated.owl_sha256
    assert pair.inferred.archive_sha256
    assert pair.inferred.owl_sha256
    assert pair.stated.source_url.endswith("/Thesaurus.OWL.zip")
    assert pair.inferred.source_url.endswith("/ThesaurusInf.OWL.zip")

    validated = validate_ncit_owl_pair(Path(pair.manifest_path))
    assert validated.manifest_identity == pair.manifest_identity
    assert validated.ontology_version == "26.07d"


@pytest.mark.unit
async def test_pair_rejects_release_mismatch_without_manifest(tmp_path: Path) -> None:
    server, base_url = _serve_pair(inferred_version="26.06e")
    try:
        pair = await download_ncit_owl_pair(tmp_path, base_url=base_url, max_retries=0)
    finally:
        server.shutdown()
        server.server_close()

    assert pair.success is False
    assert "version" in (pair.error or "").lower()
    assert not (tmp_path / "ncit-artifact-pair.json").exists()


@pytest.mark.unit
async def test_pair_validation_rejects_modified_artifact(tmp_path: Path) -> None:
    server, base_url = _serve_pair()
    try:
        pair = await download_ncit_owl_pair(tmp_path, base_url=base_url, max_retries=0)
    finally:
        server.shutdown()
        server.server_close()

    assert pair.success
    assert pair.inferred
    assert pair.manifest_path
    Path(pair.inferred.file_path).write_bytes(b"modified")
    with pytest.raises(OwlContentError, match="SHA-256"):
        validate_ncit_owl_pair(Path(pair.manifest_path))


@pytest.mark.unit
async def test_pair_rejects_swapped_variant_member(tmp_path: Path) -> None:
    stated = _zip("ThesaurusInferred.owl", _owl("26.07d"))
    inferred = _zip("Thesaurus.owl", _owl("26.07d"))
    bodies = {
        "/Thesaurus.OWL.zip": stated,
        "/ThesaurusInf.OWL.zip": inferred,
    }

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = bodies[self.path]
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    try:
        pair = await download_ncit_owl_pair(
            tmp_path, base_url=f"http://{host}:{port}", max_retries=0
        )
    finally:
        server.shutdown()
        server.server_close()

    assert pair.success is False
    assert "Expected OWL member" in (pair.error or "")
    assert not (tmp_path / "ncit-artifact-pair.json").exists()


@pytest.mark.unit
async def test_pair_reports_failure_of_second_variant_without_manifest(
    tmp_path: Path,
) -> None:
    inferred = _zip("ThesaurusInferred.owl", _owl("26.07d"))

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.endswith("Thesaurus.OWL.zip"):
                self.send_response(500)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(inferred)))
            self.end_headers()
            self.wfile.write(inferred)

        def log_message(self, *_args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    try:
        pair = await download_ncit_owl_pair(
            tmp_path, base_url=f"http://{host}:{port}", max_retries=0
        )
    finally:
        server.shutdown()
        server.server_close()

    assert pair.success is False
    assert pair.inferred
    assert pair.inferred.success
    assert pair.stated
    assert pair.stated.success is False
    assert not (tmp_path / "ncit-artifact-pair.json").exists()


@pytest.mark.unit
async def test_validation_rejects_missing_bound_archive(tmp_path: Path) -> None:
    server, base_url = _serve_pair()
    try:
        pair = await download_ncit_owl_pair(tmp_path, base_url=base_url, max_retries=0)
    finally:
        server.shutdown()
        server.server_close()

    assert pair.success
    assert pair.stated
    assert pair.manifest_path
    Path(pair.stated.archive_path).unlink()
    with pytest.raises(OwlContentError, match="Missing or unreadable"):
        validate_ncit_owl_pair(Path(pair.manifest_path))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("artifact", "artifact identity"),
        ("pair", "manifest identity"),
        ("schema", "schema"),
        ("variant", "variants are swapped"),
    ],
)
async def test_validation_rejects_tampered_manifest_identity_fields(
    tmp_path: Path, target: str, message: str
) -> None:
    server, base_url = _serve_pair()
    try:
        pair = await download_ncit_owl_pair(tmp_path, base_url=base_url, max_retries=0)
    finally:
        server.shutdown()
        server.server_close()

    assert pair.success
    assert pair.manifest_path
    manifest_path = Path(pair.manifest_path)
    document = json.loads(manifest_path.read_text())
    if target == "artifact":
        document["stated"]["artifact_identity"] = "0" * 64
    elif target == "pair":
        document["manifest_identity"] = "0" * 64
    elif target == "schema":
        document["schema_version"] = 999
    else:
        document["stated"]["variant"] = "inferred"
    manifest_path.write_text(json.dumps(document))

    with pytest.raises(OwlContentError, match=message):
        validate_ncit_owl_pair(manifest_path)


@pytest.mark.unit
def test_validation_rejects_invalid_manifest_json(tmp_path: Path) -> None:
    manifest = tmp_path / "ncit-artifact-pair.json"
    manifest.write_text("{not-json")
    with pytest.raises(OwlContentError, match="Unreadable"):
        validate_ncit_owl_pair(manifest)


@pytest.mark.integration
@pytest.mark.full_build
async def test_real_evs_artifacts_form_one_production_shaped_pair(
    tmp_path: Path,
) -> None:
    pair = await download_ncit_owl_pair(
        tmp_path, base_url=DEFAULT_OWL_BASE_URL, max_retries=3
    )

    assert pair.success, pair.error
    assert pair.stated
    assert pair.inferred
    assert pair.manifest_path
    assert pair.stated.size_bytes
    assert pair.stated.size_bytes > 700_000_000
    assert pair.inferred.size_bytes
    assert pair.inferred.size_bytes > 800_000_000
    assert pair.stated.ontology_version == pair.inferred.ontology_version
    assert validate_ncit_owl_pair(Path(pair.manifest_path))
