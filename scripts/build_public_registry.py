#!/usr/bin/env python3
"""Derive the public facility-registry bundle from retained local evidence.

Example:
    python3 scripts/build_public_registry.py --output web/data/registry-2026-07-20
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from semiconductor_atlas import public_registry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=ROOT,
                        help="repository root holding releases/ and artifacts/ (default: this checkout)")
    args = parser.parse_args()
    try:
        manifest = public_registry.build(args.root, args.output)
    except (OSError, public_registry.RegistryError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps({"output": str(args.output), "counts": manifest["counts"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
