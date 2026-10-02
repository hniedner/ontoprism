"""Source-bound ontology versions shared by Uberon/CL mapping writers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_OWL_NS = "http://www.w3.org/2002/07/owl#"
_NCIT_ONTOLOGY_IRI = NCIT_NS.removesuffix("#")
_UBERON_ONTOLOGY_IRI = "http://purl.obolibrary.org/obo/uberon.owl"
_CL_ONTOLOGY_IRI = "http://purl.obolibrary.org/obo/cl.owl"
_UBERON_VERSION_PREFIX = "http://purl.obolibrary.org/obo/uberon/releases/"
_CL_VERSION_PREFIX = "http://purl.obolibrary.org/obo/cl/releases/"


class MappingSourceVersionError(RuntimeError):
    """A mapping source cannot identify its exact ontology release."""


class SparqlSelectClient(Protocol):
    """The source-read surface required by version preflight."""

    async def select(self, query: str) -> Sequence[Mapping[str, str | None]]: ...


@dataclass(frozen=True, slots=True)
class MappingSourceVersions:
    """Versions read independently from the three source ontology headers."""

    ncit: str
    uberon: str
    cl: str

    def __post_init__(self) -> None:
        for name in ("ncit", "uberon", "cl"):
            if not getattr(self, name):
                raise MappingSourceVersionError(f"missing {name} version")

    def upstream_for(self, curie: str) -> str:
        """Return the source version for one supported upstream CURIE."""
        if curie.startswith("UBERON:"):
            return self.uberon
        if curie.startswith("CL:"):
            return self.cl
        raise MappingSourceVersionError(
            f"mapping object belongs to an unknown source ontology: {curie}"
        )


def _build_ncit_versions_query() -> str:
    return f"""\
PREFIX owl: <{_OWL_NS}>
SELECT DISTINCT ?location ?version WHERE {{
  {{
    <{_NCIT_ONTOLOGY_IRI}> a owl:Ontology ; owl:versionInfo ?version .
    BIND("default" AS ?location)
  }} UNION {{
    GRAPH <{STATED_GRAPH_IRI}> {{
      <{_NCIT_ONTOLOGY_IRI}> a owl:Ontology ; owl:versionInfo ?version .
    }}
    BIND("stated" AS ?location)
  }}
}}
"""


def _build_upstream_versions_query() -> str:
    return f"""\
PREFIX owl: <{_OWL_NS}>
SELECT DISTINCT ?ontology ?version WHERE {{
  VALUES ?ontology {{ <{_UBERON_ONTOLOGY_IRI}> <{_CL_ONTOLOGY_IRI}> }}
  ?ontology a owl:Ontology ; owl:versionIRI ?version .
}}
"""


def _version_by_key(
    rows: Sequence[Mapping[str, str | None]],
    *,
    key: str,
    expected_key: str,
    name: str,
) -> str:
    versions: list[str] = []
    for row in rows:
        row_key = row.get(key)
        version = row.get("version")
        if not row_key or not version:
            raise MappingSourceVersionError("incomplete ontology version row")
        if row_key == expected_key:
            versions.append(version)
    if not versions:
        raise MappingSourceVersionError(f"missing {name} version")
    if len(versions) != 1:
        raise MappingSourceVersionError(f"ambiguous {name} version")
    return versions[0]


def _require_version_iri(
    name: str,
    version: str,
    *,
    prefix: str,
    suffix: str,
) -> None:
    release = version.removeprefix(prefix).removesuffix(suffix)
    if not version.startswith(prefix) or not version.endswith(suffix) or not release:
        raise MappingSourceVersionError(f"{name} version IRI does not identify {name}")


async def read_mapping_source_versions(
    ncit_client: SparqlSelectClient,
    uberon_client: SparqlSelectClient,
    *,
    expected_ncit_version: str,
    expected_uberon_version: str,
) -> MappingSourceVersions:
    """Read and cross-check NCIt, Uberon, and CL's own ontology headers."""
    ncit_rows = await ncit_client.select(_build_ncit_versions_query())
    ncit_default = _version_by_key(
        ncit_rows,
        key="location",
        expected_key="default",
        name="NCIt default",
    )
    ncit_stated = _version_by_key(
        ncit_rows,
        key="location",
        expected_key="stated",
        name="NCIt stated",
    )
    if ncit_default != ncit_stated:
        raise MappingSourceVersionError("NCIt default and stated versions differ")
    if ncit_default != expected_ncit_version:
        raise MappingSourceVersionError(
            "NCIt ontology version does not match its certified source"
        )

    upstream_rows = await uberon_client.select(_build_upstream_versions_query())
    uberon = _version_by_key(
        upstream_rows,
        key="ontology",
        expected_key=_UBERON_ONTOLOGY_IRI,
        name="Uberon",
    )
    cl = _version_by_key(
        upstream_rows,
        key="ontology",
        expected_key=_CL_ONTOLOGY_IRI,
        name="CL",
    )
    _require_version_iri(
        "Uberon", uberon, prefix=_UBERON_VERSION_PREFIX, suffix="/uberon.owl"
    )
    _require_version_iri("CL", cl, prefix=_CL_VERSION_PREFIX, suffix="/cl.owl")
    if uberon != expected_uberon_version:
        raise MappingSourceVersionError(
            "Uberon ontology version does not match its certified source"
        )
    return MappingSourceVersions(ncit=ncit_default, uberon=uberon, cl=cl)
