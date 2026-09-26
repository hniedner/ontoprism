"""SPARQL builder to read a concept's decomposition from the ``ncit_decomposed`` graph.

Reads only the additive ``op:`` triples in ``DECOMPOSED_GRAPH_IRI`` (never the source
graphs). ``safe_iri`` guards the interpolated concept code.
"""

from __future__ import annotations

from ontolib.decomposition import vocab
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.sparql_transport import safe_iri


def build_publication_progress_query() -> str:
    """Aggregate D93 outcomes and review flags for the currently published run."""
    return f"""
        SELECT ?run ?publicationStatus ?publicationNotice ?category ?value
               (COUNT(DISTINCT ?concept) AS ?count) WHERE {{
            GRAPH <{vocab.DECOMPOSED_GRAPH_IRI}> {{
                <{vocab.PUBLICATION_MARKER}> <{vocab.PUBLICATION_RUN}> ?run .
                <{vocab.DEMONSTRATION_MARKER}>
                    <{vocab.PUBLICATION_STATUS}> ?publicationStatus ;
                    <{vocab.PUBLICATION_NOTICE}> ?publicationNotice .
                {{
                    ?concept <{vocab.CONCEPT_OUTCOME}> ?value .
                    BIND("outcome" AS ?category)
                }} UNION {{
                    ?concept <{vocab.HAS_REVIEW_FLAG}> ?flag .
                    ?flag <{vocab.REVIEW_FLAG_KIND}> ?value .
                    BIND("review-flag" AS ?category)
                }}
            }}
        }}
        GROUP BY ?run ?publicationStatus ?publicationNotice ?category ?value
        ORDER BY ?category ?value
    """


def build_compact_decomposition_query(concept_code: str) -> str:
    """Read subjects once, rather than scanning each optional predicate globally."""
    concept = safe_iri(concept_code, NCIT_NS)
    return f"""
        SELECT ?subject ?predicate ?value WHERE {{
            {{ SELECT ?present WHERE {{ GRAPH <{vocab.DECOMPOSED_GRAPH_IRI}> {{
                <{concept}> ?p ?o
            }} BIND(true AS ?present) }} LIMIT 1 }}
            {{
                VALUES ?subject {{ <{concept}> <{vocab.PUBLICATION_MARKER}>
                    <{vocab.DEMONSTRATION_MARKER}> }}
                GRAPH <{vocab.DECOMPOSED_GRAPH_IRI}> {{ ?subject ?predicate ?value }}
            }} UNION {{
                GRAPH <{vocab.DECOMPOSED_GRAPH_IRI}> {{
                    <{concept}> ?link ?subject .
                    FILTER(?link IN (
                        <{vocab.HAS_CONSTITUENT}>, <{vocab.HAS_REVIEW_FLAG}>))
                    ?subject ?predicate ?value
                }}
            }}
        }}
    """
