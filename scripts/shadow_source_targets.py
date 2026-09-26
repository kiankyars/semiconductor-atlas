#!/usr/bin/env python3
"""Register future shadow studies, record predictions, and seal a full observed population."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from semiconductor_atlas import prospective_target_review as shadow
from semiconductor_atlas import source_vintage_review as vintage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("register", "verify", "record", "seal", "verify-seal", "advance"):
        command = commands.add_parser(name)
        command.add_argument("--reference-root", type=Path, required=True)
        command.add_argument("--study" if name == "register" else "--registration", type=Path, required=True)
        if name == "register":
            command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "advance":
            registration, _ = shadow.validate_registration(args.registration, reference_root=args.reference_root)
            study = shadow._study(shadow.population.blobs._unblob(registration["study"]))
            directory = shadow.population._path(args.reference_root.resolve(), study["prediction_root"])
            if (directory / "seal.json").exists():
                args.command = "verify-seal"
            elif shadow._instant(shadow._now()) >= shadow._instant(study["end"]) + shadow.timedelta(seconds=study["prediction_deadline_seconds"]):
                args.command = "seal"
            else:
                args.command = "record"
        if args.command == "register":
            result = shadow.accept_registration(args.study, args.output, reference_root=args.reference_root)
        elif args.command == "record":
            result = shadow.record(args.registration, reference_root=args.reference_root)
        elif args.command == "verify-seal":
            result = shadow.validate_seal(args.registration, reference_root=args.reference_root)
        else:
            registration, raw = shadow.validate_registration(args.registration, reference_root=args.reference_root)
            if args.command == "verify":
                result = {"registration_sha256": shadow._hash(raw), "documents": len(registration["inputs"]["documents"]),
                          "network_requests": 0, "registered_inputs_replayed": True}
            else:
                study = shadow._study(shadow.population.blobs._unblob(registration["study"]))
                directory = shadow.population._path(args.reference_root.resolve(), study["prediction_root"])
                queue = shadow.population._path(args.reference_root.resolve(), registration["inputs"]["baseline_study"]["queue_path"])
                with shadow.poll._lock(queue), shadow.poll._lock(directory):
                    result = shadow.seal(args.registration, reference_root=args.reference_root)
                    if result["registration_sha256"] != shadow._hash(raw):
                        raise ValueError("registration changed between output protection and sealing")
                    result = {**vintage.write_new(directory / "seal.json", result, reference_root=args.reference_root,
                        protected_directories=registration["inputs"]["protected_directories"]), "counts": result["counts"]}
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
