# Design docs

The thinking behind OntoPrism's distinctive feature — turning a **pre-coordinated**
NCIt into **decomposed, semantic modules** — and the design of the engine that produces
it. Read in this order:

1. **[NCIt decomposition assessment](./ncit-decomposition-assessment.md)** — the *why*.
   Feasibility, strategy, and level-of-effort, grounded in figures computed against the
   live NCIt store. Establishes the core insight: NCIt already self-documents its
   pre-coordination as OWL role restrictions, and every role-modelled constituent is an
   existing active concept (100% coverage on the roles path), so decomposition is
   surfacing + re-linking, not invention.

2. **[NCIt decomposition engine](./ncit-decomposition-engine.md)** — the *how*.
   The implementable, test-driven design (Issue #4 / M5): module layout, the additive &
   reversible `ncit_decomposed` named-graph model, the detector, most-specific filler
   selection, NLP fallback + minting-as-proposal, provenance, metrics, and the phased PR
   plan.

3. **[NCIt regimen decomposition](./ncit-regimen-decomposition.md)** — an *extension*.
   Why chemotherapy regimens are a distinct **mereological** decomposition kind (a bag of
   drug components, not axis-qualified) and how that kind plugs into the same engine.

These are living design documents (design of record); as milestones land, keep them in
sync and record consequential shifts in [../DECISIONS.md](../DECISIONS.md).
