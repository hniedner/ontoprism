"""Store-neutral SPARQL 1.1 Query, Update, and Graph Store HTTP transport."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlencode, urlsplit

from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

SparqlEngine = Literal["oxigraph", "fuseki", "qlever"]


@dataclass(frozen=True, slots=True)
class SparqlEndpointProfile:
    """Absolute HTTP endpoints exposed by one SPARQL service.

    Engines disagree on endpoint layout: Oxigraph uses ``/query``, ``/update``, and
    ``/store`` while Fuseki commonly exposes ``/sparql``, ``/update``, and ``/data``.
    Keeping each operation explicit prevents a store migration from being encoded as
    path mutation scattered through the client.
    """

    service_url: str
    query_url: str
    update_url: str
    graph_store_url: str

    @classmethod
    def for_engine(
        cls,
        engine: str,
        service_url: str,
        *,
        named_graphs: tuple[str, ...],
    ) -> SparqlEndpointProfile:
        """Build the validated protocol layout selected by runtime configuration."""
        if engine == "oxigraph":
            return cls.for_oxigraph(service_url)
        if engine == "fuseki":
            return cls.for_fuseki(service_url)
        if engine == "qlever":
            return cls.for_qlever(service_url, named_graphs=named_graphs)
        raise ValueError(f"unsupported SPARQL engine: {engine!r}")

    @classmethod
    def for_oxigraph(cls, service_url: str) -> SparqlEndpointProfile:
        """Build Oxigraph's Query, Update, and Graph Store endpoint layout."""
        service = service_url.rstrip("/")
        return cls(
            service_url=service,
            query_url=f"{service}/query",
            update_url=f"{service}/update",
            graph_store_url=f"{service}/store",
        )

    @classmethod
    def for_fuseki(cls, service_url: str) -> SparqlEndpointProfile:
        """Build Fuseki's named Query, Update, and Graph Store endpoint layout."""
        service = service_url.rstrip("/")
        return cls(
            service_url=service,
            query_url=f"{service}/sparql",
            update_url=f"{service}/update",
            graph_store_url=f"{service}/data",
        )

    @classmethod
    def for_qlever(
        cls,
        service_url: str,
        *,
        named_graphs: tuple[str, ...],
    ) -> SparqlEndpointProfile:
        """Build QLever endpoints with OntoPrism's graph isolation declared.

        QLever's unconstrained dataset is the union of default and named graphs. Its
        SPARQL Protocol parameters restore the dataset OntoPrism requires: the internal
        default-graph identifier is active for unqualified patterns, while only the
        explicitly listed named graphs are available to ``GRAPH`` clauses.
        """
        service = service_url.rstrip("/")
        query_parameters = [
            (
                "default-graph-uri",
                "http://qlever.cs.uni-freiburg.de/builtin-functions/default-graph",
            ),
            *(("named-graph-uri", graph) for graph in named_graphs),
        ]
        root = f"{service}/"
        return cls(
            service_url=service,
            query_url=f"{root}?{urlencode(query_parameters)}",
            update_url=root,
            graph_store_url=root,
        )

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


class SparqlHttpClient(OxigraphHttpClient):
    """SPARQL HTTP client whose protocol endpoints are declared independently."""

    def __init__(
        self,
        profile: SparqlEndpointProfile,
        *,
        connect_timeout: float = 5.0,
        query_timeout: float = 30.0,
    ) -> None:
        super().__init__(
            profile.service_url,
            connect_timeout=connect_timeout,
            query_timeout=query_timeout,
        )
        self._query_url = profile.query_url
        self._update_url = profile.update_url
        self._store_url = profile.graph_store_url
        self._profile = profile

    @property
    def profile(self) -> SparqlEndpointProfile:
        """The immutable endpoint profile used for every protocol operation."""
        return self._profile
