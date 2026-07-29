"""Graph identity for the stated NCIt build.

NCIt source ontologies are too large and too safety-critical to publish through the
Graph Store HTTP endpoint, so no HTTP load entry point exists here: issue #181 owns
validated offline sibling-store construction and serving activation remains #148.
"""

from __future__ import annotations

STATED_GRAPH_IRI = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-stated.owl"
