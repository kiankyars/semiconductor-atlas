#!/usr/bin/env python3
"""Freeze and score offline alert-history diagnostics without changing any queue."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from semiconductor_atlas.alert_evaluation import evaluate, freeze_predictions, validate_frozen
from semiconductor_atlas.ai_critical_changes import _pretty_bytes
from semiconductor_atlas.curated_capture import _write


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--history", type=Path, required=True)
    freeze.add_argument("--baseline", type=Path, required=True)
    freeze.add_argument("--study-id", required=True)
    freeze.add_argument("--start", required=True)
    freeze.add_argument("--end", required=True)
    freeze.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--frozen", type=Path, required=True)
    score = commands.add_parser("score")
    score.add_argument("--frozen", type=Path, required=True)
    score.add_argument("--labels", type=Path, required=True)
    score.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "freeze":
            result = freeze_predictions(args.history, args.baseline, args.output,
                                        study_id=args.study_id, start=args.start, end=args.end)
        elif args.command == "verify":
            artifact, _ = validate_frozen(args.frozen)
            result = {"study_id": artifact["study_id"], "prediction_count": len(artifact["predictions"]),
                      "cohort_count": len(artifact["cohort"]), "verified": True}
        else:
            result = evaluate(args.frozen, args.labels)
            if args.output is not None:
                _write(args.output, _pretty_bytes(result))
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
