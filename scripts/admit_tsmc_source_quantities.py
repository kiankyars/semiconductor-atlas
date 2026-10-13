#!/usr/bin/env python3
"""Admit or replay four retained company-level quantities in an isolated database."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from semiconductor_atlas.tsmc_source_quantities import admit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('admit', 'verify'))
    for field in ('parent', 'review', 'source-body', 'output'):
        parser.add_argument('--' + field, type=Path, required=True)
    parser.add_argument('--expected-review-sha256', required=True)
    parser.add_argument('--expected-output-sha256')
    args = parser.parse_args(argv)
    try:
        result = admit(parent=args.parent, review_path=args.review, source_body=args.source_body,
            expected_review_sha256=args.expected_review_sha256, output=args.output,
            verify_only=args.command == 'verify', expected_output_sha256=args.expected_output_sha256)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
