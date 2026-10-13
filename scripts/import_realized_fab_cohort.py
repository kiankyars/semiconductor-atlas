#!/usr/bin/env python3
"""Admit or read-only verify reviewed realized-fab reports in an existing schema-6 core."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from semiconductor_atlas.realized_cohort_import import accept_cohort
from scripts import frozen_runtime as runtime


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("admit", "verify"))
    parser.add_argument("--database", type=Path, required=True, help="Existing schema-6 database; never created or migrated")
    for field in ("cohort", "source-body", "provenance", "frozen-runtime-bundle"):
        parser.add_argument("--" + field, type=Path, required=True)
    for field in ("expected-runtime-sha256", "expected-runtime-commit", "expected-cohort-sha256"):
        parser.add_argument("--" + field, required=True)
    args = parser.parse_args(argv)
    connection = None
    try:
        path = args.database.absolute()
        parent = runtime._directory(path.parent)
        os.close(parent)
        if not path.is_file() or path.is_symlink():
            raise ValueError("core database must be an existing regular non-symlink file")
        connection = sqlite3.connect(path.as_uri() + ("?mode=ro" if args.command == "verify" else "?mode=rw"), uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        result = accept_cohort(connection, args.cohort, source_body=args.source_body, provenance=args.provenance,
            frozen_runtime_bundle=args.frozen_runtime_bundle, expected_runtime_sha256=args.expected_runtime_sha256,
            expected_runtime_commit=args.expected_runtime_commit, expected_cohort_sha256=args.expected_cohort_sha256,
            verify_only=args.command == "verify")
    except (OSError, ValueError, sqlite3.Error) as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
