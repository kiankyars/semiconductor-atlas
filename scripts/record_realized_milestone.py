#!/usr/bin/env python3
"""Record or verify a source-reported realized milestone using retained local evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.milestone_benchmark import _read
from semiconductor_atlas import milestone_benchmark as benchmark
from semiconductor_atlas import realized_milestones as observations
from semiconductor_atlas.ai_critical_changes import _strict_json


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("admit", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--source-body", type=Path, required=True,
                             help="Existing local publisher body; never downloaded or embedded")
        command.add_argument("--provenance", type=Path, required=True,
                             help="Exact retained source metadata file")
        if name == "admit":
            command.add_argument("--review", type=Path, required=True)
            command.add_argument("--output", type=Path, required=True,
                                 help="New-only local observation artifact")
        else:
            command.add_argument("--observation", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        body, provenance = _read(args.source_body), _read(args.provenance)
        if args.command == "admit":
            review = _strict_json(_read(args.review), "realized milestone review")
            result = observations.admit(review, body=body, provenance=provenance)
            observations.verify(result, body=body, provenance=provenance)
            benchmark.write_new(args.output, result)
            raw = benchmark.canonical_bytes(result)
        else:
            raw = _read(args.observation)
            result = observations.verify(raw, body=body, provenance=provenance)
        receipt = {"artifact_sha256": result["sha256"],
                   "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                   "source_bytes_verified": len(body),
                   "provenance_sha256": hashlib.sha256(provenance).hexdigest(),
                   "database_writes": False, "publisher_bodies_embedded": False}
        if args.command == "admit":
            receipt["output"] = str(args.output.absolute())
        else:
            receipt["verified"] = True
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
