#!/usr/bin/env python3
"""Bind source-statement annotations to a replayed retained observation population."""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from semiconductor_atlas import source_statement_review as statements
from semiconductor_atlas.ai_critical_changes import _open_real_directory_fd, _pretty_bytes, _strict_json
from semiconductor_atlas.source_checks import _read


def _write_report(output: Path, raw: bytes, frozen_path: Path, reference_root: Path, frozen_sha256: str):
    output = output.absolute()
    root = reference_root.resolve()
    frozen_raw = _read(frozen_path)
    if statements._hash(frozen_raw) != frozen_sha256:
        raise ValueError("frozen population changed before report installation")
    frozen = _strict_json(frozen_raw, "verified population")
    protected = {root / Path(name).parent for name in frozen["snapshot"]["files"]}
    protected.update(root / name for name in frozen["snapshot"]["root_entries"])
    protected.update(root / row["path"] for row in frozen["snapshot"]["captures"])
    if any(output == directory or output.is_relative_to(directory) for directory in protected):
        raise ValueError("report output must be outside retained source and polling directories")
    _, directory = _open_real_directory_fd(output.parent, "report output parent", create=False)
    try:
        descriptor = os.open(output.name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                             0o644, dir_fd=directory)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen", required=True, type=Path)
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--reference-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = statements.review(args.frozen, args.labels, reference_root=args.reference_root)
        raw = _pretty_bytes(result)
        _write_report(args.output, raw, args.frozen, args.reference_root, result["frozen_sha256"])
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps({"output": str(args.output), "bytes": len(raw),
        "sha256": statements._hash(raw), "counts": result["counts"]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
