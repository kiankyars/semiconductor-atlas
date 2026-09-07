#!/usr/bin/env python3
"""Record or verify a complete reviewed operating-fab table cohort from local inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.milestone_benchmark import _read
from semiconductor_atlas import realized_fab_cohort as cohort
from semiconductor_atlas.ai_critical_changes import _strict_json


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("admit", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--source-body", type=Path, required=True,
                             help="Existing retained publisher body; never downloaded or embedded")
        command.add_argument("--provenance", type=Path, required=True,
                             help="Exact retained source metadata")
        if name == "admit":
            command.add_argument("--review", type=Path, required=True)
            command.add_argument("--anchor", type=Path, required=True,
                                 help="Exact existing realized-milestone observation artifact")
            command.add_argument("--output", type=Path, required=True,
                                 help="New-only local cohort artifact")
        else:
            command.add_argument("--cohort", type=Path, required=True,
                                 help="Cohort artifact containing its exact prior observation anchor")
    args = parser.parse_args(argv)
    try:
        body, provenance = _read(args.source_body), _read(args.provenance)
        if args.command == "admit":
            review = _strict_json(_read(args.review), "complete realized-fab cohort review")
            result = cohort.admit(review, anchor=_read(args.anchor), body=body, provenance=provenance)
            verified = cohort.verify(result, body=body, provenance=provenance)
            cohort.write_new(args.output, result)
            raw = _read(args.output)
            if raw != cohort.canonical_bytes(result):
                raise ValueError("published cohort bytes differ from the verified artifact")
        else:
            raw = _read(args.cohort)
            verified = cohort.verify(raw, body=body, provenance=provenance)
        receipt = {"artifact_sha256": verified["sha256"], "sha256": hashlib.sha256(raw).hexdigest(),
                   "bytes": len(raw), "table_rows": verified["table_rows"], "counts": verified["counts"],
                   "accepted_source_reports": verified["accepted_source_reports"],
                   "source_bytes_verified": len(body), "provenance_sha256": hashlib.sha256(provenance).hexdigest(),
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
