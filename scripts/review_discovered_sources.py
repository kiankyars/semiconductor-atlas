#!/usr/bin/env python3
"""Review discovered URLs without acquiring documents or accepting claims."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from semiconductor_atlas import discovery_review as review
from semiconductor_atlas.ai_critical_changes import _pretty_bytes
from semiconductor_atlas.curated_capture import _write


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "import", "report", "decide", "export", "restore", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--database", type=Path, required=True)
        if name == "import":
            command.add_argument("--capture", type=Path, required=True)
        elif name in {"report", "verify", "export"}:
            command.add_argument("--as-of")
            if name == "export":
                command.add_argument("--output", type=Path)
        elif name == "restore":
            command.add_argument("--events", type=Path, required=True)
        elif name == "decide":
            command.add_argument("--candidate", required=True)
            command.add_argument("--action", choices=sorted(review.ACTIONS), required=True)
            command.add_argument("--reviewer", required=True)
            command.add_argument("--reason", required=True)
            command.add_argument("--expected-event", required=True)
            command.add_argument("--evidence-ref")
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = review.initialize_queue(args.database)
        elif args.command == "import":
            result = review.import_capture(args.database, args.capture)
        elif args.command in {"report", "verify", "export"}:
            function = {"report": review.queue_report, "verify": review.verify_queue, "export": review.export_events}[args.command]
            result = function(args.database, as_of=args.as_of)
            if args.command == "export" and args.output is not None:
                _write(args.output, _pretty_bytes(result))
                result = {"output": str(args.output), "event_count": len(result["events"])}
        elif args.command == "restore":
            result = review.restore_queue(args.database, args.events)
        else:
            result = review.record_decision(args.database, args.candidate, action=args.action, reviewer=args.reviewer,
                reason=args.reason, expected_event_id=args.expected_event, evidence_ref=args.evidence_ref)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
