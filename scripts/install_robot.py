#!/usr/bin/env python
"""Install the repository-pinned ROBOT/ELK artifact after SHA-256 verification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ontolib.core.data_build_tools import install_robot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install-dir",
        type=Path,
        required=True,
        help="Dedicated directory that will contain robot.jar, robot, and provenance.",
    )
    args = parser.parse_args()
    identity = install_robot(args.install_dir)
    print(json.dumps(identity.as_dict(), sort_keys=True))


if __name__ == "__main__":
    main()
