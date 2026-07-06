"""Load the NCIt OWL into the Oxigraph store (the standalone data-build ingest step).

Downloads the NCIt OWL from NCI EVS (via :mod:`ontolib.terminologies.ncit.owl_download`)
and bulk-loads it into the running store over the SPARQL Graph Store Protocol:

- the **inferred** build → the default graph (what search / neighborhood queries read);
- the **stated** build → a distinct named graph, for the decomposition engine (#4 / D4)
  which needs the asserted axioms rather than the inferred closure.

The store is the source of truth; embeddings + the caDSR DB are built separately.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ontolib.core.logging_config import get_logger
from ontolib.terminologies.ncit.owl_download import download_ncit_owl

if TYPE_CHECKING:
    from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

logger = get_logger(__name__)

# Named graph holding the *stated* (asserted) NCIt axioms — the decomposition input.
STATED_GRAPH_IRI = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-stated.owl"
_RDF_XML = "application/rdf+xml"


async def load_owl_file(
    client: OxigraphHttpClient,
    owl_path: Path,
    *,
    graph_iri: str | None = None,
    replace: bool = True,
) -> None:
    """Bulk-load an OWL/RDF-XML file into the store (default graph unless graph_iri)."""
    logger.info(
        "Loading %s (%.1f MB) into %s",
        owl_path.name,
        owl_path.stat().st_size / 1_048_576,
        graph_iri or "the default graph",
    )
    # Stream from the file handle so a multi-GB OWL never fully materializes in memory.
    with owl_path.open("rb") as handle:
        await client.load(
            handle, content_type=_RDF_XML, graph_iri=graph_iri, replace=replace
        )


async def build_ncit_store(
    client: OxigraphHttpClient,
    output_dir: Path,
    *,
    base_url: str | None = None,
    include_stated: bool = True,
) -> dict[str, str]:
    """Download + load the inferred (default graph) and stated (named graph) NCIt OWL.

    Returns a ``{variant: file_path}`` map of what was loaded. Raises on a failed
    download (returned ``success=False``) or a failed store load (``StorageError``).
    """
    loaded: dict[str, str] = {}
    variants = ["inferred", "stated"] if include_stated else ["inferred"]
    for variant in variants:
        result = (
            await download_ncit_owl(output_dir, variant=variant, base_url=base_url)
            if base_url
            else await download_ncit_owl(output_dir, variant=variant)
        )
        if not result.success or result.file_path is None:
            raise RuntimeError(f"NCIt {variant} OWL download failed: {result.error}")
        graph_iri = STATED_GRAPH_IRI if variant == "stated" else None
        await load_owl_file(client, Path(result.file_path), graph_iri=graph_iri)
        loaded[variant] = result.file_path
    return loaded
