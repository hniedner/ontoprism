# Data provisioning (one-time, dev)

ontoprism runs its **own** isolated data services (see `docker-compose.yml`) so it never
interferes with the sibling fairdata app:

| Service | ontoprism | fairdata |
|---|---|---|
| Oxigraph NCIt | `:7888` | `:7878` |
| Oxigraph Uberon | `:7889` | `:7879` |
| PostgreSQL (pgvector) | `:5433` | `:5432` |
| backend | `:8011` | `:8001` |

The content was provisioned once from the fairdata build (no re-download / re-embed):

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

## 2. Embeddings (pgvector)

The 768-dim NCIt + caDSR embeddings (already computed by fairdata, sentence-transformers)
are copied into ontoprism's Postgres, then HNSW-indexed:

```bash
docker exec ontoprism-postgres psql -U ontoprism -d ontoprism -c "CREATE EXTENSION IF NOT EXISTS vector"
docker exec fairdata-postgres pg_dump -U fairdata -d fairdata \
  -t ncit_concepts -t cde_repository --no-owner --no-privileges \
  | docker exec -i ontoprism-postgres psql -U ontoprism -d ontoprism
```

Tables: `ncit_concepts` (202,825 rows, doc_id = concept code) and `cde_repository`
(79,827 rows, doc_id = `{public_id}:{version}`), each `embedding vector(768)` with an
HNSW cosine index. Used by the `/similar` endpoints (no runtime embedding model needed).

## Database schema (Alembic)

The pgvector tables above are also defined as an Alembic migration
(`migrations/versions/0001_embedding_tables.py`), so the schema is reproducible:

```bash
pdm run migrate         # fresh DB: create the embedding tables + HNSW indexes
pdm run migrate-stamp   # pre-existing cloned DB: mark migrated WITHOUT recreating
```

Run `migrate-stamp` **once** on the clone (its tables already exist); use `migrate` on a
from-scratch database. `migrations/env.py` reads the URL from `DATABASE_URL` / settings.

## Rebuild-from-scratch (standalone, no fairdata)

`pdm run data-build` stands ontoprism up from public sources with no fairdata
dependency (issue #7). It has four steps, runnable individually or together:

```bash
# 0. Bring up the empty data services + apply the DB schema.
pdm run up                      # oxigraph-ncit :7888, postgres :5433
pdm run migrate                 # pgvector embedding tables + ncit_search FTS cache

# 1. NCIt OWL → Oxigraph. Downloads from NCI EVS and loads the *inferred* build into
#    the default graph and the *stated* build into a distinct named graph (for the
#    decomposition engine, #4 / DECISIONS D4).
pdm run data-build owl

# 2. caDSR CDEs → SQLite. Downloads the released CDE XML and builds cde_repository.db
#    (cdes + cde_concepts + the cdes_fts FTS5 index).
pdm run data-build cadsr

# 3. Embeddings → pgvector. 768-dim sentence-transformers (all-mpnet-base-v2) for every
#    NCIt concept + caDSR CDE, plus a refresh of the NCIt FTS cache. Needs the optional
#    ML stack — install it first:
pdm install -G data-build
pdm run data-build embeddings

# …or run 1→3 in one shot:
pdm run data-build all
```

Notes:

- The embedding step is heavy (multi-GB model + compute over ~200k concepts + ~80k
  CDEs) and is a batch/offline operation — it does not run in CI. The behavioral pieces
  (XML parsing, embedding-text building, the pgvector upsert, OWL-load routing) are unit-
  and integration-tested; the full run is verified manually.
- The Oxigraph store, caDSR SQLite, and pgvector rows produced are the same shapes the
  running app reads, so a standalone build is a drop-in replacement for the fairdata
  clone described above. See [DECISIONS.md](DECISIONS.md).
