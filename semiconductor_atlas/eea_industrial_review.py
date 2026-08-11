"""Evidence-bound review artifacts for EEA industrial semiconductor leads.

The pinned EEA adapter produces discovery candidates, not facility
classifications.  This module turns that immutable candidate set into a compact
review queue and validates a separate, complete adjudication ledger.  Queue
priority controls review order only; it never determines an outcome.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qsl, urlsplit

from .adapters.eea_industrial import EEA_FILTER_VERSION, EEA_RECORD_TYPE
from .eea_industrial_snapshot import (
    VerifiedEEAIndustrialSnapshot,
    verify_eea_industrial_snapshot,
)
from .repository import stable_id


EEA_REVIEW_QUEUE_FORMAT = "semiconductor-atlas-eea-industrial-review-queue-v1"
EEA_REVIEW_FORMAT = "semiconductor-atlas-eea-industrial-review-v1"
EEA_REVIEW_MAX_BYTES = 16 * 1024 * 1024
EEA_REVIEW_MAX_CANDIDATES = 1_000
EEA_REVIEW_POLICY_VERSION = "eea-industrial-facility-scope-review-v1"

EEA_REVIEW_OUTCOMES = frozenset({"accept_in_scope", "defer", "reject_out_of_scope"})

_READ_CHUNK_BYTES = 64 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COUNTRY_CODE_RE = re.compile(r"^[A-Z]{2}$")
_SECRET_QUERY_KEYS = frozenset(
    {
        "accesstoken",
        "apikey",
        "authorization",
        "awsaccesskeyid",
        "clientsecret",
        "credential",
        "googlesignature",
        "password",
        "secret",
        "sharetoken",
        "signature",
        "token",
        "xamzcredential",
        "xamzsecuritytoken",
        "xamzsignature",
        "xgoogcredential",
        "xgoogsignature",
    }
)

_QUEUE_TOP_LEVEL_FIELDS = {
    "candidates",
    "filter_version",
    "format",
    "generated_at",
    "knowledge_cutoff_at",
    "review_policy",
    "snapshot_accepted_at",
    "snapshot_manifest_sha256",
    "source_candidate_count",
    "source_candidate_sha256",
}
_QUEUE_CANDIDATE_FIELDS = {
    "candidate_id",
    "facility_inspire_id",
    "latest_source_detail_reporting_year",
    "reason_kinds",
    "review_priority",
    "source_candidate_sha256",
    "source_values",
    "triage_basis",
}
_SOURCE_VALUE_FIELDS = {
    "address",
    "country_code",
    "facility_name",
    "functions",
    "parent_company_name",
    "parent_site_name",
    "raw_tabular_point",
}
_ADDRESS_FIELDS = {"building_number", "city", "postal_code", "street_name"}
_POINT_FIELDS = {"latitude", "longitude"}
_FUNCTION_FIELDS = {"nace_code", "nace_name"}

_REVIEW_TOP_LEVEL_FIELDS = {
    "candidate_count",
    "candidate_queue_sha256",
    "decisions",
    "filter_version",
    "format",
    "knowledge_cutoff_at",
    "reviewed_at",
    "reviewed_by",
    "snapshot_manifest_sha256",
    "source_candidate_sha256",
}
_DECISION_FIELDS = {"candidate_id", "evidence", "outcome", "reason"}
_EVIDENCE_FIELDS = {"accessed_at", "excerpt", "title", "url"}

_NAME_REASON_KINDS = frozenset(
    {
        "explicit_facility_name_candidate",
        "explicit_parent_site_name_candidate",
    }
)
_NACE_REASON_KIND = "electronic_components_nace_26_11_candidate"
_REASON_KINDS = _NAME_REASON_KINDS | {_NACE_REASON_KIND}

_TRIAGE = {
    1: "explicit_name_and_exact_nace_26_11",
    2: "explicit_name_only",
    3: "exact_nace_26_11_only",
}

_FORBIDDEN_INFERENCES = (
    "capacity_or_output",
    "current_operation_or_utilization",
    "facility_identity_merge",
    "owner_or_operator_relationship",
    "process_node_product_or_wafer_size",
    "production_ramp_or_timing",
    "site_geometry_or_surveyed_coordinates",
)


def _review_policy() -> dict[str, object]:
    return {
        "candidate_membership_is_classification": False,
        "decision_scope": (
            "whether_the_reported_source_facility_is_within_the_atlas_"
            "semiconductor_facility_or_materials_scope"
        ),
        "external_evidence_required_for": [
            "accept_in_scope",
            "reject_out_of_scope",
        ],
        "forbidden_inferences": list(_FORBIDDEN_INFERENCES),
        "policy_version": EEA_REVIEW_POLICY_VERSION,
        "queue_priority_is_outcome": False,
        "review_knowledge_cutoff_may_advance": True,
        "review_outcomes": [
            "accept_in_scope",
            "defer",
            "reject_out_of_scope",
        ],
        "triage_priorities": [
            {"basis": _TRIAGE[priority], "priority": priority}
            for priority in sorted(_TRIAGE)
        ],
    }


@dataclass(frozen=True, slots=True)
class EEAIndustrialReviewFunction:
    nace_code: str | None
    nace_name: str | None


@dataclass(frozen=True, slots=True)
class EEAIndustrialReviewQueueCandidate:
    candidate_id: str
    facility_inspire_id: str
    source_candidate_sha256: str
    review_priority: int
    triage_basis: str
    reason_kinds: tuple[str, ...]
    latest_source_detail_reporting_year: str | None
    country_code: str
    facility_name: str | None
    parent_site_name: str | None
    parent_company_name: str | None
    street_name: str | None
    building_number: str | None
    city: str | None
    postal_code: str | None
    raw_point_latitude: str | None
    raw_point_longitude: str | None
    functions: tuple[EEAIndustrialReviewFunction, ...]


@dataclass(frozen=True, slots=True)
class EEAIndustrialReviewQueue:
    format: str
    generated_at: str
    knowledge_cutoff_at: str
    snapshot_accepted_at: str
    snapshot_manifest_sha256: str
    source_candidate_sha256: str
    source_candidate_count: int
    filter_version: str
    candidates: tuple[EEAIndustrialReviewQueueCandidate, ...]
    raw_bytes: bytes
    raw_sha256: str
    path: Path | None


@dataclass(frozen=True, slots=True)
class EEAIndustrialReviewEvidence:
    url: str
    title: str
    accessed_at: str
    excerpt: str


@dataclass(frozen=True, slots=True)
class EEAIndustrialReviewDecision:
    candidate_id: str
    outcome: str
    reason: str
    evidence: tuple[EEAIndustrialReviewEvidence, ...]


@dataclass(frozen=True, slots=True)
class EEAIndustrialReview:
    format: str
    reviewed_by: str
    reviewed_at: str
    candidate_queue_sha256: str
    snapshot_manifest_sha256: str
    source_candidate_sha256: str
    filter_version: str
    knowledge_cutoff_at: str
    decisions: tuple[EEAIndustrialReviewDecision, ...]
    raw_bytes: bytes
    raw_sha256: str
    path: Path | None


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


def _object(value: object, context: str) -> dict[str, Any]:
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


def _source_candidate_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _load_strict_json(raw: bytes, context: str) -> dict[str, Any]:
    if not isinstance(raw, bytes):
        raise TypeError(f"{context} must be bytes")
    if not raw:
        raise ValueError(f"{context} must not be empty")
    if len(raw) > EEA_REVIEW_MAX_BYTES:
        raise ValueError(f"{context} exceeds the byte limit")
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
    except ValueError as error:
        raise ValueError(f"{context} is not strict JSON") from error
    payload = _object(value, context)
    try:
        canonical = _canonical_json_bytes(payload)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{context} is not canonicalizable JSON") from error
    if raw != canonical:
        raise ValueError(f"{context} is not canonical JSON")
    return payload


def _text(
    value: object,
    context: str,
    *,
    maximum_characters: int = 4_096,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum_characters
        or any(
            ord(character) < 0x20
            or 0x7F <= ord(character) <= 0x9F
            or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        )
    ):
        raise ValueError(f"{context} must be a non-empty clean string")
    return value


def _source_text(value: object, context: str) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) > 16_384
        or any(
            (ord(character) < 0x20 and character not in "\t\n\r")
            or 0x7F <= ord(character) <= 0x9F
            or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        )
    ):
        raise ValueError(f"{context} must be null or bounded source text")
    return value


def _sha256(value: object, context: str) -> str:
    text = _text(value, context, maximum_characters=64)
    if not _SHA256_RE.fullmatch(text):
        raise ValueError(f"{context} must be a lowercase SHA-256 digest")
    return text


def _timestamp(value: object, context: str) -> str:
    text = _text(value, context, maximum_characters=64)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise ValueError(f"{context} must be an ISO 8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{context} must include a timezone")
    canonical = parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if text != canonical:
        raise ValueError(f"{context} must be a canonical UTC timestamp")
    return text


def _clock(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _positive_integer(value: object, context: str, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > maximum
    ):
        raise ValueError(f"{context} must be an integer from 1 through {maximum}")
    return value


def _candidate_id(value: object, context: str) -> str:
    text = _text(value, context, maximum_characters=36)
    try:
        parsed = uuid.UUID(text)
    except ValueError as error:
        raise ValueError(f"{context} must be a canonical UUIDv5") from error
    if str(parsed) != text or parsed.version != 5:
        raise ValueError(f"{context} must be a canonical UUIDv5")
    return text


def _country_code(value: object, context: str) -> str:
    text = _text(value, context, maximum_characters=2)
    if not _COUNTRY_CODE_RE.fullmatch(text):
        raise ValueError(f"{context} must be a two-letter uppercase country code")
    return text


def _year(value: object, context: str) -> str | None:
    if value is None:
        return None
    text = _text(value, context, maximum_characters=4)
    if not text.isascii() or not text.isdigit() or not 2000 <= int(text) <= 2100:
        raise ValueError(f"{context} must be null or a four-digit reporting year")
    return text


def _string_array(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{context} must be a non-empty JSON array")
    result = tuple(
        _text(item, f"{context}[{index}]") for index, item in enumerate(value)
    )
    if result != tuple(sorted(result)) or len(result) != len(set(result)):
        raise ValueError(f"{context} must be sorted and unique")
    return result


def _stat_identity(details: os.stat_result) -> tuple[int, ...]:
    return (
        details.st_dev,
        details.st_ino,
        details.st_mode,
        details.st_nlink,
        details.st_size,
        details.st_mtime_ns,
        details.st_ctime_ns,
    )


def _read_regular(path: str | Path, context: str) -> tuple[bytes, Path]:
    candidate = Path(path).absolute()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError as error:
        raise ValueError(f"{context} is missing or unsafe: {candidate}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{context} must be a regular file")
        if before.st_size < 1 or before.st_size > EEA_REVIEW_MAX_BYTES:
            raise ValueError(f"{context} has an invalid size")
        chunks: list[bytes] = []
        total = 0
        while True:
            allowance = EEA_REVIEW_MAX_BYTES + 1 - total
            chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, allowance))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > EEA_REVIEW_MAX_BYTES:
                raise ValueError(f"{context} exceeds the byte limit")
        after = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(after):
            raise ValueError(f"{context} changed while being read")
        raw = b"".join(chunks)
        if len(raw) != before.st_size:
            raise ValueError(f"{context} size changed while being read")
        try:
            named_after = os.stat(candidate, follow_symlinks=False)
        except OSError as error:
            raise ValueError(f"{context} path changed while being read") from error
        if _stat_identity(after) != _stat_identity(named_after):
            raise ValueError(f"{context} path changed while being read")
    finally:
        os.close(descriptor)
    return raw, candidate


def _write_new_regular(path: str | Path, raw: bytes) -> Path:
    candidate = Path(path).absolute()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(candidate, flags, 0o644)
    try:
        view = memoryview(raw)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise OSError("review artifact write made no progress")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return candidate


def _projection_values(value: object, context: str) -> dict[str, Any]:
    projection = _object(value, context)
    values = projection.get("values")
    if not isinstance(values, dict):
        raise ValueError(f"{context}.values must be a JSON object")
    return values


def _raw_source_value(
    values: Mapping[str, Any], field: str, context: str
) -> str | None:
    if field not in values:
        raise ValueError(f"{context} is missing source field {field!r}")
    return _source_text(values[field], f"{context}.{field}")


def _triage(reason_kinds: tuple[str, ...]) -> tuple[int, str]:
    kinds = set(reason_kinds)
    has_name = bool(kinds & _NAME_REASON_KINDS)
    has_nace = _NACE_REASON_KIND in kinds
    if has_name and has_nace:
        priority = 1
    elif has_name:
        priority = 2
    elif has_nace:
        priority = 3
    else:
        raise ValueError("EEA candidate has no recognized review reason")
    return priority, _TRIAGE[priority]


def _queue_candidate_from_source(
    source: Mapping[str, Any],
) -> EEAIndustrialReviewQueueCandidate:
    source_raw = _source_candidate_bytes(source)
    source_digest = hashlib.sha256(source_raw).hexdigest()
    facility_inspire_id = _text(
        source.get("facility_inspire_id"), "source candidate facility_inspire_id"
    )
    if source.get("record_type") != EEA_RECORD_TYPE:
        raise ValueError("source candidate record_type drifted")

    raw_reasons = source.get("candidate_reasons")
    if not isinstance(raw_reasons, list) or not raw_reasons:
        raise ValueError("source candidate reasons must be non-empty")
    reason_kinds = tuple(
        sorted(
            {
                _text(
                    _object(reason, f"source candidate reason[{index}]").get("kind"),
                    f"source candidate reason[{index}].kind",
                )
                for index, reason in enumerate(raw_reasons)
            }
        )
    )
    if not set(reason_kinds) <= _REASON_KINDS:
        raise ValueError("source candidate contains an unknown review reason kind")
    review_priority, triage_basis = _triage(reason_kinds)

    facility_rows = source.get("facility_rows")
    if not isinstance(facility_rows, list) or len(facility_rows) != 1:
        raise ValueError("source candidate must contain exactly one facility row")
    facility = _projection_values(facility_rows[0], "source candidate facility row")
    if (
        _raw_source_value(
            facility, "Facility_INSPIRE_ID", "source candidate facility row"
        )
        != facility_inspire_id
    ):
        raise ValueError("source candidate facility identifier is inconsistent")

    site_rows = source.get("site_rows")
    if not isinstance(site_rows, list) or len(site_rows) > 1:
        raise ValueError("source candidate must contain zero or one parent-site row")
    site = (
        None
        if not site_rows
        else _projection_values(site_rows[0], "source candidate parent-site row")
    )

    raw_functions = source.get("function_rows")
    if not isinstance(raw_functions, list):
        raise ValueError("source candidate function_rows must be a JSON array")
    functions: list[EEAIndustrialReviewFunction] = []
    for index, raw_function in enumerate(raw_functions):
        values = _projection_values(
            raw_function, f"source candidate function row[{index}]"
        )
        code = _raw_source_value(
            values,
            "NACEMainEconomicActivityCode",
            f"source candidate function row[{index}]",
        )
        name = _raw_source_value(
            values,
            "NACEMainEconomicActivityName",
            f"source candidate function row[{index}]",
        )
        functions.append(EEAIndustrialReviewFunction(code, name))
    functions.sort(key=lambda item: (item.nace_code or "", item.nace_name or ""))

    country = _raw_source_value(
        facility, "countryCode", "source candidate facility row"
    )
    return EEAIndustrialReviewQueueCandidate(
        candidate_id=stable_id(
            "eea-industrial-review-candidate",
            EEA_FILTER_VERSION,
            facility_inspire_id,
        ),
        facility_inspire_id=facility_inspire_id,
        source_candidate_sha256=source_digest,
        review_priority=review_priority,
        triage_basis=triage_basis,
        reason_kinds=reason_kinds,
        latest_source_detail_reporting_year=_year(
            source.get("latest_source_detail_reporting_year"),
            "source candidate latest_source_detail_reporting_year",
        ),
        country_code=_country_code(country, "source candidate countryCode"),
        facility_name=_raw_source_value(
            facility, "nameOfFeature", "source candidate facility row"
        ),
        parent_site_name=(
            None
            if site is None
            else _raw_source_value(
                site, "nameOfFeature", "source candidate parent-site row"
            )
        ),
        parent_company_name=_raw_source_value(
            facility, "parentCompanyName", "source candidate facility row"
        ),
        street_name=_raw_source_value(
            facility, "streetName", "source candidate facility row"
        ),
        building_number=_raw_source_value(
            facility, "buildingNumber", "source candidate facility row"
        ),
        city=_raw_source_value(facility, "city", "source candidate facility row"),
        postal_code=_raw_source_value(
            facility, "postalCode", "source candidate facility row"
        ),
        raw_point_latitude=_raw_source_value(
            facility, "pointGeometryLat", "source candidate facility row"
        ),
        raw_point_longitude=_raw_source_value(
            facility, "pointGeometryLon", "source candidate facility row"
        ),
        functions=tuple(functions),
    )


def _function_payload(value: EEAIndustrialReviewFunction) -> dict[str, object]:
    return {"nace_code": value.nace_code, "nace_name": value.nace_name}


def _queue_candidate_payload(
    value: EEAIndustrialReviewQueueCandidate,
) -> dict[str, object]:
    return {
        "candidate_id": value.candidate_id,
        "facility_inspire_id": value.facility_inspire_id,
        "latest_source_detail_reporting_year": (
            value.latest_source_detail_reporting_year
        ),
        "reason_kinds": list(value.reason_kinds),
        "review_priority": value.review_priority,
        "source_candidate_sha256": value.source_candidate_sha256,
        "source_values": {
            "address": {
                "building_number": value.building_number,
                "city": value.city,
                "postal_code": value.postal_code,
                "street_name": value.street_name,
            },
            "country_code": value.country_code,
            "facility_name": value.facility_name,
            "functions": [_function_payload(item) for item in value.functions],
            "parent_company_name": value.parent_company_name,
            "parent_site_name": value.parent_site_name,
            "raw_tabular_point": {
                "latitude": value.raw_point_latitude,
                "longitude": value.raw_point_longitude,
            },
        },
        "triage_basis": value.triage_basis,
    }


def _queue_payload(
    *,
    generated_at: str,
    knowledge_cutoff_at: str,
    snapshot_accepted_at: str,
    snapshot_manifest_sha256: str,
    source_candidate_sha256: str,
    source_candidate_count: int,
    candidates: Sequence[EEAIndustrialReviewQueueCandidate],
) -> dict[str, object]:
    return {
        "candidates": [_queue_candidate_payload(item) for item in candidates],
        "filter_version": EEA_FILTER_VERSION,
        "format": EEA_REVIEW_QUEUE_FORMAT,
        "generated_at": generated_at,
        "knowledge_cutoff_at": knowledge_cutoff_at,
        "review_policy": _review_policy(),
        "snapshot_accepted_at": snapshot_accepted_at,
        "snapshot_manifest_sha256": snapshot_manifest_sha256,
        "source_candidate_count": source_candidate_count,
        "source_candidate_sha256": source_candidate_sha256,
    }


def _parse_function(value: object, context: str) -> EEAIndustrialReviewFunction:
    payload = _object(value, context)
    if set(payload) != _FUNCTION_FIELDS:
        raise ValueError(f"{context} must contain exactly the required fields")
    return EEAIndustrialReviewFunction(
        nace_code=_source_text(payload["nace_code"], f"{context}.nace_code"),
        nace_name=_source_text(payload["nace_name"], f"{context}.nace_name"),
    )


def _parse_queue_candidate(
    value: object, index: int
) -> EEAIndustrialReviewQueueCandidate:
    context = f"candidates[{index}]"
    payload = _object(value, context)
    if set(payload) != _QUEUE_CANDIDATE_FIELDS:
        raise ValueError(f"{context} must contain exactly the required fields")
    candidate_id = _candidate_id(payload["candidate_id"], f"{context}.candidate_id")
    facility_inspire_id = _text(
        payload["facility_inspire_id"], f"{context}.facility_inspire_id"
    )
    expected_id = stable_id(
        "eea-industrial-review-candidate",
        EEA_FILTER_VERSION,
        facility_inspire_id,
    )
    if candidate_id != expected_id:
        raise ValueError(f"{context}.candidate_id is not derived from the facility ID")
    priority = _positive_integer(
        payload["review_priority"], f"{context}.review_priority", 3
    )
    triage_basis = _text(payload["triage_basis"], f"{context}.triage_basis")
    if triage_basis != _TRIAGE[priority]:
        raise ValueError(f"{context}.triage_basis disagrees with review_priority")
    reason_kinds = _string_array(payload["reason_kinds"], f"{context}.reason_kinds")
    if not set(reason_kinds) <= _REASON_KINDS:
        raise ValueError(f"{context}.reason_kinds contains an unknown reason")
    if _triage(reason_kinds) != (priority, triage_basis):
        raise ValueError(f"{context} triage disagrees with its reason kinds")

    source_values = _object(payload["source_values"], f"{context}.source_values")
    if set(source_values) != _SOURCE_VALUE_FIELDS:
        raise ValueError(f"{context}.source_values schema drifted")
    address = _object(source_values["address"], f"{context}.source_values.address")
    if set(address) != _ADDRESS_FIELDS:
        raise ValueError(f"{context}.source_values.address schema drifted")
    point = _object(
        source_values["raw_tabular_point"],
        f"{context}.source_values.raw_tabular_point",
    )
    if set(point) != _POINT_FIELDS:
        raise ValueError(f"{context}.source_values.raw_tabular_point schema drifted")
    raw_functions = source_values["functions"]
    if not isinstance(raw_functions, list):
        raise ValueError(f"{context}.source_values.functions must be a JSON array")
    functions = tuple(
        _parse_function(item, f"{context}.source_values.functions[{function_index}]")
        for function_index, item in enumerate(raw_functions)
    )
    function_keys = tuple(
        (item.nace_code or "", item.nace_name or "") for item in functions
    )
    if function_keys != tuple(sorted(function_keys)):
        raise ValueError(f"{context}.source_values.functions must be sorted")

    return EEAIndustrialReviewQueueCandidate(
        candidate_id=candidate_id,
        facility_inspire_id=facility_inspire_id,
        source_candidate_sha256=_sha256(
            payload["source_candidate_sha256"],
            f"{context}.source_candidate_sha256",
        ),
        review_priority=priority,
        triage_basis=triage_basis,
        reason_kinds=reason_kinds,
        latest_source_detail_reporting_year=_year(
            payload["latest_source_detail_reporting_year"],
            f"{context}.latest_source_detail_reporting_year",
        ),
        country_code=_country_code(
            source_values["country_code"], f"{context}.source_values.country_code"
        ),
        facility_name=_source_text(
            source_values["facility_name"],
            f"{context}.source_values.facility_name",
        ),
        parent_site_name=_source_text(
            source_values["parent_site_name"],
            f"{context}.source_values.parent_site_name",
        ),
        parent_company_name=_source_text(
            source_values["parent_company_name"],
            f"{context}.source_values.parent_company_name",
        ),
        street_name=_source_text(
            address["street_name"], f"{context}.source_values.address.street_name"
        ),
        building_number=_source_text(
            address["building_number"],
            f"{context}.source_values.address.building_number",
        ),
        city=_source_text(address["city"], f"{context}.source_values.address.city"),
        postal_code=_source_text(
            address["postal_code"], f"{context}.source_values.address.postal_code"
        ),
        raw_point_latitude=_source_text(
            point["latitude"],
            f"{context}.source_values.raw_tabular_point.latitude",
        ),
        raw_point_longitude=_source_text(
            point["longitude"],
            f"{context}.source_values.raw_tabular_point.longitude",
        ),
        functions=functions,
    )


def parse_eea_industrial_review_queue_bytes(
    raw: bytes,
) -> EEAIndustrialReviewQueue:
    """Parse one canonical, decision-free EEA review queue."""

    payload = _load_strict_json(raw, "EEA industrial review queue")
    if set(payload) != _QUEUE_TOP_LEVEL_FIELDS:
        raise ValueError("EEA industrial review queue schema drifted")
    if payload["format"] != EEA_REVIEW_QUEUE_FORMAT:
        raise ValueError(f"format must be {EEA_REVIEW_QUEUE_FORMAT!r}")
    if payload["filter_version"] != EEA_FILTER_VERSION:
        raise ValueError("EEA industrial review queue filter version drifted")
    if payload["review_policy"] != _review_policy():
        raise ValueError("EEA industrial review policy drifted")
    generated_at = _timestamp(payload["generated_at"], "generated_at")
    knowledge_cutoff_at = _timestamp(
        payload["knowledge_cutoff_at"], "knowledge_cutoff_at"
    )
    snapshot_accepted_at = _timestamp(
        payload["snapshot_accepted_at"], "snapshot_accepted_at"
    )
    if _clock(knowledge_cutoff_at) > _clock(generated_at):
        raise ValueError("knowledge cutoff cannot postdate queue generation")
    if _clock(snapshot_accepted_at) > _clock(generated_at):
        raise ValueError("snapshot acceptance cannot postdate queue generation")

    source_candidate_count = _positive_integer(
        payload["source_candidate_count"],
        "source_candidate_count",
        EEA_REVIEW_MAX_CANDIDATES,
    )
    raw_candidates = payload["candidates"]
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise ValueError("candidates must be a non-empty JSON array")
    if len(raw_candidates) != source_candidate_count:
        raise ValueError("candidate count disagrees with source_candidate_count")
    candidates = tuple(
        _parse_queue_candidate(item, index) for index, item in enumerate(raw_candidates)
    )
    candidate_ids = tuple(item.candidate_id for item in candidates)
    facility_ids = tuple(item.facility_inspire_id for item in candidates)
    source_digests = tuple(item.source_candidate_sha256 for item in candidates)
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("queue candidate IDs must be unique")
    if len(set(facility_ids)) != len(facility_ids):
        raise ValueError("queue facility IDs must be unique")
    if len(set(source_digests)) != len(source_digests):
        raise ValueError("queue source-candidate hashes must be unique")
    ordering = tuple(
        (
            item.review_priority,
            item.country_code,
            item.facility_name or "",
            item.facility_inspire_id,
        )
        for item in candidates
    )
    if ordering != tuple(sorted(ordering)):
        raise ValueError("queue candidates are not in canonical review order")

    return EEAIndustrialReviewQueue(
        format=EEA_REVIEW_QUEUE_FORMAT,
        generated_at=generated_at,
        knowledge_cutoff_at=knowledge_cutoff_at,
        snapshot_accepted_at=snapshot_accepted_at,
        snapshot_manifest_sha256=_sha256(
            payload["snapshot_manifest_sha256"], "snapshot_manifest_sha256"
        ),
        source_candidate_sha256=_sha256(
            payload["source_candidate_sha256"], "source_candidate_sha256"
        ),
        source_candidate_count=source_candidate_count,
        filter_version=EEA_FILTER_VERSION,
        candidates=candidates,
        raw_bytes=raw,
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        path=None,
    )


def build_eea_industrial_review_queue(
    snapshot: VerifiedEEAIndustrialSnapshot,
    *,
    generated_at: str,
    knowledge_cutoff_at: str,
) -> EEAIndustrialReviewQueue:
    """Build a compact queue from an already verified immutable snapshot."""

    generated_at = _timestamp(generated_at, "generated_at")
    knowledge_cutoff_at = _timestamp(knowledge_cutoff_at, "knowledge_cutoff_at")
    snapshot_accepted_at = _timestamp(snapshot.accepted_at, "snapshot.accepted_at")
    if _clock(knowledge_cutoff_at) > _clock(generated_at):
        raise ValueError("knowledge cutoff cannot postdate queue generation")
    if _clock(snapshot_accepted_at) > _clock(generated_at):
        raise ValueError("snapshot acceptance cannot postdate queue generation")
    if snapshot.candidate_count != len(snapshot.scan.candidates):
        raise ValueError("verified snapshot candidate count is inconsistent")
    if not 0 < snapshot.candidate_count <= EEA_REVIEW_MAX_CANDIDATES:
        raise ValueError("verified snapshot candidate count is out of bounds")

    candidates = tuple(
        sorted(
            (
                _queue_candidate_from_source(source)
                for source in snapshot.scan.candidates
            ),
            key=lambda item: (
                item.review_priority,
                item.country_code,
                item.facility_name or "",
                item.facility_inspire_id,
            ),
        )
    )
    payload = _queue_payload(
        generated_at=generated_at,
        knowledge_cutoff_at=knowledge_cutoff_at,
        snapshot_accepted_at=snapshot_accepted_at,
        snapshot_manifest_sha256=_sha256(
            snapshot.manifest_sha256, "snapshot.manifest_sha256"
        ),
        source_candidate_sha256=_sha256(
            snapshot.candidate_sha256, "snapshot.candidate_sha256"
        ),
        source_candidate_count=snapshot.candidate_count,
        candidates=candidates,
    )
    return parse_eea_industrial_review_queue_bytes(_canonical_json_bytes(payload))


def propose_eea_industrial_review_queue(
    snapshot_root: str | Path,
    *,
    generated_at: str,
    knowledge_cutoff_at: str,
) -> EEAIndustrialReviewQueue:
    """Deep-verify a snapshot, then build its decision-free review queue."""

    snapshot = verify_eea_industrial_snapshot(snapshot_root)
    return build_eea_industrial_review_queue(
        snapshot,
        generated_at=generated_at,
        knowledge_cutoff_at=knowledge_cutoff_at,
    )


def read_eea_industrial_review_queue_file(
    path: str | Path,
) -> EEAIndustrialReviewQueue:
    """Safely read one canonical EEA review queue from a regular file."""

    raw, absolute = _read_regular(path, "EEA industrial review queue")
    return replace(parse_eea_industrial_review_queue_bytes(raw), path=absolute)


def _evidence_url(value: object, context: str) -> str:
    url = _text(value, context, maximum_characters=4_096)
    try:
        parsed = urlsplit(url)
        _ = parsed.port
    except ValueError as error:
        raise ValueError(f"{context} must be a valid HTTPS URL") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"{context} must be an HTTPS URL without user information")
    for key, _value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
        if normalized in _SECRET_QUERY_KEYS:
            raise ValueError(f"{context} contains a secret-bearing query key")
    return url


def _parse_evidence(
    value: object,
    context: str,
    *,
    knowledge_cutoff_at: str,
) -> EEAIndustrialReviewEvidence:
    payload = _object(value, context)
    if set(payload) != _EVIDENCE_FIELDS:
        raise ValueError(f"{context} must contain exactly the required fields")
    accessed_at = _timestamp(payload["accessed_at"], f"{context}.accessed_at")
    if _clock(accessed_at) > _clock(knowledge_cutoff_at):
        raise ValueError(f"{context}.accessed_at exceeds the knowledge cutoff")
    return EEAIndustrialReviewEvidence(
        url=_evidence_url(payload["url"], f"{context}.url"),
        title=_text(payload["title"], f"{context}.title", maximum_characters=512),
        accessed_at=accessed_at,
        excerpt=_text(
            payload["excerpt"],
            f"{context}.excerpt",
            maximum_characters=4_000,
        ),
    )


def _parse_decision(
    value: object,
    index: int,
    *,
    knowledge_cutoff_at: str,
) -> EEAIndustrialReviewDecision:
    context = f"decisions[{index}]"
    payload = _object(value, context)
    if set(payload) != _DECISION_FIELDS:
        raise ValueError(f"{context} must contain exactly the required fields")
    outcome = _text(payload["outcome"], f"{context}.outcome")
    if outcome not in EEA_REVIEW_OUTCOMES:
        raise ValueError(
            f"{context}.outcome must be accept_in_scope, defer, or reject_out_of_scope"
        )
    raw_evidence = payload["evidence"]
    if not isinstance(raw_evidence, list):
        raise ValueError(f"{context}.evidence must be a JSON array")
    evidence = tuple(
        _parse_evidence(
            item,
            f"{context}.evidence[{evidence_index}]",
            knowledge_cutoff_at=knowledge_cutoff_at,
        )
        for evidence_index, item in enumerate(raw_evidence)
    )
    evidence_order = tuple(
        (item.url, item.accessed_at, item.title, item.excerpt) for item in evidence
    )
    if evidence_order != tuple(sorted(evidence_order)):
        raise ValueError(f"{context}.evidence must be sorted")
    urls = tuple(item.url for item in evidence)
    if len(urls) != len(set(urls)):
        raise ValueError(f"{context}.evidence URLs must be unique")
    if outcome != "defer" and not evidence:
        raise ValueError(f"{context}.{outcome} requires external evidence")
    return EEAIndustrialReviewDecision(
        candidate_id=_candidate_id(payload["candidate_id"], f"{context}.candidate_id"),
        outcome=outcome,
        reason=_text(payload["reason"], f"{context}.reason", maximum_characters=4_000),
        evidence=evidence,
    )


def parse_eea_industrial_review_bytes(
    raw: bytes,
    *,
    queue: EEAIndustrialReviewQueue,
) -> EEAIndustrialReview:
    """Parse a complete review bound byte-for-byte to one candidate queue."""

    payload = _load_strict_json(raw, "EEA industrial review")
    if set(payload) != _REVIEW_TOP_LEVEL_FIELDS:
        raise ValueError("EEA industrial review schema drifted")
    if payload["format"] != EEA_REVIEW_FORMAT:
        raise ValueError(f"format must be {EEA_REVIEW_FORMAT!r}")
    if payload["filter_version"] != queue.filter_version:
        raise ValueError("review filter version disagrees with the queue")
    review_candidate_count = _positive_integer(
        payload["candidate_count"],
        "candidate_count",
        EEA_REVIEW_MAX_CANDIDATES,
    )
    bindings = {
        "candidate_queue_sha256": queue.raw_sha256,
        "snapshot_manifest_sha256": queue.snapshot_manifest_sha256,
        "source_candidate_sha256": queue.source_candidate_sha256,
    }
    for field, expected in bindings.items():
        if payload[field] != expected:
            raise ValueError(f"review {field} disagrees with the queue")
    if review_candidate_count != queue.source_candidate_count:
        raise ValueError("review candidate_count disagrees with the queue")
    reviewed_at = _timestamp(payload["reviewed_at"], "reviewed_at")
    if _clock(reviewed_at) < _clock(queue.generated_at):
        raise ValueError("review cannot predate queue generation")
    knowledge_cutoff_at = _timestamp(
        payload["knowledge_cutoff_at"], "knowledge_cutoff_at"
    )
    if _clock(knowledge_cutoff_at) < _clock(queue.knowledge_cutoff_at):
        raise ValueError("review knowledge cutoff cannot predate the queue cutoff")
    if _clock(knowledge_cutoff_at) > _clock(reviewed_at):
        raise ValueError("review knowledge cutoff cannot postdate the review")
    reviewed_by = _text(payload["reviewed_by"], "reviewed_by", maximum_characters=512)

    raw_decisions = payload["decisions"]
    if not isinstance(raw_decisions, list):
        raise ValueError("decisions must be a JSON array")
    if len(raw_decisions) != queue.source_candidate_count:
        raise ValueError("review must decide every queue candidate exactly once")
    decisions = tuple(
        _parse_decision(
            item,
            index,
            knowledge_cutoff_at=knowledge_cutoff_at,
        )
        for index, item in enumerate(raw_decisions)
    )
    decision_ids = tuple(item.candidate_id for item in decisions)
    expected_ids = tuple(sorted(item.candidate_id for item in queue.candidates))
    if decision_ids != expected_ids:
        raise ValueError(
            "review decisions must cover the exact queue candidate IDs in sorted order"
        )

    return EEAIndustrialReview(
        format=EEA_REVIEW_FORMAT,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
        candidate_queue_sha256=queue.raw_sha256,
        snapshot_manifest_sha256=queue.snapshot_manifest_sha256,
        source_candidate_sha256=queue.source_candidate_sha256,
        filter_version=queue.filter_version,
        knowledge_cutoff_at=knowledge_cutoff_at,
        decisions=decisions,
        raw_bytes=raw,
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        path=None,
    )


def read_eea_industrial_review_file(
    path: str | Path,
    *,
    queue: EEAIndustrialReviewQueue,
) -> EEAIndustrialReview:
    """Safely read and validate one complete EEA adjudication ledger."""

    raw, absolute = _read_regular(path, "EEA industrial review")
    return replace(parse_eea_industrial_review_bytes(raw, queue=queue), path=absolute)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build an evidence-neutral EEA candidate queue or validate a complete "
            "evidence-bound review."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    propose = subparsers.add_parser("propose")
    propose.add_argument("--snapshot", type=Path, required=True)
    propose.add_argument("--generated-at", required=True)
    propose.add_argument("--knowledge-cutoff-at", required=True)
    propose.add_argument("--output", type=Path, required=True)
    validate = subparsers.add_parser("validate-review")
    validate.add_argument("--candidates", type=Path, required=True)
    validate.add_argument("--review", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "propose":
            queue = propose_eea_industrial_review_queue(
                args.snapshot,
                generated_at=args.generated_at,
                knowledge_cutoff_at=args.knowledge_cutoff_at,
            )
            output = _write_new_regular(args.output, queue.raw_bytes)
            print(
                json.dumps(
                    {
                        "candidate_count": queue.source_candidate_count,
                        "candidate_queue_sha256": queue.raw_sha256,
                        "output": str(output),
                        "snapshot_manifest_sha256": (queue.snapshot_manifest_sha256),
                        "source_candidate_sha256": queue.source_candidate_sha256,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        queue = read_eea_industrial_review_queue_file(args.candidates)
        review = read_eea_industrial_review_file(args.review, queue=queue)
        counts: dict[str, int] = {outcome: 0 for outcome in EEA_REVIEW_OUTCOMES}
        for decision in review.decisions:
            counts[decision.outcome] += 1
        print(
            json.dumps(
                {
                    "candidate_count": len(review.decisions),
                    "candidate_queue_sha256": review.candidate_queue_sha256,
                    "outcomes": counts,
                    "review_sha256": review.raw_sha256,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except (FileExistsError, OSError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


__all__ = [
    "EEAIndustrialReview",
    "EEAIndustrialReviewDecision",
    "EEAIndustrialReviewEvidence",
    "EEAIndustrialReviewFunction",
    "EEAIndustrialReviewQueue",
    "EEAIndustrialReviewQueueCandidate",
    "EEA_REVIEW_FORMAT",
    "EEA_REVIEW_MAX_BYTES",
    "EEA_REVIEW_MAX_CANDIDATES",
    "EEA_REVIEW_OUTCOMES",
    "EEA_REVIEW_POLICY_VERSION",
    "EEA_REVIEW_QUEUE_FORMAT",
    "build_eea_industrial_review_queue",
    "parse_eea_industrial_review_bytes",
    "parse_eea_industrial_review_queue_bytes",
    "propose_eea_industrial_review_queue",
    "read_eea_industrial_review_file",
    "read_eea_industrial_review_queue_file",
]


if __name__ == "__main__":
    raise SystemExit(main())
