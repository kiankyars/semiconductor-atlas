#!/usr/bin/env python3
"""Freeze or replay a retained source-observation census without acquisition or acceptance."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from semiconductor_atlas import curated_observation_population as population
from semiconductor_atlas.ai_critical_changes import _pretty_bytes
from semiconductor_atlas.curated_capture import _write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("freeze", "verify-sources"):
        command = commands.add_parser(name)
        command.add_argument("--reference-root", type=Path, required=True)
        if name == "freeze":
            command.add_argument("--study", type=Path, required=True)
            command.add_argument("--output", type=Path, required=True)
        else:
            command.add_argument("--frozen", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "freeze":
            result = population.freeze_population(args.study, reference_root=args.reference_root)
            raw = _pretty_bytes(result)
            _write(args.output, raw)
            result = {"output": str(args.output), "bytes": len(raw), "sha256": population._hash(raw), "counts": result["counts"]}
        else:
            result = population.verify_population_sources(args.frozen, reference_root=args.reference_root)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
