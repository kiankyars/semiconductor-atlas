#!/usr/bin/env python3
"""Execute/replay a local source-guidance interval comparator; never acquire sources."""

from __future__ import annotations

import argparse
import base64
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from semiconductor_atlas import guidance_baseline as baseline
from semiconductor_atlas import milestone_benchmark as benchmark
from scripts.milestone_benchmark import _read, _bodies


def _source_bodies(review_raw):
    review = baseline._json(review_raw)
    bodies = {}
    for row in review["cases"]:
        raw = _read(Path(row["local_source_path"]))
        digest = benchmark._digest(row["document_sha256"])
        if benchmark._hash(raw) != digest:
            raise ValueError("reviewed local source differs from its pinned hash")
        if digest in bodies and bodies[digest] != raw:
            raise ValueError("duplicate source hash has differing bytes")
        bodies[digest] = raw
    return bodies


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("freeze", "verify", "review-outcomes", "score"):
        command = commands.add_parser(name)
        command.add_argument("--database", type=Path, required=True)
        if name == "freeze":
            command.add_argument("--screen", type=Path, required=True)
            command.add_argument("--input-review", type=Path, required=True)
            command.add_argument("--evidence-cutoff-at", required=True)
        else:
            command.add_argument("--vintage", type=Path, required=True)
            command.add_argument("--trusted-vintage-sha256", required=True)
        if name in {"review-outcomes", "score"}:
            command.add_argument("--source-manifest", type=Path, required=True,
                                 help="Exact local outcome evidence, not input source acquisition")
            command.add_argument("--review" if name == "review-outcomes" else "--outcomes", type=Path, required=True)
            if name == "score":
                command.add_argument("--trusted-outcomes-sha256", required=True)
        if name != "verify":
            command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        database_bytes = _read(args.database, limit=1_000_000_000)
        if args.command == "freeze":
            screen_raw, review_raw = _read(args.screen), _read(args.input_review)
            bodies = _source_bodies(review_raw)
            result = baseline.freeze(database_bytes, screen_raw, review_raw, bodies,
                                     evidence_cutoff_at=args.evidence_cutoff_at)
        else:
            vintage_raw = _read(args.vintage)
            vintage = benchmark._load(vintage_raw, baseline.VINTAGE_FORMAT)
            review_raw = base64.b64decode(vintage["review_base64"], validate=True)
            bodies = _source_bodies(review_raw)
            result = baseline.verify(database_bytes, vintage_raw, bodies,
                                     trusted_sha256=args.trusted_vintage_sha256)
            if args.command == "review-outcomes":
                outcome_input_raw = _read(args.review)
                outcome_bodies = _bodies(args.source_manifest)
                result = baseline.review_outcomes(database_bytes, vintage_raw, bodies,
                    trusted_vintage_sha256=args.trusted_vintage_sha256,
                    review=baseline._json(outcome_input_raw), bodies=outcome_bodies)
            elif args.command == "score":
                outcome_input_raw = _read(args.outcomes)
                outcome_bodies = _bodies(args.source_manifest)
                result = baseline.score(database_bytes, vintage_raw, bodies, outcome_input_raw,
                    trusted_vintage_sha256=args.trusted_vintage_sha256,
                    trusted_sha256=args.trusted_outcomes_sha256, bodies=outcome_bodies)
        if _read(args.database, limit=1_000_000_000) != database_bytes or _source_bodies(review_raw) != bodies:
            raise ValueError("input image or source files changed during execution")
        if args.command == "freeze" and (_read(args.screen) != screen_raw or _read(args.input_review) != review_raw):
            raise ValueError("screen/input review changed during execution")
        if args.command != "freeze" and _read(args.vintage) != vintage_raw:
            raise ValueError("frozen artifact changed during execution")
        if args.command in {"review-outcomes", "score"}:
            if (_read(args.review if args.command == "review-outcomes" else args.outcomes) != outcome_input_raw
                    or _bodies(args.source_manifest) != outcome_bodies):
                raise ValueError("outcome inputs changed during execution")
        raw = benchmark.canonical_bytes(result)
        receipt = {"format": result["format"], "bytes": len(raw), "sha256": benchmark._hash(raw),
                   "artifact_sha256": result["sha256"], "database_writes": False,
                   "source_acquisition": False}
        if args.command == "verify":
            receipt["denominators"] = result["denominators"]
            receipt["reexecuted_exact_bytes"] = True
        else:
            benchmark.write_new(args.output, result)
            receipt["output"] = str(args.output.absolute())
    except (OSError, ValueError, sqlite3.Error, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
