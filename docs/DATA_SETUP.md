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

## Rebuild-from-scratch (standalone, no fairdata)

The clone path depends on the local `fairdata-oxigraph:local` image + fairdata's data.
A fully standalone setup would instead: load `Thesaurus.owl` via `oxigraph load` (~minutes),
build the caDSR DB from the caDSR XML, and compute embeddings with sentence-transformers.
Tracked as a follow-up; see [DECISIONS.md](DECISIONS.md).
