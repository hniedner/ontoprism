"""SPARQL builders for NCIt concept relationships (associations + roles).

NCIt encodes two kinds of concept-to-concept relationship very differently, and a
query that ignores either silently drops it:

- **Associations** (A-codes, e.g. ``A8 Concept_In_Subset``) are asserted as *direct*
  object-property triples — ``C ncit:A8 Ctarget`` — matched by ``C ?rel ?target``.
- **Roles** (R-codes, e.g. ``R105 Disease_Has_Abnormal_Cell``) are asserted ONLY as
  OWL existential restrictions hung off ``rdfs:subClassOf``::

      C rdfs:subClassOf [ a owl:Restriction ;
                          owl:onProperty     Rxx ;
                          owl:someValuesFrom Ctarget ]

  There are **no** direct ``C Rxx Ctarget`` triples, so a query matching only direct
  predicates returns every association but ZERO roles. Rendering roles (the machine-
  readable pre-coordination axes) is the whole point of the NCIt graph view, and the
  decomposition engine is built on these restrictions.

Every role restriction in the loaded build uses ``owl:someValuesFrom`` with a named
IRI filler (no ``allValuesFrom``/``hasValue``/anonymous fillers), so the builders match
only that pattern. Version-pinned assumption — re-verify on an NCIt version bump.
"""

from ontolib.terminologies.namespaces import OWL_NS, RDFS_NS
from ontolib.terminologies.oxigraph_http_client import safe_iri

_DEFAULT_LIMIT = 100


def build_related_concepts_query(
    concept_code: str, namespace: str, *, limit: int = _DEFAULT_LIMIT
) -> str:
    """Build a query covering BOTH associations and role restrictions.

    Two UNION arms cover the two NCIt encodings. Each arm carries its **own**
    ``LIMIT`` (not a single shared post-UNION cap) so a high-degree concept's
    associations can never starve out its roles — a shared cap with no ``ORDER BY``
    could non-deterministically return all associations and zero roles. Both arms
    project ``?relation`` (property IRI) and ``?target`` (same-namespace concept);
    ``?label`` resolves the property's human-readable name.

    Raises:
        ValueError: if *concept_code* is not injection-safe.
    """
    concept_uri = safe_iri(concept_code, namespace)
    return f"""
        PREFIX rdfs: <{RDFS_NS}>
        PREFIX owl: <{OWL_NS}>

        SELECT ?relation ?target ?label WHERE {{
            {{
                SELECT ?relation ?target WHERE {{
                    <{concept_uri}> ?relation ?target .
                    FILTER(?relation != rdfs:subClassOf)
                    FILTER(STRSTARTS(STR(?target), "{namespace}"))
                }} LIMIT {limit}
            }}
            UNION
            {{
                SELECT ?relation ?target WHERE {{
                    <{concept_uri}> rdfs:subClassOf ?restriction .
                    ?restriction a owl:Restriction ;
                                 owl:onProperty ?relation ;
                                 owl:someValuesFrom ?target .
                    FILTER(STRSTARTS(STR(?target), "{namespace}"))
                }} LIMIT {limit}
            }}
            OPTIONAL {{ ?relation rdfs:label ?label }}
        }}
    """


def build_role_relationships_query(
    concept_code: str, namespace: str, *, limit: int = _DEFAULT_LIMIT
) -> str:
    """Build a role-only query (restriction traversal, excludes associations).

    Returns only R-code roles encoded as ``owl:someValuesFrom`` restrictions.
    Projects ``?rel`` (property IRI), ``?target`` (concept IRI), and an optional
    ``?label`` (the *target concept's* label).

    Raises:
        ValueError: if *concept_code* is not injection-safe.
    """
    concept_uri = safe_iri(concept_code, namespace)
    return f"""
        PREFIX rdfs: <{RDFS_NS}>
        PREFIX owl: <{OWL_NS}>

        SELECT ?rel ?target ?label WHERE {{
            <{concept_uri}> rdfs:subClassOf ?restriction .
            ?restriction a owl:Restriction ;
                         owl:onProperty ?rel ;
                         owl:someValuesFrom ?target .
            FILTER(STRSTARTS(STR(?target), "{namespace}"))
            OPTIONAL {{ ?target rdfs:label ?label }}
        }}
        LIMIT {limit}
    """
