# Changelog

All notable changes to ontoprism are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project is pre-1.0 and
does not yet follow semantic versioning.

## [Unreleased]

### Added

- **External repository clients** — ClinicalTrials.gov v2 (search + trial detail) and
  PubMed / NCBI E-utilities (search, article detail, related articles), each spanning
  the ontolib client, backend API, and SvelteKit UI. Direct-search only.
- **Built-in data refresh** — NCIt OWL download from NCI EVS and caDSR CDE archive
  download, both through a metadata-aware cache (conditional revalidation + offline
  fallback).
- **Ops / packaging** — Apache-2.0 `LICENSE`, this changelog, `backend`/`frontend`
  Dockerfiles, and a full-application Docker Compose profile (`docker-compose.app.yml`)
  for a one-command local bring-up alongside the data services.
- Alembic schema + migrations for the pgvector embedding tables; per-IP rate limiting;
  request-id correlation + structured request logging; readiness (`/ready`) and a
  startup NCIt version-pin check.
- Frontend: raw-SPARQL query panel, CDE concept-graph explorer, and a consolidated
  repository browse/search UI shared across NCIt, caDSR, ClinicalTrials, and PubMed.

### Changed

- Renamed the library package `fairlib` → `ontolib`.
- Isolated ontoprism's Oxigraph + Postgres services from the sibling fairdata app.
