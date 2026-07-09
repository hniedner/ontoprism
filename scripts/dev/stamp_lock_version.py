#!/usr/bin/env python
"""Keep `pdm.lock`'s editable local packages at the project's current version.

`pdm.lock` records `ontolib` and `ontoprism-backend` — the two editable local
packages — with a pinned `version`, and `pdm sync` reconciles the working set *to
the lock*. semantic-release stamps the three `pyproject.toml` files on release but
knows nothing about the lock, so after every release the lock still names the old
version and `pdm sync` cheerfully "downgrades" the installed metadata:

    ✔ Update ontolib 0.7.1 -> 0.1.0 successful

Nothing in the source tree reverts (the install is editable), but the installed
dist-info disagrees with `ontolib.__version__`, and the drift grows every release.

This runs as semantic-release's `build_command` (see `[tool.semantic_release]`),
with `pdm.lock` listed in `assets` so the corrected lock rides along in the release
commit.

Why not just run `pdm lock`? Because a full relock also re-resolves third-party
dependencies — it moved five unrelated packages when this was written — which would
bundle silent dependency drift into every release commit and make the lock depend on
whatever the index served that minute. `pdm lock --update-reuse` and `--refresh` both
reuse the pinned entries and do not update the local packages at all.

Editing only these two `version` fields is safe: `[metadata].content_hash` is derived
from the root project's *dependency specifiers*, not from the local packages'
versions, so it is unaffected (verified: a full relock leaves it byte-identical).

Usage:
    python scripts/dev/stamp_lock_version.py           # stamp pdm.lock
    python scripts/dev/stamp_lock_version.py --check   # exit 1 if out of sync
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCK = REPO_ROOT / "pdm.lock"
PYPROJECT = REPO_ROOT / "pyproject.toml"

# The editable local packages `pdm sync` reinstalls from the lock.
LOCAL_PACKAGES = ("ontolib", "ontoprism-backend")

_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


def project_version() -> str:
    """The root project's version — the single source of truth."""
    match = _VERSION_RE.search(PYPROJECT.read_text(encoding="utf-8"))
    if match is None:
        sys.exit(f"ERROR: no version found in {PYPROJECT}")
    return match.group(1)


def _stamp(lock_text: str, version: str) -> tuple[str, dict[str, str]]:
    """Rewrite each local package's ``version`` in *lock_text*.

    Returns the new text and a map of package -> the version it previously held.
    Only the `version` line immediately following the matching `name` line inside a
    `[[package]]` block is touched, so unrelated packages can never be rewritten.
    """
    lines = lock_text.splitlines(keepends=True)
    previous: dict[str, str] = {}
    current: str | None = None

    for i, line in enumerate(lines):
        name_match = re.match(r'^name\s*=\s*"([^"]+)"', line)
        if name_match:
            current = name_match.group(1)
            continue
        if current in LOCAL_PACKAGES:
            version_match = re.match(r'^version\s*=\s*"([^"]+)"', line)
            if version_match:
                previous[current] = version_match.group(1)
                lines[i] = f'version = "{version}"\n'
                current = None

    return "".join(lines), previous


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drift without writing (exit 1 when out of sync)",
    )
    args = parser.parse_args()

    version = project_version()
    original = LOCK.read_text(encoding="utf-8")
    stamped, previous = _stamp(original, version)

    missing = [pkg for pkg in LOCAL_PACKAGES if pkg not in previous]
    if missing:
        sys.exit(f"ERROR: {', '.join(missing)} not found in {LOCK.name}")

    stale = {pkg: was for pkg, was in previous.items() if was != version}

    if not stale:
        print(f"pdm.lock already at {version} for {', '.join(LOCAL_PACKAGES)}")
        return 0

    if args.check:
        for pkg, was in stale.items():
            print(f"  {pkg}: lock has {was}, project is {version}")
        print("\npdm.lock is stale. Run: python scripts/dev/stamp_lock_version.py")
        return 1

    LOCK.write_text(stamped, encoding="utf-8")
    for pkg, was in stale.items():
        print(f"  {pkg}: {was} -> {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
