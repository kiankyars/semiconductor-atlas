#!/usr/bin/env python3
"""Pin evaluation policy, export source-only review packets, and score separate labels."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from semiconductor_atlas import prospective_target_evaluation as evaluation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("register-policy", "verify-policy", "packet", "verify-packet", "score", "prepare-review"):
        command = commands.add_parser(name)
        command.add_argument("--registration", type=Path, required=True)
        command.add_argument("--reference-root", type=Path, required=True)
        if name != "register-policy":
            command.add_argument("--policy", type=Path, required=True)
        if name in {"verify-packet", "score"}:
            command.add_argument("--packet", type=Path, required=True)
        if name == "score":
            command.add_argument("--labels", type=Path, required=True)
        if name in {"register-policy", "packet", "score", "prepare-review"}:
            command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "register-policy":
            result = evaluation.register_policy(args.registration, args.output, reference_root=args.reference_root)
        elif args.command == "packet":
            result = evaluation.accept_packet(args.registration, args.policy, args.output, reference_root=args.reference_root)
        elif args.command == "prepare-review":
            result = evaluation.prepare_review(args.registration, args.policy, args.output, reference_root=args.reference_root)
        else:
            context = evaluation.validate_policy(args.registration, args.policy, reference_root=args.reference_root)
            if args.command == "verify-policy":
                result = {"policy_sha256": context["policy_sha256"], "registered_code_and_inputs_replayed": True, "network_requests": 0}
            elif args.command == "verify-packet":
                packet = evaluation.validate_packet(args.registration, args.policy, args.packet, reference_root=args.reference_root)
                result = {"packet_sha256": evaluation._hash(packet["packet_raw"]), "cases": len(packet["packet"]["cases"]),
                    "source_only_population_replayed": True, "reviewer_blinding_verified": False, "network_requests": 0}
            else:
                result = evaluation.evaluate(args.registration, args.policy, args.packet, args.labels, reference_root=args.reference_root)
                if result["policy_sha256"] != context["policy_sha256"]:
                    raise ValueError("evaluation policy changed before output protection")
                result = evaluation.vintage.write_new(args.output, result, reference_root=args.reference_root,
                    protected_directories=evaluation._protection(context["registration"], context["study"]))
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
