#!/usr/bin/env python3
"""Acquire or verify a bounded Taiwan MOENV EMS_S_01 snapshot."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from semiconductor_atlas.moenv_snapshot import (  # noqa: E402
    MOENV_FULL_PACKAGE_URL,
    MOENV_MAX_ARCHIVE_BYTES,
    MOENV_PACKAGE_PID,
    MOENV_RESOURCE_RID,
    VerifiedMOENVSnapshot,
    create_moenv_snapshot,
    verify_moenv_snapshot,
)


USER_AGENT = "semiconductor-atlas/0.1 (MOENV EMS_S_01 snapshot acquisition)"
DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_TIMEOUT_SECONDS = 300.0
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
DATASET_UPDATED_AT_BASIS = "official_dataset_page_displayed_asia_taipei"
RETRIEVAL_TIMESTAMP_BASIS = "upstream_download_completion"
ACQUISITION_REQUEST_BODY = {
    "rid": [MOENV_RESOURCE_RID],
    "download_type": "json",
    "pid": MOENV_PACKAGE_PID,
}
ACQUISITION_REQUEST_BYTES = json.dumps(
    ACQUISITION_REQUEST_BODY,
    ensure_ascii=False,
    separators=(",", ":"),
).encode("utf-8")
_MAX_RETAINED_HEADER_BYTES = 1024


def _canonical_utc_timestamp(value: str, context: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{context} must be a canonical UTC timestamp")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise ValueError(f"{context} must be a canonical UTC timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{context} must be a canonical UTC timestamp")
    normalized = parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if normalized != value:
        raise ValueError(f"{context} must be a canonical UTC timestamp")
    return value


def _bounded_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timeout must be a number") from error
    if not math.isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise argparse.ArgumentTypeError(
            f"timeout must be greater than zero and at most {MAX_TIMEOUT_SECONDS:g}"
        )
    return timeout


def _completion_timestamp(wall_clock: Callable[[], datetime]) -> str:
    completed_at = wall_clock()
    if not isinstance(completed_at, datetime):
        raise TypeError("wall clock must return a datetime")
    if completed_at.tzinfo is None or completed_at.utcoffset() is None:
        raise ValueError("wall clock must return a timezone-aware datetime")
    return completed_at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _safe_response_header(headers: object, name: str) -> str | None:
    getter = getattr(headers, "get", None)
    if not callable(getter):
        raise TypeError("HTTP response headers must provide get()")
    value = getter(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"HTTP {name} header must be text")
    if (
        not value
        or value != value.strip()
        or len(value.encode("utf-8")) > _MAX_RETAINED_HEADER_BYTES
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ValueError(f"HTTP {name} header is not safe to retain")
    return value


def _declared_content_length(headers: object) -> int | None:
    value = _safe_response_header(headers, "Content-Length")
    if value is None:
        return None
    if not value.isascii() or not value.isdecimal():
        raise ValueError("HTTP Content-Length header must be an ASCII integer")
    return int(value)


def _write_response_bounded(
    response: object,
    destination: Path,
    *,
    max_bytes: int,
) -> int:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("download byte limit must be a positive integer")
    headers = getattr(response, "headers", None)
    declared_size = _declared_content_length(headers)
    if declared_size is not None and declared_size > max_bytes:
        raise ValueError("MOENV full-package download exceeds the byte limit")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags, 0o600)
    total = 0
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            while True:
                remaining_with_sentinel = max_bytes - total + 1
                chunk = response.read(min(DOWNLOAD_CHUNK_BYTES, remaining_with_sentinel))
                if not isinstance(chunk, bytes):
                    raise TypeError("HTTP response body reads must return bytes")
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("MOENV full-package download exceeds the byte limit")
                stream.write(chunk)
            if total == 0:
                raise ValueError("MOENV full-package download is empty")
            if declared_size is not None and total != declared_size:
                raise ValueError("MOENV full-package download disagrees with Content-Length")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return total


def _download_official_archive(
    destination: Path,
    *,
    timeout_seconds: float,
    opener: Callable[..., object],
    wall_clock: Callable[[], datetime],
    max_bytes: int,
) -> tuple[str, str | None, str | None]:
    request = Request(
        MOENV_FULL_PACKAGE_URL,
        data=ACQUISITION_REQUEST_BYTES,
        headers={
            "Accept": "application/zip,application/octet-stream",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with opener(request, timeout=timeout_seconds) as response:
        status = getattr(response, "status", None)
        if status is not None and (
            isinstance(status, bool)
            or not isinstance(status, int)
            or not 200 <= status < 300
        ):
            raise RuntimeError(f"MOENV full-package request returned HTTP {status}")
        final_url = response.geturl()
        if final_url != MOENV_FULL_PACKAGE_URL:
            raise ValueError("MOENV full-package request was redirected")
        etag = _safe_response_header(response.headers, "ETag")
        last_modified = _safe_response_header(response.headers, "Last-Modified")
        _write_response_bounded(response, destination, max_bytes=max_bytes)
    return _completion_timestamp(wall_clock), etag, last_modified


def acquire_moenv_snapshot(
    output_dir: str | Path,
    *,
    dataset_updated_at: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    opener: Callable[..., object] = urlopen,
    wall_clock: Callable[[], datetime] = _utc_now,
    temporary_parent: str | Path | None = None,
    max_download_bytes: int = MOENV_MAX_ARCHIVE_BYTES,
) -> VerifiedMOENVSnapshot:
    """Download the fixed full package and atomically install a verified snapshot."""

    dataset_updated_at = _canonical_utc_timestamp(
        dataset_updated_at, "--dataset-updated-at"
    )
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS
    ):
        raise ValueError(
            f"timeout must be greater than zero and at most {MAX_TIMEOUT_SECONDS:g}"
        )
    output = Path(output_dir).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"refusing to overwrite MOENV snapshot: {output}")

    temporary_parent_path = (
        None if temporary_parent is None else str(Path(temporary_parent).absolute())
    )
    with tempfile.TemporaryDirectory(
        prefix="semiconductor-atlas-moenv-",
        dir=temporary_parent_path,
    ) as temporary:
        temporary_root = Path(temporary)
        os.chmod(temporary_root, 0o700)
        archive_path = temporary_root / "ems_s_01-full-package.zip"
        retrieved_at, etag, last_modified = _download_official_archive(
            archive_path,
            timeout_seconds=float(timeout_seconds),
            opener=opener,
            wall_clock=wall_clock,
            max_bytes=max_download_bytes,
        )
        return create_moenv_snapshot(
            archive_path,
            output,
            retrieved_at=retrieved_at,
            retrieval_timestamp_basis=RETRIEVAL_TIMESTAMP_BASIS,
            dataset_updated_at=dataset_updated_at,
            dataset_updated_at_basis=DATASET_UPDATED_AT_BASIS,
            download_url=MOENV_FULL_PACKAGE_URL,
            acquisition_request_body={
                "rid": [MOENV_RESOURCE_RID],
                "download_type": "json",
                "pid": MOENV_PACKAGE_PID,
            },
            upstream_etag=etag,
            upstream_last_modified=last_modified,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Acquire the official Taiwan MOENV EMS_S_01 full package into a new "
            "immutable snapshot, or replay-verify an existing snapshot offline."
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--output-dir",
        type=Path,
        metavar="NEW_SNAPSHOT",
        help="new snapshot directory for live acquisition; never overwritten",
    )
    mode.add_argument(
        "--verify-only",
        type=Path,
        metavar="SNAPSHOT",
        help="verify and replay an existing snapshot without network access",
    )
    parser.add_argument(
        "--dataset-updated-at",
        metavar="UTC_TIMESTAMP",
        help=(
            "required for live acquisition; canonical UTC timestamp read from the "
            "official dataset page displayed in Asia/Taipei"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=_bounded_timeout,
        metavar="SECONDS",
        help=(
            f"live HTTP timeout, greater than zero and at most "
            f"{MAX_TIMEOUT_SECONDS:g} (default: {DEFAULT_TIMEOUT_SECONDS:g})"
        ),
    )
    return parser


def _summary(snapshot: VerifiedMOENVSnapshot) -> dict[str, object]:
    return {
        "candidate_count": snapshot.candidate_count,
        "dataset_updated_at": snapshot.dataset_updated_at,
        "facility_count": snapshot.facility_count,
        "manifest": str((snapshot.root / "manifest.json").absolute()),
        "manifest_sha256": snapshot.manifest_sha256,
        "raw_sha256": snapshot.raw_sha256,
        "retrieved_at": snapshot.retrieved_at,
        "snapshot": str(snapshot.root.absolute()),
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    opener: Callable[..., object] = urlopen,
    wall_clock: Callable[[], datetime] = _utc_now,
    temporary_parent: str | Path | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.verify_only is not None:
            if args.dataset_updated_at is not None or args.timeout is not None:
                raise ValueError(
                    "--dataset-updated-at and --timeout apply only to live acquisition"
                )
            snapshot = verify_moenv_snapshot(args.verify_only)
        else:
            if args.dataset_updated_at is None:
                raise ValueError("--dataset-updated-at is required with --output-dir")
            snapshot = acquire_moenv_snapshot(
                args.output_dir,
                dataset_updated_at=args.dataset_updated_at,
                timeout_seconds=(
                    DEFAULT_TIMEOUT_SECONDS if args.timeout is None else args.timeout
                ),
                opener=opener,
                wall_clock=wall_clock,
                temporary_parent=temporary_parent,
            )
        print(json.dumps(_summary(snapshot), sort_keys=True))
        return 0
    except (
        FileExistsError,
        HTTPError,
        OSError,
        RuntimeError,
        TimeoutError,
        TypeError,
        URLError,
        ValueError,
    ) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
