#!/usr/bin/env python3
"""Acquire or transform the official EPA FRS national candidate snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from semiconductor_atlas.adapters.epa_frs import (  # noqa: E402
    FRS_ARCHIVE_MEMBER,
    FRS_ATTRIBUTION,
    FRS_COVERAGE,
    FRS_DATA_AS_OF_BASIS,
    FRS_DATA_AS_OF_SOURCE_URL,
    FRS_DOCUMENTATION_MEMBER,
    FRS_FILTER_VERSION,
    FRS_LICENSE,
    FRS_LICENSE_URL,
    FRS_NAICS_CODES,
    FRS_OFFICIAL_ARCHIVE_URL,
    FRS_RECORD_TYPE,
    FRS_RAW_RECORD_TYPE,
    FRS_RETRIEVAL_TIMESTAMP_BASES,
    FRS_SCOPE,
    FRS_SIC_CODES,
    candidate_jsonl_bytes,
    parse_candidate_jsonl,
    scan_frs_archive,
)


SOURCE_SNAPSHOT_FORMAT = "semiconductor-atlas-source-inputs-v1"
CANDIDATE_FILENAME = "epa-frs-semiconductor-candidates.jsonl"
USER_AGENT = "semiconductor-atlas/0.1 EPA-FRS acquisition"
RIGHTS_REVIEWED_AT = "2026-07-19"
_MAX_DOWNLOAD_BYTES = 1_000_000_000
_DOWNLOAD_CHUNK = 1024 * 1024
@dataclass(frozen=True, slots=True)
class TransportMetadata:
    etag: str | None = None
    last_modified: str | None = None


def _date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except (AttributeError, ValueError) as error:
        raise ValueError("data_as_of must use YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise ValueError("data_as_of must use YYYY-MM-DD")
    return value


def _timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise ValueError("retrieved_at must be an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("retrieved_at must include a timezone")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_new(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _copy_new(
    source: Path,
    destination: Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=False)
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as input_stream, destination.open("xb") as output_stream:
        while chunk := input_stream.read(_DOWNLOAD_CHUNK):
            output_stream.write(chunk)
            digest.update(chunk)
            size += len(chunk)
        output_stream.flush()
        os.fsync(output_stream.fileno())
    if size != expected_bytes or digest.hexdigest() != expected_sha256:
        raise ValueError("retained EPA FRS raw archive differs from the scanned source")
    _fsync_directory(destination.parent)
    _fsync_directory(destination.parent.parent)
    _fsync_directory(destination.parent.parent.parent)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _download_official(destination: Path, *, timeout: int) -> TransportMetadata:
    request = Request(
        FRS_OFFICIAL_ARCHIVE_URL,
        headers={
            "Accept": "application/zip,application/octet-stream",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    with urlopen(request, timeout=timeout) as response:
        etag = response.headers.get("ETag")
        last_modified = response.headers.get("Last-Modified")
        total = 0
        with destination.open("xb") as stream:
            while chunk := response.read(_DOWNLOAD_CHUNK):
                total += len(chunk)
                if total > _MAX_DOWNLOAD_BYTES:
                    raise ValueError("EPA FRS archive download exceeds the size limit")
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
    return TransportMetadata(etag=etag, last_modified=last_modified)


def _rights() -> dict[str, object]:
    return {
        "reviewed_at": RIGHTS_REVIEWED_AT,
        "decision": "pass_for_exact_public_archive",
        "access_level": "public",
        "license_url": FRS_LICENSE_URL,
        "no_warranty": True,
        "scope": "exact_public_national_single_archive",
    }


def _manifest(
    *,
    candidate_raw: bytes,
    scan,
    data_as_of: str,
    retrieved_at: str,
    retrieval_timestamp_basis: str,
    transport: TransportMetadata,
) -> dict[str, object]:
    candidate_sha256 = hashlib.sha256(candidate_raw).hexdigest()
    upstream_archive = {
        "url": FRS_OFFICIAL_ARCHIVE_URL,
        "sha256": scan.archive_sha256,
        "bytes": scan.archive_bytes,
        "etag": transport.etag,
        "last_modified": transport.last_modified,
    }
    upstream_csv = {
        "member": FRS_ARCHIVE_MEMBER,
        "sha256": scan.csv_sha256,
        "bytes": scan.csv_bytes,
        "crc32": scan.csv_crc32,
        "row_count": scan.row_count,
    }
    rights = _rights()
    raw_locator = f"raw/sha256/{scan.archive_sha256}.zip"
    return {
        "format": SOURCE_SNAPSHOT_FORMAT,
        "retrieved_at": retrieved_at,
        "retrieval_timestamp_basis": retrieval_timestamp_basis,
        "source_scopes": {
            FRS_SCOPE: {
                "complete": True,
                "coverage": FRS_COVERAGE,
                "filter_version": FRS_FILTER_VERSION,
                "naics_codes": list(FRS_NAICS_CODES),
                "sic_codes": list(FRS_SIC_CODES),
                "data_as_of": data_as_of,
                "data_as_of_basis": FRS_DATA_AS_OF_BASIS,
                "data_as_of_source_url": FRS_DATA_AS_OF_SOURCE_URL,
                "retrieval_timestamp_basis": retrieval_timestamp_basis,
                "candidate_count": scan.candidate_count,
                "upstream_total_rows": scan.row_count,
                "upstream_archive": upstream_archive,
                "upstream_csv": upstream_csv,
                "documentation_member": FRS_DOCUMENTATION_MEMBER,
                "raw_retention": {
                    "retained_in_snapshot": True,
                    "blob_locator": raw_locator,
                    "reason": None,
                    "upstream_sha256": scan.archive_sha256,
                    "bytes": scan.archive_bytes,
                },
                "rights": rights,
            }
        },
        "inputs": [
            {
                "path": CANDIDATE_FILENAME,
                "record_type": FRS_RECORD_TYPE,
                "url": FRS_OFFICIAL_ARCHIVE_URL,
                "bytes": len(candidate_raw),
                "sha256": candidate_sha256,
                "content_type": "application/x-ndjson",
                "artifact_kind": "deterministic_filtered_derivative",
                "archive_member": FRS_ARCHIVE_MEMBER,
                "filter_version": FRS_FILTER_VERSION,
                "data_as_of": data_as_of,
                "candidate_count": scan.candidate_count,
                "upstream_archive_sha256": scan.archive_sha256,
                "upstream_csv_sha256": scan.csv_sha256,
                "access_level": "public",
                "license": FRS_LICENSE,
                "license_url": FRS_LICENSE_URL,
                "attribution": FRS_ATTRIBUTION,
            },
            {
                "path": raw_locator,
                "record_type": FRS_RAW_RECORD_TYPE,
                "url": FRS_OFFICIAL_ARCHIVE_URL,
                "bytes": scan.archive_bytes,
                "sha256": scan.archive_sha256,
                "content_type": "application/zip",
                "artifact_kind": "upstream_official_archive",
                "data_as_of": data_as_of,
                "access_level": "public",
                "license": FRS_LICENSE,
                "license_url": FRS_LICENSE_URL,
                "attribution": FRS_ATTRIBUTION,
            },
        ],
    }


def create_snapshot(
    archive_path: str | Path,
    output_dir: str | Path,
    *,
    data_as_of: str,
    retrieved_at: str,
    retrieval_timestamp_basis: str = "operator_supplied_for_archived_bytes",
    transport: TransportMetadata | None = None,
) -> dict[str, object]:
    """Transform one complete archive into an atomically installed snapshot."""

    data_as_of = _date(data_as_of)
    retrieved_at = _timestamp(retrieved_at)
    if retrieval_timestamp_basis not in FRS_RETRIEVAL_TIMESTAMP_BASES:
        raise ValueError("invalid retrieval_timestamp_basis")
    if date.fromisoformat(data_as_of) > datetime.fromisoformat(
        retrieved_at.replace("Z", "+00:00")
    ).date():
        raise ValueError("data_as_of must not be later than retrieved_at")
    archive = Path(archive_path)
    output = Path(output_dir).absolute()
    if not output.name or output.name in {".", ".."}:
        raise ValueError(f"unsafe output directory: {output}")
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"refusing to overwrite source snapshot: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent)
    )
    try:
        scan = scan_frs_archive(archive)
        candidate_raw = candidate_jsonl_bytes(scan)
        candidate_path = stage / CANDIDATE_FILENAME
        _write_new(candidate_path, candidate_raw)
        raw_path = stage / "raw" / "sha256" / f"{scan.archive_sha256}.zip"
        _copy_new(
            archive,
            raw_path,
            expected_sha256=scan.archive_sha256,
            expected_bytes=scan.archive_bytes,
        )
        parsed = parse_candidate_jsonl(candidate_path)
        if len(parsed) != scan.candidate_count:
            raise ValueError("EPA FRS candidate derivative failed its replay count check")
        manifest = _manifest(
            candidate_raw=candidate_raw,
            scan=scan,
            data_as_of=data_as_of,
            retrieved_at=retrieved_at,
            retrieval_timestamp_basis=retrieval_timestamp_basis,
            transport=transport or TransportMetadata(),
        )
        manifest_raw = (
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        _write_new(stage / "manifest.json", manifest_raw)
        _fsync_directory(stage)
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"refusing to overwrite source snapshot: {output}")
        stage.rename(output)
        _fsync_directory(output.parent)
        return manifest
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Acquire or transform the official EPA FRS national archive into "
            "deterministic semiconductor candidate JSONL."
        )
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--data-as-of", required=True)
    parser.add_argument(
        "--archive",
        type=Path,
        help="transform this already archived national_single.zip without network access",
    )
    parser.add_argument(
        "--retrieved-at",
        help=(
            "retrieval timestamp for --archive bytes (ISO timestamp with timezone); "
            "defaults to the local transform start"
        ),
    )
    parser.add_argument(
        "--upstream-etag",
        help="HTTP ETag recorded when the --archive bytes were downloaded",
    )
    parser.add_argument(
        "--upstream-last-modified",
        help="HTTP Last-Modified recorded when the --archive bytes were downloaded",
    )
    parser.add_argument("--timeout", type=int, default=600)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.timeout <= 0:
            raise ValueError("timeout must be positive")
        data_as_of = _date(args.data_as_of)
        if args.archive is not None:
            operator_supplied_timestamp = args.retrieved_at is not None
            retrieved_at = args.retrieved_at or _now()
            manifest = create_snapshot(
                args.archive,
                args.output_dir,
                data_as_of=data_as_of,
                retrieved_at=retrieved_at,
                retrieval_timestamp_basis=(
                    "operator_supplied_for_archived_bytes"
                    if operator_supplied_timestamp
                    else "local_archive_transform_start_for_archived_bytes"
                ),
                transport=TransportMetadata(
                    etag=args.upstream_etag,
                    last_modified=args.upstream_last_modified,
                ),
            )
        else:
            if any(
                value is not None
                for value in (
                    args.retrieved_at,
                    args.upstream_etag,
                    args.upstream_last_modified,
                )
            ):
                raise ValueError(
                    "--retrieved-at and upstream transport overrides require --archive"
                )
            with tempfile.TemporaryDirectory(prefix="epa-frs-download-") as temporary:
                archive_path = Path(temporary) / "national_single.zip"
                transport = _download_official(archive_path, timeout=args.timeout)
                retrieved_at = _now()
                manifest = create_snapshot(
                    archive_path,
                    args.output_dir,
                    data_as_of=data_as_of,
                    retrieved_at=retrieved_at,
                    retrieval_timestamp_basis="upstream_download_completion",
                    transport=transport,
                )
        print(
            json.dumps(
                {
                    "candidate_count": manifest["source_scopes"][FRS_SCOPE][
                        "candidate_count"
                    ],
                    "manifest": str((Path(args.output_dir) / "manifest.json").absolute()),
                },
                sort_keys=True,
            )
        )
        return 0
    except (
        FileExistsError,
        HTTPError,
        OSError,
        RuntimeError,
        TimeoutError,
        URLError,
        ValueError,
    ) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
