# Immutable bounded run artifacts

Issue #334 introduces create-only retention only for the bounded M1.6 current replay and
its direct evidence consumer. It does not change the general decomposition `--out`
contract, decomposition semantics, database schema, API, or frontend.

## Schema and identity

`ArtifactManifest` schema v1 binds a family and locator-only generation ID, an optional
persisted run ID, exact parent-manifest identities, generator identity and argv, source
identities, artifact-relative paths with byte sizes and SHA-256 identities, retention
class and owner, completion state, and manifest identity. Manifest identity is SHA-256
over canonical JSON excluding only `manifest_identity`; schema v1 has no timestamp.
Artifact records identify exact bytes. `ArtifactUnavailableRecord` is a distinct schema
and cannot be interpreted as a manifest or as proof that bytes are present.

The bounded replay layout is:

```text
tmp/artifacts/v1/generations/m1-6-current-replay/<generation-id>/
  artifacts/decomposition.ttl
  manifest.json
  .complete
```

The UUID generation ID is allocated once as a collision-resistant locator. It has no
semantic meaning. Publication builds and fsyncs a unique staging tree, claims the final
directory with exclusive `mkdir`, derives the manifest records from the staged bytes,
copies those bytes only into that owned directory, fsyncs the manifest and directories,
writes and fsyncs `.complete` last, and byte-verifies the completed generation before
reporting success. Artifact paths cannot occupy manifest, completion, claim, or staging
control names. There is no
`os.replace` of immutable generation content. Identical retries verify every byte;
conflicts and markerless partial directories refuse without adoption or overwrite.
The create-only fsynced writer is deliberately separate from general atomic-write
helpers: replacement is valid for mutable files but would violate generation ownership.

There is no mutable `current` or `latest` reference, symlink, pointer file, promotion
operation, or adoption operation. Consumers require the exact parent manifest path and identity.
`generate-current-evidence` requires both values; resolution verifies the completion
marker, manifest identity, and all artifact bytes before supplying the bound run ID and
artifact to the existing evidence generator. A wrong identity, substituted manifest,
missing parent, or markerless interrupted generation refuses. Candidate call sites
owned by #274 are not present on this milestone base and remain deferred rather than
being recreated or cherry-picked here.

## Existing and unavailable artifacts

The existing `tmp/m1-6-current-full-corpus.ttl` and
`tmp/m1-6-prechange-v4-full-corpus.ttl` remain in place. A legacy sidecar may be recorded
under `tmp/artifacts/v1/legacy-in-place/` only after its regular-file bytes, embedded run
membership, and persisted database digest and path all agree. Recording does not move,
rewrite, or delete the TTL and assigns `critical-full-corpus` retention without expiry.

Run `neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997` with expected artifact SHA-256
`4febb77cb0e0b91418a22a08c19d9fa05d65529f00af30e85afe53a8d716424d` is unavailable:
its last-known shared path was overwritten before immutable retention. Its unavailable
record must claim no present file locator; the bytes must not be reconstructed or
described as observed. Its two known references are labels for the historical paths
`tmp/m1-6-normalized-group-policy-candidate.json` and
`tmp/m1-6-group-review-pre274-observations.json`; they do not claim those bytes are
currently present.

## Retention and inventory

`artifact-retention.toml` declares the sole cleanup-managed root,
`tmp/artifacts/v1/generations/`, and class ownership. Optional class/root budgets are
absent by default and inventory reports them as `not-declared`; absence neither refuses
generation nor invents an arbitrary byte limit.

`pdm run artifacts inventory` is read-only. It reports exact logical and allocated
totals for ignored `tmp/` and `data/`, managed generations and unavailable/partial
records, and one summary per top-level unmanaged root. Each unmanaged summary has file
count, logical and allocated bytes, unknown retention/owner/reference status, and a
bounded top-N largest-file drilldown with an explicit truncation flag. Output is bounded
by roots, classes, generations, and top-N rather than every unknown file. Unknown bytes
are read from filesystem metadata and are never hashed.

Worktrees are audit-only. Each obtainable entry reports exact path, common Git dir,
HEAD, branch or detached state, dirty/untracked/ignored counts, unique-commit status,
and `operator-action-required`. Fallow/tool-owned and #274 recovery worktrees are
preserved for operator resolution. The tool contains no worktree removal, pruning,
force, or directory-deletion operation. Missing Git, failed Git inspection, and an
empty list are distinct statuses.

The Compose project identity is `ontoprism-podman-poc`, matching the existing active
containers and volume `ontoprism-podman-poc_ontoprism_pg_data`. This change does not
rename or move resources. Inventory marks active services and volumes protected;
missing Docker, command failure, and an empty project are distinct statuses. Cleanup
never targets Compose, data roots, worktrees, or unmanaged paths and never invokes a
volume-bearing down/remove operation.

## Managed cleanup and recovery

`pdm run artifacts plan` writes a create-only, content-identified plan beneath
`tmp/artifacts/v1/cleanup-plans/`. Actions can name only complete, byte-verified,
cleanup-eligible immutable generation directories directly below the managed root.
Each action binds its exact generation path, manifest path and identity, logical bytes,
owner, retention class, references, and reason. Ordering and canonical JSON identity
are deterministic. Unknown classes/owners, partial generations, symlinks, parent or
incoming references, and critical/full-corpus evidence refuse or are excluded as
appropriate. `licensed-source` is explicitly blocked until #335 certifies the generated
repository; this protects `tmp/data/Metathesaurus_2026AA/META` independently of its
current unmanaged location.

Apply requires both the exact plan path and identity:

```text
pdm run artifacts apply --plan tmp/artifacts/v1/cleanup-plans/<identity>.json --identity <identity>
```

Before the first mutation it revalidates the plan identity/location, all action paths,
non-overlap, policy, ownership, references, manifests, completion markers, complete
inventories, and artifact hashes. Any drift refuses before deletion. Only after that
global preflight may its internal safe tree remover delete exact managed generation
directories. A later filesystem failure reports `partial-failure` with per-action
`removed`/`failed` status and exact reclaimed logical bytes; there is no rollback
promise. Safe recovery is to preserve remaining bytes, rerun read-only inventory,
resolve the operator-visible error, and create a fresh plan. Never edit or adopt a
markerless generation and never reuse an old plan after drift.
