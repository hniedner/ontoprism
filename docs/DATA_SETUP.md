# Data provisioning (one-time, dev)

ontoprism runs its **own** isolated data services (see `docker-compose.yml`) so it never
interferes with the sibling fairdata app:

| Service | ontoprism | fairdata |
|---|---|---|
| Oxigraph NCIt | `:7888` | `:7878` |
| Oxigraph Uberon | `:7889` | `:7879` |
| PostgreSQL (pgvector) | `:5433` | `:5432` |
| backend | `:8011` | `:8001` |

The Oxigraph and caDSR source artifacts were initially provisioned from fairdata
(without re-downloading); embeddings must be rebuilt and validated as described below:

## 1. Oxigraph stores (triple store)

APFS copy-on-write clones of fairdata's on-disk RocksDB stores into `./data/`
(instant, own writable copy; `./data` is gitignored):

```bash
cp -Rc ../fairdata/resources/ncit/graph_store/.   data/oxigraph-ncit/
cp -Rc ../fairdata/resources/uberon/graph_store/. data/oxigraph-uberon/
cp -c  ../fairdata/data/cde_repository/cde_repository.db data/cadsr/cde_repository.db
docker compose up -d      # serves the clones via the same oxigraph 0.5.3 image
```

Verify: `curl -s localhost:7888/query -H 'Content-Type: application/sparql-query' \
--data 'SELECT (COUNT(*) AS ?n){?s ?p ?o}'` → 12,836,426 triples.

This loopback request is an operator check against the Oxigraph service itself. The
FastAPI application exposes no public raw-SPARQL endpoint; its supported query surface
is the typed API (D44).

## 2. Embeddings (pgvector)

Embeddings are published only by ontoprism's validated build. Do **not** pipe a sibling
database dump into the serving tables: row presence, vector dimension, and HNSW indexes
cannot prove source/model provenance or completeness. In July 2026 such a clone held
4,752 valid NCIt vectors but omitted canonical C3262; the old non-empty preflight
accepted it as usable.

The migration creates stable corpus-specific serving tables (`ncit_concepts` and
`cde_repository`), an `embedding_corpus_manifest`, and build-scoped staging. Similarity
readers return rows only when a completed active manifest exists. Existing pre-migration
rows are deliberately not auto-certified.

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

## Rebuild-from-scratch (standalone, no fairdata)

`pdm run data-build` stands ontoprism up from public sources with no fairdata
dependency (issue #7). It has five steps, runnable individually or together:

```bash
# 0. Bring up the empty data services + apply the DB schema.
pdm run up                      # oxigraph-ncit :7888, postgres :5433
pdm run migrate                 # pgvector embedding tables + ncit_search FTS cache

# 1. Download, hash, same-release-bind, and revalidate the inferred/stated NCIt pair.
#    Online full-store loading is disabled (D12/D46).
pdm run data-build owl

# 2. Revalidate that pair, bulk-load it with the pinned Oxigraph CLI, and certify a
#    directly queryable inactive sibling beside NCIT_STORE_DIR. This command never
#    renames or activates the sibling; #148 owns later serving activation (D47).
pdm run data-build ncit-store

# 3. caDSR CDEs → SQLite. Downloads the released CDE XML and builds cde_repository.db
#    (cdes + cde_concepts + the cdes_fts FTS5 index).
pdm run data-build cadsr

# 4. Inspect all embedding build manifests. This is read-only and exits non-zero rather
#    than writing implicitly:
pdm install -G data-build
pdm run data-build embeddings

# 5. Explicitly build and validate NCIt, then caDSR. Each corpus has its own atomic
#    activation; this is intentionally not one cross-corpus transaction, so a caDSR
#    failure cannot corrupt or roll back accepted NCIt vectors.
pdm run data-build embeddings --publish

# Or repair one corpus independently:
pdm run data-build embeddings --publish --corpus ncit
pdm run data-build embeddings --publish --corpus cadsr

# …or run 1→5 in one explicitly mutating shot:
pdm run data-build all
```

`NCIT_STORE_DIR` defaults to `data/oxigraph-ncit`. The sibling builder resolves that
directory only to choose the same parent filesystem; the active directory is never
mounted into a loader or validation container. A successful candidate contains
`.ontoprism-ncit-candidate.json`, which records the artifact-pair identity and hashes,
release, graph layout, exact counts, loader image ID/digest/CLI version, owner, paths, and
stable source identity. A rejected candidate contains
`.ontoprism-ncit-rejected.json` and is not eligible for activation. Keep it for diagnosis
or remove only after independently verifying its owner marker.

### Source-bound decomposition runs

Run decomposition only against an endpoint serving the certified candidate described by
the required manifest.

> **Not usable end to end until #148 lands.** `data-build ncit-store` deliberately leaves
> the certified candidate inactive and there is no supported activation step yet, so an
> endpoint configured against the active store will not match the candidate observation
> and `decompose` fails closed. Do not promote a candidate by hand.

```bash
pdm run decompose \
  --source-manifest /absolute/candidate/path/.ontoprism-ncit-candidate.json \
  --branch neoplasm \
  --out data/ncit_decomposed.ttl
```

The CLI revalidates the D47 proof and compares its complete candidate observation with
the live endpoint. It persists the exact worklist and immutable source/config fingerprint
before processing. `--branch` is a closed choice: only `neoplasm` is implemented; the
former `disease` label selected identical behavior and was removed, while `regimen`
remains unavailable until its distinct component-bag algorithm is implemented.
`--resume RUN_ID` accepts only the same source, branch, scope, limit,
algorithm/config, output, and load modes; it processes exactly unfinished items. Source
drift before completion fails closed and invalidates every persisted result row. The
`--out` TTL is staged and moved into place only after that check and completion succeed,
so a drifted run leaves no artifact behind. `--load` publishes the finished TTL into
the additive `ncit_decomposed` named graph after the run; that publication is outside
the run's transactional guarantees and its full design is #147.

Every manifest records source version/hash, immutable model revision, vector dimension,
expected unique-row count, code commit, build ID, sentinels, state, and timestamps;
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

### Validation and recovery

Run `pdm run data-build embeddings` first. It prints persisted build provenance and
lifecycle evidence (completed builds include actual counts), then refuses
to mutate without `--publish`. A valid NCIt publication requires exact source-count
agreement with both the enumerated source and the configured release expectation
(`NCIT_EMBEDDING_EXPECTED_ROWS=204373` for 26.02d /
`CADSR_EMBEDDING_EXPECTED_ROWS=79827`) plus C3262;
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

Download the latest `.jar` from the [releases page](https://github.com/ontodev/robot/releases)
and place a wrapper script on PATH:

```bash
ROBOT_VERSION=1.9.6   # check latest at the releases page
curl -sSLO "https://github.com/ontodev/robot/releases/download/v$ROBOT_VERSION/robot.jar"
chmod +x robot.jar

# Create a small launcher script (adjust JAVA_HOME as needed):
cat > /usr/local/bin/robot << 'SCRIPT'
#!/bin/bash
exec java -jar /path/to/robot.jar "$@"
SCRIPT
chmod +x /usr/local/bin/robot
```

Alternatively, on macOS with Homebrew:
```bash
brew install robot
```

Verify: `robot --version` → prints a version string.

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

The harness defaults to the standard `robot` executable on PATH; no Python dependency is
added for Java or ROBOT.

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
  executable, and D47 constructs and certifies a separate inactive store with the pinned
  CLI.
- The embedding step is heavy (multi-GB model + compute over ~200k concepts + ~80k
  CDEs) and is a batch/offline operation. CI runs deterministic encoders against
  disposable pgvector to prove staged-batch invisibility, failure rollback, validation,
  retry, independent corpora, and atomic activation. Explicit `full_build` contracts
  verify the real encoder shape and inspect configured full-build artifacts; the
  expensive end-to-end build remains an operator run.
- The Oxigraph store, caDSR SQLite, and pgvector rows produced are the same shapes the
  running app reads, so a standalone build is a drop-in replacement for the fairdata
  clone described above. See [DECISIONS.md](DECISIONS.md).
