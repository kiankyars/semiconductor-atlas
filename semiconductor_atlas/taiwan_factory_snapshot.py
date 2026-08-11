"""Immutable, replay-verifiable snapshots of Taiwan's factory registry."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .adapters.taiwan_factory_registry import (
    TAIWAN_FACTORY_ARCHIVE_URL,
    TAIWAN_FACTORY_ATTRIBUTION,
    TAIWAN_FACTORY_DATASET_URL,
    TAIWAN_FACTORY_FILTER_VERSION,
    TAIWAN_FACTORY_LICENSE,
    TAIWAN_FACTORY_LICENSE_URL,
    TAIWAN_FACTORY_POINTER_URL,
    TAIWAN_FACTORY_PRODUCT_TOKEN,
    TAIWAN_FACTORY_PUBLIC_FIELDS,
    TaiwanFactoryArchiveScan,
    canonical_candidate_jsonl_bytes,
    scan_taiwan_factory_archive,
)
from .gleif_snapshot import _rename_directory_no_replace


SOURCE_SNAPSHOT_FORMAT = "semiconductor-atlas-source-inputs-v1"
TAIWAN_FACTORY_SCOPE = "taiwan_ida_registered_factory_semiconductor_candidates"
TAIWAN_FACTORY_COVERAGE = (
    "all_rows_with_exact_principal_product_token_261_semiconductor_"
    "in_one_national_production_status_archive"
)
TAIWAN_FACTORY_CANDIDATE_FILENAME = "taiwan-factory-semiconductor-candidates.jsonl"
TAIWAN_FACTORY_CANDIDATE_RECORD_TYPE = (
    "taiwan_ida_registered_factory_semiconductor_candidates"
)
TAIWAN_FACTORY_RAW_RECORD_TYPE = "taiwan_ida_registered_factory_archive"
TAIWAN_FACTORY_UPDATE_CADENCE = "irregular"
TAIWAN_FACTORY_LICENSE_SPDX = "OGDL-Taiwan-1.0"
TAIWAN_FACTORY_RIGHTS_REVIEWED_AT = "2026-07-20"
TAIWAN_FACTORY_MAX_ARCHIVE_BYTES = 100_000_000
TAIWAN_FACTORY_MAX_CANDIDATE_BYTES = 128 * 1024 * 1024
TAIWAN_FACTORY_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
TAIWAN_FACTORY_RETRIEVAL_TIMESTAMP_BASES = frozenset(
    {
        "operator_supplied_for_archived_bytes",
        "upstream_download_completion",
    }
)
TAIWAN_FACTORY_SOURCE_UPDATED_AT_BASIS = "http_last_modified_header"

_READ_CHUNK_BYTES = 1024 * 1024
_MAX_RETAINED_HEADER_BYTES = 1024
_ALLOWED_ARCHIVE_CONTENT_TYPES = frozenset(
    {
        "application/octet-stream",
        "application/x-zip-compressed",
        "application/zip",
    }
)
_LIMITATIONS = (
    "archive_contains_only_factory_registrations_with_publisher_status_production",
    "production_is_an_administrative_registration_status_not_observed_operation",
    "registration_does_not_establish_output_capacity_utilization_or_yield",
    "exact_product_token_is_candidate_evidence_not_a_complete_semiconductor_census",
    "factory_registration_number_identifies_a_registry_record_not_ownership_history",
    "absence_from_a_refresh_is_not_real_world_closure_or_inactivity_evidence",
)
_RESPONSIBLE_PERSON_OMISSION_REASON = (
    "source field is unnecessary personal data for facility intelligence"
)


@dataclass(frozen=True, slots=True)
class VerifiedTaiwanFactorySnapshot:
    root: Path
    manifest_sha256: str
    manifest_size: int
    manifest_bytes: bytes
    retrieved_at: str
    retrieval_timestamp_basis: str
    source_updated_at: str
    source_updated_at_basis: str
    raw_path: str
    raw_sha256: str
    raw_size: int
    raw_bytes: bytes
    candidate_sha256: str
    candidate_size: int
    candidate_bytes: bytes
    member_name: str
    member_sha256: str
    member_size: int
    row_count: int
    candidate_row_count: int
    candidate_count: int
    business_number_count: int
    registration_status_counts: tuple[tuple[str, int], ...]
    scan: TaiwanFactoryArchiveScan


class _DuplicateJSONKey(ValueError):
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


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _required_text(value: object, context: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ValueError(f"{context} must be a non-empty clean string")
    return value


def _optional_header(value: object, context: str) -> str | None:
    if value is None:
        return None
    text = _required_text(value, context)
    if len(text.encode("utf-8")) > _MAX_RETAINED_HEADER_BYTES:
        raise ValueError(f"{context} exceeds the retained-header byte limit")
    return text


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


def _sha256(value: object, context: str) -> str:
    text = _required_text(value, context)
    if len(text) != 64 or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise ValueError(f"{context} must be a lowercase SHA-256 digest")
    return text


def _canonical_utc_timestamp(value: object, context: str) -> str:
    text = _required_text(value, context)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise ValueError(f"{context} must be an ISO 8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{context} must include a timezone")
    normalized = parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if normalized != text:
        raise ValueError(f"{context} must be a canonical UTC timestamp")
    return text


def _timestamp_clock(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00")


def normalize_http_last_modified(value: object) -> str:
    """Return one sanitized HTTP Last-Modified value as canonical UTC."""

    raw = _optional_header(value, "HTTP Last-Modified header")
    if raw is None:
        raise ValueError("HTTP Last-Modified header is required")
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(
            "HTTP Last-Modified header is not a valid HTTP date"
        ) from error
    if parsed is None or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("HTTP Last-Modified header must include a timezone")
    return (
        parsed.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _content_type(value: object) -> str | None:
    raw = _optional_header(value, "HTTP Content-Type header")
    if raw is None:
        return None
    base = raw.split(";", 1)[0].strip().lower()
    if base not in _ALLOWED_ARCHIVE_CONTENT_TYPES:
        raise ValueError("HTTP Content-Type header does not identify a ZIP download")
    return raw


def _validate_exact_keys(
    value: dict[str, Any], expected: set[str], context: str
) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise ValueError(
            f"{context} has missing keys {missing!r} or extra keys {extra!r}"
        )


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


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


class _SnapshotReader:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).absolute()
        self._root_fd: int | None = None
        self._root_identity: tuple[int, int, int, int, int] | None = None

    def __enter__(self) -> _SnapshotReader:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(self.root, flags)
        except OSError as error:
            raise ValueError(
                "Taiwan factory snapshot root must be a non-symlink directory"
            ) from error
        details = os.fstat(descriptor)
        if not stat.S_ISDIR(details.st_mode):
            os.close(descriptor)
            raise ValueError("Taiwan factory snapshot root must be a directory")
        self._root_fd = descriptor
        self._root_identity = _identity(details)
        return self

    def __exit__(self, *_args: object) -> None:
        if self._root_fd is not None:
            os.close(self._root_fd)
            self._root_fd = None

    def assert_unchanged(self) -> None:
        if self._root_fd is None or self._root_identity is None:
            raise RuntimeError("snapshot reader is not open")
        opened = os.fstat(self._root_fd)
        try:
            named = os.stat(self.root, follow_symlinks=False)
        except OSError as error:
            raise ValueError(
                "Taiwan factory snapshot root changed during verification"
            ) from error
        if (
            not stat.S_ISDIR(named.st_mode)
            or _identity(opened) != self._root_identity
            or _identity(named) != self._root_identity
        ):
            raise ValueError("Taiwan factory snapshot root changed during verification")

    def read(self, relative_path: object, *, maximum_bytes: int) -> bytes:
        if self._root_fd is None:
            raise RuntimeError("snapshot reader is not open")
        parts = _relative_parts(relative_path, "Taiwan factory snapshot input path")
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
                        f"Taiwan factory snapshot path must not traverse symlinks: {relative_path}"
                    ) from error
                details = os.fstat(next_fd)
                if not stat.S_ISDIR(details.st_mode):
                    os.close(next_fd)
                    raise ValueError(
                        f"Taiwan factory snapshot path component is not a directory: {relative_path}"
                    )
                os.close(directory_fd)
                directory_fd = next_fd

            leaf_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            leaf_flags |= getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
            try:
                descriptor = os.open(parts[-1], leaf_flags, dir_fd=directory_fd)
            except OSError as error:
                raise ValueError(
                    f"Taiwan factory snapshot input is missing or unsafe: {relative_path}"
                ) from error
            try:
                before = os.fstat(descriptor)
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError(
                        f"Taiwan factory snapshot input must be a regular file: {relative_path}"
                    )
                if before.st_size < 0 or before.st_size > maximum_bytes:
                    raise ValueError(
                        f"Taiwan factory snapshot input exceeds its byte limit: {relative_path}"
                    )
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = os.read(
                        descriptor,
                        min(_READ_CHUNK_BYTES, maximum_bytes + 1 - total),
                    )
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > maximum_bytes:
                        raise ValueError(
                            f"Taiwan factory snapshot input exceeds its byte limit: {relative_path}"
                        )
                after = os.fstat(descriptor)
                raw = b"".join(chunks)
                if _identity(before) != _identity(after) or len(raw) != before.st_size:
                    raise ValueError(
                        f"Taiwan factory snapshot input changed while being read: {relative_path}"
                    )
                return raw
            finally:
                os.close(descriptor)
        finally:
            os.close(directory_fd)


def _tree_inventory(root: Path) -> tuple[set[str], set[str]]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Taiwan factory snapshot root must be a non-symlink directory")
    directories: set[str] = set()
    files: set[str] = set()
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as error:
            raise ValueError("Taiwan factory snapshot tree is not readable") from error
        for entry in entries:
            relative = Path(entry.path).relative_to(root).as_posix()
            if entry.is_symlink():
                raise ValueError(
                    f"Taiwan factory snapshot tree contains a symlink: {relative}"
                )
            try:
                details = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise ValueError(
                    f"Taiwan factory snapshot tree entry is unsafe: {relative}"
                ) from error
            if stat.S_ISDIR(details.st_mode):
                directories.add(relative)
                pending.append(Path(entry.path))
            elif stat.S_ISREG(details.st_mode):
                files.add(relative)
            else:
                raise ValueError(
                    "Taiwan factory snapshot tree entry must be a regular file or "
                    f"directory: {relative}"
                )
    return directories, files


def _validate_exact_tree(root: Path, raw_path: str) -> None:
    expected_directories = {"raw", "raw/sha256"}
    expected_files = {"manifest.json", TAIWAN_FACTORY_CANDIDATE_FILENAME, raw_path}
    directories, files = _tree_inventory(root)
    if directories != expected_directories or files != expected_files:
        raise ValueError(
            "Taiwan factory snapshot tree has extra or missing entries "
            f"(directories={sorted(directories)!r}, files={sorted(files)!r})"
        )


def _summary(scan: TaiwanFactoryArchiveScan) -> dict[str, object]:
    return {
        "upstream_row_count": scan.row_count,
        "raw_matching_row_count": scan.candidate_row_count,
        "candidate_count": scan.candidate_count,
        "distinct_unified_business_number_count": scan.business_number_count,
        "registration_status_counts": dict(scan.registration_status_counts),
    }


def _rights() -> dict[str, object]:
    return {
        "reviewed_at": TAIWAN_FACTORY_RIGHTS_REVIEWED_AT,
        "decision": "pass_with_required_attribution",
        "access_level": "public",
        "license": TAIWAN_FACTORY_LICENSE,
        "license_spdx": TAIWAN_FACTORY_LICENSE_SPDX,
        "license_url": TAIWAN_FACTORY_LICENSE_URL,
        "attribution": TAIWAN_FACTORY_ATTRIBUTION,
        "no_endorsement": True,
        "no_warranty": True,
    }


def _input_rights() -> dict[str, object]:
    return {
        "access_level": "public",
        "license": TAIWAN_FACTORY_LICENSE,
        "license_spdx": TAIWAN_FACTORY_LICENSE_SPDX,
        "license_url": TAIWAN_FACTORY_LICENSE_URL,
        "attribution": TAIWAN_FACTORY_ATTRIBUTION,
    }


def _privacy_policy() -> dict[str, object]:
    return {
        "source_field_present_in_retained_raw_archive": "工廠負責人姓名",
        "field_present_in_public_derivative": False,
        "omission_reason": _RESPONSIBLE_PERSON_OMISSION_REASON,
        "public_derivative_fields": list(TAIWAN_FACTORY_PUBLIC_FIELDS),
    }


def _completeness() -> dict[str, object]:
    return {
        "exact_filter_complete_within_archived_package": True,
        "national_semiconductor_facility_census_complete": False,
        "source_assertion_interval_closure": {
            "allowed_after_verified_full_same_filter_refresh": True,
            "meaning": "absence_closes_only_the_prior_source_assertion_interval",
        },
        "real_world_facility_closure_or_inactivity_from_absence": False,
    }


def _manifest(
    *,
    scan: TaiwanFactoryArchiveScan,
    candidate_raw: bytes,
    retrieved_at: str,
    retrieval_timestamp_basis: str,
    source_updated_at: str,
    upstream_last_modified: str,
    upstream_etag: str | None,
    upstream_content_type: str | None,
    upstream_content_length: int | None,
) -> dict[str, object]:
    raw_path = f"raw/sha256/{scan.archive_sha256}.zip"
    candidate_digest = hashlib.sha256(candidate_raw).hexdigest()
    summary = _summary(scan)
    response_metadata = {
        "content_length": upstream_content_length,
        "content_type": upstream_content_type,
        "etag": upstream_etag,
        "last_modified": upstream_last_modified,
    }
    return {
        "format": SOURCE_SNAPSHOT_FORMAT,
        "retrieved_at": retrieved_at,
        "retrieval_timestamp_basis": retrieval_timestamp_basis,
        "source_scopes": {
            TAIWAN_FACTORY_SCOPE: {
                "complete": True,
                "coverage": TAIWAN_FACTORY_COVERAGE,
                "dataset_id": "6569",
                "dataset_url": TAIWAN_FACTORY_DATASET_URL,
                "pointer_url": TAIWAN_FACTORY_POINTER_URL,
                "archive_url": TAIWAN_FACTORY_ARCHIVE_URL,
                "source_updated_at": source_updated_at,
                "source_updated_at_basis": TAIWAN_FACTORY_SOURCE_UPDATED_AT_BASIS,
                "update_cadence": TAIWAN_FACTORY_UPDATE_CADENCE,
                "acquisition": {
                    "method": "GET",
                    "url": TAIWAN_FACTORY_ARCHIVE_URL,
                    "response_artifact": "national_registered_factory_zip",
                    "request_body_retained": False,
                    "credential_retained": False,
                },
                "filter": {
                    "version": TAIWAN_FACTORY_FILTER_VERSION,
                    "source_field": "主要產品",
                    "match_rule": "exact_line_token",
                    "token": TAIWAN_FACTORY_PRODUCT_TOKEN,
                },
                "privacy": _privacy_policy(),
                "upstream_archive": {
                    "download_url": TAIWAN_FACTORY_ARCHIVE_URL,
                    "sha256": scan.archive_sha256,
                    "bytes": scan.archive_bytes,
                    "artifact_content_type": "application/zip",
                    "response_metadata": response_metadata,
                },
                "upstream_member": {
                    "path": scan.csv_member,
                    "sha256": scan.csv_sha256,
                    "bytes": scan.csv_bytes,
                    "crc32": scan.csv_crc32,
                    "compressed_bytes": scan.csv_compressed_bytes,
                },
                "summary": summary,
                "raw_retention": {
                    "retained_in_snapshot": True,
                    "blob_locator": raw_path,
                    "sha256": scan.archive_sha256,
                    "bytes": scan.archive_bytes,
                    "local_archive_is_canonical_acquisition_input": True,
                },
                "completeness": _completeness(),
                "limitations": list(_LIMITATIONS),
                "rights": _rights(),
            }
        },
        "inputs": [
            {
                "path": TAIWAN_FACTORY_CANDIDATE_FILENAME,
                "record_type": TAIWAN_FACTORY_CANDIDATE_RECORD_TYPE,
                "url": TAIWAN_FACTORY_DATASET_URL,
                "artifact_kind": "deterministic_privacy_minimized_filtered_derivative",
                "filter_version": TAIWAN_FACTORY_FILTER_VERSION,
                "record_count": scan.candidate_count,
                "bytes": len(candidate_raw),
                "sha256": candidate_digest,
                "content_type": "application/x-ndjson",
                **_input_rights(),
            },
            {
                "path": raw_path,
                "record_type": TAIWAN_FACTORY_RAW_RECORD_TYPE,
                "url": TAIWAN_FACTORY_ARCHIVE_URL,
                "artifact_kind": "upstream_official_national_archive",
                "record_count": scan.row_count,
                "bytes": scan.archive_bytes,
                "sha256": scan.archive_sha256,
                "content_type": "application/zip",
                "response_metadata": response_metadata,
                **_input_rights(),
            },
        ],
    }


def _copy_regular_file(source: str | Path, destination: Path) -> tuple[str, int]:
    candidate = Path(source).absolute()
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ValueError("factory archive cannot be opened without symlink protection")
    flags = os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        named_before = os.stat(candidate, follow_symlinks=False)
        if not stat.S_ISREG(named_before.st_mode):
            raise ValueError("factory archive must be a regular file")
        descriptor = os.open(candidate, flags)
    except ValueError:
        raise
    except OSError as error:
        raise ValueError(
            f"factory archive is missing or unsafe: {candidate}"
        ) from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or _identity(before) != _identity(
            named_before
        ):
            raise ValueError("factory archive path changed while it was being opened")
        if before.st_size <= 0 or before.st_size > TAIWAN_FACTORY_MAX_ARCHIVE_BYTES:
            raise ValueError(
                "factory archive is empty or exceeds the 100 MB byte limit"
            )
        digest = hashlib.sha256()
        size = 0
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            while True:
                chunk = os.read(descriptor, _READ_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > TAIWAN_FACTORY_MAX_ARCHIVE_BYTES:
                    raise ValueError("factory archive exceeds the 100 MB byte limit")
                digest.update(chunk)
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        after = os.fstat(descriptor)
        try:
            named_after = os.stat(candidate, follow_symlinks=False)
        except OSError as error:
            raise ValueError(
                "factory archive path changed while being copied"
            ) from error
        if (
            _identity(before) != _identity(after)
            or _identity(named_after) != _identity(after)
            or not stat.S_ISREG(named_after.st_mode)
            or size != before.st_size
        ):
            raise ValueError("factory archive changed while being copied")
        return digest.hexdigest(), size
    finally:
        os.close(descriptor)


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


def _verified_status_counts(value: object) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, dict):
        raise ValueError("factory registration status counts must be an object")
    result: list[tuple[str, int]] = []
    for key in sorted(value):
        status = _required_text(key, "factory registration status")
        count = _integer(value[key], f"factory registration status count {status}")
        result.append((status, count))
    return tuple(result)


def verify_taiwan_factory_snapshot(
    root: str | Path,
) -> VerifiedTaiwanFactorySnapshot:
    """Reopen, rehash, rescan, and replay a factory snapshot offline."""

    with _SnapshotReader(root) as reader:
        manifest_raw = reader.read(
            "manifest.json", maximum_bytes=TAIWAN_FACTORY_MAX_MANIFEST_BYTES
        )
        manifest = _load_json_object(manifest_raw, "Taiwan factory snapshot manifest")
        if manifest_raw != _canonical_json_bytes(manifest):
            raise ValueError("Taiwan factory snapshot manifest is not canonical JSON")
        _validate_exact_keys(
            manifest,
            {
                "format",
                "retrieved_at",
                "retrieval_timestamp_basis",
                "source_scopes",
                "inputs",
            },
            "Taiwan factory snapshot manifest",
        )
        if manifest["format"] != SOURCE_SNAPSHOT_FORMAT:
            raise ValueError("Taiwan factory snapshot has an unsupported format")
        retrieved_at = _canonical_utc_timestamp(
            manifest["retrieved_at"], "Taiwan factory snapshot retrieved_at"
        )
        retrieval_basis = _required_text(
            manifest["retrieval_timestamp_basis"],
            "Taiwan factory snapshot retrieval_timestamp_basis",
        )
        if retrieval_basis not in TAIWAN_FACTORY_RETRIEVAL_TIMESTAMP_BASES:
            raise ValueError(
                "Taiwan factory snapshot retrieval timestamp basis drifted"
            )

        source_scopes = manifest["source_scopes"]
        if not isinstance(source_scopes, dict) or set(source_scopes) != {
            TAIWAN_FACTORY_SCOPE
        }:
            raise ValueError("Taiwan factory snapshot must contain exactly one scope")
        scope = source_scopes[TAIWAN_FACTORY_SCOPE]
        if not isinstance(scope, dict):
            raise ValueError("Taiwan factory source scope must be an object")
        _validate_exact_keys(
            scope,
            {
                "complete",
                "coverage",
                "dataset_id",
                "dataset_url",
                "pointer_url",
                "archive_url",
                "source_updated_at",
                "source_updated_at_basis",
                "update_cadence",
                "acquisition",
                "filter",
                "privacy",
                "upstream_archive",
                "upstream_member",
                "summary",
                "raw_retention",
                "completeness",
                "limitations",
                "rights",
            },
            "Taiwan factory source scope",
        )
        if (
            scope["complete"] is not True
            or scope["coverage"] != TAIWAN_FACTORY_COVERAGE
        ):
            raise ValueError("Taiwan factory snapshot coverage metadata drifted")
        expected_identity = {
            "dataset_id": "6569",
            "dataset_url": TAIWAN_FACTORY_DATASET_URL,
            "pointer_url": TAIWAN_FACTORY_POINTER_URL,
            "archive_url": TAIWAN_FACTORY_ARCHIVE_URL,
            "update_cadence": TAIWAN_FACTORY_UPDATE_CADENCE,
        }
        for key, expected in expected_identity.items():
            if scope[key] != expected:
                raise ValueError(f"Taiwan factory source {key} drifted")
        source_updated_at = _canonical_utc_timestamp(
            scope["source_updated_at"], "Taiwan factory source_updated_at"
        )
        if scope["source_updated_at_basis"] != TAIWAN_FACTORY_SOURCE_UPDATED_AT_BASIS:
            raise ValueError("Taiwan factory source update timestamp basis drifted")
        if _timestamp_clock(source_updated_at) > _timestamp_clock(retrieved_at):
            raise ValueError("Taiwan factory source update is later than retrieval")

        expected_acquisition = {
            "method": "GET",
            "url": TAIWAN_FACTORY_ARCHIVE_URL,
            "response_artifact": "national_registered_factory_zip",
            "request_body_retained": False,
            "credential_retained": False,
        }
        if scope["acquisition"] != expected_acquisition:
            raise ValueError("Taiwan factory acquisition metadata drifted")
        expected_filter = {
            "version": TAIWAN_FACTORY_FILTER_VERSION,
            "source_field": "主要產品",
            "match_rule": "exact_line_token",
            "token": TAIWAN_FACTORY_PRODUCT_TOKEN,
        }
        if scope["filter"] != expected_filter:
            raise ValueError("Taiwan factory exact filter metadata drifted")
        if scope["privacy"] != _privacy_policy():
            raise ValueError("Taiwan factory privacy policy metadata drifted")
        if scope["completeness"] != _completeness():
            raise ValueError("Taiwan factory completeness metadata drifted")
        if scope["limitations"] != list(_LIMITATIONS):
            raise ValueError("Taiwan factory limitations metadata drifted")
        if scope["rights"] != _rights():
            raise ValueError("Taiwan factory rights metadata drifted")

        upstream = scope["upstream_archive"]
        if not isinstance(upstream, dict):
            raise ValueError(
                "Taiwan factory upstream archive metadata must be an object"
            )
        _validate_exact_keys(
            upstream,
            {
                "download_url",
                "sha256",
                "bytes",
                "artifact_content_type",
                "response_metadata",
            },
            "Taiwan factory upstream archive metadata",
        )
        if (
            upstream["download_url"] != TAIWAN_FACTORY_ARCHIVE_URL
            or upstream["artifact_content_type"] != "application/zip"
        ):
            raise ValueError("Taiwan factory upstream archive identity drifted")
        raw_digest = _sha256(upstream["sha256"], "Taiwan factory archive sha256")
        raw_size = _integer(
            upstream["bytes"],
            "Taiwan factory archive bytes",
            minimum=1,
            maximum=TAIWAN_FACTORY_MAX_ARCHIVE_BYTES,
        )
        response = upstream["response_metadata"]
        if not isinstance(response, dict):
            raise ValueError("Taiwan factory HTTP response metadata must be an object")
        _validate_exact_keys(
            response,
            {"content_length", "content_type", "etag", "last_modified"},
            "Taiwan factory HTTP response metadata",
        )
        content_length_value = response["content_length"]
        if content_length_value is None:
            content_length = None
        else:
            content_length = _integer(
                content_length_value,
                "Taiwan factory HTTP Content-Length",
                minimum=1,
                maximum=TAIWAN_FACTORY_MAX_ARCHIVE_BYTES,
            )
            if content_length != raw_size:
                raise ValueError(
                    "Taiwan factory HTTP Content-Length disagrees with archive"
                )
        retained_content_type = _content_type(response["content_type"])
        retained_etag = _optional_header(response["etag"], "Taiwan factory HTTP ETag")
        retained_last_modified = _optional_header(
            response["last_modified"], "Taiwan factory HTTP Last-Modified"
        )
        if retained_last_modified is None:
            raise ValueError("Taiwan factory HTTP Last-Modified is required")
        if normalize_http_last_modified(retained_last_modified) != source_updated_at:
            raise ValueError(
                "Taiwan factory source update timestamp disagrees with HTTP Last-Modified"
            )
        response_metadata = {
            "content_length": content_length,
            "content_type": retained_content_type,
            "etag": retained_etag,
            "last_modified": retained_last_modified,
        }
        if response != response_metadata:
            raise ValueError("Taiwan factory HTTP response metadata is not canonical")

        member = scope["upstream_member"]
        if not isinstance(member, dict):
            raise ValueError("Taiwan factory CSV member metadata must be an object")
        _validate_exact_keys(
            member,
            {"path", "sha256", "bytes", "crc32", "compressed_bytes"},
            "Taiwan factory CSV member metadata",
        )
        member_name = _required_text(member["path"], "Taiwan factory CSV member path")
        _relative_parts(member_name, "Taiwan factory CSV member path")
        member_digest = _sha256(member["sha256"], "Taiwan factory CSV sha256")
        member_size = _integer(member["bytes"], "Taiwan factory CSV bytes", minimum=1)
        _integer(member["crc32"], "Taiwan factory CSV crc32", maximum=0xFFFFFFFF)
        _integer(
            member["compressed_bytes"],
            "Taiwan factory CSV compressed bytes",
            minimum=1,
        )

        raw_path = f"raw/sha256/{raw_digest}.zip"
        expected_retention = {
            "retained_in_snapshot": True,
            "blob_locator": raw_path,
            "sha256": raw_digest,
            "bytes": raw_size,
            "local_archive_is_canonical_acquisition_input": True,
        }
        if scope["raw_retention"] != expected_retention:
            raise ValueError("Taiwan factory raw retention metadata drifted")

        inputs = manifest["inputs"]
        if not isinstance(inputs, list) or len(inputs) != 2:
            raise ValueError("Taiwan factory snapshot requires exactly two inputs")
        candidate_entry, raw_entry = inputs
        if not isinstance(candidate_entry, dict) or not isinstance(raw_entry, dict):
            raise ValueError("Taiwan factory snapshot inputs must be objects")
        _validate_exact_keys(
            candidate_entry,
            {
                "path",
                "record_type",
                "url",
                "artifact_kind",
                "filter_version",
                "record_count",
                "bytes",
                "sha256",
                "content_type",
                "access_level",
                "license",
                "license_spdx",
                "license_url",
                "attribution",
            },
            "Taiwan factory candidate input",
        )
        expected_candidate = {
            "path": TAIWAN_FACTORY_CANDIDATE_FILENAME,
            "record_type": TAIWAN_FACTORY_CANDIDATE_RECORD_TYPE,
            "url": TAIWAN_FACTORY_DATASET_URL,
            "artifact_kind": "deterministic_privacy_minimized_filtered_derivative",
            "filter_version": TAIWAN_FACTORY_FILTER_VERSION,
            "content_type": "application/x-ndjson",
            **_input_rights(),
        }
        for key, expected in expected_candidate.items():
            if candidate_entry[key] != expected:
                raise ValueError(f"Taiwan factory candidate input {key} drifted")
        candidate_size = _integer(
            candidate_entry["bytes"],
            "Taiwan factory candidate bytes",
            maximum=TAIWAN_FACTORY_MAX_CANDIDATE_BYTES,
        )
        candidate_digest = _sha256(
            candidate_entry["sha256"], "Taiwan factory candidate sha256"
        )

        _validate_exact_keys(
            raw_entry,
            {
                "path",
                "record_type",
                "url",
                "artifact_kind",
                "record_count",
                "bytes",
                "sha256",
                "content_type",
                "response_metadata",
                "access_level",
                "license",
                "license_spdx",
                "license_url",
                "attribution",
            },
            "Taiwan factory raw input",
        )
        expected_raw = {
            "path": raw_path,
            "record_type": TAIWAN_FACTORY_RAW_RECORD_TYPE,
            "url": TAIWAN_FACTORY_ARCHIVE_URL,
            "artifact_kind": "upstream_official_national_archive",
            "record_count": scope["summary"]["upstream_row_count"]
            if isinstance(scope["summary"], dict)
            else None,
            "bytes": raw_size,
            "sha256": raw_digest,
            "content_type": "application/zip",
            "response_metadata": response_metadata,
            **_input_rights(),
        }
        if raw_entry != expected_raw:
            raise ValueError("Taiwan factory raw input metadata drifted")

        _validate_exact_tree(reader.root, raw_path)
        raw_bytes = reader.read(
            raw_path, maximum_bytes=TAIWAN_FACTORY_MAX_ARCHIVE_BYTES
        )
        if (
            len(raw_bytes) != raw_size
            or hashlib.sha256(raw_bytes).hexdigest() != raw_digest
        ):
            raise ValueError("Taiwan factory retained archive hash or size mismatch")
        candidate_bytes = reader.read(
            TAIWAN_FACTORY_CANDIDATE_FILENAME,
            maximum_bytes=TAIWAN_FACTORY_MAX_CANDIDATE_BYTES,
        )
        if (
            len(candidate_bytes) != candidate_size
            or hashlib.sha256(candidate_bytes).hexdigest() != candidate_digest
        ):
            raise ValueError(
                "Taiwan factory candidate derivative hash or size mismatch"
            )

        scan = scan_taiwan_factory_archive(reader.root / raw_path)
        if scan.archive_sha256 != raw_digest or scan.archive_bytes != raw_size:
            raise ValueError(
                "Taiwan factory retained archive rescan identity disagrees"
            )
        expected_member = {
            "path": scan.csv_member,
            "sha256": scan.csv_sha256,
            "bytes": scan.csv_bytes,
            "crc32": scan.csv_crc32,
            "compressed_bytes": scan.csv_compressed_bytes,
        }
        if member != expected_member or member_size != scan.csv_bytes:
            raise ValueError("Taiwan factory member metadata disagrees with raw rescan")
        expected_summary = _summary(scan)
        if scope["summary"] != expected_summary:
            raise ValueError(
                "Taiwan factory summary metadata disagrees with raw rescan"
            )
        replayed = canonical_candidate_jsonl_bytes(scan)
        if candidate_bytes != replayed:
            raise ValueError("Taiwan factory candidate derivative does not replay")
        if candidate_entry["record_count"] != scan.candidate_count:
            raise ValueError("Taiwan factory candidate record count disagrees")
        status_counts = _verified_status_counts(
            expected_summary["registration_status_counts"]
        )
        if status_counts != scan.registration_status_counts:
            raise ValueError("Taiwan factory registration status counts disagree")

        # Reopen every retained file after semantic scanning to detect replacement or
        # in-place mutation across the complete verification interval.
        if (
            reader.read(
                "manifest.json", maximum_bytes=TAIWAN_FACTORY_MAX_MANIFEST_BYTES
            )
            != manifest_raw
        ):
            raise ValueError("Taiwan factory manifest changed during verification")
        if (
            reader.read(raw_path, maximum_bytes=TAIWAN_FACTORY_MAX_ARCHIVE_BYTES)
            != raw_bytes
        ):
            raise ValueError("Taiwan factory archive changed during verification")
        if (
            reader.read(
                TAIWAN_FACTORY_CANDIDATE_FILENAME,
                maximum_bytes=TAIWAN_FACTORY_MAX_CANDIDATE_BYTES,
            )
            != candidate_bytes
        ):
            raise ValueError("Taiwan factory derivative changed during verification")
        _validate_exact_tree(reader.root, raw_path)
        reader.assert_unchanged()

        return VerifiedTaiwanFactorySnapshot(
            root=reader.root,
            manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
            manifest_size=len(manifest_raw),
            manifest_bytes=manifest_raw,
            retrieved_at=retrieved_at,
            retrieval_timestamp_basis=retrieval_basis,
            source_updated_at=source_updated_at,
            source_updated_at_basis=TAIWAN_FACTORY_SOURCE_UPDATED_AT_BASIS,
            raw_path=raw_path,
            raw_sha256=raw_digest,
            raw_size=raw_size,
            raw_bytes=raw_bytes,
            candidate_sha256=candidate_digest,
            candidate_size=candidate_size,
            candidate_bytes=candidate_bytes,
            member_name=member_name,
            member_sha256=member_digest,
            member_size=member_size,
            row_count=scan.row_count,
            candidate_row_count=scan.candidate_row_count,
            candidate_count=scan.candidate_count,
            business_number_count=scan.business_number_count,
            registration_status_counts=status_counts,
            scan=scan,
        )


def create_taiwan_factory_snapshot(
    archive_path: str | Path,
    output_dir: str | Path,
    *,
    retrieved_at: str,
    upstream_last_modified: str,
    retrieval_timestamp_basis: str = "operator_supplied_for_archived_bytes",
    upstream_etag: str | None = None,
    upstream_content_type: str | None = None,
    upstream_content_length: int | None = None,
) -> VerifiedTaiwanFactorySnapshot:
    """Transform one official ZIP into a new atomically installed snapshot."""

    retrieved_at = _canonical_utc_timestamp(retrieved_at, "retrieved_at")
    retrieval_timestamp_basis = _required_text(
        retrieval_timestamp_basis, "retrieval_timestamp_basis"
    )
    if retrieval_timestamp_basis not in TAIWAN_FACTORY_RETRIEVAL_TIMESTAMP_BASES:
        raise ValueError("invalid Taiwan factory retrieval_timestamp_basis")
    upstream_last_modified = _optional_header(
        upstream_last_modified, "upstream_last_modified"
    )
    if upstream_last_modified is None:
        raise ValueError("upstream_last_modified is required")
    source_updated_at = normalize_http_last_modified(upstream_last_modified)
    if _timestamp_clock(source_updated_at) > _timestamp_clock(retrieved_at):
        raise ValueError("HTTP Last-Modified must not be later than retrieved_at")
    upstream_etag = _optional_header(upstream_etag, "upstream_etag")
    upstream_content_type = _content_type(upstream_content_type)
    if upstream_content_length is not None:
        upstream_content_length = _integer(
            upstream_content_length,
            "upstream_content_length",
            minimum=1,
            maximum=TAIWAN_FACTORY_MAX_ARCHIVE_BYTES,
        )

    output = Path(output_dir).absolute()
    if not output.name or output.name in {".", ".."}:
        raise ValueError(f"unsafe Taiwan factory snapshot output directory: {output}")
    if output.exists() or output.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite Taiwan factory snapshot: {output}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)

    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent))
    installed = False
    try:
        provisional = stage / "raw" / "sha256" / ".archive.part"
        copied_digest, copied_size = _copy_regular_file(archive_path, provisional)
        if (
            upstream_content_length is not None
            and upstream_content_length != copied_size
        ):
            raise ValueError("upstream Content-Length disagrees with archive bytes")
        raw_path = stage / "raw" / "sha256" / f"{copied_digest}.zip"
        os.rename(provisional, raw_path)
        scan = scan_taiwan_factory_archive(raw_path)
        if scan.archive_sha256 != copied_digest or scan.archive_bytes != copied_size:
            raise ValueError(
                "factory archive scan identity disagrees with retained bytes"
            )
        candidate_raw = canonical_candidate_jsonl_bytes(scan)
        if not isinstance(candidate_raw, bytes):
            raise TypeError("canonical_candidate_jsonl_bytes must return bytes")
        if len(candidate_raw) > TAIWAN_FACTORY_MAX_CANDIDATE_BYTES:
            raise ValueError("factory candidate derivative exceeds the byte limit")
        _write_new(stage / TAIWAN_FACTORY_CANDIDATE_FILENAME, candidate_raw)
        manifest = _manifest(
            scan=scan,
            candidate_raw=candidate_raw,
            retrieved_at=retrieved_at,
            retrieval_timestamp_basis=retrieval_timestamp_basis,
            source_updated_at=source_updated_at,
            upstream_last_modified=upstream_last_modified,
            upstream_etag=upstream_etag,
            upstream_content_type=upstream_content_type,
            upstream_content_length=upstream_content_length,
        )
        manifest_raw = _canonical_json_bytes(manifest)
        if len(manifest_raw) > TAIWAN_FACTORY_MAX_MANIFEST_BYTES:
            raise ValueError("Taiwan factory manifest exceeds the byte limit")
        _write_new(stage / "manifest.json", manifest_raw)
        _fsync_directory(stage / "raw" / "sha256")
        _fsync_directory(stage / "raw")
        _fsync_directory(stage)
        verify_taiwan_factory_snapshot(stage)
        if output.exists() or output.is_symlink():
            raise FileExistsError(
                f"refusing to overwrite Taiwan factory snapshot: {output}"
            )
        _rename_directory_no_replace(stage, output)
        installed = True
        _fsync_directory(output.parent)
        return verify_taiwan_factory_snapshot(output)
    finally:
        if not installed and stage.exists():
            shutil.rmtree(stage)


__all__ = [
    "TAIWAN_FACTORY_CANDIDATE_FILENAME",
    "TAIWAN_FACTORY_CANDIDATE_RECORD_TYPE",
    "TAIWAN_FACTORY_COVERAGE",
    "TAIWAN_FACTORY_LICENSE_SPDX",
    "TAIWAN_FACTORY_MAX_ARCHIVE_BYTES",
    "TAIWAN_FACTORY_RAW_RECORD_TYPE",
    "TAIWAN_FACTORY_RETRIEVAL_TIMESTAMP_BASES",
    "TAIWAN_FACTORY_SCOPE",
    "TAIWAN_FACTORY_SOURCE_UPDATED_AT_BASIS",
    "TAIWAN_FACTORY_UPDATE_CADENCE",
    "VerifiedTaiwanFactorySnapshot",
    "create_taiwan_factory_snapshot",
    "normalize_http_last_modified",
    "verify_taiwan_factory_snapshot",
]
