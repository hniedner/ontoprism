"""Write the machine-generated production SPARQL inventory snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ontolib.terminologies.sparql_inventory import summarize_sparql_inventory


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = summarize_sparql_inventory(args.root.resolve())
    args.output.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
