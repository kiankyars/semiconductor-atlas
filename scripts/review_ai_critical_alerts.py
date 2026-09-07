#!/usr/bin/env python3
"""Review retained AI-critical change proposals without enabling alert delivery."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from semiconductor_atlas import ai_critical_alert_review as review
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
    for name in ("init", "import", "report", "decide", "export", "restore", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--database", type=Path, required=True)
        if name == "import":
            command.add_argument("--bundle", type=Path, required=True)
            command.add_argument("--prior", type=Path, required=True)
            command.add_argument("--current", type=Path, required=True)
            command.add_argument("--review", type=Path, required=True,
                                 help="Manifest-bound admission review; not permission to deliver")
        elif name == "report":
            command.add_argument("--as-of", help="UTC admission/decision cutoff, not event date")
        elif name == "decide":
            command.add_argument("--alert", required=True)
            command.add_argument("--action", choices=("acknowledge", "resolve", "retract", "reopen"),
                                 required=True)
            command.add_argument("--reviewer", required=True)
            command.add_argument("--reason", required=True)
            command.add_argument("--expected-event", required=True)
            command.add_argument("--evidence-ref", type=_evidence_ref, action="append", default=[],
                                 help="JSON reference to retained evidence; repeat for multiple fragments")
        elif name == "export":
            command.add_argument("--output", type=Path,
                                 help="New portable export file; otherwise print exact JSON")
        elif name == "restore":
            command.add_argument("--events", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = review.initialize_queue(args.database)
        elif args.command == "import":
            result = review.import_bundle(args.database, args.bundle, args.prior, args.current, args.review)
        elif args.command == "report":
            result = review.queue_report(args.database, as_of=args.as_of)
        elif args.command == "decide":
            result = review.record_decision(
                args.database, args.alert, action=args.action, reviewer=args.reviewer,
                reason=args.reason, expected_event_id=args.expected_event, evidence_refs=args.evidence_ref,
            )
        elif args.command == "export":
            result = review.export_queue(args.database)
            if args.output is not None:
                _write(args.output, _pretty_bytes(result))
                result = {"output": str(args.output), "event_count": len(result["events"])}
        elif args.command == "restore":
            result = review.restore_queue(args.database, args.events)
        else:
            result = review.verify_queue(args.database)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
