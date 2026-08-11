"""Immutable, replay-verifiable snapshots of Taiwan MOENV EMS_S_01 archives."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .adapters.moenv_ems import (
    MOENV_ATTRIBUTION,
    MOENV_DATASET_URL,
    MOENV_FIELDS,
    MOENV_FILTER_VERSION,
    MOENV_INDUSTRY_GROUP,
    MOENV_INDUSTRY_LABELS,
    MOENV_LICENSE,
    MOENV_LICENSE_URL,
    MOENVArchiveScan,
    canonical_candidate_jsonl_bytes,
    scan_moenv_archive,
)
from .gleif_snapshot import _rename_directory_no_replace


SOURCE_SNAPSHOT_FORMAT = "semiconductor-atlas-source-inputs-v1"
MOENV_SCOPE = "moenv_ems_s_01_semiconductor_candidates"
MOENV_COVERAGE = (
    "all_deduplicated_exact_source_variants_with_industry_group_261_"
    "in_one_archived_full_package"
)
MOENV_CANDIDATE_FILENAME = "moenv-ems-s-01-semiconductor-candidates.jsonl"
MOENV_CANDIDATE_RECORD_TYPE = "moenv_ems_s_01_semiconductor_candidates"
MOENV_RAW_RECORD_TYPE = "moenv_ems_s_01_full_package_archive"
MOENV_API_DOCUMENTATION_URL = "https://data.moenv.gov.tw/swagger"
MOENV_API_ENDPOINT_URL = "https://data.moenv.gov.tw/api/v2/ems_s_01"
MOENV_API_GUIDE_URL = "https://data.moenv.gov.tw/paradigm"
MOENV_FULL_PACKAGE_URL = (
    "https://data.moenv.gov.tw/api/frontstage/dataset/resource.download"
)
MOENV_PACKAGE_PID = "816037bc-53f1-4951-b32d-8e607b948344"
MOENV_RESOURCE_RID = "56ea8602-c7d5-4c27-ac20-236e51e889c4"
MOENV_UPDATE_CADENCE = "daily"
MOENV_LICENSE_SPDX = "OGDL-Taiwan-1.0"
MOENV_RIGHTS_REVIEWED_AT = "2026-07-20"
MOENV_MAX_ARCHIVE_BYTES = 250_000_000
MOENV_MAX_CANDIDATE_BYTES = 512 * 1024 * 1024
MOENV_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MOENV_RETRIEVAL_TIMESTAMP_BASES = frozenset(
    {
        "operator_supplied_for_archived_bytes",
        "upstream_download_completion",
        "local_archive_transform_start_for_archived_bytes",
    }
)
MOENV_DATASET_UPDATED_AT_BASES = frozenset(
    {"official_dataset_page_displayed_asia_taipei"}
)

_READ_CHUNK_BYTES = 1024 * 1024
_SECRET_QUERY_KEY_PARTS = (
    "accesskey",
    "apikey",
    "authorization",
    "credential",
    "key",
    "password",
    "secret",
    "servicekey",
    "token",
)
_LIMITATIONS = (
    "environmental_control_registry_not_a_complete_manufacturing_census",
    "includes_current_and_deregistered_historical_entities",
    "source_reported_industry_classification_is_candidate_evidence_not_operating_proof",
    "emsno_is_a_source_registry_identifier_not_ownership_or_operator_evidence",
    "coordinates_can_be_missing_and_are_not_independently_surveyed_by_this_adapter",
    "conflicting_duplicate_source_rows_are_preserved_as_distinct_variants",
)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


@dataclass(frozen=True, slots=True)
class VerifiedMOENVAcquisition:
    method: str
    url: str
    resource_ids: tuple[str, ...]
    download_type: str
    package_id: str
    canonical_request_body: bytes


@dataclass(frozen=True, slots=True)
class VerifiedMOENVSnapshot:
    root: Path
    manifest_sha256: str
    manifest_size: int
    manifest_bytes: bytes
    retrieved_at: str
    retrieval_timestamp_basis: str
    dataset_updated_at: str
    dataset_updated_at_basis: str
    acquisition: VerifiedMOENVAcquisition
    download_url: str
    raw_path: str
    raw_sha256: str
    raw_size: int
    raw_bytes: bytes
    candidate_sha256: str
    candidate_size: int
    candidate_bytes: bytes
    member_name: str
    member_sha256: str
    member_md5: str
    publisher_md5: str
    row_count: int
    candidate_count: int
    facility_count: int
    code_counts: tuple[tuple[str, int], ...]
    current_regulation_count: int
    valid_coordinate_count: int
    conflicting_facility_count: int
    scan: MOENVArchiveScan


class _DuplicateJSONKey(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJSONKey(f"duplicate JSON object key {key!r}")
        value[key] = item
    return value


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


def _canonical_compact_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _required_text(value: object, context: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 0x20 for character in value)
    ):
        raise ValueError(f"{context} must be a non-empty clean string")
    return value


def _optional_text(value: object, context: str) -> str | None:
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


def _digest(value: object, context: str, length: int) -> str:
    text = _required_text(value, context)
    if len(text) != length or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{context} must be a lowercase hexadecimal digest")
    return text


def _sha256(value: object, context: str) -> str:
    return _digest(value, context, 64)


def _md5(value: object, context: str) -> str:
    return _digest(value, context, 32)


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


def _validate_exact_keys(
    value: dict[str, Any], expected: set[str], context: str
) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise ValueError(
            f"{context} has invalid fields (missing={missing!r}, extra={extra!r})"
        )


def _normalized_query_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _validated_https_url(value: object, context: str) -> str:
    url = _required_text(value, context)
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError(f"{context} must be a credential-free HTTPS URL")
    for key, _query_value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized = _normalized_query_key(key)
        if any(part in normalized for part in _SECRET_QUERY_KEY_PARTS):
            raise ValueError(f"{context} contains a secret-like query key")
    return url


def _validated_download_url(value: object, context: str) -> str:
    url = _validated_https_url(value, context)
    parsed = urlsplit(url)
    if parsed.hostname != "data.moenv.gov.tw" or parsed.port is not None:
        raise ValueError(f"{context} must use the official data.moenv.gov.tw host")
    if parsed.path != urlsplit(MOENV_FULL_PACKAGE_URL).path or parsed.query:
        raise ValueError(f"{context} must identify the EMS_S_01 full-package endpoint")
    return url


def _validated_acquisition_body(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("MOENV acquisition request body must be an object")
    _validate_exact_keys(
        value,
        {"rid", "download_type", "pid"},
        "MOENV acquisition request body",
    )
    if value["download_type"] != "json":
        raise ValueError("MOENV acquisition request must select the JSON full package")
    pid = _required_text(value["pid"], "MOENV acquisition pid")
    if not _UUID_RE.fullmatch(pid):
        raise ValueError("MOENV acquisition pid must be a lowercase UUID")
    if pid != MOENV_PACKAGE_PID:
        raise ValueError("MOENV acquisition pid does not identify EMS_S_01")
    rid_value = value["rid"]
    if not isinstance(rid_value, list) or len(rid_value) != 1:
        raise ValueError("MOENV acquisition rid must contain exactly one resource UUID")
    rid = _required_text(rid_value[0], "MOENV acquisition rid[0]")
    if not _UUID_RE.fullmatch(rid):
        raise ValueError("MOENV acquisition rid must be a lowercase UUID")
    if rid != MOENV_RESOURCE_RID:
        raise ValueError("MOENV acquisition rid does not identify the EMS_S_01 resource")
    return {"rid": [rid], "download_type": "json", "pid": pid}


def _validate_manifest_urls(value: object, context: str = "manifest") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_manifest_urls(item, f"{context}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_manifest_urls(item, f"{context}[{index}]")
    elif isinstance(value, str) and value.startswith(("https://", "http://")):
        _validated_https_url(value, context)


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
                f"MOENV snapshot root must be a non-symlink directory: {self.root}"
            ) from error
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise ValueError("MOENV snapshot root must be a directory")
        self._root_fd = descriptor
        return self

    def __exit__(self, *_args: object) -> None:
        if self._root_fd is not None:
            os.close(self._root_fd)
            self._root_fd = None

    def read(self, relative_path: object, *, maximum_bytes: int) -> bytes:
        if self._root_fd is None:
            raise RuntimeError("snapshot reader is not open")
        parts = _relative_parts(relative_path, "MOENV snapshot input path")
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
                        f"MOENV snapshot path must not traverse symlinks: {relative_path}"
                    ) from error
                if not stat.S_ISDIR(os.fstat(next_fd).st_mode):
                    os.close(next_fd)
                    raise ValueError(
                        f"MOENV snapshot path component is not a directory: {relative_path}"
                    )
                os.close(directory_fd)
                directory_fd = next_fd

            leaf_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            leaf_flags |= getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
            try:
                descriptor = os.open(parts[-1], leaf_flags, dir_fd=directory_fd)
            except OSError as error:
                raise ValueError(
                    f"MOENV snapshot input is missing or unsafe: {relative_path}"
                ) from error
            try:
                before = os.fstat(descriptor)
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError(
                        f"MOENV snapshot input must be a regular file: {relative_path}"
                    )
                if before.st_size < 0 or before.st_size > maximum_bytes:
                    raise ValueError(
                        f"MOENV snapshot input exceeds its byte limit: {relative_path}"
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
                            f"MOENV snapshot input exceeds its byte limit: {relative_path}"
                        )
                after = os.fstat(descriptor)
                before_identity = (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                )
                after_identity = (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                )
                raw = b"".join(chunks)
                if before_identity != after_identity or len(raw) != before.st_size:
                    raise ValueError(
                        f"MOENV snapshot input changed while being read: {relative_path}"
                    )
                return raw
            finally:
                os.close(descriptor)
        finally:
            os.close(directory_fd)


def _tree_inventory(root: Path) -> tuple[set[str], set[str]]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("MOENV snapshot root must be a non-symlink directory")
    directories: set[str] = set()
    files: set[str] = set()
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as error:
            raise ValueError("MOENV snapshot tree is not readable") from error
        for entry in entries:
            relative = Path(entry.path).relative_to(root).as_posix()
            if entry.is_symlink():
                raise ValueError(f"MOENV snapshot tree contains a symlink: {relative}")
            try:
                details = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise ValueError(f"MOENV snapshot tree entry is unsafe: {relative}") from error
            if stat.S_ISDIR(details.st_mode):
                directories.add(relative)
                pending.append(Path(entry.path))
            elif stat.S_ISREG(details.st_mode):
                files.add(relative)
            else:
                raise ValueError(
                    f"MOENV snapshot tree entry must be a regular file or directory: {relative}"
                )
    return directories, files


def _validate_exact_tree(root: Path, raw_path: str) -> None:
    expected_directories = {"raw", "raw/sha256"}
    expected_files = {"manifest.json", MOENV_CANDIDATE_FILENAME, raw_path}
    directories, files = _tree_inventory(root)
    if directories != expected_directories or files != expected_files:
        raise ValueError(
            "MOENV snapshot tree has extra or missing entries "
            f"(directories={sorted(directories)!r}, files={sorted(files)!r})"
        )


def _variant_rows(scan: MOENVArchiveScan) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    facilities = tuple(scan.facilities)
    if len(facilities) != scan.facility_count:
        raise ValueError("MOENV scan facility count disagrees with facilities")
    seen_emsnos: set[str] = set()
    for facility in facilities:
        emsno = _required_text(facility.emsno, "MOENV facility emsno")
        if emsno in seen_emsnos:
            raise ValueError(f"MOENV scan repeats facility {emsno}")
        seen_emsnos.add(emsno)
        for variant in facility.variants:
            row = variant.row
            if not isinstance(row, dict) or tuple(row) != tuple(MOENV_FIELDS):
                raise ValueError("MOENV scan variant lacks the exact source fields")
            if any(
                (
                    not isinstance(row[field], str)
                    if field not in {"admino", "facno"}
                    else not isinstance(row[field], str) and row[field] is not None
                )
                for field in MOENV_FIELDS
            ):
                raise ValueError(
                    "MOENV scan variant violates the source field nullability contract"
                )
            rows.append(row)
    if len(rows) != scan.candidate_count:
        raise ValueError("MOENV scan candidate count disagrees with retained variants")
    return tuple(rows)


def _row_value(row: dict[str, object], requested: str) -> str:
    matches = [value for key, value in row.items() if key.casefold() == requested.casefold()]
    if len(matches) != 1:
        raise ValueError(f"MOENV source schema lacks one unambiguous {requested} field")
    value = matches[0]
    if not isinstance(value, str):
        raise ValueError(f"MOENV source field {requested} must be a string")
    return value


def _summary(scan: MOENVArchiveScan) -> dict[str, object]:
    rows = _variant_rows(scan)
    code_counts = {code: 0 for code in sorted(MOENV_INDUSTRY_LABELS)}
    for row in rows:
        industry_id = _row_value(row, "industryid")
        if industry_id not in code_counts:
            raise ValueError(
                f"MOENV candidate contains unexpected exact IndustryID {industry_id!r}"
            )
        code_counts[industry_id] += 1
    return {
        "upstream_row_count": scan.row_count,
        "industry_group_row_count": scan.industry_group_row_count,
        "raw_matching_row_count": scan.candidate_row_count,
        "deduplicated_variant_count": scan.candidate_count,
        "facility_count": scan.facility_count,
        "conflicting_facility_count": scan.conflicting_facility_count,
        "exact_industry_code_variant_counts": code_counts,
        "current_regulation_facility_count": scan.current_regulation_count,
        "valid_coordinate_facility_count": scan.valid_coordinate_count,
    }


def _rights() -> dict[str, object]:
    return {
        "reviewed_at": MOENV_RIGHTS_REVIEWED_AT,
        "decision": "pass_with_required_attribution",
        "access_level": "public",
        "license": MOENV_LICENSE,
        "license_spdx": MOENV_LICENSE_SPDX,
        "license_url": MOENV_LICENSE_URL,
        "attribution": MOENV_ATTRIBUTION,
        "no_endorsement": True,
        "no_warranty": True,
    }


def _input_rights() -> dict[str, object]:
    return {
        "access_level": "public",
        "license": MOENV_LICENSE,
        "license_spdx": MOENV_LICENSE_SPDX,
        "license_url": MOENV_LICENSE_URL,
        "attribution": MOENV_ATTRIBUTION,
    }


def _manifest(
    *,
    scan: MOENVArchiveScan,
    candidate_raw: bytes,
    retrieved_at: str,
    retrieval_timestamp_basis: str,
    dataset_updated_at: str,
    dataset_updated_at_basis: str,
    download_url: str,
    acquisition_request_body: dict[str, object],
    upstream_etag: str | None,
    upstream_last_modified: str | None,
) -> dict[str, object]:
    raw_path = f"raw/sha256/{scan.archive_sha256}.zip"
    summary = _summary(scan)
    candidate_digest = hashlib.sha256(candidate_raw).hexdigest()
    return {
        "format": SOURCE_SNAPSHOT_FORMAT,
        "retrieved_at": retrieved_at,
        "retrieval_timestamp_basis": retrieval_timestamp_basis,
        "source_scopes": {
            MOENV_SCOPE: {
                "complete": True,
                "coverage": MOENV_COVERAGE,
                "dataset_id": "EMS_S_01",
                "dataset_url": MOENV_DATASET_URL,
                "dataset_updated_at": dataset_updated_at,
                "dataset_updated_at_basis": dataset_updated_at_basis,
                "acquisition": {
                    "method": "POST",
                    "url": download_url,
                    "request_body": acquisition_request_body,
                    "response_artifact": "full_package_json_zip",
                    "credential_retained": False,
                },
                "api": {
                    "endpoint_url": MOENV_API_ENDPOINT_URL,
                    "documentation_url": MOENV_API_DOCUMENTATION_URL,
                    "guide_url": MOENV_API_GUIDE_URL,
                    "live_api_requires_member_key": True,
                    "credential_retained": False,
                },
                "update_cadence": MOENV_UPDATE_CADENCE,
                "filter": {
                    "version": MOENV_FILTER_VERSION,
                    "industry_group": MOENV_INDUSTRY_GROUP,
                    "exact_industry_codes": {
                        code: MOENV_INDUSTRY_LABELS[code]
                        for code in sorted(MOENV_INDUSTRY_LABELS)
                    },
                },
                "upstream_archive": {
                    "download_url": download_url,
                    "sha256": scan.archive_sha256,
                    "bytes": scan.archive_bytes,
                    "content_type": "application/zip",
                    "etag": upstream_etag,
                    "last_modified": upstream_last_modified,
                },
                "upstream_member": {
                    "path": scan.json_member,
                    "sha256": scan.json_sha256,
                    "md5": scan.json_md5,
                    "publisher_md5": scan.publisher_md5,
                    "bytes": scan.json_bytes,
                    "crc32": scan.json_crc32,
                    "compressed_bytes": scan.json_compressed_bytes,
                },
                "publisher_checksum_member": {
                    "path": scan.hash_member,
                    "sha256": scan.hash_member_sha256,
                    "bytes": scan.hash_member_bytes,
                    "crc32": scan.hash_member_crc32,
                    "compressed_bytes": scan.hash_member_compressed_bytes,
                },
                "summary": summary,
                "raw_retention": {
                    "retained_in_snapshot": True,
                    "blob_locator": raw_path,
                    "sha256": scan.archive_sha256,
                    "bytes": scan.archive_bytes,
                    "local_archive_is_canonical_acquisition_input": True,
                },
                "completeness": {
                    "exact_filter_complete_within_archived_package": True,
                    "national_semiconductor_facility_census_complete": False,
                    "source_assertion_interval_closure": {
                        "allowed_after_verified_full_same_filter_refresh": True,
                        "meaning": "absence_closes_only_the_prior_source_assertion_interval",
                    },
                    "real_world_facility_closure_or_inactivity_from_absence": False,
                },
                "limitations": list(_LIMITATIONS),
                "rights": _rights(),
            }
        },
        "inputs": [
            {
                "path": MOENV_CANDIDATE_FILENAME,
                "record_type": MOENV_CANDIDATE_RECORD_TYPE,
                "url": MOENV_DATASET_URL,
                "artifact_kind": "deterministic_filtered_derivative",
                "filter_version": MOENV_FILTER_VERSION,
                "record_count": scan.candidate_count,
                "facility_count": scan.facility_count,
                "bytes": len(candidate_raw),
                "sha256": candidate_digest,
                "content_type": "application/x-ndjson",
                **_input_rights(),
            },
            {
                "path": raw_path,
                "record_type": MOENV_RAW_RECORD_TYPE,
                "url": download_url,
                "artifact_kind": "upstream_official_full_package_archive",
                "bytes": scan.archive_bytes,
                "sha256": scan.archive_sha256,
                "content_type": "application/zip",
                "etag": upstream_etag,
                "last_modified": upstream_last_modified,
                **_input_rights(),
            },
        ],
    }


def _copy_regular_file(source: str | Path, destination: Path) -> tuple[str, int]:
    candidate = Path(source).absolute()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError as error:
        raise ValueError(f"MOENV archive is missing or unsafe: {candidate}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("MOENV archive must be a regular file")
        if before.st_size <= 0 or before.st_size > MOENV_MAX_ARCHIVE_BYTES:
            raise ValueError("MOENV archive is empty or exceeds the byte limit")
        digest = hashlib.sha256()
        size = 0
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            while True:
                chunk = os.read(descriptor, _READ_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > MOENV_MAX_ARCHIVE_BYTES:
                    raise ValueError("MOENV archive exceeds the byte limit")
                digest.update(chunk)
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        after = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if before_identity != after_identity or size != before.st_size:
            raise ValueError("MOENV archive changed while being copied")
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


def verify_moenv_snapshot(root: str | Path) -> VerifiedMOENVSnapshot:
    """Rehash, rescan, and replay one EMS_S_01 snapshot without network access."""

    with _SnapshotReader(root) as reader:
        manifest_raw = reader.read("manifest.json", maximum_bytes=MOENV_MAX_MANIFEST_BYTES)
        manifest = _load_json_object(manifest_raw, "MOENV snapshot manifest")
        if manifest_raw != _canonical_json_bytes(manifest):
            raise ValueError("MOENV snapshot manifest is not canonical JSON")
        _validate_manifest_urls(manifest)
        _validate_exact_keys(
            manifest,
            {
                "format",
                "retrieved_at",
                "retrieval_timestamp_basis",
                "source_scopes",
                "inputs",
            },
            "MOENV snapshot manifest",
        )
        if manifest["format"] != SOURCE_SNAPSHOT_FORMAT:
            raise ValueError("MOENV snapshot has an unsupported manifest format")
        retrieved_at = _canonical_utc_timestamp(
            manifest["retrieved_at"], "MOENV snapshot retrieved_at"
        )
        retrieval_timestamp_basis = _required_text(
            manifest["retrieval_timestamp_basis"],
            "MOENV snapshot retrieval_timestamp_basis",
        )
        if retrieval_timestamp_basis not in MOENV_RETRIEVAL_TIMESTAMP_BASES:
            raise ValueError("MOENV snapshot has an invalid retrieval timestamp basis")

        source_scopes = manifest["source_scopes"]
        if not isinstance(source_scopes, dict) or set(source_scopes) != {MOENV_SCOPE}:
            raise ValueError("MOENV snapshot must contain exactly one EMS_S_01 scope")
        scope = source_scopes[MOENV_SCOPE]
        if not isinstance(scope, dict):
            raise ValueError("MOENV EMS_S_01 scope must be an object")
        _validate_exact_keys(
            scope,
            {
                "complete",
                "coverage",
                "dataset_id",
                "dataset_url",
                "dataset_updated_at",
                "dataset_updated_at_basis",
                "acquisition",
                "api",
                "update_cadence",
                "filter",
                "upstream_archive",
                "upstream_member",
                "publisher_checksum_member",
                "summary",
                "raw_retention",
                "completeness",
                "limitations",
                "rights",
            },
            "MOENV EMS_S_01 scope",
        )
        if scope["complete"] is not True or scope["coverage"] != MOENV_COVERAGE:
            raise ValueError("MOENV scope must declare exact archived-package coverage")
        if scope["dataset_id"] != "EMS_S_01" or scope["dataset_url"] != MOENV_DATASET_URL:
            raise ValueError("MOENV scope has invalid dataset identity")
        dataset_updated_at = _canonical_utc_timestamp(
            scope["dataset_updated_at"], "MOENV dataset_updated_at"
        )
        dataset_updated_at_basis = _required_text(
            scope["dataset_updated_at_basis"], "MOENV dataset_updated_at_basis"
        )
        if dataset_updated_at_basis not in MOENV_DATASET_UPDATED_AT_BASES:
            raise ValueError("MOENV dataset update timestamp has an invalid basis")
        if _timestamp_clock(dataset_updated_at) > _timestamp_clock(retrieved_at):
            raise ValueError("MOENV dataset update timestamp is later than retrieval")
        if scope["update_cadence"] != MOENV_UPDATE_CADENCE:
            raise ValueError("MOENV scope update cadence has drifted")

        acquisition = scope["acquisition"]
        if not isinstance(acquisition, dict):
            raise ValueError("MOENV acquisition metadata must be an object")
        _validate_exact_keys(
            acquisition,
            {"method", "url", "request_body", "response_artifact", "credential_retained"},
            "MOENV acquisition metadata",
        )
        if (
            acquisition["method"] != "POST"
            or acquisition["response_artifact"] != "full_package_json_zip"
            or acquisition["credential_retained"] is not False
        ):
            raise ValueError("MOENV acquisition metadata has drifted")
        acquisition_url = _validated_download_url(
            acquisition["url"], "MOENV acquisition URL"
        )
        acquisition_request_body = _validated_acquisition_body(
            acquisition["request_body"]
        )

        expected_api = {
            "endpoint_url": MOENV_API_ENDPOINT_URL,
            "documentation_url": MOENV_API_DOCUMENTATION_URL,
            "guide_url": MOENV_API_GUIDE_URL,
            "live_api_requires_member_key": True,
            "credential_retained": False,
        }
        if scope["api"] != expected_api:
            raise ValueError("MOENV API metadata has drifted or retains a credential")
        expected_filter = {
            "version": MOENV_FILTER_VERSION,
            "industry_group": MOENV_INDUSTRY_GROUP,
            "exact_industry_codes": {
                code: MOENV_INDUSTRY_LABELS[code]
                for code in sorted(MOENV_INDUSTRY_LABELS)
            },
        }
        if scope["filter"] != expected_filter:
            raise ValueError("MOENV exact industry filter metadata has drifted")
        if scope["rights"] != _rights():
            raise ValueError("MOENV rights metadata has drifted")
        if scope["limitations"] != list(_LIMITATIONS):
            raise ValueError("MOENV limitations metadata has drifted")
        expected_completeness = {
            "exact_filter_complete_within_archived_package": True,
            "national_semiconductor_facility_census_complete": False,
            "source_assertion_interval_closure": {
                "allowed_after_verified_full_same_filter_refresh": True,
                "meaning": "absence_closes_only_the_prior_source_assertion_interval",
            },
            "real_world_facility_closure_or_inactivity_from_absence": False,
        }
        if scope["completeness"] != expected_completeness:
            raise ValueError("MOENV completeness metadata has drifted")

        upstream_archive = scope["upstream_archive"]
        if not isinstance(upstream_archive, dict):
            raise ValueError("MOENV upstream archive metadata must be an object")
        _validate_exact_keys(
            upstream_archive,
            {"download_url", "sha256", "bytes", "content_type", "etag", "last_modified"},
            "MOENV upstream archive metadata",
        )
        download_url = _validated_download_url(
            upstream_archive["download_url"], "MOENV upstream download_url"
        )
        if acquisition_url != download_url:
            raise ValueError("MOENV acquisition URL disagrees with upstream archive URL")
        raw_digest = _sha256(upstream_archive["sha256"], "MOENV archive sha256")
        raw_size = _integer(
            upstream_archive["bytes"],
            "MOENV archive bytes",
            minimum=1,
            maximum=MOENV_MAX_ARCHIVE_BYTES,
        )
        if upstream_archive["content_type"] != "application/zip":
            raise ValueError("MOENV upstream archive must declare application/zip")
        upstream_etag = _optional_text(upstream_archive["etag"], "MOENV archive etag")
        upstream_last_modified = _optional_text(
            upstream_archive["last_modified"], "MOENV archive last_modified"
        )
        raw_path = f"raw/sha256/{raw_digest}.zip"

        member = scope["upstream_member"]
        if not isinstance(member, dict):
            raise ValueError("MOENV upstream member metadata must be an object")
        _validate_exact_keys(
            member,
            {
                "path",
                "sha256",
                "md5",
                "publisher_md5",
                "bytes",
                "crc32",
                "compressed_bytes",
            },
            "MOENV upstream member metadata",
        )
        member_name = _required_text(member["path"], "MOENV upstream member path")
        _relative_parts(member_name, "MOENV upstream member path")
        member_sha256 = _sha256(member["sha256"], "MOENV upstream member sha256")
        member_md5 = _md5(member["md5"], "MOENV upstream member md5")
        publisher_md5 = _md5(member["publisher_md5"], "MOENV publisher md5")
        member_size = _integer(member["bytes"], "MOENV upstream member bytes", minimum=1)
        _integer(member["crc32"], "MOENV upstream member crc32", maximum=0xFFFFFFFF)
        _integer(
            member["compressed_bytes"],
            "MOENV upstream member compressed bytes",
            minimum=1,
        )

        checksum_member = scope["publisher_checksum_member"]
        if not isinstance(checksum_member, dict):
            raise ValueError("MOENV publisher checksum member metadata must be an object")
        _validate_exact_keys(
            checksum_member,
            {"path", "sha256", "bytes", "crc32", "compressed_bytes"},
            "MOENV publisher checksum member metadata",
        )
        _relative_parts(
            checksum_member["path"], "MOENV publisher checksum member path"
        )
        _sha256(
            checksum_member["sha256"], "MOENV publisher checksum member sha256"
        )
        _integer(
            checksum_member["bytes"],
            "MOENV publisher checksum member bytes",
            minimum=1,
        )
        _integer(
            checksum_member["crc32"],
            "MOENV publisher checksum member crc32",
            maximum=0xFFFFFFFF,
        )
        _integer(
            checksum_member["compressed_bytes"],
            "MOENV publisher checksum member compressed bytes",
            minimum=1,
        )

        raw_retention = scope["raw_retention"]
        expected_retention = {
            "retained_in_snapshot": True,
            "blob_locator": raw_path,
            "sha256": raw_digest,
            "bytes": raw_size,
            "local_archive_is_canonical_acquisition_input": True,
        }
        if raw_retention != expected_retention:
            raise ValueError("MOENV raw retention metadata disagrees with the archive")

        inputs = manifest["inputs"]
        if not isinstance(inputs, list) or len(inputs) != 2:
            raise ValueError("MOENV snapshot requires exactly two inputs")
        candidate_entry, raw_entry = inputs
        if not isinstance(candidate_entry, dict) or not isinstance(raw_entry, dict):
            raise ValueError("MOENV snapshot inputs must be objects")
        _validate_exact_keys(
            candidate_entry,
            {
                "path",
                "record_type",
                "url",
                "artifact_kind",
                "filter_version",
                "record_count",
                "facility_count",
                "bytes",
                "sha256",
                "content_type",
                "access_level",
                "license",
                "license_spdx",
                "license_url",
                "attribution",
            },
            "MOENV candidate input",
        )
        expected_candidate_metadata = {
            "path": MOENV_CANDIDATE_FILENAME,
            "record_type": MOENV_CANDIDATE_RECORD_TYPE,
            "url": MOENV_DATASET_URL,
            "artifact_kind": "deterministic_filtered_derivative",
            "filter_version": MOENV_FILTER_VERSION,
            "content_type": "application/x-ndjson",
            **_input_rights(),
        }
        for key, expected in expected_candidate_metadata.items():
            if candidate_entry[key] != expected:
                raise ValueError(f"MOENV candidate input has invalid {key}")
        candidate_size = _integer(
            candidate_entry["bytes"],
            "MOENV candidate bytes",
            maximum=MOENV_MAX_CANDIDATE_BYTES,
        )
        candidate_digest = _sha256(candidate_entry["sha256"], "MOENV candidate sha256")

        _validate_exact_keys(
            raw_entry,
            {
                "path",
                "record_type",
                "url",
                "artifact_kind",
                "bytes",
                "sha256",
                "content_type",
                "etag",
                "last_modified",
                "access_level",
                "license",
                "license_spdx",
                "license_url",
                "attribution",
            },
            "MOENV raw input",
        )
        expected_raw_metadata = {
            "path": raw_path,
            "record_type": MOENV_RAW_RECORD_TYPE,
            "url": download_url,
            "artifact_kind": "upstream_official_full_package_archive",
            "bytes": raw_size,
            "sha256": raw_digest,
            "content_type": "application/zip",
            "etag": upstream_etag,
            "last_modified": upstream_last_modified,
            **_input_rights(),
        }
        if raw_entry != expected_raw_metadata:
            raise ValueError("MOENV raw input metadata disagrees with the archive")

        _validate_exact_tree(reader.root, raw_path)
        raw_bytes = reader.read(raw_path, maximum_bytes=MOENV_MAX_ARCHIVE_BYTES)
        if len(raw_bytes) != raw_size:
            raise ValueError("MOENV retained archive size mismatch")
        if hashlib.sha256(raw_bytes).hexdigest() != raw_digest:
            raise ValueError("MOENV retained archive hash mismatch")
        candidate_bytes = reader.read(
            MOENV_CANDIDATE_FILENAME, maximum_bytes=MOENV_MAX_CANDIDATE_BYTES
        )
        if len(candidate_bytes) != candidate_size:
            raise ValueError("MOENV candidate derivative size mismatch")
        if hashlib.sha256(candidate_bytes).hexdigest() != candidate_digest:
            raise ValueError("MOENV candidate derivative hash mismatch")

        scan = scan_moenv_archive(reader.root / raw_path)
        if scan.archive_sha256 != raw_digest or scan.archive_bytes != raw_size:
            raise ValueError("MOENV retained archive rescan identity disagrees")
        expected_member = {
            "path": scan.json_member,
            "sha256": scan.json_sha256,
            "md5": scan.json_md5,
            "publisher_md5": scan.publisher_md5,
            "bytes": scan.json_bytes,
            "crc32": scan.json_crc32,
            "compressed_bytes": scan.json_compressed_bytes,
        }
        if member != expected_member:
            raise ValueError("MOENV upstream member metadata disagrees with raw rescan")
        expected_checksum_member = {
            "path": scan.hash_member,
            "sha256": scan.hash_member_sha256,
            "bytes": scan.hash_member_bytes,
            "crc32": scan.hash_member_crc32,
            "compressed_bytes": scan.hash_member_compressed_bytes,
        }
        if checksum_member != expected_checksum_member:
            raise ValueError(
                "MOENV publisher checksum member metadata disagrees with raw rescan"
            )
        if member_size != scan.json_bytes:
            raise ValueError("MOENV upstream member size disagrees with raw rescan")
        expected_summary = _summary(scan)
        if scope["summary"] != expected_summary:
            raise ValueError("MOENV summary metadata disagrees with raw rescan")
        replayed_candidates = canonical_candidate_jsonl_bytes(scan)
        if candidate_bytes != replayed_candidates:
            raise ValueError("MOENV candidate derivative does not replay from raw archive")
        if candidate_entry["record_count"] != scan.candidate_count:
            raise ValueError("MOENV candidate input record count disagrees")
        if candidate_entry["facility_count"] != scan.facility_count:
            raise ValueError("MOENV candidate input facility count disagrees")

        code_counts = expected_summary["exact_industry_code_variant_counts"]
        if not isinstance(code_counts, dict):
            raise AssertionError("MOENV summary code counts must be an object")
        return VerifiedMOENVSnapshot(
            root=reader.root,
            manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
            manifest_size=len(manifest_raw),
            manifest_bytes=manifest_raw,
            retrieved_at=retrieved_at,
            retrieval_timestamp_basis=retrieval_timestamp_basis,
            dataset_updated_at=dataset_updated_at,
            dataset_updated_at_basis=dataset_updated_at_basis,
            acquisition=VerifiedMOENVAcquisition(
                method="POST",
                url=acquisition_url,
                resource_ids=tuple(acquisition_request_body["rid"]),
                download_type=str(acquisition_request_body["download_type"]),
                package_id=str(acquisition_request_body["pid"]),
                canonical_request_body=_canonical_compact_json_bytes(
                    acquisition_request_body
                ),
            ),
            download_url=download_url,
            raw_path=raw_path,
            raw_sha256=raw_digest,
            raw_size=raw_size,
            raw_bytes=raw_bytes,
            candidate_sha256=candidate_digest,
            candidate_size=candidate_size,
            candidate_bytes=candidate_bytes,
            member_name=member_name,
            member_sha256=member_sha256,
            member_md5=member_md5,
            publisher_md5=publisher_md5,
            row_count=scan.row_count,
            candidate_count=scan.candidate_count,
            facility_count=scan.facility_count,
            code_counts=tuple((key, code_counts[key]) for key in sorted(code_counts)),
            current_regulation_count=scan.current_regulation_count,
            valid_coordinate_count=scan.valid_coordinate_count,
            conflicting_facility_count=scan.conflicting_facility_count,
            scan=scan,
        )


def create_moenv_snapshot(
    archive_path: str | Path,
    output_dir: str | Path,
    *,
    retrieved_at: str,
    dataset_updated_at: str,
    dataset_updated_at_basis: str,
    download_url: str,
    acquisition_request_body: dict[str, object],
    retrieval_timestamp_basis: str = "operator_supplied_for_archived_bytes",
    upstream_etag: str | None = None,
    upstream_last_modified: str | None = None,
) -> VerifiedMOENVSnapshot:
    """Transform one operator-supplied full-package ZIP and install it atomically."""

    retrieved_at = _canonical_utc_timestamp(retrieved_at, "retrieved_at")
    dataset_updated_at = _canonical_utc_timestamp(
        dataset_updated_at, "dataset_updated_at"
    )
    dataset_updated_at_basis = _required_text(
        dataset_updated_at_basis, "dataset_updated_at_basis"
    )
    if dataset_updated_at_basis not in MOENV_DATASET_UPDATED_AT_BASES:
        raise ValueError("invalid MOENV dataset_updated_at_basis")
    if _timestamp_clock(dataset_updated_at) > _timestamp_clock(retrieved_at):
        raise ValueError("dataset_updated_at must not be later than retrieved_at")
    retrieval_timestamp_basis = _required_text(
        retrieval_timestamp_basis, "retrieval_timestamp_basis"
    )
    if retrieval_timestamp_basis not in MOENV_RETRIEVAL_TIMESTAMP_BASES:
        raise ValueError("invalid MOENV retrieval_timestamp_basis")
    download_url = _validated_download_url(download_url, "download_url")
    acquisition_request_body = _validated_acquisition_body(acquisition_request_body)
    upstream_etag = _optional_text(upstream_etag, "upstream_etag")
    upstream_last_modified = _optional_text(
        upstream_last_modified, "upstream_last_modified"
    )
    output = Path(output_dir).absolute()
    if not output.name or output.name in {".", ".."}:
        raise ValueError(f"unsafe MOENV snapshot output directory: {output}")
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"refusing to overwrite MOENV snapshot: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent))
    installed = False
    try:
        provisional = stage / "raw" / "sha256" / ".archive.part"
        copied_digest, copied_size = _copy_regular_file(archive_path, provisional)
        raw_path = stage / "raw" / "sha256" / f"{copied_digest}.zip"
        os.rename(provisional, raw_path)
        scan = scan_moenv_archive(raw_path)
        if scan.archive_sha256 != copied_digest or scan.archive_bytes != copied_size:
            raise ValueError("MOENV archive scan identity disagrees with retained bytes")
        candidate_raw = canonical_candidate_jsonl_bytes(scan)
        if not isinstance(candidate_raw, bytes):
            raise TypeError("canonical_candidate_jsonl_bytes must return bytes")
        if len(candidate_raw) > MOENV_MAX_CANDIDATE_BYTES:
            raise ValueError("MOENV candidate derivative exceeds the byte limit")
        _write_new(stage / MOENV_CANDIDATE_FILENAME, candidate_raw)
        manifest = _manifest(
            scan=scan,
            candidate_raw=candidate_raw,
            retrieved_at=retrieved_at,
            retrieval_timestamp_basis=retrieval_timestamp_basis,
            dataset_updated_at=dataset_updated_at,
            dataset_updated_at_basis=dataset_updated_at_basis,
            download_url=download_url,
            acquisition_request_body=acquisition_request_body,
            upstream_etag=upstream_etag,
            upstream_last_modified=upstream_last_modified,
        )
        _validate_manifest_urls(manifest)
        manifest_raw = _canonical_json_bytes(manifest)
        if len(manifest_raw) > MOENV_MAX_MANIFEST_BYTES:
            raise ValueError("MOENV snapshot manifest exceeds the byte limit")
        _write_new(stage / "manifest.json", manifest_raw)
        _fsync_directory(stage / "raw" / "sha256")
        _fsync_directory(stage / "raw")
        _fsync_directory(stage)
        verify_moenv_snapshot(stage)
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"refusing to overwrite MOENV snapshot: {output}")
        _rename_directory_no_replace(stage, output)
        installed = True
        _fsync_directory(output.parent)
        return verify_moenv_snapshot(output)
    finally:
        if not installed and stage.exists():
            shutil.rmtree(stage)


__all__ = [
    "MOENV_API_DOCUMENTATION_URL",
    "MOENV_API_ENDPOINT_URL",
    "MOENV_API_GUIDE_URL",
    "MOENV_ATTRIBUTION",
    "MOENV_CANDIDATE_FILENAME",
    "MOENV_CANDIDATE_RECORD_TYPE",
    "MOENV_COVERAGE",
    "MOENV_DATASET_UPDATED_AT_BASES",
    "MOENV_FULL_PACKAGE_URL",
    "MOENV_PACKAGE_PID",
    "MOENV_LICENSE_SPDX",
    "MOENV_RAW_RECORD_TYPE",
    "MOENV_RESOURCE_RID",
    "MOENV_RETRIEVAL_TIMESTAMP_BASES",
    "MOENV_SCOPE",
    "MOENV_UPDATE_CADENCE",
    "VerifiedMOENVSnapshot",
    "VerifiedMOENVAcquisition",
    "create_moenv_snapshot",
    "verify_moenv_snapshot",
]
