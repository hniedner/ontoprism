# Research Scripts

This directory contains experimental Python scripts used to verify architectural hypotheses and prototype decomposition strategies during the development of the ONTOPRISM engine.

## Overview

These scripts are **not** part of the production codebase and should not be used for routine task execution. They serve as a record of technical investigations (e.g., verifying the $R82$ anatomy-axis resolution or axis-selection strategies) documented in `docs/DECISIONS.md`.

## Scripts

### `anatomy_resolve.py`
Verifies if combining `rdfs:subClassOf+` with transitive `R82` (part-of) paths resolves anatomy-axis ambiguity without external ontology lookups.
*Ref: DECISION D16*

### `differentia_extractor.py`
A prototype extractor implementing a specific "axis-selection" strategy (filtering for R88, R101, R105). Provides a baseline for comparing extraction precision/recall against the Golden Set.
*Ref: DECISION D15/D20*

## Usage

Most scripts require an active Oxigraph instance running on `:7888` and arguments representing NCIt concept codes (e.g., `C6135`).

```bash
python scripts/research/anatomy_resolve.py C6135
```
