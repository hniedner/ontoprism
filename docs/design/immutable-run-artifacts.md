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

`generate-current-evidence` requires both the exact parent `manifest.json` path and its
manifest identity. Resolution verifies the completion marker, manifest identity, and
all artifact bytes before supplying the bound run ID and artifact to the existing
evidence generator. Candidate call sites not present on this branch are deferred rather
than recreated here.

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

`artifact-retention.toml` is class-based and intentionally has no arbitrary byte or age
budget. `pdm run artifacts inventory` is read-only. It reports logical and allocated
usage for ignored `tmp/` and `data/`, registered Git worktrees, managed records,
unmanaged paths, availability, references, retention classes, and Compose resources.
Marker-less generation directories are reported as partial rather than omitted.
Unknown large files are sized from filesystem metadata and are not digested. There is
no delete, prune, apply, worktree-removal, adoption, or authorization mode.

The Compose project identity is `ontoprism-podman-poc`, matching the existing active
containers and volume `ontoprism-podman-poc_ontoprism_pg_data`. This change does not
rename or move resources and never invokes `down -v` or `--volumes`.
