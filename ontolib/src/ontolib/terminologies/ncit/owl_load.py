"""Fail-closed compatibility boundary for retired NCIt HTTP store loading.

NCIt source ontologies are too large and too safety-critical to publish through the
Graph Store HTTP endpoint. Issue #181 owns validated offline sibling-store construction;
serving activation remains #148. These functions remain temporarily importable so stale
callers receive an explicit safety error instead of silently regaining the old behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

STATED_GRAPH_IRI = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-stated.owl"
_FORBIDDEN_MESSAGE = (
    "NCIt full-ontology HTTP loading is disabled; build a validated sibling store "
    "offline."
)


class FullOntologyHttpLoadForbiddenError(RuntimeError):
    """A caller attempted the retired NCIt source-ontology HTTP mutation path."""


async def load_owl_file(
    _client: OxigraphHttpClient,
    _owl_path: Path,
    *,
    graph_iri: str | None = None,
    replace: bool = True,
) -> None:
    """Reject every full-ontology Graph Store HTTP load."""
    del graph_iri, replace
    raise FullOntologyHttpLoadForbiddenError(_FORBIDDEN_MESSAGE)


async def build_ncit_store(
    _client: OxigraphHttpClient,
    _output_dir: Path,
    *,
    base_url: str | None = None,
    include_stated: bool = True,
) -> dict[str, str]:
    """Reject the retired download-and-HTTP-load workflow."""
    del base_url, include_stated
    raise FullOntologyHttpLoadForbiddenError(_FORBIDDEN_MESSAGE)
