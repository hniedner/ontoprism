# Security Policy

## Reporting a vulnerability

**Do not open a public issue for security vulnerabilities.**

Please report suspected vulnerabilities privately:

- Preferred (once this repository is public): use GitHub's **private vulnerability
  reporting** — the **Report a vulnerability** button under the repository's
  **Security** tab. This opens a private advisory visible only to maintainers.
- Otherwise: email the maintainer listed in the repository's project metadata
  (`pyproject.toml` → `[project].authors`).

Please include: affected component (`ontolib`, `backend`, or `frontend`), version or
commit, a description, and reproduction steps. We aim to acknowledge within 5 business
days and to agree a coordinated disclosure timeline.

## Supported versions

This project is pre-1.0. Only the latest released version on `main` receives security
fixes. See [`CHANGELOG.md`](CHANGELOG.md) for released versions.

## Handling scope

- The frontend talks only to the backend; the backend owns all Oxigraph/Postgres
  access. Report auth, injection, SSRF, or data-exposure issues against the backend
  API surface (`backend/src/backend/api/`).
- The SPARQL passthrough endpoint is read-only by construction (it POSTs to Oxigraph's
  `/query`); report any way to reach a mutating operation through it.
- Secrets must never be committed. Report any exposed credential immediately; it will
  be rotated.

## Our controls

- Branch `main` blocks force-pushes and deletion; releases are automated from
  Conventional-Commit history.
- Dependabot vulnerability alerts and automated security fixes are enabled.
- The default `GITHUB_TOKEN` for workflows is read-only; workflows request write scopes
  explicitly and narrowly.
