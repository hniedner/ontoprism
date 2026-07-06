# Decomposition golden set (extraction research oracle)

Curated `{concept → intended (axis, filler) constituents}` used to drive the
stated-extraction research loop. Correct extraction of stated pre-coordination is
**curation-heavy, not mechanical** — a genus-chain walk over-collects and most-specific
selection can pick the wrong filler (engine design
[§6.2](../../../../docs/design/ncit-decomposition-engine.md)). So we iterate:

1. **Curate** the intended constituents for a concept in `neoplasm.json` (SME-validated).
2. **Run** a candidate extractor against the stated store:
   `pdm run decompose-spike` (see `scripts/decomposition_spike.py`).
3. **Score** precision/recall vs the golden set (via `ontolib.decomposition.score`).
4. **Iterate** the boundary heuristic (defining-role classification, differentia-vs-
   inherited cutoff, most-specific) until precision/recall converge, accepting
   `needs_review` for genuinely ambiguous axes.

`neoplasm.json` is a **starter seed** — it needs SME validation and expansion to
~20–50 concepts before it is a trustworthy regression gate. Only once a candidate
extractor reaches acceptable precision/recall on a validated set does it graduate from
`scripts/` into the library as the production extractor (unblocking #4/5b).

Format: `constituents` is a list of `[axis, filler]` pairs — `axis` is an NCIt role code
(`R101`) or an `op:` axis (`op:Morphology`); `filler` is an NCIt concept code.
