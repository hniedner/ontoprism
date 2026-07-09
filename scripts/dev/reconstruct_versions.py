#!/usr/bin/env python
"""Seed the release history that predates automated versioning.

ontoprism reached ~0.7.0-worth of work across 27 merged PRs before any tag existed.
python-semantic-release computes the next version by walking commits *since the last
tag*, so with no tags it would restart at 0.0.0 and re-announce three months of work
as a first release. This script retro-tags the milestone boundaries so the first
automated release continues the history instead of overwriting it.

The version boundaries below are milestones, not per-PR bumps: each one is the merge
commit where a coherent capability became complete. They are recorded here (rather
than derived from commit subjects) because only 27 of 56 pre-tag commits used
Conventional Commit subjects — a parser-driven reconstruction would silently drop
half the history. `CHANGELOG.md` narrates the same boundaries.

Usage:
    python scripts/dev/reconstruct_versions.py            # verify only (default)
    python scripts/dev/reconstruct_versions.py --write    # create local tags
    python scripts/dev/reconstruct_versions.py --write --push   # ...and push them

Idempotent: an existing tag pointing at the expected commit is left alone; one
pointing somewhere else is a hard error (never silently moved).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from typing import NamedTuple


class Milestone(NamedTuple):
    """One reconstructed release: a tag pinned to the merge commit that completed it."""

    tag: str
    commit: str
    date: str
    summary: str


# Ordered oldest → newest. Commits are on main's first-parent line.
MILESTONES: tuple[Milestone, ...] = (
    Milestone(
        "v0.1.0",
        "acb66e5",
        "2026-07-03",
        "NCIt + caDSR explorer: Oxigraph storage, read APIs, SvelteKit UI, "
        "refresh, isolated services, pgvector embeddings + semantic search",
    ),
    Milestone(
        "v0.2.0",
        "5d4710b",
        "2026-07-04",
        "fairlib → ontolib rename; professional UI, browse mode, interactive "
        "graph explorer",
    ),
    Milestone(
        "v0.3.0",
        "1843c78",
        "2026-07-05",
        "Platform hardening: API security, Alembic schema, rate limiting, "
        "readiness, SPARQL UI, quality gates (#19-#25)",
    ),
    Milestone(
        "v0.4.0",
        "074c7af",
        "2026-07-05",
        "External repositories (ClinicalTrials.gov, PubMed), download caches, "
        "ops/packaging, frontend tests, CI integration services (#26-#32)",
    ),
    Milestone(
        "v0.5.0",
        "5a2c1bf",
        "2026-07-06",
        "Full-text search, graph-explorer parity + minimap, reproducible data "
        "build, >90% coverage gate (#33-#37)",
    ),
    Milestone(
        "v0.6.0",
        "c3e90a4",
        "2026-07-06",
        "Decomposition engine 5a: detector + extract core, stated NCIt OWL, "
        "read API, UI panel, golden-set spike (#38-#43)",
    ),
    Milestone(
        "v0.7.0",
        "4143d6b",
        "2026-07-08",
        "Decomposition 5b: run orchestrator, CLI, provenance hardening; "
        "multi-parent DAG + most-specific filler decisions (#45, #46)",
    ),
)


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _resolve(commit: str) -> str:
    """Full SHA for *commit*, or exit if it does not exist."""
    try:
        return _git("rev-parse", f"{commit}^{{commit}}")
    except subprocess.CalledProcessError:
        sys.exit(f"ERROR: commit {commit} not found — has main been rewritten?")


def _existing_tag_target(tag: str) -> str | None:
    """Full commit SHA *tag* points at, or None when the tag does not exist."""
    try:
        return _git("rev-list", "-n", "1", tag)
    except subprocess.CalledProcessError:
        return None


def _assert_on_main(sha: str, tag: str) -> None:
    try:
        _git("merge-base", "--is-ancestor", sha, "origin/main")
    except subprocess.CalledProcessError:
        sys.exit(f"ERROR: {tag} target {sha[:8]} is not an ancestor of origin/main.")


def _remote_tags() -> set[str]:
    """Milestone tag names that already exist on origin."""
    out = _git("ls-remote", "--tags", "origin")
    return {
        line.split("refs/tags/", 1)[1].removesuffix("^{}")
        for line in out.splitlines()
        if "refs/tags/" in line
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="create missing tags locally"
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="push every milestone tag that origin is missing",
    )
    args = parser.parse_args()

    if args.push and not args.write:
        sys.exit("ERROR: --push requires --write")

    to_create: list[tuple[Milestone, str]] = []

    for ms in MILESTONES:
        sha = _resolve(ms.commit)
        _assert_on_main(sha, ms.tag)
        existing = _existing_tag_target(ms.tag)

        if existing is None:
            to_create.append((ms, sha))
            print(f"  MISSING  {ms.tag:<8} -> {sha[:8]}  {ms.date}")
        elif existing == sha:
            print(f"  ok       {ms.tag:<8} -> {sha[:8]}  {ms.date}")
        else:
            sys.exit(
                f"ERROR: {ms.tag} already points at {existing[:8]}, expected "
                f"{sha[:8]}. Refusing to move an existing tag — resolve by hand."
            )

    if to_create and not args.write:
        print(f"\n{len(to_create)} tag(s) missing. Re-run with --write to create.")
        return 1

    for ms, sha in to_create:
        _git("tag", "-a", ms.tag, sha, "-m", f"{ms.tag} ({ms.date})\n\n{ms.summary}")
        print(f"  created  {ms.tag} -> {sha[:8]}")

    # Local existence is not remote existence: semantic-release resolves the last
    # release from origin's tags. Push whatever origin lacks, not merely whatever
    # this invocation happened to create — otherwise a rerun reports success while
    # leaving origin untagged, and the first release restarts the version at zero.
    unpushed = [ms.tag for ms in MILESTONES if ms.tag not in _remote_tags()]

    if not unpushed:
        print("\nAll milestone tags present locally and on origin.")
        return 0

    if args.push:
        _git("push", "origin", *unpushed)
        print(f"\nPushed {len(unpushed)} tag(s) to origin: {' '.join(unpushed)}")
    else:
        print(f"\n{len(unpushed)} tag(s) missing on origin. Push with:")
        print("  git push origin " + " ".join(unpushed))
        print("  (or re-run this script with --write --push)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
