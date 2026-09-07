#!/usr/bin/env python3
"""Capture reviewed NIST indexes, replay retained bytes, or rebuild discovery inventory."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from semiconductor_atlas.ai_critical_changes import _pretty_bytes
from semiconductor_atlas.curated_capture import _write
from semiconductor_atlas.nist_discovery import capture_indexes, discovery_inventory, validate_capture


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture")
    capture.add_argument("--plan", type=Path, required=True)
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--repository-root", type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("--run", type=Path, required=True)
    inventory = commands.add_parser("inventory")
    inventory.add_argument("--run", type=Path, action="append", required=True)
    inventory.add_argument("--as-of", required=True)
    inventory.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "capture":
            result = capture_indexes(args.plan, args.output, repository_root=args.repository_root)
        elif args.command == "verify":
            result = validate_capture(args.run)
        else:
            result = discovery_inventory(args.run, as_of=args.as_of)
            _write(args.output, _pretty_bytes(result))
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    if args.command != "inventory" and result["attention_required"]:
        parser.exit(2, "Capture retained and verified; discovery requires attention.\n")


if __name__ == "__main__":
    main()
