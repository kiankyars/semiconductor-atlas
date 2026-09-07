#!/usr/bin/env python3
"""Build or replay an offline discovery-to-reviewed-source handoff; never acquire URLs."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from semiconductor_atlas import discovery_handoff as handoff


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--review", type=Path, required=True)
    build.add_argument("--reference-root", type=Path)
    build.add_argument("--output", type=Path, required=True)
    for name in ("verify", "history"):
        command = commands.add_parser(name)
        command.add_argument("--artifact", type=Path, required=True)
        if name == "history":
            command.add_argument("--as-of", required=True)
    args = parser.parse_args()
    try:
        if args.command == "build":
            result = handoff.write_handoff(args.review, args.output, reference_root=args.reference_root)
        elif args.command == "verify":
            result = handoff.validate_handoff(args.artifact)
        else:
            result = handoff.replay_handoff(args.artifact, as_of=args.as_of)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
