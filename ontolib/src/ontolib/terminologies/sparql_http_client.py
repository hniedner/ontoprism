"""Store-neutral SPARQL 1.1 Query, Update, and Graph Store HTTP transport."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self
from urllib.parse import urlencode, urlsplit

from rdflib import Graph
from rdflib.exceptions import ParserError

from ontolib.terminologies.sparql_transport import (
    SparqlTransportClient,
    flatten_bindings,
    parse_ask_result,
    safe_iri,
)

if TYPE_CHECKING:
    from typing import BinaryIO

__all__ = [
    "SparqlEndpointProfile",
    "SparqlHttpClient",
    "flatten_bindings",
    "parse_ask_result",
    "safe_iri",
]

_QLEVER_DEFAULT_GRAPH = (
    "http://qlever.cs.uni-freiburg.de/builtin-functions/default-graph"
)
_CONSTANT_GRAPH_IRI = re.compile(r"\bGRAPH\s*<([^<>\s]+)>", re.IGNORECASE)
_TURTLE_MEDIA_TYPES = frozenset(
    {"text/turtle", "application/turtle", "application/x-turtle"}
)


@dataclass(frozen=True, slots=True)
class SparqlEndpointProfile:
    """Absolute HTTP endpoints exposed by one SPARQL service.

    Keeping each operation explicit prevents endpoint-layout assumptions from being
    scattered through the client.
    """

    service_url: str
    query_url: str
    update_url: str
    graph_store_url: str
    dataset_default_graph: str | None = None
    dataset_named_graphs: tuple[str, ...] | None = None
    normalize_turtle_uploads: bool = False

    @classmethod
    def for_engine(
        cls,
        engine: str,
        service_url: str,
        *,
        named_graphs: tuple[str, ...],
    ) -> SparqlEndpointProfile:
        """Build the validated protocol layout selected by runtime configuration."""
        if engine == "qlever":
            return cls.for_qlever(service_url, named_graphs=named_graphs)
        raise ValueError(f"unsupported SPARQL engine: {engine!r}")

    @classmethod
    def for_standard_paths(cls, service_url: str) -> SparqlEndpointProfile:
        """Build the ``/query``, ``/update``, ``/store`` protocol layout.

        This is retained for bounded HTTP test doubles and generic protocol peers;
        production ontology services use :meth:`for_qlever`.
        """
        service = service_url.rstrip("/")
        return cls(
            service_url=service,
            query_url=f"{service}/query",
            update_url=f"{service}/update",
            graph_store_url=f"{service}/store",
        )

    @classmethod
    def for_qlever(
        cls,
        service_url: str,
        *,
        named_graphs: tuple[str, ...] | None,
    ) -> SparqlEndpointProfile:
        """Build QLever endpoints with OntoPrism's graph isolation declared.

        QLever's unconstrained dataset is the union of default and named graphs. Its
        SPARQL Protocol parameters restore the dataset OntoPrism requires: the internal
        default-graph identifier is active for unqualified patterns, while only the
        explicitly listed named graphs are available to ``GRAPH`` clauses.
        """
        service = service_url.rstrip("/")
        root = f"{service}/"
        query_url = root
        if named_graphs is not None:
            query_parameters = [
                (
                    "default-graph-uri",
                    _QLEVER_DEFAULT_GRAPH,
                ),
                *(("named-graph-uri", graph) for graph in named_graphs),
            ]
            query_url = f"{root}?{urlencode(query_parameters)}"
        return cls(
            service_url=service,
            query_url=query_url,
            update_url=root,
            graph_store_url=root,
            dataset_default_graph=(
                _QLEVER_DEFAULT_GRAPH if named_graphs is not None else None
            ),
            dataset_named_graphs=named_graphs,
            normalize_turtle_uploads=True,
        )

    def query_url_for(self, query: str) -> str:
        """Return the QLever dataset URL required by one query.

        A constrained QLever dataset must enumerate every visible named graph. The
        stable NCIt graphs are declared by the profile; exact constant graph IRIs in
        a query are added per request so crash-safe run-scoped staging graphs remain
        queryable without exposing QLever's union default graph. ``GRAPH ?g`` never
        widens the dataset and therefore sees only the profile's declared graphs.
        """
        if self.dataset_default_graph is None or self.dataset_named_graphs is None:
            return self.query_url
        named_graphs = list(self.dataset_named_graphs)
        for graph_iri in _CONSTANT_GRAPH_IRI.findall(query):
            if graph_iri not in named_graphs:
                named_graphs.append(graph_iri)
        parameters = [
            ("default-graph-uri", self.dataset_default_graph),
            *(("named-graph-uri", graph) for graph in named_graphs),
        ]
        root = self.query_url.split("?", maxsplit=1)[0]
        return f"{root}?{urlencode(parameters)}"

    def __post_init__(self) -> None:
        for field in (
            "service_url",
            "query_url",
            "update_url",
            "graph_store_url",
        ):
            value = getattr(self, field)
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"{field} must be an absolute HTTP(S) URL")


class SparqlHttpClient(SparqlTransportClient):
    """SPARQL HTTP client whose protocol endpoints are declared independently."""

    def __init__(
        self,
        profile: SparqlEndpointProfile | str,
        *,
        connect_timeout: float = 5.0,
        query_timeout: float = 30.0,
    ) -> None:
        if isinstance(profile, str):
            profile = SparqlEndpointProfile.for_standard_paths(profile)
        super().__init__(
            profile.service_url,
            connect_timeout=connect_timeout,
            query_timeout=query_timeout,
        )
        self._query_url = profile.query_url
        self._update_url = profile.update_url
        self._store_url = profile.graph_store_url
        self._profile = profile

    @classmethod
    def for_qlever(
        cls,
        service_url: str,
        *,
        named_graphs: tuple[str, ...] | None = None,
        connect_timeout: float = 5.0,
        query_timeout: float = 30.0,
    ) -> Self:
        """Create a client for a QLever endpoint and its isolated graph dataset."""
        return cls(
            SparqlEndpointProfile.for_qlever(
                service_url,
                named_graphs=named_graphs,
            ),
            connect_timeout=connect_timeout,
            query_timeout=query_timeout,
        )

    @classmethod
    def for_standard_paths(
        cls,
        service_url: str,
        *,
        connect_timeout: float = 5.0,
        query_timeout: float = 30.0,
    ) -> Self:
        """Create a client for the generic three-path protocol layout."""
        return cls(
            SparqlEndpointProfile.for_standard_paths(service_url),
            connect_timeout=connect_timeout,
            query_timeout=query_timeout,
        )

    @property
    def profile(self) -> SparqlEndpointProfile:
        """The immutable endpoint profile used for every protocol operation."""
        return self._profile

    def _query_endpoint(self, query: str) -> str:
        return self._profile.query_url_for(query)

    async def load(
        self,
        data: bytes | BinaryIO,
        *,
        content_type: str,
        graph_iri: str | None = None,
        replace: bool = True,
    ) -> None:
        """Upload RDF, losslessly normalizing Turtle for QLever.

        QLever's Graph Store Turtle parser expands collection objects without the
        RDF ``first``/``rest`` triples required by OWL list traversal. Production
        source builds already use Jena-to-N-Triples; this local normalization gives
        incremental curation writes the same RDF semantics.
        """
        media_type = content_type.partition(";")[0].strip().lower()
        if self._profile.normalize_turtle_uploads and media_type in _TURTLE_MEDIA_TYPES:
            graph = Graph()
            try:
                if isinstance(data, bytes):
                    graph.parse(data=data, format="turtle")
                else:
                    graph.parse(file=data, format="turtle")
            except (ParserError, UnicodeError, ValueError) as exc:
                raise ValueError(f"invalid Turtle upload: {exc}") from exc
            serialized = graph.serialize(format="nt")
            data = serialized.encode("utf-8")
            content_type = "application/n-triples"
        await super().load(
            data,
            content_type=content_type,
            graph_iri=graph_iri,
            replace=replace,
        )
