"""NCIt annotation-property codes used for concept metadata.

NCIt models concept metadata as annotation properties keyed by opaque codes
(``P97`` etc.) rather than standard RDF terms. These are the ones the repository
read model surfaces; verified against the loaded store.
"""

# Annotation properties (literal-valued).
DEFINITION = "P97"
SEMANTIC_TYPE = "P106"
PREFERRED_NAME = "P108"
DISPLAY_NAME = "P107"
FULL_SYNONYM = "P90"
