#!/usr/bin/env python3
"""Create or verify an immutable local EEA Industrial Reporting v16 snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from semiconductor_atlas.eea_industrial_snapshot import (  # noqa: E402
    create_eea_industrial_snapshot,
    verify_eea_industrial_snapshot,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Package already acquired exact EEA v16 bytes, or verify an existing "
            "snapshot entirely offline. This command never accepts or retains access "
            "credentials."
        )
    )
    parser.add_argument("--verify-only", type=Path)
    parser.add_argument("--accdb", type=Path)
    parser.add_argument("--official-evidence-dir", type=Path)
    parser.add_argument("--primary-extraction-dir", type=Path)
    parser.add_argument("--independent-extraction-metadata", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--download-started-at")
    parser.add_argument("--retrieved-at")
    parser.add_argument("--metadata-retrieved-at")
    parser.add_argument("--primary-extraction-started-at")
    parser.add_argument("--primary-extraction-completed-at")
    parser.add_argument("--independent-extraction-started-at")
    parser.add_argument("--independent-extraction-completed-at")
    parser.add_argument("--candidate-generated-at")
    parser.add_argument("--accepted-at")
    return parser


def _summary(snapshot) -> dict[str, object]:
    return {
        "accepted_at": snapshot.accepted_at,
        "candidate_count": snapshot.candidate_count,
        "candidate_sha256": snapshot.candidate_sha256,
        "candidate_size": snapshot.candidate_size,
        "manifest": str((snapshot.root / "manifest.json").absolute()),
        "manifest_sha256": snapshot.manifest_sha256,
        "raw_sha256": snapshot.raw_sha256,
        "raw_size": snapshot.raw_size,
        "retrieved_at": snapshot.retrieved_at,
    }


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.verify_only is not None:
            creation_values = [
                args.accdb,
                args.official_evidence_dir,
                args.primary_extraction_dir,
                args.independent_extraction_metadata,
                args.output_dir,
                args.download_started_at,
                args.retrieved_at,
                args.metadata_retrieved_at,
                args.primary_extraction_started_at,
                args.primary_extraction_completed_at,
                args.independent_extraction_started_at,
                args.independent_extraction_completed_at,
                args.candidate_generated_at,
                args.accepted_at,
            ]
            if any(value is not None for value in creation_values):
                raise ValueError(
                    "--verify-only cannot be combined with creation arguments"
                )
            snapshot = verify_eea_industrial_snapshot(args.verify_only)
        else:
            required = {
                "--accdb": args.accdb,
                "--official-evidence-dir": args.official_evidence_dir,
                "--primary-extraction-dir": args.primary_extraction_dir,
                "--independent-extraction-metadata": args.independent_extraction_metadata,
                "--output-dir": args.output_dir,
                "--download-started-at": args.download_started_at,
                "--retrieved-at": args.retrieved_at,
                "--metadata-retrieved-at": args.metadata_retrieved_at,
                "--primary-extraction-started-at": args.primary_extraction_started_at,
                "--primary-extraction-completed-at": args.primary_extraction_completed_at,
                "--independent-extraction-started-at": args.independent_extraction_started_at,
                "--independent-extraction-completed-at": args.independent_extraction_completed_at,
                "--candidate-generated-at": args.candidate_generated_at,
                "--accepted-at": args.accepted_at,
            }
            missing = [flag for flag, value in required.items() if value is None]
            if missing:
                raise ValueError(f"creation requires: {', '.join(missing)}")
            snapshot = create_eea_industrial_snapshot(
                accdb_path=args.accdb,
                official_evidence_directory=args.official_evidence_dir,
                primary_extraction_directory=args.primary_extraction_dir,
                independent_extraction_metadata_path=args.independent_extraction_metadata,
                output_directory=args.output_dir,
                download_started_at=args.download_started_at,
                retrieved_at=args.retrieved_at,
                metadata_retrieved_at=args.metadata_retrieved_at,
                primary_extraction_started_at=args.primary_extraction_started_at,
                primary_extraction_completed_at=args.primary_extraction_completed_at,
                independent_extraction_started_at=args.independent_extraction_started_at,
                independent_extraction_completed_at=args.independent_extraction_completed_at,
                candidate_generated_at=args.candidate_generated_at,
                accepted_at=args.accepted_at,
            )
        print(json.dumps(_summary(snapshot), ensure_ascii=False, sort_keys=True))
        return 0
    except (FileExistsError, OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
