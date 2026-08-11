"""Bounded acquisition and offline verification for GLEIF Level 1 snapshots."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import math
import os
import re
import shutil
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.request import Request, urlopen

from .adapters.gleif_lei import (
    GLEIFLEISnapshot,
    GLEIF_LEI_CANONICAL_FORMAT,
    canonical_lei_payload_bytes,
    parse_lei_jsonapi_bytes,
)


SOURCE_SNAPSHOT_FORMAT = "semiconductor-atlas-source-inputs-v1"
GLEIF_SCOPE = "gleif_lei_level_1"
GLEIF_COVERAGE = "exact_declared_lei_allowlist_level_1_only"
GLEIF_API_BASE_URL = "https://api.gleif.org/api/v1/lei-records"
GLEIF_API_URL_TEMPLATE = GLEIF_API_BASE_URL + "/{lei}"
GLEIF_ALLOWLIST_FILENAME = "gleif-lei-allowlist.txt"
GLEIF_CANONICAL_FILENAME = "gleif-lei-level-1.json"
GLEIF_CANONICAL_RECORD_TYPE = "gleif_lei_level_1_canonical"
GLEIF_RAW_RECORD_TYPE = "gleif_lei_jsonapi_response"
GLEIF_LICENSE = "CC0-1.0"
GLEIF_LICENSE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
GLEIF_TERMS_URL = "https://www.gleif.org/en/meta/lei-data-terms-of-use"
GLEIF_ATTRIBUTION = (
    "Source: Global Legal Entity Identifier Foundation (GLEIF), LEI data, "
    "CC0 1.0; no GLEIF endorsement implied."
)
GLEIF_RIGHTS_REVIEWED_AT = "2026-07-19"
GLEIF_PUBLISHED_REQUEST_LIMIT_PER_MINUTE = 60
GLEIF_MINIMUM_REQUEST_INTERVAL_SECONDS = 1.1
GLEIF_MAX_ALLOWLIST_RECORDS = 500
GLEIF_MAX_ALLOWLIST_BYTES = 16_384
GLEIF_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
GLEIF_MAX_TOTAL_RESPONSE_BYTES = 256 * 1024 * 1024
GLEIF_MAX_CANONICAL_BYTES = 128 * 1024 * 1024
GLEIF_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
GLEIF_MAX_ATTEMPTS = 5
GLEIF_USER_AGENT = "semiconductor-atlas/0.1 GLEIF-Level-1 acquisition"

_LEI_RE = re.compile(r"^[0-9A-Z]{20}$")
_ALLOWED_CONTENT_TYPES = frozenset(
    {"application/json", "application/vnd.api+json"}
)
_RETRIEVAL_TIMESTAMP_BASIS = "process_clock_after_each_successful_response"
_RELATIONSHIP_POLICY = {
    "included": False,
    "reason": "deferred_to_separately_versioned_level_2_snapshot",
}
_READ_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class GLEIFHTTPResponse:
    body: bytes
    final_url: str | None = None
    content_type: str | None = "application/vnd.api+json"
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True, slots=True)
class VerifiedGLEIFResponse:
    lei: str
    url: str
    retrieved_at: str
    golden_copy_publish_date: str
    sha256: str
    size: int
    content_type: str
    etag: str | None
    last_modified: str | None
    raw_bytes: bytes


@dataclass(frozen=True, slots=True)
class VerifiedGLEIFSnapshot:
    root: Path
    manifest_sha256: str
    manifest_size: int
    manifest_bytes: bytes
    retrieved_at: str
    golden_copy_publish_date: str
    leis: tuple[str, ...]
    allowlist_sha256: str
    allowlist_bytes: bytes
    canonical_sha256: str
    canonical_bytes: bytes
    responses: tuple[VerifiedGLEIFResponse, ...]
    attempts_used: int


class _DuplicateJSONKey(ValueError):
    pass


class _GoldenCopyRotation(RuntimeError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKey(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value}")


def _load_json_object(raw: bytes, context: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError(f"{context} must be valid UTF-8") from error
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except _DuplicateJSONKey as error:
        raise ValueError(f"{context} contains {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"{context} is not valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a JSON object")
    return value


def _required_text(value: object, context: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 0x20 for character in value)
    ):
        raise ValueError(f"{context} must be a non-empty clean string")
    return value


def _optional_header(value: object, context: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, context)


def _integer(
    value: object,
    context: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{context} must be an integer of at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{context} must not exceed {maximum}")
    return value


def _positive_number(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must be a positive finite number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{context} must be a positive finite number")
    return result


def _sha256(value: object, context: str) -> str:
    text = _required_text(value, context)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{context} must be a lowercase SHA-256 digest")
    return text


def _parse_timestamp(value: object, context: str) -> datetime:
    text = _required_text(value, context)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise ValueError(f"{context} must be an ISO 8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{context} must include a timezone")
    return parsed.astimezone(UTC)


def _canonical_utc_timestamp(value: object, context: str) -> tuple[str, datetime]:
    text = _required_text(value, context)
    parsed = _parse_timestamp(text, context)
    normalized = parsed.isoformat().replace("+00:00", "Z")
    if normalized != text:
        raise ValueError(f"{context} must be a canonical UTC timestamp")
    return text, parsed


def _timestamp_from_clock(clock: Callable[[], datetime]) -> str:
    value = clock()
    if not isinstance(value, datetime):
        raise TypeError("wall_clock must return a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("wall_clock must return a timezone-aware datetime")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _lei_checksum_is_valid(lei: str) -> bool:
    numeric = "".join(str(int(character, 36)) for character in lei)
    remainder = 0
    for character in numeric:
        remainder = (remainder * 10 + int(character)) % 97
    return remainder == 1


def _validated_lei(value: object, context: str) -> str:
    lei = _required_text(value, context)
    if not _LEI_RE.fullmatch(lei) or not _lei_checksum_is_valid(lei):
        raise ValueError(f"{context} must be a valid 20-character LEI")
    return lei


def canonical_lei_allowlist_bytes(leis: tuple[str, ...] | list[str]) -> bytes:
    """Return the strict line-oriented representation of a sorted LEI tuple."""

    if not isinstance(leis, (tuple, list)):
        raise TypeError("leis must be a tuple or list")
    normalized = tuple(
        _validated_lei(value, f"leis[{index}]") for index, value in enumerate(leis)
    )
    if not normalized:
        raise ValueError("GLEIF LEI allowlist must not be empty")
    if len(normalized) > GLEIF_MAX_ALLOWLIST_RECORDS:
        raise ValueError(
            f"GLEIF LEI allowlist exceeds {GLEIF_MAX_ALLOWLIST_RECORDS} records"
        )
    if len(set(normalized)) != len(normalized):
        raise ValueError("GLEIF LEI allowlist contains duplicate LEIs")
    if tuple(sorted(normalized)) != normalized:
        raise ValueError("GLEIF LEI allowlist must be sorted")
    raw = ("\n".join(normalized) + "\n").encode("ascii")
    if len(raw) > GLEIF_MAX_ALLOWLIST_BYTES:
        raise ValueError("GLEIF LEI allowlist exceeds the byte limit")
    return raw


def parse_lei_allowlist_bytes(raw: bytes) -> tuple[str, ...]:
    """Parse a canonical sorted, unique, newline-terminated LEI allowlist."""

    if not isinstance(raw, bytes):
        raise TypeError("GLEIF LEI allowlist must be bytes")
    if not raw or len(raw) > GLEIF_MAX_ALLOWLIST_BYTES:
        raise ValueError("GLEIF LEI allowlist is empty or exceeds the byte limit")
    try:
        text = raw.decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("GLEIF LEI allowlist must be canonical ASCII") from error
    if not text.endswith("\n") or "\r" in text:
        raise ValueError("GLEIF LEI allowlist must use LF and end with one LF")
    lines = text[:-1].split("\n")
    if not lines or any(not line for line in lines):
        raise ValueError("GLEIF LEI allowlist must not contain blank lines")
    leis = tuple(
        _validated_lei(line, f"allowlist line {index}")
        for index, line in enumerate(lines, start=1)
    )
    if canonical_lei_allowlist_bytes(leis) != raw:
        raise ValueError("GLEIF LEI allowlist is not canonical")
    return leis


def _relative_parts(value: object, context: str) -> tuple[str, ...]:
    path = _required_text(value, context)
    if "\\" in path or len(path) > 1024:
        raise ValueError(f"{context} is not a canonical relative path")
    parsed = PurePosixPath(path)
    if (
        parsed.is_absolute()
        or parsed.as_posix() != path
        or not parsed.parts
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise ValueError(f"{context} is not a canonical relative path")
    return parsed.parts


class _SnapshotReader:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).absolute()
        self._root_fd: int | None = None

    def __enter__(self) -> _SnapshotReader:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(self.root, flags)
        except OSError as error:
            raise ValueError(
                f"GLEIF snapshot root must be a non-symlink directory: {self.root}"
            ) from error
        details = os.fstat(descriptor)
        if not stat.S_ISDIR(details.st_mode):
            os.close(descriptor)
            raise ValueError("GLEIF snapshot root must be a directory")
        self._root_fd = descriptor
        return self

    def __exit__(self, *_args: object) -> None:
        if self._root_fd is not None:
            os.close(self._root_fd)
            self._root_fd = None

    def read(self, relative_path: object, *, maximum_bytes: int) -> bytes:
        if self._root_fd is None:
            raise RuntimeError("snapshot reader is not open")
        parts = _relative_parts(relative_path, "GLEIF snapshot input path")
        directory_fd = os.dup(self._root_fd)
        try:
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_flags |= getattr(os, "O_NOFOLLOW", 0)
            directory_flags |= getattr(os, "O_CLOEXEC", 0)
            for part in parts[:-1]:
                try:
                    next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
                except OSError as error:
                    raise ValueError(
                        f"GLEIF snapshot path must not traverse symlinks: {relative_path}"
                    ) from error
                details = os.fstat(next_fd)
                if not stat.S_ISDIR(details.st_mode):
                    os.close(next_fd)
                    raise ValueError(
                        f"GLEIF snapshot path component is not a directory: {relative_path}"
                    )
                os.close(directory_fd)
                directory_fd = next_fd

            leaf_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            leaf_flags |= getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
            try:
                descriptor = os.open(parts[-1], leaf_flags, dir_fd=directory_fd)
            except OSError as error:
                raise ValueError(
                    f"GLEIF snapshot input is missing or unsafe: {relative_path}"
                ) from error
            try:
                before = os.fstat(descriptor)
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError(
                        f"GLEIF snapshot input must be a regular file: {relative_path}"
                    )
                if before.st_size < 0 or before.st_size > maximum_bytes:
                    raise ValueError(
                        f"GLEIF snapshot input exceeds its byte limit: {relative_path}"
                    )
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, maximum_bytes + 1 - total))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > maximum_bytes:
                        raise ValueError(
                            f"GLEIF snapshot input exceeds its byte limit: {relative_path}"
                        )
                after = os.fstat(descriptor)
                if (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                ) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ):
                    raise ValueError(
                        f"GLEIF snapshot input changed while being read: {relative_path}"
                    )
                raw = b"".join(chunks)
                if len(raw) != before.st_size:
                    raise ValueError(
                        f"GLEIF snapshot input size changed while being read: {relative_path}"
                    )
                return raw
            finally:
                os.close(descriptor)
        finally:
            os.close(directory_fd)


def read_lei_allowlist_file(path: str | Path) -> bytes:
    """Read an allowlist from one regular, non-symlink file."""

    candidate = Path(path).absolute()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError as error:
        raise ValueError(f"GLEIF allowlist is missing or unsafe: {candidate}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("GLEIF allowlist must be a regular file")
        if before.st_size > GLEIF_MAX_ALLOWLIST_BYTES:
            raise ValueError("GLEIF allowlist exceeds the byte limit")
        raw = b""
        while len(raw) <= GLEIF_MAX_ALLOWLIST_BYTES:
            chunk = os.read(descriptor, GLEIF_MAX_ALLOWLIST_BYTES + 1 - len(raw))
            if not chunk:
                break
            raw += chunk
        if len(raw) > GLEIF_MAX_ALLOWLIST_BYTES:
            raise ValueError("GLEIF allowlist exceeds the byte limit")
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or len(raw) != before.st_size:
            raise ValueError("GLEIF allowlist changed while being read")
    finally:
        os.close(descriptor)
    parse_lei_allowlist_bytes(raw)
    return raw


def _content_type(value: object, context: str) -> str:
    content_type = _required_text(value, context)
    base_type = content_type.split(";", 1)[0].strip().lower()
    if base_type not in _ALLOWED_CONTENT_TYPES:
        raise ValueError(f"{context} must identify JSON:API or JSON content")
    return content_type


def _rights() -> dict[str, object]:
    return {
        "reviewed_at": GLEIF_RIGHTS_REVIEWED_AT,
        "decision": "pass_for_exact_public_api_responses",
        "access_level": "public",
        "license": GLEIF_LICENSE,
        "license_url": GLEIF_LICENSE_URL,
        "terms_url": GLEIF_TERMS_URL,
        "attribution": GLEIF_ATTRIBUTION,
        "no_endorsement": True,
    }


def _input_rights() -> dict[str, object]:
    rights = _rights()
    return {
        "access_level": rights["access_level"],
        "license": rights["license"],
        "license_url": rights["license_url"],
        "attribution": rights["attribution"],
    }


def _write_new(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rename_directory_no_replace(source: Path, destination: Path) -> None:
    """Atomically rename a directory while refusing an existing destination."""

    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(os.fsencode(source), os.fsencode(destination), 0x00000004)
    elif sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        try:
            rename = libc.renameat2
        except AttributeError as error:
            raise RuntimeError(
                "atomic no-replace directory installation is unavailable"
            ) from error
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(
            -100,
            os.fsencode(source),
            -100,
            os.fsencode(destination),
            0x00000001,
        )
    elif os.name == "nt":
        os.rename(source, destination)
        return
    else:
        raise RuntimeError("atomic no-replace directory installation is unavailable")

    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(
            error_number,
            "refusing to overwrite GLEIF snapshot",
            str(destination),
        )
    raise OSError(error_number, os.strerror(error_number), str(destination))


def _combined_snapshot(
    golden_copy_publish_date: str,
    parsed_responses: list[GLEIFLEISnapshot],
) -> GLEIFLEISnapshot:
    records = tuple(snapshot.records[0] for snapshot in parsed_responses)
    joined_digests = "".join(snapshot.raw_sha256 for snapshot in parsed_responses)
    return GLEIFLEISnapshot(
        golden_copy_publish_date=golden_copy_publish_date,
        records=records,
        raw_sha256=hashlib.sha256(joined_digests.encode("ascii")).hexdigest(),
        raw_bytes=sum(snapshot.raw_bytes for snapshot in parsed_responses),
    )


def _response_entry(
    *,
    lei: str,
    url: str,
    retrieved_at: str,
    publish_date: str,
    response: GLEIFHTTPResponse,
    digest: str,
) -> dict[str, object]:
    result: dict[str, object] = {
        "path": f"raw/sha256/{digest}.json",
        "record_type": GLEIF_RAW_RECORD_TYPE,
        "url": url,
        "requested_lei": lei,
        "retrieved_at": retrieved_at,
        "golden_copy_publish_date": publish_date,
        "artifact_kind": "upstream_official_api_response",
        "bytes": len(response.body),
        "sha256": digest,
        "content_type": response.content_type,
        "etag": response.etag,
        "last_modified": response.last_modified,
        **_input_rights(),
    }
    return result


def _manifest(
    *,
    allowlist_raw: bytes,
    leis: tuple[str, ...],
    retrieved_at: str,
    golden_copy_publish_date: str,
    attempts_used: int,
    max_attempts: int,
    timeout_seconds: float,
    canonical_raw: bytes,
    response_entries: list[dict[str, object]],
) -> dict[str, object]:
    allowlist_digest = hashlib.sha256(allowlist_raw).hexdigest()
    canonical_digest = hashlib.sha256(canonical_raw).hexdigest()
    canonical_entry = {
        "path": GLEIF_CANONICAL_FILENAME,
        "record_type": GLEIF_CANONICAL_RECORD_TYPE,
        "url": GLEIF_API_BASE_URL,
        "artifact_kind": "deterministic_canonical_derivative",
        "canonical_format": GLEIF_LEI_CANONICAL_FORMAT,
        "golden_copy_publish_date": golden_copy_publish_date,
        "record_count": len(leis),
        "bytes": len(canonical_raw),
        "sha256": canonical_digest,
        "content_type": "application/json",
        **_input_rights(),
    }
    return {
        "format": SOURCE_SNAPSHOT_FORMAT,
        "retrieved_at": retrieved_at,
        "retrieval_timestamp_basis": _RETRIEVAL_TIMESTAMP_BASIS,
        "source_scopes": {
            GLEIF_SCOPE: {
                "complete": True,
                "coverage": GLEIF_COVERAGE,
                "api_base_url": GLEIF_API_BASE_URL,
                "leis": list(leis),
                "allowlist": {
                    "path": GLEIF_ALLOWLIST_FILENAME,
                    "bytes": len(allowlist_raw),
                    "sha256": allowlist_digest,
                    "record_count": len(leis),
                },
                "golden_copy_publish_date": golden_copy_publish_date,
                "canonical_format": GLEIF_LEI_CANONICAL_FORMAT,
                "response_count": len(response_entries),
                "attempts_used": attempts_used,
                "request_policy": {
                    "published_limit_requests_per_minute": (
                        GLEIF_PUBLISHED_REQUEST_LIMIT_PER_MINUTE
                    ),
                    "minimum_request_start_interval_seconds": (
                        GLEIF_MINIMUM_REQUEST_INTERVAL_SECONDS
                    ),
                    "timeout_seconds": timeout_seconds,
                    "max_attempts": max_attempts,
                    "maximum_response_bytes": GLEIF_MAX_RESPONSE_BYTES,
                    "maximum_total_response_bytes": GLEIF_MAX_TOTAL_RESPONSE_BYTES,
                },
                "relationships": dict(_RELATIONSHIP_POLICY),
                "rights": _rights(),
            }
        },
        "inputs": [canonical_entry, *response_entries],
    }


def _validate_exact_keys(
    value: dict[str, Any], expected: set[str], context: str
) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise ValueError(
            f"{context} has invalid fields (missing={missing!r}, extra={extra!r})"
        )


def verify_gleif_snapshot(root: str | Path) -> VerifiedGLEIFSnapshot:
    """Verify and replay a GLEIF Level 1 snapshot without network access."""

    with _SnapshotReader(root) as reader:
        manifest_raw = reader.read(
            "manifest.json", maximum_bytes=GLEIF_MAX_MANIFEST_BYTES
        )
        manifest = _load_json_object(manifest_raw, "GLEIF snapshot manifest")
        _validate_exact_keys(
            manifest,
            {
                "format",
                "retrieved_at",
                "retrieval_timestamp_basis",
                "source_scopes",
                "inputs",
            },
            "GLEIF snapshot manifest",
        )
        if manifest["format"] != SOURCE_SNAPSHOT_FORMAT:
            raise ValueError("GLEIF snapshot has an unsupported manifest format")
        if manifest["retrieval_timestamp_basis"] != _RETRIEVAL_TIMESTAMP_BASIS:
            raise ValueError("GLEIF snapshot has an invalid retrieval timestamp basis")
        retrieved_at, retrieved_clock = _canonical_utc_timestamp(
            manifest["retrieved_at"], "GLEIF snapshot retrieved_at"
        )

        source_scopes = manifest["source_scopes"]
        if not isinstance(source_scopes, dict) or set(source_scopes) != {GLEIF_SCOPE}:
            raise ValueError("GLEIF snapshot must contain exactly one Level 1 scope")
        scope = source_scopes[GLEIF_SCOPE]
        if not isinstance(scope, dict):
            raise ValueError("GLEIF Level 1 scope must be an object")
        _validate_exact_keys(
            scope,
            {
                "complete",
                "coverage",
                "api_base_url",
                "leis",
                "allowlist",
                "golden_copy_publish_date",
                "canonical_format",
                "response_count",
                "attempts_used",
                "request_policy",
                "relationships",
                "rights",
            },
            "GLEIF Level 1 scope",
        )
        if scope["complete"] is not True or scope["coverage"] != GLEIF_COVERAGE:
            raise ValueError("GLEIF Level 1 scope must declare exact allowlist coverage")
        if scope["api_base_url"] != GLEIF_API_BASE_URL:
            raise ValueError("GLEIF Level 1 scope has an invalid API base URL")
        if scope["canonical_format"] != GLEIF_LEI_CANONICAL_FORMAT:
            raise ValueError("GLEIF Level 1 scope has an invalid canonical format")
        if scope["relationships"] != _RELATIONSHIP_POLICY:
            raise ValueError("GLEIF Level 1 relationship policy has drifted")
        if scope["rights"] != _rights():
            raise ValueError("GLEIF Level 1 rights metadata has drifted")

        golden_copy_publish_date = _required_text(
            scope["golden_copy_publish_date"],
            "GLEIF Level 1 golden_copy_publish_date",
        )
        golden_copy_clock = _parse_timestamp(
            golden_copy_publish_date,
            "GLEIF Level 1 golden_copy_publish_date",
        )
        if golden_copy_clock > retrieved_clock:
            raise ValueError("GLEIF Golden Copy publication is later than retrieval")

        declared_leis = scope["leis"]
        if not isinstance(declared_leis, list):
            raise ValueError("GLEIF Level 1 leis must be an array")
        leis = tuple(
            _validated_lei(value, f"GLEIF Level 1 leis[{index}]")
            for index, value in enumerate(declared_leis)
        )
        expected_allowlist_raw = canonical_lei_allowlist_bytes(leis)

        allowlist = scope["allowlist"]
        if not isinstance(allowlist, dict):
            raise ValueError("GLEIF Level 1 allowlist metadata must be an object")
        _validate_exact_keys(
            allowlist,
            {"path", "bytes", "sha256", "record_count"},
            "GLEIF Level 1 allowlist metadata",
        )
        if allowlist["path"] != GLEIF_ALLOWLIST_FILENAME:
            raise ValueError("GLEIF Level 1 allowlist path is invalid")
        allowlist_size = _integer(
            allowlist["bytes"],
            "GLEIF Level 1 allowlist bytes",
            minimum=1,
            maximum=GLEIF_MAX_ALLOWLIST_BYTES,
        )
        allowlist_digest = _sha256(
            allowlist["sha256"], "GLEIF Level 1 allowlist sha256"
        )
        if _integer(
            allowlist["record_count"],
            "GLEIF Level 1 allowlist record_count",
            minimum=1,
            maximum=GLEIF_MAX_ALLOWLIST_RECORDS,
        ) != len(leis):
            raise ValueError("GLEIF Level 1 allowlist record count disagrees")
        allowlist_raw = reader.read(
            GLEIF_ALLOWLIST_FILENAME, maximum_bytes=GLEIF_MAX_ALLOWLIST_BYTES
        )
        if len(allowlist_raw) != allowlist_size:
            raise ValueError("GLEIF Level 1 allowlist size mismatch")
        if hashlib.sha256(allowlist_raw).hexdigest() != allowlist_digest:
            raise ValueError("GLEIF Level 1 allowlist hash mismatch")
        if allowlist_raw != expected_allowlist_raw:
            raise ValueError("GLEIF Level 1 allowlist bytes disagree with scope")
        if parse_lei_allowlist_bytes(allowlist_raw) != leis:
            raise ValueError("GLEIF Level 1 allowlist replay disagrees with scope")

        request_policy = scope["request_policy"]
        if not isinstance(request_policy, dict):
            raise ValueError("GLEIF Level 1 request policy must be an object")
        _validate_exact_keys(
            request_policy,
            {
                "published_limit_requests_per_minute",
                "minimum_request_start_interval_seconds",
                "timeout_seconds",
                "max_attempts",
                "maximum_response_bytes",
                "maximum_total_response_bytes",
            },
            "GLEIF Level 1 request policy",
        )
        if (
            request_policy["published_limit_requests_per_minute"]
            != GLEIF_PUBLISHED_REQUEST_LIMIT_PER_MINUTE
            or request_policy["minimum_request_start_interval_seconds"]
            != GLEIF_MINIMUM_REQUEST_INTERVAL_SECONDS
            or request_policy["maximum_response_bytes"] != GLEIF_MAX_RESPONSE_BYTES
            or request_policy["maximum_total_response_bytes"]
            != GLEIF_MAX_TOTAL_RESPONSE_BYTES
        ):
            raise ValueError("GLEIF Level 1 request policy has drifted")
        declared_timeout = _positive_number(
            request_policy["timeout_seconds"],
            "GLEIF Level 1 timeout_seconds",
        )
        if declared_timeout > 300:
            raise ValueError("GLEIF Level 1 timeout_seconds must not exceed 300")
        max_attempts = _integer(
            request_policy["max_attempts"],
            "GLEIF Level 1 max_attempts",
            minimum=1,
            maximum=GLEIF_MAX_ATTEMPTS,
        )
        attempts_used = _integer(
            scope["attempts_used"],
            "GLEIF Level 1 attempts_used",
            minimum=1,
            maximum=max_attempts,
        )

        inputs = manifest["inputs"]
        if not isinstance(inputs, list) or len(inputs) != len(leis) + 1:
            raise ValueError("GLEIF snapshot input count does not match its allowlist")
        if _integer(
            scope["response_count"],
            "GLEIF Level 1 response_count",
            minimum=1,
            maximum=GLEIF_MAX_ALLOWLIST_RECORDS,
        ) != len(leis):
            raise ValueError("GLEIF Level 1 response count disagrees")

        canonical_entry = inputs[0]
        if not isinstance(canonical_entry, dict):
            raise ValueError("GLEIF canonical input must be an object")
        _validate_exact_keys(
            canonical_entry,
            {
                "path",
                "record_type",
                "url",
                "artifact_kind",
                "canonical_format",
                "golden_copy_publish_date",
                "record_count",
                "bytes",
                "sha256",
                "content_type",
                "access_level",
                "license",
                "license_url",
                "attribution",
            },
            "GLEIF canonical input",
        )
        expected_canonical_metadata = {
            "path": GLEIF_CANONICAL_FILENAME,
            "record_type": GLEIF_CANONICAL_RECORD_TYPE,
            "url": GLEIF_API_BASE_URL,
            "artifact_kind": "deterministic_canonical_derivative",
            "canonical_format": GLEIF_LEI_CANONICAL_FORMAT,
            "golden_copy_publish_date": golden_copy_publish_date,
            "record_count": len(leis),
            "content_type": "application/json",
            **_input_rights(),
        }
        for key, expected in expected_canonical_metadata.items():
            if canonical_entry[key] != expected:
                raise ValueError(f"GLEIF canonical input has invalid {key}")
        canonical_size = _integer(
            canonical_entry["bytes"],
            "GLEIF canonical input bytes",
            minimum=1,
            maximum=GLEIF_MAX_CANONICAL_BYTES,
        )
        canonical_digest = _sha256(
            canonical_entry["sha256"], "GLEIF canonical input sha256"
        )
        canonical_raw = reader.read(
            GLEIF_CANONICAL_FILENAME, maximum_bytes=GLEIF_MAX_CANONICAL_BYTES
        )
        if len(canonical_raw) != canonical_size:
            raise ValueError("GLEIF canonical input size mismatch")
        if hashlib.sha256(canonical_raw).hexdigest() != canonical_digest:
            raise ValueError("GLEIF canonical input hash mismatch")

        parsed_responses: list[GLEIFLEISnapshot] = []
        verified_responses: list[VerifiedGLEIFResponse] = []
        paths = {GLEIF_CANONICAL_FILENAME}
        total_response_bytes = 0
        previous_retrieved_clock: datetime | None = None
        for index, (lei, raw_entry) in enumerate(zip(leis, inputs[1:]), start=1):
            if not isinstance(raw_entry, dict):
                raise ValueError(f"GLEIF raw input {index} must be an object")
            _validate_exact_keys(
                raw_entry,
                {
                    "path",
                    "record_type",
                    "url",
                    "requested_lei",
                    "retrieved_at",
                    "golden_copy_publish_date",
                    "artifact_kind",
                    "bytes",
                    "sha256",
                    "content_type",
                    "etag",
                    "last_modified",
                    "access_level",
                    "license",
                    "license_url",
                    "attribution",
                },
                f"GLEIF raw input {index}",
            )
            digest = _sha256(raw_entry["sha256"], f"GLEIF raw input {index} sha256")
            expected_path = f"raw/sha256/{digest}.json"
            expected_url = GLEIF_API_URL_TEMPLATE.format(lei=lei)
            expected_raw_metadata = {
                "path": expected_path,
                "record_type": GLEIF_RAW_RECORD_TYPE,
                "url": expected_url,
                "requested_lei": lei,
                "golden_copy_publish_date": golden_copy_publish_date,
                "artifact_kind": "upstream_official_api_response",
                **_input_rights(),
            }
            for key, expected in expected_raw_metadata.items():
                if raw_entry[key] != expected:
                    raise ValueError(f"GLEIF raw input {index} has invalid {key}")
            if expected_path in paths:
                raise ValueError("GLEIF snapshot contains duplicate input paths")
            paths.add(expected_path)
            response_size = _integer(
                raw_entry["bytes"],
                f"GLEIF raw input {index} bytes",
                minimum=1,
                maximum=GLEIF_MAX_RESPONSE_BYTES,
            )
            total_response_bytes += response_size
            if total_response_bytes > GLEIF_MAX_TOTAL_RESPONSE_BYTES:
                raise ValueError("GLEIF snapshot raw responses exceed the total byte limit")
            content_type = _content_type(
                raw_entry["content_type"], f"GLEIF raw input {index} content_type"
            )
            etag = _optional_header(raw_entry["etag"], f"GLEIF raw input {index} etag")
            last_modified = _optional_header(
                raw_entry["last_modified"],
                f"GLEIF raw input {index} last_modified",
            )
            response_retrieved_at, response_retrieved_clock = _canonical_utc_timestamp(
                raw_entry["retrieved_at"],
                f"GLEIF raw input {index} retrieved_at",
            )
            if response_retrieved_clock < golden_copy_clock:
                raise ValueError("GLEIF response predates its Golden Copy publication")
            if response_retrieved_clock > retrieved_clock:
                raise ValueError("GLEIF response retrieval is later than snapshot retrieval")
            if (
                previous_retrieved_clock is not None
                and response_retrieved_clock < previous_retrieved_clock
            ):
                raise ValueError("GLEIF response retrieval times must be non-decreasing")
            previous_retrieved_clock = response_retrieved_clock

            raw = reader.read(expected_path, maximum_bytes=GLEIF_MAX_RESPONSE_BYTES)
            if len(raw) != response_size:
                raise ValueError(f"GLEIF raw input {index} size mismatch")
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError(f"GLEIF raw input {index} hash mismatch")
            parsed = parse_lei_jsonapi_bytes(raw)
            if len(parsed.records) != 1 or parsed.records[0].lei != lei:
                raise ValueError(
                    f"GLEIF raw input {index} does not return exactly requested LEI {lei}"
                )
            if parsed.golden_copy_publish_date != golden_copy_publish_date:
                raise ValueError("GLEIF raw inputs do not share one Golden Copy publication")
            parsed_responses.append(parsed)
            verified_responses.append(
                VerifiedGLEIFResponse(
                    lei=lei,
                    url=expected_url,
                    retrieved_at=response_retrieved_at,
                    golden_copy_publish_date=golden_copy_publish_date,
                    sha256=digest,
                    size=response_size,
                    content_type=content_type,
                    etag=etag,
                    last_modified=last_modified,
                    raw_bytes=raw,
                )
            )

        if previous_retrieved_clock != retrieved_clock:
            raise ValueError("GLEIF snapshot retrieval must equal its final response retrieval")
        combined = _combined_snapshot(golden_copy_publish_date, parsed_responses)
        replayed_canonical = canonical_lei_payload_bytes(combined)
        if canonical_raw != replayed_canonical:
            raise ValueError("GLEIF canonical derivative does not replay from raw responses")

        return VerifiedGLEIFSnapshot(
            root=reader.root,
            manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
            manifest_size=len(manifest_raw),
            manifest_bytes=manifest_raw,
            retrieved_at=retrieved_at,
            golden_copy_publish_date=golden_copy_publish_date,
            leis=leis,
            allowlist_sha256=allowlist_digest,
            allowlist_bytes=allowlist_raw,
            canonical_sha256=canonical_digest,
            canonical_bytes=canonical_raw,
            responses=tuple(verified_responses),
            attempts_used=attempts_used,
        )


def fetch_gleif_response(url: str, timeout_seconds: float) -> GLEIFHTTPResponse:
    """Fetch one exact GLEIF API resource with a bounded response body."""

    request = Request(
        url,
        headers={
            "Accept": "application/vnd.api+json,application/json",
            "Accept-Encoding": "identity",
            "User-Agent": GLEIF_USER_AGENT,
        },
        method="GET",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        final_url = response.geturl()
        if final_url != url:
            raise ValueError(f"GLEIF API redirected exact resource request to {final_url}")
        status_code = getattr(response, "status", 200)
        if status_code != 200:
            raise ValueError(f"GLEIF API returned HTTP {status_code}")
        content_encoding = response.headers.get("Content-Encoding")
        if content_encoding not in {None, "identity"}:
            raise ValueError("GLEIF API response must not use content encoding")
        declared_length = response.headers.get("Content-Length")
        if declared_length is not None:
            try:
                length = int(declared_length)
            except ValueError as error:
                raise ValueError("GLEIF API returned an invalid Content-Length") from error
            if length < 0 or length > GLEIF_MAX_RESPONSE_BYTES:
                raise ValueError("GLEIF API response exceeds the byte limit")
        body = response.read(GLEIF_MAX_RESPONSE_BYTES + 1)
        if len(body) > GLEIF_MAX_RESPONSE_BYTES:
            raise ValueError("GLEIF API response exceeds the byte limit")
        return GLEIFHTTPResponse(
            body=body,
            final_url=final_url,
            content_type=response.headers.get("Content-Type"),
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
        )


class _RequestPacer:
    def __init__(
        self,
        monotonic_clock: Callable[[], float],
        sleeper: Callable[[float], None],
    ) -> None:
        self._clock = monotonic_clock
        self._sleep = sleeper
        self._last_started: float | None = None

    def before_request(self) -> None:
        now = self._clock()
        if not isinstance(now, (int, float)) or not math.isfinite(float(now)):
            raise ValueError("monotonic_clock must return a finite number")
        now = float(now)
        if self._last_started is not None:
            target = self._last_started + GLEIF_MINIMUM_REQUEST_INTERVAL_SECONDS
            for _ in range(4):
                remaining = target - now
                if remaining <= 1e-9:
                    break
                self._sleep(remaining)
                next_now = self._clock()
                if (
                    not isinstance(next_now, (int, float))
                    or not math.isfinite(float(next_now))
                    or float(next_now) < now
                ):
                    raise ValueError("monotonic_clock must not move backward")
                now = float(next_now)
            else:
                raise RuntimeError("sleep did not satisfy the GLEIF request interval")
            if now + 1e-9 < target:
                raise RuntimeError("sleep did not satisfy the GLEIF request interval")
        self._last_started = now


def _validated_fetch_response(
    response: object, requested_url: str
) -> GLEIFHTTPResponse:
    if not isinstance(response, GLEIFHTTPResponse):
        raise TypeError("GLEIF transport must return GLEIFHTTPResponse")
    if not isinstance(response.body, bytes) or not response.body:
        raise ValueError("GLEIF transport response body must be non-empty bytes")
    if len(response.body) > GLEIF_MAX_RESPONSE_BYTES:
        raise ValueError("GLEIF API response exceeds the byte limit")
    if response.final_url not in {None, requested_url}:
        raise ValueError("GLEIF transport response URL does not match its request")
    _content_type(response.content_type, "GLEIF transport content_type")
    _optional_header(response.etag, "GLEIF transport etag")
    _optional_header(response.last_modified, "GLEIF transport last_modified")
    return response


def create_gleif_snapshot(
    allowlist_raw: bytes,
    output_dir: str | Path,
    *,
    transport: Callable[[str, float], GLEIFHTTPResponse] | None = None,
    wall_clock: Callable[[], datetime] | None = None,
    monotonic_clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
    timeout_seconds: float = 30.0,
    max_attempts: int = 3,
) -> VerifiedGLEIFSnapshot:
    """Acquire one exact-allowlist snapshot and install it atomically."""

    leis = parse_lei_allowlist_bytes(allowlist_raw)
    timeout_seconds = _positive_number(timeout_seconds, "timeout_seconds")
    if timeout_seconds > 300:
        raise ValueError("timeout_seconds must not exceed 300")
    max_attempts = _integer(
        max_attempts,
        "max_attempts",
        minimum=1,
        maximum=GLEIF_MAX_ATTEMPTS,
    )
    output = Path(output_dir).absolute()
    if not output.name or output.name in {".", ".."}:
        raise ValueError(f"unsafe GLEIF snapshot output directory: {output}")
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"refusing to overwrite GLEIF snapshot: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    fetch = transport or fetch_gleif_response
    now = wall_clock or (lambda: datetime.now(UTC))
    pace = _RequestPacer(monotonic_clock or time.monotonic, sleeper or time.sleep)
    seen_rotation_dates: list[tuple[str, str]] = []

    for attempt in range(1, max_attempts + 1):
        stage = Path(
            tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent)
        )
        installed = False
        try:
            _write_new(stage / GLEIF_ALLOWLIST_FILENAME, allowlist_raw)
            parsed_responses: list[GLEIFLEISnapshot] = []
            response_entries: list[dict[str, object]] = []
            publish_date: str | None = None
            final_retrieved_at: str | None = None
            total_response_bytes = 0

            for lei in leis:
                pace.before_request()
                url = GLEIF_API_URL_TEMPLATE.format(lei=lei)
                response = _validated_fetch_response(fetch(url, timeout_seconds), url)
                total_response_bytes += len(response.body)
                if total_response_bytes > GLEIF_MAX_TOTAL_RESPONSE_BYTES:
                    raise ValueError(
                        "GLEIF API responses exceed the snapshot total byte limit"
                    )
                parsed = parse_lei_jsonapi_bytes(response.body)
                if len(parsed.records) != 1 or parsed.records[0].lei != lei:
                    raise ValueError(
                        f"GLEIF API did not return exactly requested LEI {lei}"
                    )
                if publish_date is None:
                    publish_date = parsed.golden_copy_publish_date
                elif parsed.golden_copy_publish_date != publish_date:
                    seen_rotation_dates.append(
                        (publish_date, parsed.golden_copy_publish_date)
                    )
                    raise _GoldenCopyRotation

                retrieved_at = _timestamp_from_clock(now)
                if _parse_timestamp(
                    parsed.golden_copy_publish_date,
                    "GLEIF Golden Copy publishDate",
                ) > _parse_timestamp(retrieved_at, "GLEIF response retrieved_at"):
                    raise ValueError("GLEIF Golden Copy publication is later than retrieval")
                if final_retrieved_at is not None and _parse_timestamp(
                    retrieved_at, "GLEIF response retrieved_at"
                ) < _parse_timestamp(
                    final_retrieved_at, "prior GLEIF response retrieved_at"
                ):
                    raise ValueError("wall_clock must not move backward")
                final_retrieved_at = retrieved_at
                digest = hashlib.sha256(response.body).hexdigest()
                raw_path = stage / "raw" / "sha256" / f"{digest}.json"
                _write_new(raw_path, response.body)
                parsed_responses.append(parsed)
                response_entries.append(
                    _response_entry(
                        lei=lei,
                        url=url,
                        retrieved_at=retrieved_at,
                        publish_date=parsed.golden_copy_publish_date,
                        response=response,
                        digest=digest,
                    )
                )

            if publish_date is None or final_retrieved_at is None:
                raise AssertionError("non-empty GLEIF allowlist produced no responses")
            canonical_raw = canonical_lei_payload_bytes(
                _combined_snapshot(publish_date, parsed_responses)
            )
            if len(canonical_raw) > GLEIF_MAX_CANONICAL_BYTES:
                raise ValueError("GLEIF canonical derivative exceeds the byte limit")
            _write_new(stage / GLEIF_CANONICAL_FILENAME, canonical_raw)
            manifest = _manifest(
                allowlist_raw=allowlist_raw,
                leis=leis,
                retrieved_at=final_retrieved_at,
                golden_copy_publish_date=publish_date,
                attempts_used=attempt,
                max_attempts=max_attempts,
                timeout_seconds=timeout_seconds,
                canonical_raw=canonical_raw,
                response_entries=response_entries,
            )
            manifest_raw = (
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    allow_nan=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
            if len(manifest_raw) > GLEIF_MAX_MANIFEST_BYTES:
                raise ValueError("GLEIF snapshot manifest exceeds the byte limit")
            _write_new(stage / "manifest.json", manifest_raw)
            raw_sha_directory = stage / "raw" / "sha256"
            _fsync_directory(raw_sha_directory)
            _fsync_directory(raw_sha_directory.parent)
            _fsync_directory(stage)
            verify_gleif_snapshot(stage)
            if output.exists() or output.is_symlink():
                raise FileExistsError(
                    f"refusing to overwrite GLEIF snapshot: {output}"
                )
            _rename_directory_no_replace(stage, output)
            installed = True
            _fsync_directory(output.parent)
            return verify_gleif_snapshot(output)
        except _GoldenCopyRotation:
            if attempt == max_attempts:
                observed = ", ".join(
                    f"{first} -> {second}" for first, second in seen_rotation_dates
                )
                raise ValueError(
                    "GLEIF Golden Copy changed during every acquisition attempt"
                    + (f": {observed}" if observed else "")
                )
        finally:
            if not installed and stage.exists():
                shutil.rmtree(stage)

    raise AssertionError("GLEIF acquisition attempts exhausted without a result")


__all__ = [
    "GLEIFHTTPResponse",
    "GLEIF_API_BASE_URL",
    "GLEIF_API_URL_TEMPLATE",
    "GLEIF_ATTRIBUTION",
    "GLEIF_CANONICAL_FILENAME",
    "GLEIF_CANONICAL_RECORD_TYPE",
    "GLEIF_COVERAGE",
    "GLEIF_LICENSE",
    "GLEIF_LICENSE_URL",
    "GLEIF_MAX_ALLOWLIST_RECORDS",
    "GLEIF_MAX_RESPONSE_BYTES",
    "GLEIF_MINIMUM_REQUEST_INTERVAL_SECONDS",
    "GLEIF_RAW_RECORD_TYPE",
    "GLEIF_SCOPE",
    "GLEIF_TERMS_URL",
    "VerifiedGLEIFResponse",
    "VerifiedGLEIFSnapshot",
    "canonical_lei_allowlist_bytes",
    "create_gleif_snapshot",
    "fetch_gleif_response",
    "parse_lei_allowlist_bytes",
    "read_lei_allowlist_file",
    "verify_gleif_snapshot",
]
