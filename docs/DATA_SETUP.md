# Data provisioning (one-time, dev)

ontoprism runs its **own** isolated data services (see `docker-compose.yml`). A fresh
checkout uses digest-pinned official service images and does not require another
repository or a locally built image:

| Service | ontoprism | fairdata |
|---|---|---|
| QLever NCIt | `:7888` | `:7878` |
| QLever Uberon/CL | `:7889` | `:7879` |
| PostgreSQL (pgvector) | `:5433` | `:5432` |
| backend | `:8011` | `:8001` |
| SvelteKit Node/dev server | `:5175` | `:5173` |

In this document, **repository/index** names an implemented storage or query surface;
**ontology adapter** names D86's future capability-declaring platform boundary. The presence of
NCIt, Uberon/CL, caDSR, or ICD-O repositories does not imply that a generic adapter system exists.

## 1. Install the pinned data-build tools

Install the repository dependencies and copy the environment template:

```bash
cp .env.example .env
pdm install --dev
npm ci --prefix frontend
```

The copied `.env` also binds the private SvelteKit BFF target:

```dotenv
ONTOPRISM_FASTAPI_ORIGIN=http://127.0.0.1:8011
ONTOPRISM_FASTAPI_TIMEOUT_MS=5000
```

An entitled local operator configures the same private consumer key for SvelteKit and
FastAPI by setting it once in the root `.env`:

```dotenv
ICDO_ENTITLEMENT_KEY=<operator-provided-entitlement>
ENABLE_LICENSED_MAPPINGS=false
```

Leave `ENABLE_LICENSED_MAPPINGS=false` unless the operator is separately authorized to
include ICD-O alignments in NCIt mapping and decomposition responses. Restart both
application processes after changing either value. The supported workflow never enters
the key in a browser cookie, header, form, or URL (`pdm run pytest
backend/tests/test_icdo_api.py backend/tests/test_repository_metadata_api.py -q` and
`cd frontend && npx playwright test e2e/ssr-bff.spec.ts`, 2026-08-14).

`pdm run start-all` exports that file to both application processes. Direct Vite
development reads the repository root through `envDir`. A production adapter-node build
does not load `.env` automatically; inject the same private variables into the process,
for example `node --env-file=../.env build` from `frontend/`. Also set adapter-node
`ORIGIN` to the public application origin. Only configure forwarded-protocol/host headers
when a trusted reverse proxy overwrites them; accepting client-supplied forwarded headers
would allow origin spoofing. The SvelteKit BFF strips browser-supplied `Forwarded`,
`X-Forwarded-*`, `X-Real-IP`, and hop-by-hop headers before calling FastAPI; configure
the public proxy-to-SvelteKit hop independently from the private SvelteKit-to-FastAPI hop.
The BFF strips browser cookies from every proxied request. On protected FastAPI paths it
also replaces caller entitlement headers with `ICDO_ENTITLEMENT_KEY`. The key remains
private process configuration and only an
opaque access state reaches rendered page data (`cd frontend && npx vitest run
src/lib/server/fastapi.test.ts src/lib/server/icdo-access.test.ts`, 2026-08-14).
The BFF supplies FastAPI with SvelteKit's socket-derived client address. Behind a trusted
proxy, set adapter-node `ADDRESS_HEADER` (and `XFF_DEPTH` for `x-forwarded-for`) only when
that proxy overwrites the selected header; otherwise leave them unset.

NCIt is published by EVS as stated and inferred RDF/XML OWL. QLever indexes
N-Triples/Turtle/N-Quads, so the build uses Apache Jena RIOT only as a streaming,
serialization-preserving RDF/XML-to-N-Triples converter. The installer downloads the
pinned Apache archive over HTTPS, verifies its SHA-256 digest, and records its exact
identity before the build can use it:

```bash
pdm run python scripts/install_jena.py --install-dir "$PWD/.tools/jena-6.1.0"
```

`cp .env.example .env` configures this certified repository-local installation for
every `pdm run` command; no per-shell export is required.

The pinned converter archive digest is
`sha256:653108a91fd9b309a89bc756258bae0bca01587cef475942d11852e3beba2ae3`
(`shasum -a 256 .tools/jena-6.1.0/apache-jena-6.1.0.tar.gz`, 2026-08-10).
RIOT runs inside the digest-pinned Java runtime declared in
`ontolib.core.data_build_tools`; no host Java installation is required for NCIt index
construction.

## 2. Build the ontology indexes, then start services

QLever must have an offline index before its server starts. Build the NCIt and
Uberon/CL indexes first, then launch the services and migrate Postgres:

```bash
pdm run data-build owl
pdm run data-build ncit-bootstrap  # first install only; refuses any existing target
pdm run data-build uberon-store
docker compose up -d
pdm run migrate
```

Verify the pinned processes and service endpoints after the indexes are active:

```bash
docker compose images
docker compose ps
curl -fsS localhost:7888/ -H 'Content-Type: application/sparql-query' \
  --data 'ASK {}'
```

This loopback request is an operator check against the QLever service itself. The
FastAPI application exposes no public raw-SPARQL endpoint; its supported query surface
is the typed API (D44).

### Supported local Podman runtime

The supported local workflow runs OntoPrism against the Docker-compatible API of the
rootless Podman machine named `ontoprism-vm`. Install the currently supported Homebrew
clients and create that machine manually; this is setup, not an agent operation:

```bash
/opt/homebrew/bin/brew install podman docker docker-compose
/opt/homebrew/bin/podman machine init --provider applehv --cpus 8 --memory 32768 --disk-size 120 --now --update-connection ontoprism-vm
```

The supported Compose provider is Docker Compose v2 from the Homebrew
`/opt/homebrew/bin/docker-compose` executable. Python `podman-compose` is not supported.
Verify the installation and the non-rootful running machine without resetting it:

```bash
/opt/homebrew/bin/podman --version
/opt/homebrew/bin/podman machine inspect ontoprism-vm
/opt/homebrew/bin/docker --version
/opt/homebrew/bin/docker-compose version
```

The data-service `docker-compose.yml` is exercised unchanged. The
application smoke check uses the unchanged data and app Compose files plus a generated,
temporary Podman override. The replay operations pin the Homebrew Docker, Podman, and
external Docker Compose v2 provider plus PDM paths. The shared `verify` runner uses the
current Python environment and resolves both `pdm` and `npm` from `PATH`; it does not pin
either executable to a Homebrew path.

Use the fixed wrappers so `DOCKER_HOST` is derived from the inspected machine socket and
`PODMAN_COMPOSE_PROVIDER` is controlled rather than inherited from the shell:

```bash
pdm run agent-replay inspect-podman
pdm run agent-replay check-podman-api
pdm run agent-replay podman-compose-up
pdm run agent-replay podman-compose-check
pdm run agent-replay podman-health-reject
pdm run agent-replay podman-test-integration
pdm run agent-replay podman-test-full-store
pdm run agent-replay podman-verify
pdm run agent-replay podman-compose-down
pdm run agent-replay podman-app-smoke
```

The enhanced-NCIt showcase has two fixed local operator commands:

```bash
pdm run agent-replay activate-enhanced-ncit-showcase
pdm run agent-replay verify-enhanced-ncit-showcase
```

Activation replaces only the isolated showcase graph at the configured NCIt endpoint;
verification is read-only. Both require exact packaged-decision readback and write ignored
local evidence to `tmp/m1-6-enhanced-showcase-readiness.json`. This is local graph
activation evidence, not production readiness, scientific publication, equivalence, or
NCI adoption.

To make an unwrapped, literal `pdm run verify` use Podman, activate the dedicated Docker
context first:

```bash
pdm run agent-replay activate-podman-docker-context
pdm run verify
```

`activate-podman-docker-context` reports the prior context, derives the endpoint only from
the running rootless `ontoprism-vm`, creates or safely updates only the exact
`ontoprism-podman` context, selects it, and verifies the selected endpoint and Podman API.
It does not set persistent environment variables or edit shell configuration. The
shell-free `verify` runner explicitly reports and removes inherited Docker selector
variables as part of its default-context contract, so its Docker calls use the selected
context without a per-command override. It deliberately leaves Podman selected. For a
manual non-destructive rollback, inspect `/opt/homebrew/bin/docker context ls`, then select
a valid operator-owned context explicitly with `/opt/homebrew/bin/docker context use
<context-name>`. The activation output reports the prior context for this purpose. Never
delete a context as part of rollback.

Useful `inspect-podman` evidence requires an already running, valid rootless `ontoprism-vm`
and its Docker-compatible socket. Given that prerequisite, the operation collects bounded
best-effort runtime diagnostics: each command's output and exit code are evidence, while
overall exit 0 means collection completed rather than every diagnostic succeeded. It does
not turn a missing, stopped, rootful, or invalid machine into a successful runtime check.
`check-podman-api` exercises the
pinned Docker-compatible API and Compose provider. `podman-compose-up` rejects any occupied
fixed data port before starting the unchanged data Compose stack. `podman-compose-check`
validates the exact three-service inventory, health, owner labels, loopback bindings, and
exact PostgreSQL named-volume identity. Its QLever bind-path checks validate the resolved
host index paths mounted by both QLever containers. Separately, its
PostgreSQL-to-QLever DNS checks prove service-name resolution.
The PostgreSQL container resolves `qlever-ncit` and `qlever-uberon`.
`podman-health-reject` exercises Compose's nonzero unhealthy-service rejection.
`podman-test-integration` and `podman-test-full-store` run the repository's integration and
configured-corpus gates with `DOCKER_HOST` pinned to the inspected machine socket.
`podman-verify` instead fails closed unless the selected context is exactly
`ontoprism-podman` at that same inspected endpoint, then runs the complete default-context
verification gate. A successful verification under any other selected runtime is not
presented as Podman verification.
`podman-compose-down` accepts a partial owned stack, refuses any present wrong-owner
resource, performs project-scoped cleanup without `-v`, and then inspects the exact
`ontoprism-podman-poc_ontoprism_pg_data` identity and owner labels to prove the populated
volume still exists. QLever continues to use the repository's existing bind-mounted
indexes.

Run `podman-app-smoke` only after `podman-compose-down`. It enforces availability of fixed
ports 5433, 7888, 7889, and 8080; refuses existing primary-stack containers; verifies the
retained volume's exact ownership before mounting it; and exercises Caddy root, the C3262
BFF response, and service DNS in the exact direction implemented: the API container resolves `web`, `postgres`, `qlever-ncit`, and `qlever-uberon`. The wrapper always scopes Compose cleanup and
removes generated override and temporary data directories. A failed operation remains the
primary CLI error, with each cleanup failure also printed to stderr. Do not run a second
local stack on the same loopback ports, and never mount the PostgreSQL volume from two
stacks concurrently. GitHub CI remains on Docker; local Podman runtime selection does not
change CI.

The M1 26.07d review uses a separately certified stated-source store on `:7890`; it does
not replace the application store on `:7888`. Run the combined read-only corpus contracts
with both lanes explicit:

```bash
NCIT_SPARQL_URL=http://localhost:7888 \
NCIT_STATED_SPARQL_URL=http://localhost:7890 \
pdm run test-integration-full-store
```

`NCIT_STATED_SPARQL_URL` affects only stated-graph full-store contracts and falls back to
`NCIT_SPARQL_URL` when omitted.

## 3. Embeddings (pgvector)

Embeddings are published only by ontoprism's validated build. Do **not** pipe a sibling
database dump into the serving tables: row presence, vector dimension, and HNSW indexes
cannot prove source/model provenance or completeness. In July 2026 such a clone held
4,752 valid NCIt vectors but omitted canonical C3262; the old non-empty preflight
accepted it as usable.

The migration creates stable corpus-specific serving tables (`ncit_concepts` and
`cde_repository`), an `embedding_corpus_manifest`, and build-scoped staging. Similarity
readers return rows only when a completed active manifest exists for the certified
repository `source_identity`. The manifest also records `source_hash`, the canonical
ordered serving-content fingerprint. Existing pre-migration rows are deliberately not
auto-certified; migration 0015 deactivates manifests whose source proxy is unknowable.

## Database schema (Alembic)

The pgvector tables above are also defined as an Alembic migration
(`migrations/versions/0001_embedding_tables.py`), so the schema is reproducible:

```bash
pdm run migrate         # fresh DB: create the embedding tables + HNSW indexes
pdm run migrate-stamp   # pre-existing cloned DB: mark migrated WITHOUT recreating
```

Use `migrate` on a fresh database. For an imported legacy database whose embedding
tables already exist but Alembic has never tracked them, `migrate-stamp` stamps the
actual predecessor (`0001_embedding_tables`) and then upgrades through every later
migration; it never stamps the current head without creating publication schema.
Legacy embedding rows remain inactive until an explicit validated rebuild.

## Rebuild from public sources

`pdm run data-build` builds ontoprism from public sources. Its steps are runnable
individually or together:

```bash
# 1. Download, hash, same-release-bind, and revalidate the inferred/stated NCIt pair.
#    Online full-store loading is disabled (D12/D46).
pdm run data-build owl

# 2a. On a fresh machine, build, validate, and atomically install the first NCIt index.
pdm run data-build ncit-bootstrap

# 2b. On an existing installation, build and certify an inactive sibling instead.
#     This command never replaces the active index.
pdm run data-build ncit-store

# 2c. Activate the exact emitted candidate with the journaled maintenance workflow.
pdm run data-build ncit-activate --candidate-manifest /exact/emitted/manifest/path

# 3. Download, certify, and initially install the Uberon/CL index.
pdm run data-build uberon-store

# 4. Start the serving indexes and Postgres, then apply the schema.
pdm run up
pdm run migrate

# 5. caDSR CDEs → SQLite. Downloads the released CDE XML and builds cde_repository.db
#    (cdes + cde_concepts + the cdes_fts FTS5 index).
pdm run data-build cadsr

# 6. Inspect all embedding build manifests. This is read-only and exits non-zero rather
#    than writing implicitly:
pdm install -G data-build
pdm run data-build embeddings

# 7. Explicitly build and validate NCIt, then caDSR. Each corpus has its own atomic
#    activation; this is intentionally not one cross-corpus transaction, so a caDSR
#    failure cannot corrupt or roll back accepted NCIt vectors.
pdm run data-build embeddings --publish

# Or repair one corpus independently:
pdm run data-build embeddings --publish --corpus ncit
pdm run data-build embeddings --publish --corpus cadsr

# …or provision a fresh/partially provisioned installation in one mutating shot.
# `all` builds the offline indexes, starts QLever/Postgres with `docker compose up
# -d --wait`, applies Alembic, then builds caDSR and publishes embeddings. It refuses
# replacement of an existing Uberon/CL index; use the individual refresh commands on
# an established installation.
pdm run data-build all
```

`NCIT_STORE_DIR` defaults to `data/qlever-ncit`. The sibling builder resolves that
directory only to choose the same parent filesystem; the active directory is never
mounted into a loader or validation container. A successful candidate contains
`.ontoprism-ncit-candidate.json`, which records the artifact-pair identity and hashes,
release, graph layout, exact counts, loader image ID/digest/CLI version, owner, paths, and
stable source identity. A rejected candidate contains
`.ontoprism-ncit-rejected.json` and is not eligible for activation. Keep it for diagnosis
or remove only after independently verifying its owner marker.

### Journaled NCIt maintenance activation

`ncit-store` prints the exact absolute path of the certified candidate it built. Pass
that emitted manifest path unchanged to the activation command; do not discover a
candidate with a glob, rename either store, or edit the manifest:

```bash
pdm run data-build ncit-store
pdm run data-build ncit-activate --candidate-manifest /exact/emitted/.qlever-ncit.candidate-OWNER/.ontoprism-ncit-candidate.json
```

The serving contract is QLever index basename `ncit`, revision `65f84b4`, in
`docker.io/adfreiburg/qlever@sha256:abeb20ae245184cee2991a99c22a9bb0a62f6884bb1a03747bf7e56165cb0ca6`
(`docker compose config qlever-ncit`, then `docker inspect ontoprism-qlever-ncit`,
2026-08-10). The command verifies that executable/index identity, the exact configured
active and manifest paths, both independent owner markers, same-filesystem rename
support, store-format identity, complete QLever file inventory, and free-space headroom
before it stops `qlever-ncit` (`pdm run pytest
ontolib/tests/terminologies/test_ncit_activation.py -q`, exit 0, 2026-08-10).
It holds NCIt embedding-source publication coordination while it stops the service,
fsyncs `data/.qlever-ncit.activation.json` and the containing directory, moves the old
active directory to the journal's one exact rollback path, activates the candidate,
force-recreates the service, restores the PostgreSQL-bound projection, and runs the
release/graph/count/restriction/stated-differential plus composed-definition and browse
health workload (`pdm run pytest ontolib/tests/terminologies/test_ncit_activation.py
ontolib/tests/terminologies/test_ncit_activation_integration.py -q`, exit 0,
2026-08-10).

**Activation refusal.** A mismatched path, owner, filesystem, sidecar, store format,
executable identity, headroom check, or a different candidate while a nonterminal
journal exists fails before an unsafe rename. Preserve the journal and every named path;
do not run a prefix cleanup or an in-place QLever load. Correct the reported condition
and retry the identical `ncit-activate --candidate-manifest ...` command.

**Automatic rollback.** If service recreation, projection reconciliation, or health
validation fails before cleanup, the command preserves the failed candidate at its
exact journaled path, restores the complete prior active directory, recreates QLever,
and reruns the same health workload before reporting a rolled-back activation. A cleanup
failure after successful health leaves the accepted active store and rollback directory
bound by the journal for a safe retry; never delete that directory manually.

**Interrupted recovery.** Inspect the durable phase without changing it, then retry the
identical command with the identical manifest argument:

```bash
jq . data/.qlever-ncit.activation.json
pdm run data-build ncit-activate \
  --candidate-manifest /the/same/path/passed/to/the/interrupted/command
```

The retry reconciles an ambiguous successful rename or cleanup from exact owner markers
and continues or rolls back from the persisted phase. Terminal journals are retained;
when a later, different certified candidate starts, the command moves the exact terminal
journal to `.qlever-ncit.activation-OWNER-complete.json` (or `-rolled-back.json`) before
fsyncing a new preflight journal. If rollback recovery itself cannot recreate and
validate the old service, stop: preserve all exact paths and the journal for diagnosis.

### Source-bound decomposition runs

Run decomposition only against an endpoint serving the certified index described by
the required manifest. On a first installation, `ncit-bootstrap` creates the active
index and its manifest, so decomposition is usable end to end. During a later refresh,
`ncit-store` deliberately leaves the new certified candidate inactive; keep using the
active manifest until `ncit-activate` completes. Do not promote a refresh candidate by
hand.

```bash
pdm run decompose \
  --source-manifest /absolute/active/or/candidate/path/.ontoprism-ncit-candidate.json \
  --branch neoplasm \
  --out data/ncit_decomposed.ttl
```

For the deterministic, review-only 26.07d M1 slice:

```bash
pdm run decompose \
  --source-manifest /absolute/active/or/candidate/path/.ontoprism-ncit-candidate.json \
  --branch neoplasm \
  --sample-manifest samples/ncit-26.07d-m1-review.json \
  --out data/ncit-26.07d-m1-review.ttl
```

The sample manifest records the exact ordered codes, overlapping strata and rationales,
source identity/version, and selection method. Its digest is part of run/resume identity.
Sample execution validates every code against the revalidated hierarchy before
provenance, requires `--out`, and rejects `--total-limit`, `--load`, and equivalence
emission. It does not replace the later full-corpus acceptance run.

The CLI revalidates the D47 proof and compares its complete candidate observation with
the live endpoint. It persists the exact worklist and immutable source/config fingerprint
before processing. `--branch` is a closed choice: `neoplasm` selects strict descendants
of `C3262`; `disease` selects strict descendants of `C2991`, including the neoplasm
population. Both share the axis-qualified algorithm, while their hierarchy root and
scope-algorithm version are independent fingerprint dimensions. `regimen` remains
unavailable until its distinct component-bag algorithm is implemented. `--resume RUN_ID`
accepts only the same source, branch, root, scope algorithm, limit,
algorithm/config, output, and load modes; it processes exactly unfinished items. Source
drift before publication fails closed and invalidates every persisted result row. The
`--out` TTL is staged, flushed, validated, and source-checked before publication. With
`--load`, the CLI loads a unique staging graph and transactionally replaces the additive
`ncit_decomposed` graph together with its publication marker. It then atomically replaces
and directory-syncs the file before marking the run complete. Failures after publication
intent is journaled but before completion remain separately visible and resumable;
matching marker-ahead retries reconcile without replaying a committed graph update.
Preflight failures fail the run, while post-completion lock-release failures surface
without demoting it (D53).

Every manifest records source version/hash, certified proxy `source_identity`, immutable
model revision, vector dimension, expected unique-row count, code commit, build ID,
sentinels, state, and timestamps;
completed manifests additionally record the validated actual count. The pinned model is
`sentence-transformers/all-mpnet-base-v2@e8c3b32edf5434bc2275fc9bab85f82640a19130`.
NCIt fingerprints every ordered source record and recomputes version/count/fingerprint
before activation; caDSR fingerprints/rechecks canonical ordered CDE rows. Source drift
fails the candidate rather than claiming a long HTTP paging run was a transactional
source snapshot.

To verify the real external encoder contract after installing the data-build group:

```bash
pdm run test-integration-full-build -k pinned_sentence_transformer
```

This explicitly downloads/loads the pinned model revision and requires one 768-vector
per input. It is `full_build` and intentionally excluded from the lightweight seeded CI
job; absence is a failed applicable manual contract, not a skip.

The source-qualified decomposition hierarchy contract is also an explicit `full_build`
test:

```bash
pdm run pytest \
  ontolib/tests/terminologies/test_ncit_sibling_store_integration.py::test_complete_pinned_ncit_pair_builds_certified_sibling \
  -v
```

It revalidates the cached `ncit-artifact-pair.json`, including both OWL hashes and their
same-release binding, rebuilds an inactive sibling with the pinned loader, and evaluates
both hierarchy branches only after the candidate proof matches the live temporary
endpoint. It never uses the configured active store and is deliberately excluded from
`test`, `test-ci`, `test-integration`, and the read-only `full_store` lane. It also
checks that the tracked 26.07d review sample names that exact certified source and that
all selected codes are members of its neoplasm hierarchy.

### Validation and recovery

Run `pdm run data-build embeddings` first. It prints persisted build provenance and
lifecycle evidence (completed builds include actual counts), then refuses
to mutate without `--publish`. A valid NCIt publication requires exact source-count
agreement with both the enumerated source and the configured release expectation
(`NCIT_EMBEDDING_EXPECTED_ROWS=206860` for the 26.07d inferred default graph /
`CADSR_EMBEDDING_EXPECTED_ROWS=79835`) plus C3262;
caDSR likewise requires exact source/release count agreement and `2517527:4`.

Build batches commit only to build-scoped staging. If encoding, validation, or
activation fails after the candidate manifest starts, the previous serving rows and
active manifest remain unchanged and the workflow attempts to record `failed`; if
PostgreSQL itself is unavailable, failure persistence is best-effort and the original
error remains primary. Preflight
version/count/configuration failures occur before candidate creation. Repair the source/model/environment, inspect
again, then run a new explicit `--publish`; no wildcard cleanup or implicit promotion
is performed. Activation holds a per-corpus PostgreSQL advisory transaction lock and
replaces stable rows plus the active manifest in one transaction.

### Repository readiness and refresh metadata

`GET /ready` returns HTTP 200 only when the public local repositories are certified:
NCIt, caDSR, and Uberon/CL. Protected ICD-O metadata is deliberately excluded from that
public response. `GET /api/v1/icdo/access` checks consumer entitlement before certifying
all three served ICD-O datasets and returns only `ready-and-entitled`; its 403 and 503
responses drive the navigation access marker. Each access check and entitlement-gated
`POST /api/v1/refresh` certifies the current active generations, while ordinary repository
reads continue to certify their requested active generation
(`pdm run pytest
backend/tests/test_repository_metadata_api.py backend/tests/test_icdo_api.py -q`,
2026-08-14). NCIt additionally
requires its active manifest, completed activation journal (including `activated_at`),
release, and live default/stated observation to agree. Ready values carry each
repository's certified identities and observations. A single refusal returns HTTP 503
with that repository's typed reason, such as `manifest-missing`,
`activation-incomplete`, `release-mismatch`, or `observation-mismatch`; an unhealthy
value deliberately contains no certified identity fields.

The entitlement-gated refresh report returns discriminated metadata for NCIt, caDSR,
Uberon/CL, and each
served ICD-O edition/axis dataset. caDSR identifies its persisted source archive and
canonical serving rows; Uberon/CL identifies its certified combined index; ICD-O binds
the active generation to its source digest, exact serving digest, and row count. The
refresh page displays these values and the exact unhealthy reason; it does not turn
endpoint reachability into a ready claim (D68).

## Validation tools (ROBOT + ELK)

The non-circular validation harness uses the [OBO ROBOT CLI](https://robot.obolibrary.org/)
(Apache-2.0) to check the OWL 2 EL profile and classify with the ELK reasoner (which ships
with ROBOT).  ROBOT requires a Java 21 runtime.

### Install Java 21

**macOS (Homebrew):**
```bash
brew install openjdk@21
```

**Linux (Debian/Ubuntu):**
```bash
sudo apt install openjdk-21-jdk-headless
```

Verify: `java -version` → `openjdk version "21" …`.

### Install ROBOT

Use the repository installer. It downloads the pinned release to a temporary file,
requires its SHA-256 identity to match, and only then publishes the JAR, launcher, and
`robot-tool.json` provenance record:

```bash
pdm run python scripts/install_robot.py --install-dir "$PWD/.tools/robot-1.9.10"
```

`cp .env.example .env` configures this certified repository-local installation for
every `pdm run` command. Verify with `pdm run ./.tools/robot-1.9.10/robot --version`;
it must print `ROBOT version 1.9.10`. The pinned digest is
`sha256:16a73c074f3df359a7338a84b4e0788785fe06117f931bb9796e9619ea776105`
(`gh release view v1.9.10 --repo ontodev/robot --json assets`, 2026-08-09); the official
release exposes that SHA-256 asset digest and no separate signature asset in its asset
list (same command, 2026-08-09).

### Usage

All harness functions invoke `robot` via `subprocess`.  The EL profile gate and ELK
classification use:

```bash
robot validate-profile --profile EL --input <ontology.owl>
robot reason --reasoner ELK --input <ontology.owl> --output <inferred.owl>
```

**Memory:** for large ontologies (e.g. the full NCIt stated build), pass JVM heap via the
launcher or `JAVA_OPTS`:
```bash
robot reason --reasoner ELK -Xmx32g --input <ontology.owl> --output <inferred.owl>
```

`data-build xref-promote` requires `ONTOPRISM_ROBOT_DIR`, supplied by the PDM-loaded
`.env`; it revalidates the JAR, launcher,
metadata, and observed version before starting, then persists the source, version, and
digest in the xref run metrics. Direct library contract tests may use `robot` on `PATH`;
no Python dependency is added for Java or ROBOT.

### Running the promotion pass (#73)

Candidate ingest writes `closeMatch/proposed` records; **only the promotion pass turns
them into the identity-grade `exactMatch/validated` bridges the caDSR coverage number
(`COV`, §13.3) counts.**  It classifies a small merged fragment per candidate, so it
needs `robot` on PATH:

```bash
pdm run data-build xref            # ingest candidates (closeMatch/proposed)
pdm run data-build xref-promote --golden <curated-sssom.json>   # promote (needs robot)
pdm run data-build xref-coverage   # the published COV number
```

`--golden` is optional but load-bearing on a cold store. The SME-signed `exactMatch`
pairs it carries do **two** jobs: they are the **trusted anchors** that structural
corroboration is measured against for *other* candidates, and they are admissible
standalone evidence for *themselves* (D28 accepts human curation alone), so the golden
pairs promote directly — which is what first moves `COV` off zero.

**When a zero-promotion run is trustworthy, and when it is a broken pipeline.** With no
anchors and no curation, nothing can be corroborated and the pass promotes nothing —
that is correct, conservative behaviour. But do **not** read every zero that way:

- `considered: 0` means no candidates were ingested — run `data-build xref` first.
- A non-zero `reasoner_errors`, or `status: failed`, means the reasoner could not run
  (the command also exits non-zero). This is **not** "no candidate qualified" — check
  that `robot` and Java are on PATH.
- The run **refuses to start** (loudly) if the stated NCIt graph or the upstream store
  is empty, because every candidate would then fail for a reason that has nothing to do
  with the candidate.
- If the log warns that **zero `owl:disjointWith` axioms** loaded, the reasoner has
  nothing to refute with: promotion is resting entirely on the evidence policy, and the
  result should not be described as "reasoner-validated".

Notes:

- **Never send a source OWL through Graph Store HTTP or load it directly into the active
  store.** `data-build owl` produces `Thesaurus-inferred.owl`,
  `Thesaurus-stated.owl`, their distinct cached archives, and
  `ncit-artifact-pair.json`. Revalidate that manifest before use. The *stated* build alone
  expands beyond 700 MB; D12 records why HTTP loading is unsafe, D46 makes the prohibition
  executable, and D67 records the pinned streaming Jena-to-N-Triples plus offline QLever
  index path.
- The embedding step is heavy (multi-GB model + compute over ~200k concepts + ~80k
  CDEs) and is a batch/offline operation. CI runs deterministic encoders against
  disposable pgvector to prove staged-batch invisibility, failure rollback, validation,
  retry, independent corpora, and reconcilable ordered activation. Explicit `full_build` contracts
  verify the real encoder shape and inspect configured full-build artifacts; the
  expensive end-to-end build remains an operator run.
- The QLever indexes, caDSR SQLite, and pgvector rows produced are the same shapes the
  running app reads. See [DECISIONS.md](DECISIONS.md).
