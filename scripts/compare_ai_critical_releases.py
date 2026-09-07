#!/usr/bin/env python3
"""Compare two AI-critical manufacturing release vintages."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from semiconductor_atlas.ai_critical_changes import (
    validate_change_output_locations,
    write_change_bundle,
    write_deterministic_change_archive,
)


def compare(
    prior: Path,
    current: Path,
    output: Path,
    archive: Path | None,
) -> dict[str, object]:
    validate_change_output_locations(prior, current, output, archive)
    manifest = write_change_bundle(prior, current, output)
    result: dict[str, object] = {
        "output": str(output.resolve()),
        "manifest": manifest,
    }
    if archive is not None:
        archive_result = write_deterministic_change_archive(
            output,
            archive,
            protected_directories=(prior, current),
        )
        result["archive"] = archive_result
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a deterministic cross-vintage AI-critical change bundle"
    )
    parser.add_argument("--prior", required=True, type=Path)
    parser.add_argument("--current", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    try:
        result = compare(args.prior, args.current, args.output, args.archive)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
