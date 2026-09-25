"""SPARQL builder to read a concept's decomposition from the ``ncit_decomposed`` graph.

Reads only the additive ``op:`` triples in ``DECOMPOSED_GRAPH_IRI`` (never the source
graphs). ``safe_iri`` guards the interpolated concept code.
"""

from __future__ import annotations

from ontolib.decomposition import vocab
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.sparql_transport import safe_iri


def build_decomposition_query(concept_code: str) -> str:
    """Return the concept's ``representationStatus``/``decomposedOn`` and every
    constituent (axis, filler, source, most-specific) from the decomposed graph.

    The status is optional so the query still returns a concept's constituents even if
    it is present but unflagged; the constituent block is optional so a flagged concept
    with no constituents still returns its status row.

    Raises:
        ValueError: if *concept_code* is not injection-safe.
    """
    concept_uri = safe_iri(concept_code, NCIT_NS)
    return f"""
        SELECT ?publicationStatus ?publicationNotice ?outcome ?outcomeReason
               ?flag ?flagKind ?flagReason ?status ?decomposedOn
               ?axis ?filler ?axisSource ?sourceRole ?mostSpecific
               ?axisAmbiguous ?sourceStructuralGroup
               ?normalizedProjectionGroup ?normalizedProjectionGroupLabel
               ?needsReview ?sourceDefinitionFact WHERE {{
                {{ SELECT ?present WHERE {{
                    GRAPH <{vocab.DECOMPOSED_GRAPH_IRI}> {{
                        <{concept_uri}> ?presencePredicate ?presenceValue .
                    }}
                    BIND(true AS ?present)
                }} LIMIT 1 }}
            GRAPH <{vocab.DECOMPOSED_GRAPH_IRI}> {{
                OPTIONAL {{
                    <{vocab.DEMONSTRATION_MARKER}>
                        <{vocab.PUBLICATION_STATUS}> ?publicationStatus ;
                        <{vocab.PUBLICATION_NOTICE}> ?publicationNotice .
                }}
                OPTIONAL {{ <{concept_uri}> <{vocab.CONCEPT_OUTCOME}> ?outcome }}
                OPTIONAL {{ <{concept_uri}> <{vocab.OUTCOME_REASON}> ?outcomeReason }}
                OPTIONAL {{
                    <{concept_uri}> <{vocab.HAS_REVIEW_FLAG}> ?flag .
                    OPTIONAL {{ ?flag <{vocab.REVIEW_FLAG_KIND}> ?flagKind }}
                    OPTIONAL {{ ?flag <{vocab.REVIEW_FLAG_REASON}> ?flagReason }}
                }}
                OPTIONAL {{ <{concept_uri}> <{vocab.REPRESENTATION_STATUS}> ?status }}
                OPTIONAL {{ <{concept_uri}> <{vocab.DECOMPOSED_ON}> ?decomposedOn }}
                OPTIONAL {{
                    <{concept_uri}> <{vocab.HAS_CONSTITUENT}> ?c .
                    ?c <{vocab.AXIS}> ?axis ;
                       <{vocab.FILLER}> ?filler .
                    OPTIONAL {{ ?c <{vocab.AXIS_SOURCE}> ?axisSource }}
                    OPTIONAL {{ ?c <{vocab.SOURCE_ROLE}> ?sourceRole }}
                    OPTIONAL {{ ?c <{vocab.MOST_SPECIFIC}> ?mostSpecific }}
                    OPTIONAL {{ ?c <{vocab.AXIS_AMBIGUOUS}> ?axisAmbiguous }}
                    OPTIONAL {{
                        ?c <{vocab.SOURCE_STRUCTURAL_GROUP}> ?sourceStructuralGroup
                    }}
                    OPTIONAL {{
                        ?c <{vocab.NORMALIZED_PROJECTION_GROUP}>
                           ?normalizedProjectionGroup
                    }}
                    OPTIONAL {{
                        ?c <{vocab.NORMALIZED_PROJECTION_GROUP_LABEL}>
                           ?normalizedProjectionGroupLabel
                    }}
                    OPTIONAL {{ ?c <{vocab.NEEDS_REVIEW}> ?needsReview }}
                    OPTIONAL {{
                        ?c <{vocab.SOURCE_DEFINITION_FACT}> ?sourceDefinitionFact
                    }}
                }}
            }}
        }}
    """


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
