#!/usr/bin/env python3
"""Report cohort monitoring gaps and document-check freshness without network access."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from semiconductor_atlas.curated_capture import _now, _write
from semiconductor_atlas.ai_critical_changes import _pretty_bytes
from semiconductor_atlas.curated_coverage import coverage_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--as-of")
    parser.add_argument("--repository-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = coverage_report(args.catalog, args.database, as_of=args.as_of or _now(),
                                 repository_root=args.repository_root)
        if args.output is not None:
            _write(args.output, _pretty_bytes(result))
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
