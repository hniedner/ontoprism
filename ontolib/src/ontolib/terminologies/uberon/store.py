"""Certify the publisher Uberon/CL artifact and build its immutable QLever index."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import iterparse
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    model_validator,
)

from ontolib.core.data_build_tools import (
    JENA_JRE_IMAGE,
    JENA_RIOT_ARTIFACT,
    QLEVER_IMAGE,
    QLEVER_TOOL,
    tool_identity_document,
)
from ontolib.core.download_cache import DownloadOutcome, cached_download
from ontolib.terminologies.ncit.sibling_store import (
    QLEVER_INDEX_VERSION,
    LoaderIdentity,
)
from ontolib.terminologies.sparql_http_client import SparqlHttpClient

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from xml.etree.ElementTree import Element

UBERON_ARTIFACT_MANIFEST_FILENAME = "uberon-artifact.json"
UBERON_INDEX_MANIFEST_FILENAME = ".ontoprism-uberon-index.json"
UBERON_OWNER_MARKER_FILENAME = ".ontoprism-uberon-owner"
UBERON_VERSION_IRI = (
    "http://purl.obolibrary.org/obo/uberon/releases/2026-06-19/uberon.owl"
)
UBERON_SOURCE_URL = (
    "https://github.com/obophenotype/uberon/releases/download/v2026-06-23/uberon.owl"
)
UBERON_SOURCE_SHA256 = (
    "938f51e7c3fc9fcbe5a2863eb346da8033737e568af5836958891c4c6bfb1192"
)

_OWL = "http://www.w3.org/2002/07/owl#"
_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_DC = "http://purl.org/dc/elements/1.1/"
_CL_PREFIX = "http://purl.obolibrary.org/obo/CL_"
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_OWNER = re.compile(r"[0-9a-f]{32}")
_MIN_TRIPLES = 500_000
_MAX_TRIPLES = 2_000_000
UBERON_INDEX_SCHEMA_VERSION = 3


class UberonArtifactError(RuntimeError):
    """An Uberon/CL source or QLever index failed a source-bound contract."""


class _Proof(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


def _identity(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise UberonArtifactError(f"cannot read Uberon artifact: {exc}") from exc
    return digest.hexdigest()


class UberonArtifactManifest(_Proof):
    """Content identity and publisher metadata for one full Uberon/CL OWL release."""

    schema_version: int = 1
    source_url: str
    file_path: str
    size_bytes: int
    sha256: str
    version_iri: str
    includes_cell_ontology: bool
    artifact_identity: str

    @model_validator(mode="after")
    def _canonical_fields(self) -> UberonArtifactManifest:
        if self.schema_version != 1:
            raise ValueError("unsupported Uberon artifact schema")
        if urlsplit(self.source_url).scheme != "https":
            raise ValueError("Uberon source URL must use HTTPS")
        if self.size_bytes < 1:
            raise ValueError("Uberon source must not be empty")
        if _HEX_64.fullmatch(self.sha256) is None:
            raise ValueError("Uberon SHA-256 is not canonical")
        if _HEX_64.fullmatch(self.artifact_identity) is None:
            raise ValueError("Uberon artifact identity is not canonical")
        return self


class UberonServingFingerprint(_Proof):
    """Canonical identity of every Uberon/CL value exposed by repository reads."""

    rows: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    uberon_classes: int = Field(gt=0)
    cl_classes: int = Field(gt=0)
    uberon_searchable_classes: int = Field(gt=0)
    cl_searchable_classes: int = Field(gt=0)


class UberonIndexObservation(_Proof):
    """Production-shaped facts queried from a candidate default-graph index."""

    version_iri: str | None
    triples: int
    has_uberon_lung: bool
    has_cell_class: bool
    has_ncit_xref: bool
    serving: UberonServingFingerprint | None = None


class CertifiedUberonIndexObservation(_Proof):
    """Validated observation admitted to an installed index and ready response."""

    version_iri: str
    triples: PositiveInt
    has_uberon_lung: Literal[True]
    has_cell_class: Literal[True]
    has_ncit_xref: Literal[True]
    serving: UberonServingFingerprint


class UberonIndexManifest(_Proof):
    """Exact source, toolchain, and observations for a published QLever index."""

    schema_version: int = UBERON_INDEX_SCHEMA_VERSION
    owner: str
    target_path: str
    artifact_manifest_path: str
    artifact_identity: str
    source_identity: str
    loader: LoaderIdentity
    installed_at: AwareDatetime
    observation: CertifiedUberonIndexObservation

    @model_validator(mode="after")
    def _canonical_fields(self) -> UberonIndexManifest:
        if self.schema_version != UBERON_INDEX_SCHEMA_VERSION:
            raise ValueError("unsupported Uberon index schema")
        if _OWNER.fullmatch(self.owner) is None:
            raise ValueError("Uberon index owner is not canonical")
        if _HEX_64.fullmatch(self.artifact_identity) is None:
            raise ValueError("Uberon artifact identity is not canonical")
        if _HEX_64.fullmatch(self.source_identity) is None:
            raise ValueError("Uberon source identity is not canonical")
        return self


class UberonIndexRuntime(Protocol):
    """External converter/index/server operations required by the orchestrator."""

    def identify_loader(self) -> LoaderIdentity: ...

    def load_default_graph(
        self, source_path: Path, candidate_path: Path, owner: str
    ) -> None: ...

    async def observe_default_graph(
        self,
        candidate_path: Path,
        owner: str,
        observer: Callable[[str], Awaitable[UberonIndexObservation]],
    ) -> UberonIndexObservation: ...


def _artifact_identity(
    *,
    source_url: str,
    size_bytes: int,
    sha256: str,
    version_iri: str,
    includes_cell_ontology: bool,
) -> str:
    return _identity(
        {
            "source_url": source_url,
            "size_bytes": size_bytes,
            "sha256": sha256,
            "version_iri": version_iri,
            "includes_cell_ontology": includes_cell_ontology,
        }
    )


def _publisher_element_facts(
    element: Element,
) -> tuple[str | None, bool, bool]:
    tag = element.tag
    attributes = element.attrib
    if tag == f"{{{_OWL}}}versionIRI":
        return attributes.get(f"{{{_RDF}}}resource"), False, False
    if tag == f"{{{_DC}}}source":
        return (
            None,
            attributes.get(f"{{{_RDF}}}resource")
            == "http://purl.obolibrary.org/obo/cl.owl",
            False,
        )
    if tag == f"{{{_OWL}}}Class":
        iri = attributes.get(f"{{{_RDF}}}about", "")
        return None, False, iri.startswith(_CL_PREFIX)
    return None, False, False


def _publisher_metadata(path: Path) -> tuple[str | None, bool, bool]:
    version_iri: str | None = None
    declares_cl = False
    has_cl_class = False
    try:
        for _event, element in iterparse(path, events=("end",)):
            version, declares, has_class = _publisher_element_facts(element)
            if version is not None:
                version_iri = version
            declares_cl = declares_cl or declares
            has_cl_class = has_cl_class or has_class
            element.clear()
    except (OSError, DefusedXmlException, ValueError) as exc:
        raise UberonArtifactError(f"Uberon RDF/XML is invalid: {exc}") from exc
    return version_iri, declares_cl, has_cl_class


def _require_https_source(source_url: str) -> None:
    if urlsplit(source_url).scheme != "https":
        raise UberonArtifactError("Uberon source URL must use HTTPS")


def _source_size(source: Path) -> int:
    try:
        return source.stat().st_size
    except OSError as exc:
        raise UberonArtifactError(f"cannot stat Uberon artifact: {exc}") from exc


def _require_publisher_metadata(
    metadata: tuple[str | None, bool, bool],
    *,
    expected_version_iri: str,
) -> None:
    version_iri, declares_cl, has_cl_class = metadata
    if version_iri != expected_version_iri:
        raise UberonArtifactError(
            "Uberon version IRI does not match the configured release: "
            f"{version_iri!r} != {expected_version_iri!r}"
        )
    if not declares_cl or not has_cl_class:
        raise UberonArtifactError(
            "full Uberon artifact does not contain its declared Cell Ontology content"
        )


def _validate_stored_publisher_metadata(
    manifest: UberonArtifactManifest,
    metadata: tuple[str | None, bool, bool],
) -> None:
    version_iri, declares_cl, has_cl_class = metadata
    if version_iri != manifest.version_iri:
        raise UberonArtifactError("Uberon artifact version IRI changed")
    cell_proof = (declares_cl, has_cl_class, manifest.includes_cell_ontology)
    if cell_proof != (True, True, True):
        raise UberonArtifactError("Uberon artifact lost Cell Ontology content")


def _write_json(path: Path, value: BaseModel) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def certify_uberon_artifact(
    source_path: Path,
    *,
    source_url: str,
    expected_version_iri: str,
    expected_sha256: str | None = None,
) -> UberonArtifactManifest:
    """Inspect a completed publisher file, then bind and persist its identity."""
    source = source_path.resolve()
    _require_https_source(source_url)
    _require_publisher_metadata(
        _publisher_metadata(source),
        expected_version_iri=expected_version_iri,
    )
    size_bytes = _source_size(source)
    digest = _sha256(source)
    if expected_sha256 is not None and digest != expected_sha256:
        raise UberonArtifactError(
            "Uberon artifact does not match the publisher digest: "
            f"{digest} != {expected_sha256}"
        )
    manifest = UberonArtifactManifest(
        source_url=source_url,
        file_path=str(source),
        size_bytes=size_bytes,
        sha256=digest,
        version_iri=expected_version_iri,
        includes_cell_ontology=True,
        artifact_identity=_artifact_identity(
            source_url=source_url,
            size_bytes=size_bytes,
            sha256=digest,
            version_iri=expected_version_iri,
            includes_cell_ontology=True,
        ),
    )
    _write_json(source.parent / UBERON_ARTIFACT_MANIFEST_FILENAME, manifest)
    return manifest


async def download_uberon_artifact(
    output_dir: Path,
    *,
    source_url: str,
    expected_version_iri: str,
    expected_sha256: str | None = None,
    max_retries: int = 3,
    downloader: Callable[..., Awaitable[DownloadOutcome]] = cached_download,
) -> UberonArtifactManifest:
    """Download the full publisher OWL, then inspect and bind its completed bytes."""
    _require_https_source(source_url)
    destination = output_dir.resolve() / "uberon.owl"
    outcome = await downloader(source_url, destination, max_retries=max_retries)
    if outcome.manifest.url != source_url:
        raise UberonArtifactError(
            "cached Uberon source URL does not match the configured source URL"
        )
    if Path(outcome.path).resolve() != destination:
        raise UberonArtifactError("Uberon downloader returned an unexpected path")
    return certify_uberon_artifact(
        destination,
        source_url=source_url,
        expected_version_iri=expected_version_iri,
        expected_sha256=expected_sha256,
    )


def validate_uberon_artifact(manifest_path: Path) -> UberonArtifactManifest:
    """Recompute the source identity and publisher metadata from a stored manifest."""
    try:
        manifest = UberonArtifactManifest.model_validate_json(manifest_path.read_text())
    except (OSError, ValueError) as exc:
        raise UberonArtifactError(
            f"Uberon artifact manifest is invalid: {exc}"
        ) from exc
    source = Path(manifest.file_path)
    observed_digest = _sha256(source)
    if observed_digest != manifest.sha256:
        raise UberonArtifactError(
            f"Uberon artifact digest changed: {observed_digest} != {manifest.sha256}"
        )
    observed_size = _source_size(source)
    if observed_size != manifest.size_bytes:
        raise UberonArtifactError("Uberon artifact size changed")
    _validate_stored_publisher_metadata(manifest, _publisher_metadata(source))
    expected_identity = _artifact_identity(
        source_url=manifest.source_url,
        size_bytes=manifest.size_bytes,
        sha256=manifest.sha256,
        version_iri=manifest.version_iri,
        includes_cell_ontology=manifest.includes_cell_ontology,
    )
    if manifest.artifact_identity != expected_identity:
        raise UberonArtifactError("Uberon artifact identity does not match its proof")
    return manifest


def _validate_observation(
    observation: UberonIndexObservation | CertifiedUberonIndexObservation,
    *,
    expected_version_iri: str,
) -> CertifiedUberonIndexObservation:
    if observation.version_iri != expected_version_iri:
        raise UberonArtifactError("candidate Uberon version does not match its source")
    if not _MIN_TRIPLES <= observation.triples <= _MAX_TRIPLES:
        raise UberonArtifactError("candidate Uberon triple count is outside bounds")
    if not observation.has_uberon_lung:
        raise UberonArtifactError("candidate lacks the required Uberon lung concept")
    if not observation.has_cell_class:
        raise UberonArtifactError("candidate lacks required Cell Ontology content")
    if not observation.has_ncit_xref:
        raise UberonArtifactError("candidate lacks an NCIt cross-reference")
    if observation.serving is None:
        raise UberonArtifactError("candidate lacks a serving-content fingerprint")
    return CertifiedUberonIndexObservation.model_validate(observation.model_dump())


def _index_source_identity(
    *,
    artifact_identity: str,
    loader: LoaderIdentity,
    observation: UberonIndexObservation | CertifiedUberonIndexObservation,
    schema_version: int = UBERON_INDEX_SCHEMA_VERSION,
) -> str:
    return _identity(
        {
            "schema_version": schema_version,
            "artifact_identity": artifact_identity,
            "loader": loader.model_dump(mode="json"),
            "observation": observation.model_dump(mode="json"),
        }
    )


def _read_index_manifest(manifest_path: Path) -> UberonIndexManifest:
    try:
        return UberonIndexManifest.model_validate_json(manifest_path.read_text())
    except (OSError, ValueError) as exc:
        raise UberonArtifactError(f"Uberon index manifest is invalid: {exc}") from exc


def _validate_index_owner(
    manifest_path: Path,
    manifest: UberonIndexManifest,
) -> None:
    target = Path(manifest.target_path).resolve()
    if (
        manifest_path.name != UBERON_INDEX_MANIFEST_FILENAME
        or manifest_path.resolve().parent != target
    ):
        raise UberonArtifactError("Uberon index manifest path does not match target")
    try:
        marker = (target / UBERON_OWNER_MARKER_FILENAME).read_text().strip()
    except OSError as exc:
        raise UberonArtifactError("Uberon owner marker is missing") from exc
    if marker != manifest.owner:
        raise UberonArtifactError("Uberon owner marker does not match")


def _validate_index_loader(loader: LoaderIdentity) -> None:
    exact = LoaderIdentity(
        image=loader.image,
        image_id=loader.image_id,
        cli_version=loader.cli_version,
        tool=loader.tool,
        converter=loader.converter,
        converter_runtime_image=loader.converter_runtime_image,
    )
    actual = (
        loader,
        loader.image,
        loader.cli_version,
        loader.tool,
        loader.converter,
        loader.converter_runtime_image,
    )
    expected = (
        exact,
        QLEVER_IMAGE,
        QLEVER_INDEX_VERSION,
        tool_identity_document(QLEVER_TOOL),
        tool_identity_document(JENA_RIOT_ARTIFACT.identity),
        JENA_JRE_IMAGE,
    )
    if actual != expected:
        raise UberonArtifactError(
            "Uberon loader identity does not match pinned runtime"
        )


def _validate_index_source(
    manifest: UberonIndexManifest,
    artifact: UberonArtifactManifest,
) -> None:
    _validate_observation(
        manifest.observation,
        expected_version_iri=artifact.version_iri,
    )
    expected = _index_source_identity(
        artifact_identity=manifest.artifact_identity,
        loader=manifest.loader,
        observation=manifest.observation,
        schema_version=manifest.schema_version,
    )
    if manifest.source_identity != expected:
        raise UberonArtifactError("Uberon source identity does not match index proof")


def validate_uberon_index_proof(
    manifest_path: Path,
) -> tuple[UberonIndexManifest, UberonArtifactManifest]:
    """Revalidate and return one index manifest with its source artifact proof."""
    manifest = _read_index_manifest(manifest_path)
    _validate_index_owner(manifest_path, manifest)
    artifact = validate_uberon_artifact(Path(manifest.artifact_manifest_path))
    if artifact.artifact_identity != manifest.artifact_identity:
        raise UberonArtifactError("Uberon artifact identity does not match index proof")
    _validate_index_loader(manifest.loader)
    _validate_index_source(manifest, artifact)
    return manifest, artifact


def validate_uberon_index_manifest(manifest_path: Path) -> UberonIndexManifest:
    """Revalidate the owner, source, toolchain, and observations of one index."""
    manifest, _artifact = validate_uberon_index_proof(manifest_path)
    return manifest


async def build_uberon_index(
    artifact_manifest_path: Path,
    target_path: Path,
    *,
    runtime: UberonIndexRuntime,
    owner: str | None = None,
) -> UberonIndexManifest:
    """Build, query-validate, and atomically install an initially absent index."""
    artifact = validate_uberon_artifact(artifact_manifest_path)
    target = target_path.resolve()
    if target.exists():
        raise UberonArtifactError(f"Uberon index target already exists: {target}")
    owner = owner or uuid4().hex
    if _OWNER.fullmatch(owner) is None:
        raise UberonArtifactError("Uberon index owner must be 32 lowercase hex digits")
    candidate = target.parent / f".{target.name}.candidate-{owner}"
    if candidate.exists():
        raise UberonArtifactError(f"Uberon candidate already exists: {candidate}")
    candidate.mkdir(mode=0o700, parents=True)
    (candidate / UBERON_OWNER_MARKER_FILENAME).write_text(owner + "\n")
    loader = runtime.identify_loader()
    runtime.load_default_graph(Path(artifact.file_path), candidate, owner)
    raw_observation = await runtime.observe_default_graph(
        candidate, owner, observe_uberon_index
    )
    observation = _validate_observation(
        raw_observation, expected_version_iri=artifact.version_iri
    )
    manifest = UberonIndexManifest(
        schema_version=UBERON_INDEX_SCHEMA_VERSION,
        owner=owner,
        target_path=str(target),
        artifact_manifest_path=str(artifact_manifest_path.resolve()),
        artifact_identity=artifact.artifact_identity,
        source_identity=_index_source_identity(
            artifact_identity=artifact.artifact_identity,
            loader=loader,
            observation=observation,
            schema_version=UBERON_INDEX_SCHEMA_VERSION,
        ),
        loader=loader,
        installed_at=datetime.now(UTC),
        observation=observation,
    )
    _write_json(candidate / UBERON_INDEX_MANIFEST_FILENAME, manifest)
    candidate.replace(target)
    return validate_uberon_index_manifest(target / UBERON_INDEX_MANIFEST_FILENAME)


async def observe_uberon_index(endpoint_url: str) -> UberonIndexObservation:
    """Query the release, corpus size, and required real-data sentinels."""
    async with SparqlHttpClient.for_qlever(endpoint_url) as client:
        versions = await client.select_once(
            "PREFIX owl: <http://www.w3.org/2002/07/owl#> "
            "SELECT ?version WHERE { ?ontology a owl:Ontology ; "
            "owl:versionIRI ?version } LIMIT 2",
            required_variables={"version"},
        )
        version = versions[0].get("version") if len(versions) == 1 else None
        rows = await client.select_once(
            "SELECT (COUNT(*) AS ?count) WHERE { ?s ?p ?o }",
            required_variables={"count"},
        )
        if len(rows) != 1 or "count" not in rows[0]:
            raise UberonArtifactError("candidate has no unique triple count")
        return UberonIndexObservation(
            version_iri=version,
            triples=int(rows[0]["count"]),
            has_uberon_lung=await client.ask_once(
                "ASK { <http://purl.obolibrary.org/obo/UBERON_0002048> "
                "a <http://www.w3.org/2002/07/owl#Class> }"
            ),
            has_cell_class=await client.ask_once(
                "ASK { ?class a <http://www.w3.org/2002/07/owl#Class> "
                "FILTER(STRSTARTS(STR(?class), "
                '"http://purl.obolibrary.org/obo/CL_")) }'
            ),
            has_ncit_xref=await client.ask_once(
                "ASK { ?concept "
                "<http://www.geneontology.org/formats/oboInOwl#hasDbXref> ?xref "
                'FILTER(STRSTARTS(STR(?xref), "NCIT:")) }'
            ),
            serving=await observe_uberon_serving_fingerprint(client),
        )


async def observe_uberon_serving_fingerprint(
    client: SparqlHttpClient,
) -> UberonServingFingerprint:
    """Hash canonical rows for every value exposed by Uberon repository reads."""
    query = """
    PREFIX owl: <http://www.w3.org/2002/07/owl#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX oio: <http://www.geneontology.org/formats/oboInOwl#>
    SELECT ?kind ?subject ?predicate ?object WHERE {
      {
        ?subject a owl:Class .
        FILTER(STRSTARTS(STR(?subject), "http://purl.obolibrary.org/obo/UBERON_") ||
               STRSTARTS(STR(?subject), "http://purl.obolibrary.org/obo/CL_"))
        BIND("class" AS ?kind) BIND("" AS ?predicate) BIND("" AS ?object)
      } UNION {
        ?subject a owl:Class ; rdfs:label ?object .
        BIND("label" AS ?kind) BIND("" AS ?predicate)
      } UNION {
        ?subject a owl:Class ; oio:hasExactSynonym ?object .
        BIND("synonym" AS ?kind) BIND("" AS ?predicate)
      } UNION {
        ?subject a owl:Class ;
          <http://purl.obolibrary.org/obo/IAO_0000115> ?object .
        BIND("definition" AS ?kind) BIND("" AS ?predicate)
      } UNION {
        ?subject a owl:Class ; oio:hasDbXref ?object .
        BIND("xref" AS ?kind) BIND("" AS ?predicate)
      } UNION {
        ?subject a owl:Class ; rdfs:subClassOf ?object . FILTER(isIRI(?object))
        BIND("subclass" AS ?kind) BIND("" AS ?predicate)
      } UNION {
        ?subject a owl:Class ; rdfs:subClassOf ?restriction .
        ?restriction a owl:Restriction ; owl:onProperty ?predicate ;
          owl:someValuesFrom ?object .
        BIND("restriction" AS ?kind)
      } UNION {
        ?subject a owl:Class ; rdfs:subClassOf ?restriction .
        ?restriction a owl:Restriction ; owl:onProperty ?predicate .
        ?predicate rdfs:label ?object .
        BIND("relation-label" AS ?kind)
      }
      FILTER(STRSTARTS(STR(?subject), "http://purl.obolibrary.org/obo/UBERON_") ||
             STRSTARTS(STR(?subject), "http://purl.obolibrary.org/obo/CL_"))
    } ORDER BY ?kind ?subject ?predicate ?object
    """
    rows = await client.select(
        query, required_variables={"kind", "subject", "predicate", "object"}
    )
    digest = hashlib.sha256()
    uberon_classes = 0
    cl_classes = 0
    uberon_searchable_classes: set[str] = set()
    cl_searchable_classes: set[str] = set()
    for row in rows:
        canonical = json.dumps(
            [row["kind"], row["subject"], row["predicate"], row["object"]],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode()
        digest.update(canonical + b"\n")
        if row["kind"] == "class":
            if row["subject"].startswith("http://purl.obolibrary.org/obo/UBERON_"):
                uberon_classes += 1
            else:
                cl_classes += 1
        elif row["kind"] == "label":
            if row["subject"].startswith("http://purl.obolibrary.org/obo/UBERON_"):
                uberon_searchable_classes.add(row["subject"])
            else:
                cl_searchable_classes.add(row["subject"])
    return UberonServingFingerprint(
        rows=len(rows),
        sha256=digest.hexdigest(),
        uberon_classes=uberon_classes,
        cl_classes=cl_classes,
        uberon_searchable_classes=len(uberon_searchable_classes),
        cl_searchable_classes=len(cl_searchable_classes),
    )
