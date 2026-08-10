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
        SELECT ?status ?decomposedOn ?axis ?filler ?axisSource ?sourceRole ?mostSpecific
               ?group ?needsReview ?sourceDefinitionFact WHERE {{
            GRAPH <{vocab.DECOMPOSED_GRAPH_IRI}> {{
                OPTIONAL {{ <{concept_uri}> <{vocab.REPRESENTATION_STATUS}> ?status }}
                OPTIONAL {{ <{concept_uri}> <{vocab.DECOMPOSED_ON}> ?decomposedOn }}
                OPTIONAL {{
                    <{concept_uri}> <{vocab.HAS_CONSTITUENT}> ?c .
                    ?c <{vocab.AXIS}> ?axis ;
                       <{vocab.FILLER}> ?filler .
                    OPTIONAL {{ ?c <{vocab.AXIS_SOURCE}> ?axisSource }}
                    OPTIONAL {{ ?c <{vocab.SOURCE_ROLE}> ?sourceRole }}
                    OPTIONAL {{ ?c <{vocab.MOST_SPECIFIC}> ?mostSpecific }}
                    OPTIONAL {{ ?c <{vocab.GROUP}> ?group }}
                    OPTIONAL {{ ?c <{vocab.NEEDS_REVIEW}> ?needsReview }}
                    OPTIONAL {{
                        ?c <{vocab.SOURCE_DEFINITION_FACT}> ?sourceDefinitionFact
                    }}
                }}
            }}
        }}
    """
