#!/usr/bin/env python3
"""Admit or exactly replay one reviewed source-native monthly rate."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from semiconductor_atlas.reviewed_source_rates import admit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('admit', 'verify'):
        command = commands.add_parser(name)
        for option in ('parent', 'review', 'reference-root', 'output'):
            command.add_argument('--' + option, type=Path, required=True)
        command.add_argument('--expected-review-sha256', required=True)
        if name == 'verify':
            command.add_argument('--expected-output-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        result = admit(parent=args.parent, review_path=args.review, reference_root=args.reference_root,
            expected_review_sha256=args.expected_review_sha256, output=args.output,
            verify_only=args.command == 'verify', expected_output_sha256=getattr(args, 'expected_output_sha256', None))
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
