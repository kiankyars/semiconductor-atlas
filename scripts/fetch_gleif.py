#!/usr/bin/env python3
"""Acquire or verify a bounded exact-LEI GLEIF Level 1 snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence
from urllib.error import HTTPError, URLError


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from semiconductor_atlas.gleif_snapshot import (  # noqa: E402
    create_gleif_snapshot,
    read_lei_allowlist_file,
    verify_gleif_snapshot,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Acquire exact GLEIF Level 1 records from a canonical LEI allowlist, "
            "or verify an existing snapshot entirely offline."
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--allowlist",
        type=Path,
        help="canonical sorted file containing one declared LEI per line",
    )
    mode.add_argument(
        "--verify-only",
        type=Path,
        metavar="SNAPSHOT",
        help="verify and replay this existing snapshot without network access",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="new snapshot directory; required with --allowlist",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    return parser


def _summary(snapshot) -> dict[str, object]:
    return {
        "attempts_used": snapshot.attempts_used,
        "golden_copy_publish_date": snapshot.golden_copy_publish_date,
        "lei_count": len(snapshot.leis),
        "manifest": str((snapshot.root / "manifest.json").absolute()),
        "manifest_sha256": snapshot.manifest_sha256,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.verify_only is not None:
            if args.output_dir is not None:
                raise ValueError("--output-dir cannot be used with --verify-only")
            if args.timeout != 30.0 or args.max_attempts != 3:
                raise ValueError(
                    "--timeout and --max-attempts apply only to acquisition"
                )
            snapshot = verify_gleif_snapshot(args.verify_only)
        else:
            if args.output_dir is None:
                raise ValueError("--output-dir is required with --allowlist")
            allowlist_raw = read_lei_allowlist_file(args.allowlist)
            snapshot = create_gleif_snapshot(
                allowlist_raw,
                args.output_dir,
                timeout_seconds=args.timeout,
                max_attempts=args.max_attempts,
            )
        print(json.dumps(_summary(snapshot), sort_keys=True))
        return 0
    except (
        FileExistsError,
        HTTPError,
        OSError,
        RuntimeError,
        TimeoutError,
        TypeError,
        URLError,
        ValueError,
    ) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
