# OntoPrism frontend

SvelteKit 5 user interface for OntoPrism's current NCIt-centered ontology exploration
product. It renders certified local NCIt, caDSR, Uberon/CL, and entitlement-gated ICD-O
repository views and links to supported remote clinical-trial and literature services through
the FastAPI/BFF boundary.

The ontology-generic platform is a target architecture, not a shipped frontend capability.
This application does not currently claim generic adapters, ontology editing/reasoning, generic
AI authoring, or release-forward reconciliation.

## Development

```sh
npm ci --prefix frontend
npm --prefix frontend run dev
```

The default development server uses port `5175`; the same-origin `/api` BFF reads the private
`ONTOPRISM_FASTAPI_ORIGIN`. Set environment values in the repository root `.env` as described
in [`../docs/DATA_SETUP.md`](../docs/DATA_SETUP.md).

## Quality and build

```sh
npm --prefix frontend run test:unit -- --run
npm --prefix frontend run lint
npm --prefix frontend run check
npm --prefix frontend run build
```
