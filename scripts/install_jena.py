#!/usr/bin/env python
"""Install the repository-pinned Apache Jena RIOT distribution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ontolib.core.data_build_tools import install_jena


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install-dir",
        type=Path,
        required=True,
        help="Dedicated directory for RIOT, its libraries, archive, and provenance.",
    )
    args = parser.parse_args()
    identity = install_jena(args.install_dir)
    print(json.dumps(identity.as_dict(), sort_keys=True))


if __name__ == "__main__":
    main()
