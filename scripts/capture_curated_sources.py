#!/usr/bin/env python3
"""Capture reviewed fixed URLs, or replay a retained acquisition without network."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from semiconductor_atlas.curated_capture import capture_sources, validate_capture


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture")
    capture.add_argument("--plan", type=Path, required=True)
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--review-root", type=Path)
    capture.add_argument("--prior-checks", type=Path)
    capture.add_argument("--prior-root", type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "capture":
            result = capture_sources(
                args.plan, args.output, prior_checks=args.prior_checks,
                prior_root=args.prior_root, review_root=args.review_root,
            )
        else:
            result = validate_capture(args.run)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
