#!/usr/bin/env python3
"""Freeze and evaluate source-bound timing scenarios, not calibrated capacity forecasts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from semiconductor_atlas import milestone_benchmark as benchmark
from semiconductor_atlas.ai_critical_changes import _open_real_directory_fd, _strict_json


SOURCE_MANIFEST_FORMAT = "semiconductor-atlas-milestone-local-sources-v1"


def _identity(status) -> tuple:
    return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns, status.st_ctime_ns)


def _read(path: Path, *, limit=benchmark.MAX_BYTES) -> bytes:
    path = path.absolute()
    _, parent = _open_real_directory_fd(path.parent, "benchmark input parent", create=False)
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
                raise ValueError("benchmark input must be a bounded, nonempty regular file")
            chunks, length = [], 0
            while chunk := os.read(descriptor, min(1024 * 1024, limit + 1 - length)):
                chunks.append(chunk)
                length += len(chunk)
                if length > limit:
                    raise ValueError("benchmark input exceeded its byte limit while reading")
            if _identity(os.fstat(descriptor)) != _identity(before) or length != before.st_size:
                raise ValueError("benchmark input changed while reading")
            named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if _identity(named) != _identity(before):
                raise ValueError("benchmark input pathname changed while reading")
            _, current = _open_real_directory_fd(path.parent, "benchmark input parent", create=False)
            try:
                if (os.fstat(current).st_dev, os.fstat(current).st_ino) != (os.fstat(parent).st_dev, os.fstat(parent).st_ino):
                    raise ValueError("benchmark input parent changed while reading")
            finally:
                os.close(current)
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def _json(path: Path):
    return _strict_json(_read(path), "benchmark JSON input")


def _bodies(path: Path) -> dict[str, bytes]:
    manifest = _json(path)
    if (not isinstance(manifest, dict) or set(manifest) != {"format", "documents"}
            or manifest["format"] != SOURCE_MANIFEST_FORMAT
            or not isinstance(manifest["documents"], list)
            or len(manifest["documents"]) > benchmark.MAX_CASES):
        raise ValueError("invalid local source manifest")
    bodies, length = {}, 0
    for row in manifest["documents"]:
        if not isinstance(row, dict) or set(row) != {"sha256", "path"}:
            raise ValueError("local source reference requires exactly sha256 and path")
        digest = benchmark._digest(row["sha256"])
        if digest in bodies or not isinstance(row["path"], str) or not Path(row["path"]).is_absolute():
            raise ValueError("local sources require unique hashes and explicit absolute paths")
        body = _read(Path(row["path"]))
        if hashlib.sha256(body).hexdigest() != digest:
            raise ValueError("local source body differs from its declared hash")
        length += len(body)
        if length > benchmark.MAX_BYTES:
            raise ValueError("local source bodies exceed the combined byte limit")
        bodies[digest] = body
    return bodies


@contextmanager
def _database(path: Path):
    path = path.absolute()
    _, parent = _open_real_directory_fd(path.parent, "benchmark database parent", create=False)
    try:
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("benchmark requires an existing regular non-symlink database")
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            after = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise ValueError("benchmark database pathname changed")
            _, current = _open_real_directory_fd(path.parent, "benchmark database parent", create=False)
            try:
                if (os.fstat(current).st_dev, os.fstat(current).st_ino) != (os.fstat(parent).st_dev, os.fstat(parent).st_ino):
                    raise ValueError("benchmark database parent changed")
            finally:
                os.close(current)
        finally:
            connection.close()
    finally:
        os.close(parent)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("freeze-study", "freeze-vintage", "verify-vintage", "review-outcomes", "score"):
        command = commands.add_parser(name)
        command.add_argument("--database", type=Path, required=True,
                             help="Existing read-only schema-5 database; never migrated")
        if name == "freeze-study":
            command.add_argument("--specification", type=Path, required=True)
        elif name == "freeze-vintage":
            for field in ("study", "predictions", "model-artifact", "configuration"):
                command.add_argument("--" + field, type=Path, required=True)
            command.add_argument("--evidence-cutoff-at", required=True)
            command.add_argument("--horizon-end", required=True)
        else:
            command.add_argument("--vintage", type=Path, required=True)
        if name in {"review-outcomes", "score"}:
            command.add_argument("--source-manifest", type=Path, required=True,
                                 help="Hash-to-local-file references; raw bodies never become outputs")
            command.add_argument("--review" if name == "review-outcomes" else "--outcomes",
                                 type=Path, required=True)
        if name != "verify-vintage":
            command.add_argument("--output", type=Path, required=True, help="New-only artifact path")
    args = parser.parse_args(argv)
    try:
        with _database(args.database) as connection:
            if args.command == "freeze-study":
                result = benchmark.freeze_study(connection, _json(args.specification))
            elif args.command == "freeze-vintage":
                result = benchmark.freeze_vintage(connection, _read(args.study),
                    evidence_cutoff_at=args.evidence_cutoff_at, horizon_end=args.horizon_end,
                    predictions=_json(args.predictions), model_artifact=_read(args.model_artifact, limit=1_000_000),
                    configuration=_json(args.configuration))
            elif args.command == "verify-vintage":
                result = benchmark.verify_vintage(connection, _read(args.vintage))
            elif args.command == "review-outcomes":
                vintage = benchmark.verify_vintage(connection, _read(args.vintage))
                review = _json(args.review)
                if not isinstance(review, dict) or set(review) != {"reviewed_by", "prior_exposure", "outcomes"}:
                    raise ValueError("outcome review requires reviewer, prior exposure and complete outcomes")
                result = benchmark.review_outcomes(vintage, **review, bodies=_bodies(args.source_manifest))
            else:
                result = benchmark.score(connection, _read(args.vintage), _read(args.outcomes),
                                         bodies=_bodies(args.source_manifest))
        if args.command == "verify-vintage":
            receipt = {"verified_vintage_sha256": result["sha256"],
                       "roster_cases": len(result["cases"]), "database_writes": False}
        else:
            benchmark.write_new(args.output, result)
            raw = benchmark.canonical_bytes(result)
            receipt = {"output": str(args.output.absolute()), "bytes": len(raw),
                       "sha256": hashlib.sha256(raw).hexdigest(), "artifact_sha256": result["sha256"],
                       "database_writes": False}
    except (OSError, ValueError, sqlite3.Error) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
