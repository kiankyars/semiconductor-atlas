"""Immutable, replay-verifiable Taiwan MOF BGMOPEN1 snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .adapters.taiwan_mof_tax_registry import (
    TAIWAN_MOF_ARCHIVE_URL,
    TAIWAN_MOF_ATTRIBUTION,
    TAIWAN_MOF_DATASET_ID,
    TAIWAN_MOF_DATASET_URL,
    TAIWAN_MOF_FILTER_VERSION,
    TAIWAN_MOF_LICENSE,
    TAIWAN_MOF_LICENSE_URL,
    TAIWAN_MOF_MAX_ALLOWLIST_BYTES,
    TAIWAN_MOF_MAX_ARCHIVE_BYTES,
    TAIWAN_MOF_PUBLIC_FIELDS,
    TAIWAN_MOF_RESOURCE_NAME,
    TaiwanMOFArchiveScan,
    canonical_matched_jsonl_bytes,
    canonical_ubn_allowlist_bytes,
    parse_ubn_allowlist_bytes,
    scan_taiwan_mof_archive,
)
from .gleif_snapshot import _rename_directory_no_replace
from .moenv_snapshot import MOENV_SCOPE, verify_moenv_snapshot
from .taiwan_factory_snapshot import (
    TAIWAN_FACTORY_SCOPE,
    verify_taiwan_factory_snapshot,
)


SOURCE_SNAPSHOT_FORMAT = "semiconductor-atlas-source-inputs-v1"
TAIWAN_MOF_SCOPE = "taiwan_mof_bgmopen1_allowlisted_active_tax_registrations"
TAIWAN_MOF_COVERAGE = (
    "exact_ubn_allowlist_lookup_across_one_complete_active_tax_registration_archive"
)
TAIWAN_MOF_ALLOWLIST_FILENAME = "taiwan-mof-ubn-allowlist.txt"
TAIWAN_MOF_MATCHED_FILENAME = "taiwan-mof-active-tax-registrations.jsonl"
TAIWAN_MOF_ALLOWLIST_RECORD_TYPE = "taiwan_mof_exact_ubn_allowlist"
TAIWAN_MOF_MATCHED_RECORD_TYPE = "taiwan_mof_active_tax_registration_matches"
TAIWAN_MOF_RAW_RECORD_TYPE = "taiwan_mof_bgmopen1_archive"
TAIWAN_MOF_UPDATE_CADENCE = "daily"
TAIWAN_MOF_LICENSE_SPDX = "OGDL-Taiwan-1.0"
TAIWAN_MOF_RIGHTS_REVIEWED_AT = "2026-07-20"
TAIWAN_MOF_ALLOWLIST_DERIVATION_VERSION = (
    "verified-moenv-and-factory-exact-ubn-union-v1"
)
TAIWAN_MOF_SOURCE_UPDATED_AT_BASIS = "http_last_modified_header"
TAIWAN_MOF_PUBLISHER_DATE_BASIS = "first_csv_data_row_address_column_dd_mon_yy"
TAIWAN_MOF_RETRIEVAL_TIMESTAMP_BASES = frozenset(
    {"operator_supplied_for_archived_bytes", "upstream_download_completion"}
)
TAIWAN_MOF_MAX_MATCHED_BYTES = 64 * 1024 * 1024
TAIWAN_MOF_MAX_MANIFEST_BYTES = 4 * 1024 * 1024

_READ_CHUNK_BYTES = 1024 * 1024
_MAX_RETAINED_HEADER_BYTES = 1024
_ALLOWED_ARCHIVE_CONTENT_TYPES = frozenset(
    {
        "application/octet-stream",
        "application/x-zip-compressed",
        "application/zip",
    }
)
_UBN_CHARS = frozenset("0123456789")
_TAIWAN_UTC_OFFSET = timedelta(hours=8)
_LIMITATIONS = (
    "dataset_contains_active_tax_registrations_only_not_complete_registration_history",
    "unified_business_number_identifies_a_tax_unit_not_necessarily_a_legal_company",
    "branch_tax_units_must_not_be_collapsed_into_head_office_or_parent_without_review",
    "tax_registration_does_not_establish_facility_ownership_or_observed_operation",
    "absence_does_not_invalidate_a_historical_identifier_or_prove_business_closure",
    "allowlist_is_semiconductor_candidate_scope_not_a_complete_industry_census",
)
_PRIVACY_OMITTED_FIELDS = ("資本額", "使用統一發票")


@dataclass(frozen=True, slots=True)
class TaiwanMOFExcludedValue:
    source_scope: str
    source_record_key: str
    value: str
    reason: str


@dataclass(frozen=True, slots=True)
class TaiwanMOFConflict:
    source_scope: str
    source_record_key: str
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TaiwanMOFSourceBinding:
    scope: str
    manifest_sha256: str
    raw_sha256: str
    candidate_sha256: str
    source_record_count: int
    source_variant_count: int
    accepted_value_count: int


@dataclass(frozen=True, slots=True)
class TaiwanMOFAllowlist:
    values: tuple[str, ...]
    raw_bytes: bytes
    sha256: str
    moenv: TaiwanMOFSourceBinding
    factory: TaiwanMOFSourceBinding
    blank_source_value_count: int
    malformed_values: tuple[TaiwanMOFExcludedValue, ...]
    conflicting_records: tuple[TaiwanMOFConflict, ...]
    conflicting_values: tuple[TaiwanMOFExcludedValue, ...]


@dataclass(frozen=True, slots=True)
class VerifiedTaiwanMOFSnapshot:
    root: Path
    manifest_sha256: str
    manifest_size: int
    manifest_bytes: bytes
    retrieved_at: str
    retrieval_timestamp_basis: str
    source_updated_at: str
    source_updated_at_basis: str
    publisher_date_raw: str
    publisher_date: str
    publisher_date_basis: str
    raw_path: str
    raw_sha256: str
    raw_size: int
    raw_bytes: bytes
    allowlist_sha256: str
    allowlist_size: int
    allowlist_bytes: bytes
    allowlist_values: tuple[str, ...]
    matched_sha256: str
    matched_size: int
    matched_bytes: bytes
    row_count: int
    business_row_count: int
    allowlist_count: int
    matched_count: int
    missing_count: int
    missing_business_numbers: tuple[str, ...]
    organization_type_counts: tuple[tuple[str, int], ...]
    allowlist_derivation: dict[str, object]
    scan: TaiwanMOFArchiveScan


@dataclass(frozen=True, slots=True)
class _SnapshotTreeIdentity:
    raw_path: str
    root_inode: tuple[int, int]
    directory_inodes: tuple[tuple[str, int, int], ...]
    file_identities: tuple[tuple[str, int, int, int, int, int], ...]


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


def _canonical_publisher_date(value: object) -> date:
    text = _required_text(value, "MOF publisher CSV date")
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as error:
        raise ValueError("MOF publisher CSV date is invalid") from error
    if parsed.isoformat() != text:
        raise ValueError("MOF publisher CSV date is not canonical")
    return parsed


def _validate_publisher_date_binding(
    publisher_date: date,
    *,
    source_updated_at: str,
    retrieved_at: str,
) -> None:
    source_clock = _timestamp_clock(source_updated_at)
    retrieval_clock = _timestamp_clock(retrieved_at)
    source_utc_date = source_clock.date()
    source_taiwan_date = (source_clock + _TAIWAN_UTC_OFFSET).date()
    retrieval_taiwan_date = (retrieval_clock + _TAIWAN_UTC_OFFSET).date()
    if publisher_date not in {source_utc_date, source_taiwan_date}:
        raise ValueError(
            "MOF publisher CSV date must match the UTC or Taiwan-local calendar "
            "date of HTTP Last-Modified"
        )
    if publisher_date > retrieval_taiwan_date:
        raise ValueError(
            "MOF publisher CSV date must not be later than the Taiwan-local "
            "retrieval date"
        )


def normalize_http_last_modified(value: object) -> str:
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
    if raw.split(";", 1)[0].strip().lower() not in _ALLOWED_ARCHIVE_CONTENT_TYPES:
        raise ValueError("HTTP Content-Type header does not identify a ZIP download")
    return raw


def _validate_exact_keys(
    value: dict[str, Any], expected: set[str], context: str
) -> None:
    if set(value) != expected:
        raise ValueError(
            f"{context} has missing keys {sorted(expected - set(value))!r} or "
            f"extra keys {sorted(set(value) - expected)!r}"
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


def _is_exact_ubn(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 8
        and set(value) <= _UBN_CHARS
        and value.isascii()
    )


def _snapshot_unchanged(before: object, after: object, label: str) -> None:
    fields = ("manifest_sha256", "raw_sha256", "candidate_sha256")
    if any(getattr(before, field) != getattr(after, field) for field in fields):
        raise ValueError(f"{label} snapshot changed while deriving the MOF allowlist")


def build_taiwan_mof_allowlist(
    moenv_snapshot_dir: str | Path,
    factory_snapshot_dir: str | Path,
) -> TaiwanMOFAllowlist:
    """Derive the exact UBN union from two fully verified accepted snapshots."""

    moenv = verify_moenv_snapshot(moenv_snapshot_dir)
    factory = verify_taiwan_factory_snapshot(factory_snapshot_dir)

    moenv_values: set[str] = set()
    factory_values: set[str] = set()
    malformed: list[TaiwanMOFExcludedValue] = []
    conflicts: list[TaiwanMOFConflict] = []
    conflicting_values: list[TaiwanMOFExcludedValue] = []
    blank_count = 0
    moenv_variant_count = 0

    for facility in moenv.scan.facilities:
        raw_values: set[str] = set()
        for variant in facility.variants:
            moenv_variant_count += 1
            value = variant.row["uniformno"]
            if not isinstance(value, str):
                raise ValueError("verified MOENV uniformno must be text")
            if not value:
                blank_count += 1
            else:
                raw_values.add(value)
        ordered_values = tuple(sorted(raw_values))
        if len(ordered_values) > 1:
            conflicts.append(
                TaiwanMOFConflict(MOENV_SCOPE, facility.emsno, ordered_values)
            )
            for value in ordered_values:
                conflicting_values.append(
                    TaiwanMOFExcludedValue(
                        MOENV_SCOPE,
                        facility.emsno,
                        value,
                        "conflicting_nonblank_values_for_one_source_record",
                    )
                )
                if not _is_exact_ubn(value):
                    malformed.append(
                        TaiwanMOFExcludedValue(
                            MOENV_SCOPE,
                            facility.emsno,
                            value,
                            "not_exactly_eight_ascii_digits",
                        )
                    )
            continue
        if not ordered_values:
            continue
        value = ordered_values[0]
        if _is_exact_ubn(value):
            moenv_values.add(value)
        else:
            malformed.append(
                TaiwanMOFExcludedValue(
                    MOENV_SCOPE,
                    facility.emsno,
                    value,
                    "not_exactly_eight_ascii_digits",
                )
            )

    for record in factory.scan.records:
        value = record.row["unified_business_number"]
        if not _is_exact_ubn(value):
            raise ValueError("verified factory snapshot contains a noncanonical UBN")
        factory_values.add(str(value))

    values = tuple(sorted(moenv_values | factory_values))
    raw = canonical_ubn_allowlist_bytes(list(values))
    result = TaiwanMOFAllowlist(
        values=values,
        raw_bytes=raw,
        sha256=hashlib.sha256(raw).hexdigest(),
        moenv=TaiwanMOFSourceBinding(
            scope=MOENV_SCOPE,
            manifest_sha256=moenv.manifest_sha256,
            raw_sha256=moenv.raw_sha256,
            candidate_sha256=moenv.candidate_sha256,
            source_record_count=moenv.facility_count,
            source_variant_count=moenv_variant_count,
            accepted_value_count=len(moenv_values),
        ),
        factory=TaiwanMOFSourceBinding(
            scope=TAIWAN_FACTORY_SCOPE,
            manifest_sha256=factory.manifest_sha256,
            raw_sha256=factory.raw_sha256,
            candidate_sha256=factory.candidate_sha256,
            source_record_count=factory.candidate_count,
            source_variant_count=factory.candidate_count,
            accepted_value_count=len(factory_values),
        ),
        blank_source_value_count=blank_count,
        malformed_values=tuple(
            sorted(
                malformed,
                key=lambda item: (
                    item.source_scope,
                    item.source_record_key,
                    item.value,
                ),
            )
        ),
        conflicting_records=tuple(
            sorted(
                conflicts, key=lambda item: (item.source_scope, item.source_record_key)
            )
        ),
        conflicting_values=tuple(
            sorted(
                conflicting_values,
                key=lambda item: (
                    item.source_scope,
                    item.source_record_key,
                    item.value,
                ),
            )
        ),
    )

    refreshed_moenv = verify_moenv_snapshot(moenv_snapshot_dir)
    refreshed_factory = verify_taiwan_factory_snapshot(factory_snapshot_dir)
    _snapshot_unchanged(moenv, refreshed_moenv, "MOENV")
    _snapshot_unchanged(factory, refreshed_factory, "factory")
    return result


def _source_binding_payload(binding: TaiwanMOFSourceBinding) -> dict[str, object]:
    return {
        "scope": binding.scope,
        "manifest_sha256": binding.manifest_sha256,
        "raw_sha256": binding.raw_sha256,
        "candidate_sha256": binding.candidate_sha256,
        "source_record_count": binding.source_record_count,
        "source_variant_count": binding.source_variant_count,
        "accepted_value_count": binding.accepted_value_count,
    }


def _excluded_payload(value: TaiwanMOFExcludedValue) -> dict[str, object]:
    return {
        "source_scope": value.source_scope,
        "source_record_key": value.source_record_key,
        "value": value.value,
        "reason": value.reason,
    }


def _conflict_payload(value: TaiwanMOFConflict) -> dict[str, object]:
    return {
        "source_scope": value.source_scope,
        "source_record_key": value.source_record_key,
        "values": list(value.values),
    }


def _allowlist_derivation(allowlist: TaiwanMOFAllowlist) -> dict[str, object]:
    return {
        "version": TAIWAN_MOF_ALLOWLIST_DERIVATION_VERSION,
        "rules": {
            "accepted_value_shape": "exactly_eight_ascii_digits",
            "blank_values_included": False,
            "conflicting_source_record_values_included_from_that_record": False,
            "union_order": "sorted_unique_ascii",
        },
        "sources": {
            "moenv": _source_binding_payload(allowlist.moenv),
            "factory": _source_binding_payload(allowlist.factory),
        },
        "diagnostics": {
            "blank_source_value_count": allowlist.blank_source_value_count,
            "malformed_values": [
                _excluded_payload(value) for value in allowlist.malformed_values
            ],
            "conflicting_records": [
                _conflict_payload(value) for value in allowlist.conflicting_records
            ],
            "conflicting_values": [
                _excluded_payload(value) for value in allowlist.conflicting_values
            ],
        },
        "output": {
            "path": TAIWAN_MOF_ALLOWLIST_FILENAME,
            "record_count": len(allowlist.values),
            "bytes": len(allowlist.raw_bytes),
            "sha256": allowlist.sha256,
        },
    }


def _validate_source_binding(value: object, expected_scope: str, context: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    _validate_exact_keys(
        value,
        {
            "scope",
            "manifest_sha256",
            "raw_sha256",
            "candidate_sha256",
            "source_record_count",
            "source_variant_count",
            "accepted_value_count",
        },
        context,
    )
    if value["scope"] != expected_scope:
        raise ValueError(f"{context} source scope drifted")
    for key in ("manifest_sha256", "raw_sha256", "candidate_sha256"):
        _sha256(value[key], f"{context} {key}")
    record_count = _integer(value["source_record_count"], f"{context} record count")
    variant_count = _integer(value["source_variant_count"], f"{context} variant count")
    accepted_count = _integer(
        value["accepted_value_count"], f"{context} accepted count"
    )
    if variant_count < record_count or accepted_count > variant_count:
        raise ValueError(f"{context} counts are inconsistent")


def _validate_excluded_values(value: object, context: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    ordering: list[tuple[str, str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"{context}[{index}] must be an object")
        _validate_exact_keys(
            item,
            {"source_scope", "source_record_key", "value", "reason"},
            f"{context}[{index}]",
        )
        scope = _required_text(item["source_scope"], f"{context}[{index}] scope")
        key = _required_text(item["source_record_key"], f"{context}[{index}] key")
        raw = _required_text(item["value"], f"{context}[{index}] value")
        _required_text(item["reason"], f"{context}[{index}] reason")
        ordering.append((scope, key, raw))
    if ordering != sorted(ordering) or len(ordering) != len(set(ordering)):
        raise ValueError(f"{context} must be sorted and unique")


def _validate_conflicts(value: object, context: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    ordering: list[tuple[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"{context}[{index}] must be an object")
        _validate_exact_keys(
            item,
            {"source_scope", "source_record_key", "values"},
            f"{context}[{index}]",
        )
        scope = _required_text(item["source_scope"], f"{context}[{index}] scope")
        key = _required_text(item["source_record_key"], f"{context}[{index}] key")
        values = item["values"]
        if (
            not isinstance(values, list)
            or len(values) < 2
            or any(not isinstance(raw, str) or not raw for raw in values)
            or values != sorted(set(values))
        ):
            raise ValueError(f"{context}[{index}] values must be sorted unique strings")
        ordering.append((scope, key))
    if ordering != sorted(ordering) or len(ordering) != len(set(ordering)):
        raise ValueError(f"{context} must be sorted and unique")


def _validate_allowlist_derivation(
    value: object,
    *,
    allowlist_count: int,
    allowlist_size: int,
    allowlist_sha256: str,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("MOF allowlist derivation must be an object")
    _validate_exact_keys(
        value,
        {"version", "rules", "sources", "diagnostics", "output"},
        "MOF allowlist derivation",
    )
    if value["version"] != TAIWAN_MOF_ALLOWLIST_DERIVATION_VERSION:
        raise ValueError("MOF allowlist derivation version drifted")
    expected_rules = {
        "accepted_value_shape": "exactly_eight_ascii_digits",
        "blank_values_included": False,
        "conflicting_source_record_values_included_from_that_record": False,
        "union_order": "sorted_unique_ascii",
    }
    if value["rules"] != expected_rules:
        raise ValueError("MOF allowlist derivation rules drifted")
    sources = value["sources"]
    if not isinstance(sources, dict) or set(sources) != {"moenv", "factory"}:
        raise ValueError("MOF allowlist derivation sources drifted")
    _validate_source_binding(sources["moenv"], MOENV_SCOPE, "MOF MOENV binding")
    _validate_source_binding(
        sources["factory"], TAIWAN_FACTORY_SCOPE, "MOF factory binding"
    )
    diagnostics = value["diagnostics"]
    if not isinstance(diagnostics, dict):
        raise ValueError("MOF allowlist diagnostics must be an object")
    _validate_exact_keys(
        diagnostics,
        {
            "blank_source_value_count",
            "malformed_values",
            "conflicting_records",
            "conflicting_values",
        },
        "MOF allowlist diagnostics",
    )
    _integer(diagnostics["blank_source_value_count"], "MOF blank source value count")
    _validate_excluded_values(diagnostics["malformed_values"], "MOF malformed values")
    _validate_conflicts(diagnostics["conflicting_records"], "MOF conflicting records")
    _validate_excluded_values(
        diagnostics["conflicting_values"], "MOF conflicting values"
    )
    expected_output = {
        "path": TAIWAN_MOF_ALLOWLIST_FILENAME,
        "record_count": allowlist_count,
        "bytes": allowlist_size,
        "sha256": allowlist_sha256,
    }
    if value["output"] != expected_output:
        raise ValueError("MOF allowlist derivation output disagrees with artifact")
    return value


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
                "MOF snapshot root must be a non-symlink directory"
            ) from error
        details = os.fstat(descriptor)
        if not stat.S_ISDIR(details.st_mode):
            os.close(descriptor)
            raise ValueError("MOF snapshot root must be a directory")
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
            raise ValueError("MOF snapshot root changed during verification") from error
        if (
            not stat.S_ISDIR(named.st_mode)
            or _identity(opened) != self._root_identity
            or _identity(named) != self._root_identity
        ):
            raise ValueError("MOF snapshot root changed during verification")

    def read(self, relative_path: object, *, maximum_bytes: int) -> bytes:
        if self._root_fd is None:
            raise RuntimeError("snapshot reader is not open")
        parts = _relative_parts(relative_path, "MOF snapshot input path")
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
                        f"MOF snapshot path must not traverse symlinks: {relative_path}"
                    ) from error
                if not stat.S_ISDIR(os.fstat(next_fd).st_mode):
                    os.close(next_fd)
                    raise ValueError(
                        f"MOF snapshot path component is not a directory: {relative_path}"
                    )
                os.close(directory_fd)
                directory_fd = next_fd
            leaf_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            leaf_flags |= getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
            try:
                descriptor = os.open(parts[-1], leaf_flags, dir_fd=directory_fd)
            except OSError as error:
                raise ValueError(
                    f"MOF snapshot input is missing or unsafe: {relative_path}"
                ) from error
            try:
                before = os.fstat(descriptor)
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError(
                        f"MOF snapshot input must be a regular file: {relative_path}"
                    )
                if before.st_size < 0 or before.st_size > maximum_bytes:
                    raise ValueError(
                        f"MOF snapshot input exceeds its byte limit: {relative_path}"
                    )
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = os.read(
                        descriptor, min(_READ_CHUNK_BYTES, maximum_bytes + 1 - total)
                    )
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > maximum_bytes:
                        raise ValueError(
                            f"MOF snapshot input exceeds its byte limit: {relative_path}"
                        )
                after = os.fstat(descriptor)
                raw = b"".join(chunks)
                if _identity(before) != _identity(after) or len(raw) != before.st_size:
                    raise ValueError(
                        f"MOF snapshot input changed while being read: {relative_path}"
                    )
                return raw
            finally:
                os.close(descriptor)
        finally:
            os.close(directory_fd)


def _tree_inventory(root: Path) -> tuple[set[str], set[str]]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("MOF snapshot root must be a non-symlink directory")
    directories: set[str] = set()
    files: set[str] = set()
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as error:
            raise ValueError("MOF snapshot tree is not readable") from error
        for entry in entries:
            relative = Path(entry.path).relative_to(root).as_posix()
            if entry.is_symlink():
                raise ValueError(f"MOF snapshot tree contains a symlink: {relative}")
            details = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(details.st_mode):
                directories.add(relative)
                pending.append(Path(entry.path))
            elif stat.S_ISREG(details.st_mode):
                files.add(relative)
            else:
                raise ValueError(
                    f"MOF snapshot tree entry must be a regular file or directory: {relative}"
                )
    return directories, files


def _validate_exact_tree(root: Path, raw_path: str) -> None:
    directories, files = _tree_inventory(root)
    expected_directories = {"raw", "raw/sha256"}
    expected_files = {
        "manifest.json",
        TAIWAN_MOF_ALLOWLIST_FILENAME,
        TAIWAN_MOF_MATCHED_FILENAME,
        raw_path,
    }
    if directories != expected_directories or files != expected_files:
        raise ValueError("MOF snapshot tree has extra or missing entries")


def _capture_snapshot_tree_identity(root: Path, raw_path: str) -> _SnapshotTreeIdentity:
    """Capture the exact verified tree without re-reading large file contents."""

    root = root.absolute()
    _validate_exact_tree(root, raw_path)
    try:
        root_metadata = os.stat(root, follow_symlinks=False)
    except OSError as error:
        raise ValueError("MOF snapshot root is not readable") from error
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise ValueError("MOF snapshot root must be a non-symlink directory")

    directory_inodes: list[tuple[str, int, int]] = []
    for relative_path in ("raw", "raw/sha256"):
        metadata = os.stat(root / relative_path, follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("MOF snapshot tree directory identity changed")
        directory_inodes.append((relative_path, metadata.st_dev, metadata.st_ino))

    file_identities: list[tuple[str, int, int, int, int, int]] = []
    for relative_path in (
        "manifest.json",
        TAIWAN_MOF_ALLOWLIST_FILENAME,
        TAIWAN_MOF_MATCHED_FILENAME,
        raw_path,
    ):
        metadata = os.stat(root / relative_path, follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("MOF snapshot tree file identity changed")
        file_identities.append((relative_path, *_identity(metadata)))

    _validate_exact_tree(root, raw_path)
    refreshed_root = os.stat(root, follow_symlinks=False)
    root_inode = (root_metadata.st_dev, root_metadata.st_ino)
    if not stat.S_ISDIR(refreshed_root.st_mode) or root_inode != (
        refreshed_root.st_dev,
        refreshed_root.st_ino,
    ):
        raise ValueError("MOF snapshot root changed while capturing tree identity")
    return _SnapshotTreeIdentity(
        raw_path=raw_path,
        root_inode=root_inode,
        directory_inodes=tuple(directory_inodes),
        file_identities=tuple(file_identities),
    )


def _assert_installed_tree_identity(
    root: Path, expected: _SnapshotTreeIdentity
) -> None:
    if _capture_snapshot_tree_identity(root, expected.raw_path) != expected:
        raise ValueError("installed MOF snapshot identity or tree changed")


def _rollback_failed_install(
    output: Path,
    stage: Path,
    expected: _SnapshotTreeIdentity,
) -> None:
    """Move only the just-installed inode back to staging for safe cleanup."""

    try:
        metadata = os.stat(output, follow_symlinks=False)
    except OSError as error:
        raise RuntimeError(
            f"MOF post-install rollback could not inspect target: {output}"
        ) from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != expected.root_inode
    ):
        raise RuntimeError(
            f"MOF post-install rollback refused a replaced target: {output}"
        )
    if stage.exists() or stage.is_symlink():
        raise RuntimeError(
            f"MOF post-install rollback staging path unexpectedly exists: {stage}"
        )
    _rename_directory_no_replace(output, stage)
    _fsync_directory(output.parent)


def _rights() -> dict[str, object]:
    return {
        "reviewed_at": TAIWAN_MOF_RIGHTS_REVIEWED_AT,
        "decision": "pass_with_required_attribution",
        "access_level": "public",
        "license": TAIWAN_MOF_LICENSE,
        "license_spdx": TAIWAN_MOF_LICENSE_SPDX,
        "license_url": TAIWAN_MOF_LICENSE_URL,
        "attribution": TAIWAN_MOF_ATTRIBUTION,
        "no_endorsement": True,
        "no_warranty": True,
    }


def _input_rights() -> dict[str, object]:
    return {
        "access_level": "public",
        "license": TAIWAN_MOF_LICENSE,
        "license_spdx": TAIWAN_MOF_LICENSE_SPDX,
        "license_url": TAIWAN_MOF_LICENSE_URL,
        "attribution": TAIWAN_MOF_ATTRIBUTION,
    }


def _privacy_policy() -> dict[str, object]:
    return {
        "scope": "exact_semiconductor_candidate_ubn_allowlist_only",
        "full_active_tax_roster_derivative_created": False,
        "retained_fields": list(TAIWAN_MOF_PUBLIC_FIELDS),
        "omitted_source_fields": list(_PRIVACY_OMITTED_FIELDS),
        "business_address_retained_for_identity_resolution": True,
        "sole_proprietor_records_are_not_promoted_to_legal_persons": True,
    }


def _summary(scan: TaiwanMOFArchiveScan) -> dict[str, object]:
    return {
        "upstream_row_count_after_header_including_publisher_date": scan.row_count,
        "active_tax_registration_row_count": scan.business_row_count,
        "distinct_unified_business_number_count": scan.distinct_business_number_count,
        "allowlist_count": scan.allowlist_count,
        "matched_count": scan.matched_count,
        "missing_count": scan.missing_count,
        "missing_unified_business_numbers": list(scan.missing_business_numbers),
        "matched_organization_type_counts": dict(scan.organization_type_counts),
    }


def _manifest(
    *,
    scan: TaiwanMOFArchiveScan,
    allowlist: TaiwanMOFAllowlist,
    matched_raw: bytes,
    retrieved_at: str,
    retrieval_timestamp_basis: str,
    source_updated_at: str,
    upstream_last_modified: str,
    upstream_etag: str | None,
    upstream_content_type: str | None,
    upstream_content_length: int | None,
) -> dict[str, object]:
    raw_path = f"raw/sha256/{scan.archive_sha256}.zip"
    matched_sha256 = hashlib.sha256(matched_raw).hexdigest()
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
            TAIWAN_MOF_SCOPE: {
                "complete": True,
                "coverage": TAIWAN_MOF_COVERAGE,
                "dataset_id": TAIWAN_MOF_DATASET_ID,
                "resource_name": TAIWAN_MOF_RESOURCE_NAME,
                "dataset_url": TAIWAN_MOF_DATASET_URL,
                "archive_url": TAIWAN_MOF_ARCHIVE_URL,
                "source_updated_at": source_updated_at,
                "source_updated_at_basis": TAIWAN_MOF_SOURCE_UPDATED_AT_BASIS,
                "publisher_csv_date_raw": scan.publisher_date_raw,
                "publisher_csv_date": scan.publisher_date,
                "publisher_csv_date_basis": TAIWAN_MOF_PUBLISHER_DATE_BASIS,
                "update_cadence": TAIWAN_MOF_UPDATE_CADENCE,
                "acquisition": {
                    "method": "GET",
                    "url": TAIWAN_MOF_ARCHIVE_URL,
                    "response_artifact": "bgmopen1_zip",
                    "request_body_retained": False,
                    "credential_retained": False,
                },
                "filter": {
                    "version": TAIWAN_MOF_FILTER_VERSION,
                    "source_field": "統一編號",
                    "match_rule": "exact_membership_in_pinned_sorted_ubn_allowlist",
                    "allowlist_path": TAIWAN_MOF_ALLOWLIST_FILENAME,
                    "allowlist_sha256": allowlist.sha256,
                    "allowlist_count": len(allowlist.values),
                },
                "allowlist_derivation": _allowlist_derivation(allowlist),
                "privacy": _privacy_policy(),
                "upstream_archive": {
                    "download_url": TAIWAN_MOF_ARCHIVE_URL,
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
                "summary": _summary(scan),
                "raw_retention": {
                    "retained_in_snapshot": True,
                    "blob_locator": raw_path,
                    "sha256": scan.archive_sha256,
                    "bytes": scan.archive_bytes,
                    "local_archive_is_canonical_acquisition_input": True,
                },
                "limitations": list(_LIMITATIONS),
                "rights": _rights(),
            }
        },
        "inputs": [
            {
                "path": TAIWAN_MOF_ALLOWLIST_FILENAME,
                "record_type": TAIWAN_MOF_ALLOWLIST_RECORD_TYPE,
                "artifact_kind": "deterministic_cross_snapshot_allowlist",
                "record_count": len(allowlist.values),
                "bytes": len(allowlist.raw_bytes),
                "sha256": allowlist.sha256,
                "content_type": "text/plain; charset=us-ascii",
            },
            {
                "path": TAIWAN_MOF_MATCHED_FILENAME,
                "record_type": TAIWAN_MOF_MATCHED_RECORD_TYPE,
                "url": TAIWAN_MOF_DATASET_URL,
                "artifact_kind": "deterministic_privacy_conscious_allowlist_derivative",
                "filter_version": TAIWAN_MOF_FILTER_VERSION,
                "record_count": scan.matched_count,
                "bytes": len(matched_raw),
                "sha256": matched_sha256,
                "content_type": "application/x-ndjson",
                **_input_rights(),
            },
            {
                "path": raw_path,
                "record_type": TAIWAN_MOF_RAW_RECORD_TYPE,
                "url": TAIWAN_MOF_ARCHIVE_URL,
                "artifact_kind": "upstream_official_active_tax_registration_archive",
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
        raise ValueError("MOF archive cannot be opened without symlink protection")
    flags = os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        named_before = os.stat(candidate, follow_symlinks=False)
        if not stat.S_ISREG(named_before.st_mode):
            raise ValueError("MOF archive must be a regular file")
        descriptor = os.open(candidate, flags)
    except ValueError:
        raise
    except OSError as error:
        raise ValueError(f"MOF archive is missing or unsafe: {candidate}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or _identity(before) != _identity(
            named_before
        ):
            raise ValueError("MOF archive path changed while it was being opened")
        if not 0 < before.st_size <= TAIWAN_MOF_MAX_ARCHIVE_BYTES:
            raise ValueError("MOF archive is empty or exceeds the 100 MB byte limit")
        digest = hashlib.sha256()
        size = 0
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            while True:
                chunk = os.read(descriptor, _READ_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > TAIWAN_MOF_MAX_ARCHIVE_BYTES:
                    raise ValueError("MOF archive exceeds the 100 MB byte limit")
                digest.update(chunk)
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        after = os.fstat(descriptor)
        named_after = os.stat(candidate, follow_symlinks=False)
        if (
            _identity(before) != _identity(after)
            or _identity(named_after) != _identity(after)
            or not stat.S_ISREG(named_after.st_mode)
            or size != before.st_size
        ):
            raise ValueError("MOF archive changed while being copied")
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


def _organization_counts(value: object) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, dict):
        raise ValueError("MOF organization type counts must be an object")
    result: list[tuple[str, int]] = []
    for key in sorted(value):
        organization_type = _required_text(key, "MOF organization type")
        count = _integer(value[key], f"MOF organization count {organization_type}")
        result.append((organization_type, count))
    return tuple(result)


def verify_taiwan_mof_snapshot(root: str | Path) -> VerifiedTaiwanMOFSnapshot:
    """Reopen, rehash, rescan, and replay one MOF snapshot offline."""

    with _SnapshotReader(root) as reader:
        manifest_raw = reader.read(
            "manifest.json", maximum_bytes=TAIWAN_MOF_MAX_MANIFEST_BYTES
        )
        manifest = _load_json_object(manifest_raw, "MOF snapshot manifest")
        if manifest_raw != _canonical_json_bytes(manifest):
            raise ValueError("MOF snapshot manifest is not canonical JSON")
        _validate_exact_keys(
            manifest,
            {
                "format",
                "retrieved_at",
                "retrieval_timestamp_basis",
                "source_scopes",
                "inputs",
            },
            "MOF snapshot manifest",
        )
        if manifest["format"] != SOURCE_SNAPSHOT_FORMAT:
            raise ValueError("MOF snapshot has an unsupported format")
        retrieved_at = _canonical_utc_timestamp(
            manifest["retrieved_at"], "MOF retrieved_at"
        )
        retrieval_basis = _required_text(
            manifest["retrieval_timestamp_basis"], "MOF retrieval_timestamp_basis"
        )
        if retrieval_basis not in TAIWAN_MOF_RETRIEVAL_TIMESTAMP_BASES:
            raise ValueError("MOF retrieval timestamp basis drifted")

        source_scopes = manifest["source_scopes"]
        if not isinstance(source_scopes, dict) or set(source_scopes) != {
            TAIWAN_MOF_SCOPE
        }:
            raise ValueError("MOF snapshot must contain exactly one source scope")
        scope = source_scopes[TAIWAN_MOF_SCOPE]
        if not isinstance(scope, dict):
            raise ValueError("MOF source scope must be an object")
        _validate_exact_keys(
            scope,
            {
                "complete",
                "coverage",
                "dataset_id",
                "resource_name",
                "dataset_url",
                "archive_url",
                "source_updated_at",
                "source_updated_at_basis",
                "publisher_csv_date_raw",
                "publisher_csv_date",
                "publisher_csv_date_basis",
                "update_cadence",
                "acquisition",
                "filter",
                "allowlist_derivation",
                "privacy",
                "upstream_archive",
                "upstream_member",
                "summary",
                "raw_retention",
                "limitations",
                "rights",
            },
            "MOF source scope",
        )
        if scope["complete"] is not True or scope["coverage"] != TAIWAN_MOF_COVERAGE:
            raise ValueError("MOF coverage metadata drifted")
        identity = {
            "dataset_id": TAIWAN_MOF_DATASET_ID,
            "resource_name": TAIWAN_MOF_RESOURCE_NAME,
            "dataset_url": TAIWAN_MOF_DATASET_URL,
            "archive_url": TAIWAN_MOF_ARCHIVE_URL,
            "update_cadence": TAIWAN_MOF_UPDATE_CADENCE,
        }
        for key, expected in identity.items():
            if scope[key] != expected:
                raise ValueError(f"MOF {key} drifted")
        source_updated_at = _canonical_utc_timestamp(
            scope["source_updated_at"], "MOF source_updated_at"
        )
        if scope["source_updated_at_basis"] != TAIWAN_MOF_SOURCE_UPDATED_AT_BASIS:
            raise ValueError("MOF source update timestamp basis drifted")
        if _timestamp_clock(source_updated_at) > _timestamp_clock(retrieved_at):
            raise ValueError("MOF source update is later than retrieval")
        publisher_date_raw = _required_text(
            scope["publisher_csv_date_raw"], "MOF publisher CSV date raw"
        )
        publisher_date = _required_text(
            scope["publisher_csv_date"], "MOF publisher CSV date"
        )
        parsed_publisher_date = _canonical_publisher_date(publisher_date)
        _validate_publisher_date_binding(
            parsed_publisher_date,
            source_updated_at=source_updated_at,
            retrieved_at=retrieved_at,
        )
        if scope["publisher_csv_date_basis"] != TAIWAN_MOF_PUBLISHER_DATE_BASIS:
            raise ValueError("MOF publisher CSV date basis drifted")
        expected_acquisition = {
            "method": "GET",
            "url": TAIWAN_MOF_ARCHIVE_URL,
            "response_artifact": "bgmopen1_zip",
            "request_body_retained": False,
            "credential_retained": False,
        }
        if scope["acquisition"] != expected_acquisition:
            raise ValueError("MOF acquisition metadata drifted")
        if scope["privacy"] != _privacy_policy():
            raise ValueError("MOF privacy metadata drifted")
        if scope["limitations"] != list(_LIMITATIONS):
            raise ValueError("MOF limitations metadata drifted")
        if scope["rights"] != _rights():
            raise ValueError("MOF rights metadata drifted")

        upstream = scope["upstream_archive"]
        if not isinstance(upstream, dict):
            raise ValueError("MOF upstream archive metadata must be an object")
        _validate_exact_keys(
            upstream,
            {
                "download_url",
                "sha256",
                "bytes",
                "artifact_content_type",
                "response_metadata",
            },
            "MOF upstream archive metadata",
        )
        if (
            upstream["download_url"] != TAIWAN_MOF_ARCHIVE_URL
            or upstream["artifact_content_type"] != "application/zip"
        ):
            raise ValueError("MOF upstream archive identity drifted")
        raw_sha256 = _sha256(upstream["sha256"], "MOF archive sha256")
        raw_size = _integer(
            upstream["bytes"],
            "MOF archive bytes",
            minimum=1,
            maximum=TAIWAN_MOF_MAX_ARCHIVE_BYTES,
        )
        response = upstream["response_metadata"]
        if not isinstance(response, dict):
            raise ValueError("MOF HTTP response metadata must be an object")
        _validate_exact_keys(
            response,
            {"content_length", "content_type", "etag", "last_modified"},
            "MOF HTTP response metadata",
        )
        if response["content_length"] is None:
            content_length = None
        else:
            content_length = _integer(
                response["content_length"],
                "MOF HTTP Content-Length",
                minimum=1,
                maximum=TAIWAN_MOF_MAX_ARCHIVE_BYTES,
            )
            if content_length != raw_size:
                raise ValueError("MOF HTTP Content-Length disagrees with archive")
        retained_content_type = _content_type(response["content_type"])
        retained_etag = _optional_header(response["etag"], "MOF HTTP ETag")
        retained_last_modified = _optional_header(
            response["last_modified"], "MOF HTTP Last-Modified"
        )
        if (
            retained_last_modified is None
            or normalize_http_last_modified(retained_last_modified) != source_updated_at
        ):
            raise ValueError(
                "MOF source update timestamp disagrees with HTTP Last-Modified"
            )
        response_metadata = {
            "content_length": content_length,
            "content_type": retained_content_type,
            "etag": retained_etag,
            "last_modified": retained_last_modified,
        }
        if response != response_metadata:
            raise ValueError("MOF HTTP response metadata is not canonical")

        raw_path = f"raw/sha256/{raw_sha256}.zip"
        expected_retention = {
            "retained_in_snapshot": True,
            "blob_locator": raw_path,
            "sha256": raw_sha256,
            "bytes": raw_size,
            "local_archive_is_canonical_acquisition_input": True,
        }
        if scope["raw_retention"] != expected_retention:
            raise ValueError("MOF raw retention metadata drifted")

        inputs = manifest["inputs"]
        if not isinstance(inputs, list) or len(inputs) != 3:
            raise ValueError("MOF snapshot requires exactly three inputs")
        allowlist_entry, matched_entry, raw_entry = inputs
        if not all(isinstance(entry, dict) for entry in inputs):
            raise ValueError("MOF snapshot inputs must be objects")
        _validate_exact_keys(
            allowlist_entry,
            {
                "path",
                "record_type",
                "artifact_kind",
                "record_count",
                "bytes",
                "sha256",
                "content_type",
            },
            "MOF allowlist input",
        )
        allowlist_count = _integer(
            allowlist_entry["record_count"], "MOF allowlist count", minimum=1
        )
        allowlist_size = _integer(
            allowlist_entry["bytes"],
            "MOF allowlist bytes",
            minimum=1,
            maximum=TAIWAN_MOF_MAX_ALLOWLIST_BYTES,
        )
        allowlist_sha256 = _sha256(allowlist_entry["sha256"], "MOF allowlist sha256")
        expected_allowlist_metadata = {
            "path": TAIWAN_MOF_ALLOWLIST_FILENAME,
            "record_type": TAIWAN_MOF_ALLOWLIST_RECORD_TYPE,
            "artifact_kind": "deterministic_cross_snapshot_allowlist",
            "record_count": allowlist_count,
            "bytes": allowlist_size,
            "sha256": allowlist_sha256,
            "content_type": "text/plain; charset=us-ascii",
        }
        if allowlist_entry != expected_allowlist_metadata:
            raise ValueError("MOF allowlist input metadata drifted")
        expected_filter = {
            "version": TAIWAN_MOF_FILTER_VERSION,
            "source_field": "統一編號",
            "match_rule": "exact_membership_in_pinned_sorted_ubn_allowlist",
            "allowlist_path": TAIWAN_MOF_ALLOWLIST_FILENAME,
            "allowlist_sha256": allowlist_sha256,
            "allowlist_count": allowlist_count,
        }
        if scope["filter"] != expected_filter:
            raise ValueError("MOF exact allowlist filter metadata drifted")
        derivation = _validate_allowlist_derivation(
            scope["allowlist_derivation"],
            allowlist_count=allowlist_count,
            allowlist_size=allowlist_size,
            allowlist_sha256=allowlist_sha256,
        )

        _validate_exact_keys(
            matched_entry,
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
            "MOF matched input",
        )
        matched_count = _integer(matched_entry["record_count"], "MOF matched count")
        matched_size = _integer(
            matched_entry["bytes"],
            "MOF matched bytes",
            maximum=TAIWAN_MOF_MAX_MATCHED_BYTES,
        )
        matched_sha256 = _sha256(matched_entry["sha256"], "MOF matched sha256")
        expected_matched_metadata = {
            "path": TAIWAN_MOF_MATCHED_FILENAME,
            "record_type": TAIWAN_MOF_MATCHED_RECORD_TYPE,
            "url": TAIWAN_MOF_DATASET_URL,
            "artifact_kind": "deterministic_privacy_conscious_allowlist_derivative",
            "filter_version": TAIWAN_MOF_FILTER_VERSION,
            "record_count": matched_count,
            "bytes": matched_size,
            "sha256": matched_sha256,
            "content_type": "application/x-ndjson",
            **_input_rights(),
        }
        if matched_entry != expected_matched_metadata:
            raise ValueError("MOF matched input metadata drifted")

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
            "MOF raw input",
        )
        raw_record_count = _integer(
            raw_entry["record_count"], "MOF raw record count", minimum=1
        )
        expected_raw_metadata = {
            "path": raw_path,
            "record_type": TAIWAN_MOF_RAW_RECORD_TYPE,
            "url": TAIWAN_MOF_ARCHIVE_URL,
            "artifact_kind": "upstream_official_active_tax_registration_archive",
            "record_count": raw_record_count,
            "bytes": raw_size,
            "sha256": raw_sha256,
            "content_type": "application/zip",
            "response_metadata": response_metadata,
            **_input_rights(),
        }
        if raw_entry != expected_raw_metadata:
            raise ValueError("MOF raw input metadata drifted")

        member = scope["upstream_member"]
        if not isinstance(member, dict):
            raise ValueError("MOF CSV member metadata must be an object")
        _validate_exact_keys(
            member,
            {"path", "sha256", "bytes", "crc32", "compressed_bytes"},
            "MOF CSV member metadata",
        )
        member_path = _required_text(member["path"], "MOF CSV member path")
        _relative_parts(member_path, "MOF CSV member path")
        _sha256(member["sha256"], "MOF CSV member sha256")
        _integer(member["bytes"], "MOF CSV member bytes", minimum=1)
        _integer(member["crc32"], "MOF CSV member crc32", maximum=0xFFFFFFFF)
        _integer(member["compressed_bytes"], "MOF CSV compressed bytes", minimum=1)

        _validate_exact_tree(reader.root, raw_path)
        allowlist_bytes = reader.read(
            TAIWAN_MOF_ALLOWLIST_FILENAME, maximum_bytes=TAIWAN_MOF_MAX_ALLOWLIST_BYTES
        )
        if (
            len(allowlist_bytes) != allowlist_size
            or hashlib.sha256(allowlist_bytes).hexdigest() != allowlist_sha256
        ):
            raise ValueError("MOF allowlist hash or size mismatch")
        allowlist_values = parse_ubn_allowlist_bytes(allowlist_bytes)
        if len(allowlist_values) != allowlist_count:
            raise ValueError("MOF allowlist record count mismatch")
        raw_bytes = reader.read(raw_path, maximum_bytes=TAIWAN_MOF_MAX_ARCHIVE_BYTES)
        if (
            len(raw_bytes) != raw_size
            or hashlib.sha256(raw_bytes).hexdigest() != raw_sha256
        ):
            raise ValueError("MOF archive hash or size mismatch")
        matched_bytes = reader.read(
            TAIWAN_MOF_MATCHED_FILENAME, maximum_bytes=TAIWAN_MOF_MAX_MATCHED_BYTES
        )
        if (
            len(matched_bytes) != matched_size
            or hashlib.sha256(matched_bytes).hexdigest() != matched_sha256
        ):
            raise ValueError("MOF matched derivative hash or size mismatch")

        scan = scan_taiwan_mof_archive(reader.root / raw_path, list(allowlist_values))
        if scan.archive_sha256 != raw_sha256 or scan.archive_bytes != raw_size:
            raise ValueError("MOF retained archive rescan identity disagrees")
        expected_member = {
            "path": scan.csv_member,
            "sha256": scan.csv_sha256,
            "bytes": scan.csv_bytes,
            "crc32": scan.csv_crc32,
            "compressed_bytes": scan.csv_compressed_bytes,
        }
        if member != expected_member:
            raise ValueError("MOF member metadata disagrees with raw rescan")
        if (
            scan.publisher_date_raw != publisher_date_raw
            or scan.publisher_date != publisher_date
        ):
            raise ValueError("MOF publisher CSV date disagrees with raw rescan")
        if scope["summary"] != _summary(scan):
            raise ValueError("MOF summary metadata disagrees with raw rescan")
        if raw_record_count != scan.row_count or matched_count != scan.matched_count:
            raise ValueError("MOF input counts disagree with raw rescan")
        if matched_bytes != canonical_matched_jsonl_bytes(scan):
            raise ValueError("MOF matched derivative does not replay")
        organization_counts = _organization_counts(
            _summary(scan)["matched_organization_type_counts"]
        )

        if (
            reader.read("manifest.json", maximum_bytes=TAIWAN_MOF_MAX_MANIFEST_BYTES)
            != manifest_raw
        ):
            raise ValueError("MOF manifest changed during verification")
        if (
            reader.read(
                TAIWAN_MOF_ALLOWLIST_FILENAME,
                maximum_bytes=TAIWAN_MOF_MAX_ALLOWLIST_BYTES,
            )
            != allowlist_bytes
        ):
            raise ValueError("MOF allowlist changed during verification")
        if (
            reader.read(raw_path, maximum_bytes=TAIWAN_MOF_MAX_ARCHIVE_BYTES)
            != raw_bytes
        ):
            raise ValueError("MOF archive changed during verification")
        if (
            reader.read(
                TAIWAN_MOF_MATCHED_FILENAME, maximum_bytes=TAIWAN_MOF_MAX_MATCHED_BYTES
            )
            != matched_bytes
        ):
            raise ValueError("MOF derivative changed during verification")
        _validate_exact_tree(reader.root, raw_path)
        reader.assert_unchanged()

        return VerifiedTaiwanMOFSnapshot(
            root=reader.root,
            manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
            manifest_size=len(manifest_raw),
            manifest_bytes=manifest_raw,
            retrieved_at=retrieved_at,
            retrieval_timestamp_basis=retrieval_basis,
            source_updated_at=source_updated_at,
            source_updated_at_basis=TAIWAN_MOF_SOURCE_UPDATED_AT_BASIS,
            publisher_date_raw=publisher_date_raw,
            publisher_date=publisher_date,
            publisher_date_basis=TAIWAN_MOF_PUBLISHER_DATE_BASIS,
            raw_path=raw_path,
            raw_sha256=raw_sha256,
            raw_size=raw_size,
            raw_bytes=raw_bytes,
            allowlist_sha256=allowlist_sha256,
            allowlist_size=allowlist_size,
            allowlist_bytes=allowlist_bytes,
            allowlist_values=allowlist_values,
            matched_sha256=matched_sha256,
            matched_size=matched_size,
            matched_bytes=matched_bytes,
            row_count=scan.row_count,
            business_row_count=scan.business_row_count,
            allowlist_count=scan.allowlist_count,
            matched_count=scan.matched_count,
            missing_count=scan.missing_count,
            missing_business_numbers=scan.missing_business_numbers,
            organization_type_counts=organization_counts,
            allowlist_derivation=derivation,
            scan=scan,
        )


def _verified_snapshot_identity(
    snapshot: VerifiedTaiwanMOFSnapshot,
) -> tuple[object, ...]:
    return (
        snapshot.manifest_sha256,
        snapshot.manifest_size,
        snapshot.raw_path,
        snapshot.raw_sha256,
        snapshot.raw_size,
        snapshot.allowlist_sha256,
        snapshot.allowlist_size,
        snapshot.matched_sha256,
        snapshot.matched_size,
    )


def verify_taiwan_mof_allowlist_sources(
    root: str | Path,
    moenv_snapshot_dir: str | Path,
    factory_snapshot_dir: str | Path,
) -> VerifiedTaiwanMOFSnapshot:
    """Re-derive the pinned allowlist from its two source snapshots and compare."""

    snapshot = verify_taiwan_mof_snapshot(root)
    rebuilt = build_taiwan_mof_allowlist(moenv_snapshot_dir, factory_snapshot_dir)
    refreshed = verify_taiwan_mof_snapshot(root)
    if _verified_snapshot_identity(snapshot) != _verified_snapshot_identity(refreshed):
        raise ValueError("MOF snapshot changed while re-deriving its allowlist")
    if refreshed.allowlist_bytes != rebuilt.raw_bytes:
        raise ValueError(
            "MOF allowlist does not re-derive from the bound source snapshots"
        )
    expected_derivation = _allowlist_derivation(rebuilt)
    if refreshed.allowlist_derivation != expected_derivation:
        raise ValueError(
            "MOF allowlist source bindings or diagnostics do not re-derive"
        )
    return refreshed


def create_taiwan_mof_snapshot(
    archive_path: str | Path,
    output_dir: str | Path,
    *,
    moenv_snapshot_dir: str | Path,
    factory_snapshot_dir: str | Path,
    retrieved_at: str,
    upstream_last_modified: str,
    retrieval_timestamp_basis: str = "operator_supplied_for_archived_bytes",
    upstream_etag: str | None = None,
    upstream_content_type: str | None = None,
    upstream_content_length: int | None = None,
) -> VerifiedTaiwanMOFSnapshot:
    """Create and atomically install one source-bound BGMOPEN1 snapshot."""

    retrieved_at = _canonical_utc_timestamp(retrieved_at, "retrieved_at")
    retrieval_timestamp_basis = _required_text(
        retrieval_timestamp_basis, "retrieval_timestamp_basis"
    )
    if retrieval_timestamp_basis not in TAIWAN_MOF_RETRIEVAL_TIMESTAMP_BASES:
        raise ValueError("invalid MOF retrieval_timestamp_basis")
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
            maximum=TAIWAN_MOF_MAX_ARCHIVE_BYTES,
        )
    allowlist = build_taiwan_mof_allowlist(moenv_snapshot_dir, factory_snapshot_dir)

    output = Path(output_dir).absolute()
    if not output.name or output.name in {".", ".."}:
        raise ValueError(f"unsafe MOF snapshot output directory: {output}")
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"refusing to overwrite MOF snapshot: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent))
    installed = False
    try:
        provisional = stage / "raw" / "sha256" / ".archive.part"
        copied_sha256, copied_size = _copy_regular_file(archive_path, provisional)
        if (
            upstream_content_length is not None
            and upstream_content_length != copied_size
        ):
            raise ValueError("upstream Content-Length disagrees with MOF archive bytes")
        raw_path = stage / "raw" / "sha256" / f"{copied_sha256}.zip"
        os.rename(provisional, raw_path)
        scan = scan_taiwan_mof_archive(raw_path, list(allowlist.values))
        if scan.archive_sha256 != copied_sha256 or scan.archive_bytes != copied_size:
            raise ValueError("MOF archive scan identity disagrees with retained bytes")
        _validate_publisher_date_binding(
            _canonical_publisher_date(scan.publisher_date),
            source_updated_at=source_updated_at,
            retrieved_at=retrieved_at,
        )
        matched_raw = canonical_matched_jsonl_bytes(scan)
        if len(matched_raw) > TAIWAN_MOF_MAX_MATCHED_BYTES:
            raise ValueError("MOF matched derivative exceeds the byte limit")
        _write_new(stage / TAIWAN_MOF_ALLOWLIST_FILENAME, allowlist.raw_bytes)
        _write_new(stage / TAIWAN_MOF_MATCHED_FILENAME, matched_raw)
        manifest = _manifest(
            scan=scan,
            allowlist=allowlist,
            matched_raw=matched_raw,
            retrieved_at=retrieved_at,
            retrieval_timestamp_basis=retrieval_timestamp_basis,
            source_updated_at=source_updated_at,
            upstream_last_modified=upstream_last_modified,
            upstream_etag=upstream_etag,
            upstream_content_type=upstream_content_type,
            upstream_content_length=upstream_content_length,
        )
        manifest_raw = _canonical_json_bytes(manifest)
        if len(manifest_raw) > TAIWAN_MOF_MAX_MANIFEST_BYTES:
            raise ValueError("MOF snapshot manifest exceeds the byte limit")
        _write_new(stage / "manifest.json", manifest_raw)
        _fsync_directory(stage / "raw" / "sha256")
        _fsync_directory(stage / "raw")
        _fsync_directory(stage)
        tree_before_verification = _capture_snapshot_tree_identity(
            stage, raw_path.relative_to(stage).as_posix()
        )
        staged_snapshot = verify_taiwan_mof_allowlist_sources(
            stage, moenv_snapshot_dir, factory_snapshot_dir
        )
        expected_tree = _capture_snapshot_tree_identity(
            stage, raw_path.relative_to(stage).as_posix()
        )
        if tree_before_verification != expected_tree:
            raise ValueError(
                "MOF staged snapshot changed during source-aware verification"
            )
        installed_snapshot = replace(staged_snapshot, root=output)
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"refusing to overwrite MOF snapshot: {output}")
        _rename_directory_no_replace(stage, output)
        installed = True
        try:
            _assert_installed_tree_identity(output, expected_tree)
            _fsync_directory(output.parent)
        except BaseException as check_error:
            try:
                _rollback_failed_install(output, stage, expected_tree)
            except BaseException as rollback_error:
                raise RuntimeError(
                    "MOF post-install identity check failed and guarded rollback "
                    f"could not remove the newly installed target {output}: "
                    f"{check_error}"
                ) from rollback_error
            installed = False
            raise
        return installed_snapshot
    finally:
        if not installed and stage.exists():
            shutil.rmtree(stage)


__all__ = [
    "TAIWAN_MOF_ALLOWLIST_DERIVATION_VERSION",
    "TAIWAN_MOF_ALLOWLIST_FILENAME",
    "TAIWAN_MOF_ALLOWLIST_RECORD_TYPE",
    "TAIWAN_MOF_COVERAGE",
    "TAIWAN_MOF_LICENSE_SPDX",
    "TAIWAN_MOF_MATCHED_FILENAME",
    "TAIWAN_MOF_MATCHED_RECORD_TYPE",
    "TAIWAN_MOF_RAW_RECORD_TYPE",
    "TAIWAN_MOF_RETRIEVAL_TIMESTAMP_BASES",
    "TAIWAN_MOF_SCOPE",
    "TAIWAN_MOF_SOURCE_UPDATED_AT_BASIS",
    "TAIWAN_MOF_UPDATE_CADENCE",
    "TaiwanMOFAllowlist",
    "TaiwanMOFConflict",
    "TaiwanMOFExcludedValue",
    "TaiwanMOFSourceBinding",
    "VerifiedTaiwanMOFSnapshot",
    "build_taiwan_mof_allowlist",
    "create_taiwan_mof_snapshot",
    "normalize_http_last_modified",
    "verify_taiwan_mof_allowlist_sources",
    "verify_taiwan_mof_snapshot",
]
