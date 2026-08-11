#!/usr/bin/env python3
"""Acquire or replay-verify Taiwan's national registered-factory snapshot."""

from __future__ import annotations

import argparse
import json
import math
import os
import ssl
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from semiconductor_atlas.adapters.taiwan_factory_registry import (  # noqa: E402
    TAIWAN_FACTORY_ARCHIVE_URL,
)
from semiconductor_atlas.taiwan_factory_snapshot import (  # noqa: E402
    TAIWAN_FACTORY_MAX_ARCHIVE_BYTES,
    VerifiedTaiwanFactorySnapshot,
    create_taiwan_factory_snapshot,
    normalize_http_last_modified,
    verify_taiwan_factory_snapshot,
)


USER_AGENT = (
    "semiconductor-atlas/0.1 (Taiwan IDA registered-factory snapshot acquisition)"
)
DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_TIMEOUT_SECONDS = 300.0
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
RETRIEVAL_TIMESTAMP_BASIS = "upstream_download_completion"
_MAX_RETAINED_HEADER_BYTES = 1024


def _official_ssl_context() -> ssl.SSLContext:
    """Build a verified context compatible with the fixed official endpoint.

    Python 3.14 enables OpenSSL's strict-X.509 mode in its default context.  The
    official GCIS chain currently omits a Subject Key Identifier that strict
    mode requires, although normal platform trust validation succeeds.  Clear
    only that additional strictness flag; certificate-chain validation and
    hostname verification remain mandatory.
    """

    context = ssl.create_default_context()
    strict_flag = getattr(ssl, "VERIFY_X509_STRICT", None)
    if strict_flag is not None:
        context.verify_flags &= ~strict_flag
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError("official endpoint TLS context is not fully verified")
    return context


def _official_urlopen(request: object, *, timeout: float):
    full_url = getattr(request, "full_url", None)
    get_method = getattr(request, "get_method", None)
    if full_url != TAIWAN_FACTORY_ARCHIVE_URL or not callable(get_method):
        raise ValueError("official TLS opener accepts only the fixed factory URL")
    if get_method() != "GET":
        raise ValueError("official TLS opener accepts only GET requests")
    return urlopen(request, timeout=timeout, context=_official_ssl_context())


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
    result = int(value)
    if result <= 0:
        raise ValueError("HTTP Content-Length header must be positive")
    return result


def _write_response_bounded(
    response: object,
    destination: Path,
    *,
    max_bytes: int = TAIWAN_FACTORY_MAX_ARCHIVE_BYTES,
) -> int:
    if (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or not 0 < max_bytes <= TAIWAN_FACTORY_MAX_ARCHIVE_BYTES
    ):
        raise ValueError("download byte limit must be positive and at most 100 MB")
    headers = getattr(response, "headers", None)
    declared_size = _declared_content_length(headers)
    if declared_size is not None and declared_size > max_bytes:
        raise ValueError("Taiwan factory download exceeds the 100 MB byte limit")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags, 0o600)
    total = 0
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            while True:
                remaining_with_sentinel = max_bytes - total + 1
                chunk = response.read(
                    min(DOWNLOAD_CHUNK_BYTES, remaining_with_sentinel)
                )
                if not isinstance(chunk, bytes):
                    raise TypeError("HTTP response body reads must return bytes")
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(
                        "Taiwan factory download exceeds the 100 MB byte limit"
                    )
                stream.write(chunk)
            if total == 0:
                raise ValueError("Taiwan factory download is empty")
            if declared_size is not None and total != declared_size:
                raise ValueError(
                    "Taiwan factory download disagrees with HTTP Content-Length"
                )
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
) -> tuple[str, str, str | None, str | None, int | None]:
    request = Request(
        TAIWAN_FACTORY_ARCHIVE_URL,
        headers={
            "Accept": "application/zip,application/x-zip-compressed,application/octet-stream",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    with opener(request, timeout=timeout_seconds) as response:
        status = getattr(response, "status", None)
        if status is not None and (
            isinstance(status, bool)
            or not isinstance(status, int)
            or not 200 <= status < 300
        ):
            raise RuntimeError(f"Taiwan factory request returned HTTP {status}")
        final_url = response.geturl()
        if final_url != TAIWAN_FACTORY_ARCHIVE_URL:
            raise ValueError("Taiwan factory archive request was redirected")
        last_modified = _safe_response_header(response.headers, "Last-Modified")
        if last_modified is None:
            raise ValueError("Taiwan factory response omitted HTTP Last-Modified")
        # Validate the source clock before persisting any response bytes.
        normalize_http_last_modified(last_modified)
        etag = _safe_response_header(response.headers, "ETag")
        content_type = _safe_response_header(response.headers, "Content-Type")
        content_length = _declared_content_length(response.headers)
        _write_response_bounded(response, destination, max_bytes=max_bytes)
    return (
        _completion_timestamp(wall_clock),
        last_modified,
        etag,
        content_type,
        content_length,
    )


def acquire_taiwan_factory_snapshot(
    output_dir: str | Path,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    opener: Callable[..., object] = _official_urlopen,
    wall_clock: Callable[[], datetime] = _utc_now,
    temporary_parent: str | Path | None = None,
    max_download_bytes: int = TAIWAN_FACTORY_MAX_ARCHIVE_BYTES,
) -> VerifiedTaiwanFactorySnapshot:
    """Download the fixed official ZIP and atomically install one snapshot."""

    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS
    ):
        raise ValueError(
            f"timeout must be greater than zero and at most {MAX_TIMEOUT_SECONDS:g}"
        )
    if (
        isinstance(max_download_bytes, bool)
        or not isinstance(max_download_bytes, int)
        or not 0 < max_download_bytes <= TAIWAN_FACTORY_MAX_ARCHIVE_BYTES
    ):
        raise ValueError("download byte limit must be positive and at most 100 MB")
    output = Path(output_dir).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite Taiwan factory snapshot: {output}"
        )

    temporary_parent_path = (
        None if temporary_parent is None else str(Path(temporary_parent).absolute())
    )
    with tempfile.TemporaryDirectory(
        prefix="semiconductor-atlas-taiwan-factory-",
        dir=temporary_parent_path,
    ) as temporary:
        temporary_root = Path(temporary)
        os.chmod(temporary_root, 0o700)
        archive_path = temporary_root / "registered-factories.zip"
        (
            retrieved_at,
            last_modified,
            etag,
            content_type,
            content_length,
        ) = _download_official_archive(
            archive_path,
            timeout_seconds=float(timeout_seconds),
            opener=opener,
            wall_clock=wall_clock,
            max_bytes=max_download_bytes,
        )
        return create_taiwan_factory_snapshot(
            archive_path,
            output,
            retrieved_at=retrieved_at,
            retrieval_timestamp_basis=RETRIEVAL_TIMESTAMP_BASIS,
            upstream_last_modified=last_modified,
            upstream_etag=etag,
            upstream_content_type=content_type,
            upstream_content_length=content_length,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Acquire the fixed official Taiwan registered-factory archive into a "
            "new immutable snapshot, or replay-verify a snapshot offline."
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
        "--timeout",
        type=_bounded_timeout,
        metavar="SECONDS",
        help=(
            f"live HTTP timeout, greater than zero and at most "
            f"{MAX_TIMEOUT_SECONDS:g} (default: {DEFAULT_TIMEOUT_SECONDS:g})"
        ),
    )
    return parser


def _summary(snapshot: VerifiedTaiwanFactorySnapshot) -> dict[str, object]:
    return {
        "business_number_count": snapshot.business_number_count,
        "candidate_count": snapshot.candidate_count,
        "manifest": str((snapshot.root / "manifest.json").absolute()),
        "manifest_sha256": snapshot.manifest_sha256,
        "raw_sha256": snapshot.raw_sha256,
        "retrieved_at": snapshot.retrieved_at,
        "row_count": snapshot.row_count,
        "snapshot": str(snapshot.root.absolute()),
        "source_updated_at": snapshot.source_updated_at,
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    opener: Callable[..., object] = _official_urlopen,
    wall_clock: Callable[[], datetime] = _utc_now,
    temporary_parent: str | Path | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.verify_only is not None:
            if args.timeout is not None:
                raise ValueError("--timeout applies only to live acquisition")
            snapshot = verify_taiwan_factory_snapshot(args.verify_only)
        else:
            snapshot = acquire_taiwan_factory_snapshot(
                args.output_dir,
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
