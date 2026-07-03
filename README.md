# ONTOPRISM

**Pre-coordination Refactoring Into Semantic Modules** — an ontology storage, query, and
graph-visualization platform whose distinctive purpose is to produce a **decomposed
(non-pre-coordinated) NCIt**.

## What it does

1. **Storage + visualization platform** — ported from the sibling `fairdata` codebase: an Oxigraph
   graph store, a FastAPI backend (concept detail with roles/associations, search, graph
   neighborhood, guarded SPARQL), and a SvelteKit 5 frontend (query interface with result table +
   graph explorer). Houses the **NCIt** and **caDSR** repositories.
2. **Decomposed NCIt** — an engine that replaces pre-coordinated concepts with their
   post-coordinated constituents (roles-first, NLP fallback), **additively and reversibly**: every
   original concept is retained and flagged `legacy-precoordinated`, written to a separate
   `ncit_decomposed` named graph. Existing caDSR CDE→concept mappings are preserved.

## Status

Bootstrapping (M0). See the milestone plan and decomposition assessment (kept locally under `tmp/`,
not tracked in git).

## Stack

Python 3.13 · PDM · FastAPI · SvelteKit 5 · Oxigraph · PostgreSQL · ruff · basedpyright · pytest · vitest
