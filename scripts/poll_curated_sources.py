#!/usr/bin/env python3
"""Run one due-checked source polling invocation, recovering retained packets first."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from semiconductor_atlas.curated_poll import poll_once


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path)
    parser.add_argument("--force", action="store_true", help="One manual cadence override; never overrides review or recovery gates")
    args = parser.parse_args()
    try:
        result = poll_once(args.config, repository_root=args.repository_root, force=args.force)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
