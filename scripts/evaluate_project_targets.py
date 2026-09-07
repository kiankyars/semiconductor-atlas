#!/usr/bin/env python3
"""Evaluate accepted source-project comparisons, not physical production forecasts."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from semiconductor_atlas import project_target_population as population, project_target_evaluation as evaluation
from semiconductor_atlas.ai_critical_changes import _pretty_bytes
from semiconductor_atlas.curated_capture import _write


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("freeze", "verify", "verify-sources", "score"):
        command = commands.add_parser(name)
        if name in {"freeze", "verify-sources"}:
            command.add_argument("--database", type=Path, required=True, help="Existing read-only core database; no migration")
            command.add_argument("--reference-root", type=Path, required=True)
            command.add_argument("--source-queue", type=Path, required=True)
        if name == "freeze":
            command.add_argument("--study", type=Path, required=True)
            command.add_argument("--history", type=Path, required=True, help="Exact unified v2 event export")
        else:
            command.add_argument("--frozen", type=Path, required=True)
        if name == "score":
            command.add_argument("--labels", type=Path, required=True)
        if name in {"freeze", "score"}:
            command.add_argument("--output", type=Path, required=True, help="New-only output file")
    args = parser.parse_args()
    try:
        if args.command in {"freeze", "verify-sources"}:
            connection = sqlite3.connect(args.database.absolute().as_uri() + "?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            try:
                if args.command == "freeze":
                    result = population.freeze_population(connection, args.study, args.history,
                        reference_root=args.reference_root, source_queue=args.source_queue)
                else:
                    result = population.verify_population_sources(connection, args.frozen,
                        reference_root=args.reference_root, source_queue=args.source_queue)
            finally:
                connection.close()
        elif args.command == "verify":
            result, raw = population.validate_population(args.frozen)
            result = {"frozen_sha256": population._hash(raw), "opportunities": len(result["opportunities"]),
                      "predictions": len(result["predictions"]), "validation_scope": result["validation_scope"]}
        else:
            result = evaluation.evaluate(args.frozen, args.labels)
        if args.command in {"freeze", "score"}:
            raw = _pretty_bytes(result)
            _write(args.output, raw)
            result = {"output": str(args.output), "sha256": population._hash(raw), "bytes": len(raw)}
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
