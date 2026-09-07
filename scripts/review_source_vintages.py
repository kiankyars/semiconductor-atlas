#!/usr/bin/env python3
"""Freeze, verify or review all shared retained NIST award URLs across selected vintages."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from semiconductor_atlas import source_vintage_review as vintage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("freeze", "verify", "review"):
        command = commands.add_parser(name)
        command.add_argument("--reference-root", type=Path, required=True)
        command.add_argument("--study" if name == "freeze" else "--frozen", type=Path, required=True)
        if name == "review":
            command.add_argument("--labels", type=Path, required=True)
        if name != "verify":
            command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "freeze":
            result = vintage.freeze(args.study, reference_root=args.reference_root)
            protection = result["snapshot"]["protected_directories"]
        else:
            frozen, raw = vintage.validate(args.frozen, reference_root=args.reference_root)
            protection = frozen["snapshot"]["protected_directories"]
            result = vintage.review(args.frozen, args.labels, reference_root=args.reference_root) if args.command == "review" else {
                "frozen_sha256": vintage._hash(raw), "counts": frozen["snapshot"]["counts"], "exact_source_replay": True, "network_requests": 0}
            if args.command == "review" and result["frozen_sha256"] != vintage._hash(raw):
                raise ValueError("cohort changed between output protection and review")
        if args.command != "verify":
            result = {**vintage.write_new(args.output, result, reference_root=args.reference_root, protected_directories=protection),
                "counts": result["snapshot"]["counts"] if args.command == "freeze" else result["counts"]}
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
