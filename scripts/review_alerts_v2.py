#!/usr/bin/env python3
"""Review facility and source-project proposals together without enabling delivery."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from semiconductor_atlas import alert_review_v2 as review
from semiconductor_atlas import project_target_changes
from semiconductor_atlas.ai_critical_changes import _pretty_bytes, _strict_json
from semiconductor_atlas.curated_capture import _write


def _evidence_ref(value: str) -> dict:
    try:
        result = _strict_json(value.encode("utf-8"), "evidence reference")
        if not isinstance(result, dict):
            raise ValueError("evidence reference must be a JSON object")
        return result
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "restore", "report", "verify", "export", "import-project", "import-facility", "decide", "build-project"):
        command = commands.add_parser(name)
        command.add_argument("--database", type=Path, required=True,
                             help="Read-only core database" if name == "build-project" else "Version-2 alert review database")
        if name == "restore":
            command.add_argument("--events", type=Path, required=True,
                                 help="Validated legacy v1 or unified v2 export; target must be new")
        elif name in {"report", "verify", "export"}:
            command.add_argument("--as-of", help="UTC knowledge/admission cutoff, not physical event date")
            if name == "export":
                command.add_argument("--output", type=Path, help="New export file; otherwise print exact JSON")
        elif name == "import-project":
            command.add_argument("--packet", type=Path, required=True)
            command.add_argument("--review", type=Path, required=True,
                                 help="Packet-bound alert-review admission, not the source acquisition review")
        elif name == "import-facility":
            for argument in ("bundle", "prior", "current", "review"):
                command.add_argument(f"--{argument}", type=Path, required=True)
        elif name == "build-project":
            command.add_argument("--review", type=Path, required=True,
                                 help="Previously accepted project-target claim review")
            command.add_argument("--source-queue", type=Path, required=True)
            command.add_argument("--reference-root", type=Path, required=True)
            command.add_argument("--output", type=Path, required=True,
                                 help="New proposal packet file; no claim acceptance or schema migration")
        elif name == "decide":
            command.add_argument("--alert", required=True)
            command.add_argument("--action", choices=("acknowledge", "resolve", "retract", "reopen"), required=True)
            command.add_argument("--reviewer", required=True)
            command.add_argument("--reason", required=True)
            command.add_argument("--expected-event", required=True)
            command.add_argument("--evidence-ref", type=_evidence_ref, action="append", default=[],
                                 help="Exact retained evidence JSON reference; repeat for multiple references")
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = review.initialize_queue(args.database)
        elif args.command == "restore":
            result = review.restore_queue(args.database, args.events)
        elif args.command == "report":
            result = review.queue_report(args.database, as_of=args.as_of)
        elif args.command == "verify":
            result = review.verify_queue(args.database, as_of=args.as_of)
        elif args.command == "export":
            result = review.export_queue(args.database, as_of=args.as_of)
            if args.output is not None:
                _write(args.output, _pretty_bytes(result))
                result = {"output": str(args.output), "event_count": len(result["events"])}
        elif args.command == "import-project":
            result = review.import_project_packet(args.database, args.packet, args.review)
        elif args.command == "import-facility":
            result = review.import_bundle(args.database, args.bundle, args.prior, args.current, args.review)
        elif args.command == "build-project":
            connection = sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                connection.row_factory = sqlite3.Row
                result = project_target_changes.build_packet(
                    connection, args.review, reference_root=args.reference_root,
                    source_queue=args.source_queue,
                )
            finally:
                connection.close()
            raw = _pretty_bytes(result)
            _write(args.output, raw)
            result = {"output": str(args.output), "packet_id": result["packet_id"],
                      "packet_sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        else:
            result = review.record_decision(
                args.database, args.alert, action=args.action, reviewer=args.reviewer,
                reason=args.reason, expected_event_id=args.expected_event,
                evidence_refs=args.evidence_ref,
            )
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
