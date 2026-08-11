"""Reviewed, evidence-bound identity resolution for Taiwan facility records.

Candidate generation is deliberately read-only.  It proposes a facility match only
when one invariant, nonblank MOENV ``facno`` can be normalized by the documented
factory-registration adapter and joined to an exact registration number imported
from Taiwan's factory registry.  Names, addresses, and business numbers are review
evidence; they are never candidate-generation keys.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import re
import sqlite3
import stat
import sys
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .adapters.taiwan_factory_registry import (
    normalize_factory_registration_number,
)
from .database import connect, schema_version
from .models import (
    EntityResolutionCandidate,
    EntityResolutionDecision,
    EntityResolutionRun,
    EntityResolutionRunInput,
    EntityResolutionStatus,
    ResolutionDecisionOutcome,
    SourceEntityAssignment,
)
from .moenv_snapshot import VerifiedMOENVSnapshot, verify_moenv_snapshot
from .repository import (
    add_entity_resolution_candidate,
    add_entity_resolution_decision,
    add_entity_resolution_run,
    add_entity_resolution_run_input,
    add_source_entity_assignment,
    finalize_entity_resolution_run,
    stable_id,
    supersede_source_entity_assignment,
    validate_database,
)
from .taiwan_factory_snapshot import (
    VerifiedTaiwanFactorySnapshot,
    verify_taiwan_factory_snapshot,
)


TAIWAN_FACILITY_CANDIDATE_FORMAT = (
    "semiconductor-atlas-taiwan-facility-candidates-v1"
)
TAIWAN_FACILITY_REVIEW_FORMAT = "semiconductor-atlas-taiwan-facility-review-v1"
TAIWAN_FACILITY_RESOLVER_VERSION = (
    "taiwan-moenv-factory-registration-reviewed-v1"
)
TAIWAN_FACILITY_MAX_CANDIDATES = 5_000
TAIWAN_FACILITY_MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
TAIWAN_FACILITY_MIN_SCHEMA_VERSION = 4

MOENV_SOURCE_KEY = "taiwan-moenv-ems:ems_s_01"
FACTORY_SOURCE_KEY = "taiwan-ida-factory:registered-factories"
MOENV_FACTORY_NUMBER_PREDICATE = "moenv.facno"
FACTORY_NUMBER_PREDICATE = (
    "taiwan_factory_registry.factory_registration_number"
)

_MOENV_PREDICATES = {
    MOENV_FACTORY_NUMBER_PREDICATE: "factory_registration_numbers",
    "name": "names",
    "address.street": "addresses",
    "moenv.uniformno": "unified_business_numbers",
}
_FACTORY_PREDICATES = {
    FACTORY_NUMBER_PREDICATE: "factory_registration_numbers",
    "name": "names",
    "address.street": "addresses",
    "taiwan_factory_registry.unified_business_number": (
        "unified_business_numbers"
    ),
    "taiwan_factory_registry.registration_status": "registration_statuses",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FACTORY_NUMBER_RE = re.compile(r"^[A-Z0-9]{8}$")
_OUTCOMES = frozenset({"match", "reject", "defer"})
_NORMALIZATION_METHODS = frozenset(
    {"exact_8_character_registration", "documented_legacy_certificate"}
)
_READ_CHUNK_BYTES = 64 * 1024
_SAVEPOINTS = itertools.count()

_BINDING_FIELDS = {
    "source_key",
    "ingestion_run_id",
    "ingestion_run_sha256",
    "input_document_id",
    "snapshot_manifest_sha256",
    "snapshot_candidate_sha256",
    "snapshot_raw_sha256",
    "evidence_producer_ingestion_runs",
}
_INGESTION_IDENTITY_FIELDS = {"ingestion_run_id", "ingestion_run_sha256"}
_EVIDENCE_VALUE_FIELDS = {
    "claim_version_id",
    "raw_value",
    "source_record_id",
    "source_record_key",
}
_OBSERVED_EVIDENCE_FIELDS = {
    "factory_registration_number",
    "names",
    "addresses",
    "unified_business_numbers",
}
_TARGET_EVIDENCE_FIELDS = _OBSERVED_EVIDENCE_FIELDS | {
    "registration_statuses"
}
_CANDIDATE_FIELDS = {
    "candidate_id",
    "observed_source_record_id",
    "observed_source_record_key",
    "observed_entity_id",
    "observed_entity_stable_key",
    "observed_entity_kind",
    "target_source_record_id",
    "target_source_record_key",
    "target_entity_id",
    "target_entity_stable_key",
    "target_entity_kind",
    "raw_factory_registration_number",
    "normalized_factory_registration_number",
    "normalization_method",
    "observed_evidence",
    "target_evidence",
    "observed_evidence_claim_version_ids",
    "target_evidence_claim_version_ids",
    "score",
    "candidate_rank",
}
_CANDIDATE_TOP_LEVEL_FIELDS = {
    "format",
    "database_schema_version",
    "database_schema_sha256",
    "knowledge_cutoff_at",
    "moenv",
    "factory_registry",
    "candidates",
}
_REVIEW_TOP_LEVEL_FIELDS = {
    "format",
    "candidate_artifact_canonical_sha256",
    "reviewed_by",
    "reviewed_at",
    "decisions",
}
_REVIEW_DECISION_FIELDS = {
    "candidate_id",
    "outcome",
    "reason",
    "assignment_valid_from",
}


@dataclass(frozen=True, slots=True)
class TaiwanFacilityIngestionIdentity:
    """Immutable identity of one succeeded evidence-producing ingestion run."""

    ingestion_run_id: str
    ingestion_run_sha256: str


@dataclass(frozen=True, slots=True)
class TaiwanFacilitySourceBinding:
    """Snapshot and immutable ingestion identity for one side of the join."""

    source_key: str
    ingestion_run_id: str
    ingestion_run_sha256: str
    input_document_id: str
    snapshot_manifest_sha256: str
    snapshot_candidate_sha256: str
    snapshot_raw_sha256: str
    evidence_producer_ingestion_runs: tuple[
        TaiwanFacilityIngestionIdentity, ...
    ]


@dataclass(frozen=True, slots=True, order=True)
class TaiwanFacilityEvidenceValue:
    """One exact raw scalar and the claim version that states it."""

    raw_value: str
    claim_version_id: str
    source_record_id: str
    source_record_key: str


@dataclass(frozen=True, slots=True)
class TaiwanFacilityCandidate:
    """One registration-number proposal and all evidence shown to review."""

    candidate_id: str
    observed_source_record_id: str
    observed_source_record_key: str
    observed_entity_id: str
    observed_entity_stable_key: str
    target_source_record_id: str
    target_source_record_key: str
    target_entity_id: str
    target_entity_stable_key: str
    raw_factory_registration_number: str
    normalized_factory_registration_number: str
    normalization_method: str
    observed_factory_registration_number: TaiwanFacilityEvidenceValue
    observed_names: tuple[TaiwanFacilityEvidenceValue, ...]
    observed_addresses: tuple[TaiwanFacilityEvidenceValue, ...]
    observed_unified_business_numbers: tuple[TaiwanFacilityEvidenceValue, ...]
    target_factory_registration_number: TaiwanFacilityEvidenceValue
    target_names: tuple[TaiwanFacilityEvidenceValue, ...]
    target_addresses: tuple[TaiwanFacilityEvidenceValue, ...]
    target_unified_business_numbers: tuple[TaiwanFacilityEvidenceValue, ...]
    target_registration_statuses: tuple[TaiwanFacilityEvidenceValue, ...]
    observed_evidence_claim_version_ids: tuple[str, ...]
    target_evidence_claim_version_ids: tuple[str, ...]
    score: float
    candidate_rank: int
    observed_entity_kind: str = "facility"
    target_entity_kind: str = "facility"


@dataclass(frozen=True, slots=True)
class TaiwanFacilityCandidateArtifact:
    """Strict candidate artifact with raw and canonical byte identities."""

    format: str
    database_schema_version: int
    database_schema_sha256: str
    knowledge_cutoff_at: str
    moenv: TaiwanFacilitySourceBinding
    factory_registry: TaiwanFacilitySourceBinding
    candidates: tuple[TaiwanFacilityCandidate, ...]
    raw_bytes: bytes
    raw_sha256: str
    canonical_bytes: bytes
    canonical_sha256: str
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class TaiwanFacilityReviewDecision:
    """Human disposition of exactly one proposed candidate."""

    candidate_id: str
    outcome: str
    reason: str
    assignment_valid_from: str | None


@dataclass(frozen=True, slots=True)
class TaiwanFacilityReviewArtifact:
    """Complete review bound to one canonical candidate artifact."""

    format: str
    candidate_artifact_canonical_sha256: str
    reviewed_by: str
    reviewed_at: str
    decisions: tuple[TaiwanFacilityReviewDecision, ...]
    raw_bytes: bytes
    raw_sha256: str
    canonical_bytes: bytes
    canonical_sha256: str
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class TaiwanFacilityAcceptanceResult:
    """Identifiers and write counts for a completed or exact replayed review."""

    resolution_run_id: str
    candidate_ids: tuple[str, ...]
    decision_ids: tuple[str, ...]
    assignment_ids: tuple[str, ...]
    superseded_assignment_ids: tuple[str, ...]
    reaffirmed_assignment_ids: tuple[str, ...]
    replayed: bool
    rows_written: int


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


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _object(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a JSON object")
    return value


def _clean_text(value: object, context: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(
            ord(character) < 0x20
            or 0x7F <= ord(character) <= 0x9F
            or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        )
    ):
        raise ValueError(f"{context} must be a non-empty clean string")
    return value


def _optional_clean_text(value: object, context: str) -> str | None:
    if value is None:
        return None
    return _clean_text(value, context)


def _raw_source_text(value: object, context: str) -> str:
    """Validate exact source text without silently trimming or cleaning it."""

    if (
        not isinstance(value, str)
        or not value
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    ):
        raise ValueError(f"{context} must be non-empty Unicode source text")
    return value


def _sha256(value: object, context: str) -> str:
    text = _clean_text(value, context)
    if not _SHA256_RE.fullmatch(text):
        raise ValueError(f"{context} must be a lowercase SHA-256 digest")
    return text


def _canonical_timestamp(value: object, context: str) -> str:
    text = _clean_text(value, context)
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


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _canonical_date(value: object, context: str) -> str:
    text = _clean_text(value, context)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"{context} must use YYYY-MM-DD") from error
    if parsed.isoformat() != text:
        raise ValueError(f"{context} must use YYYY-MM-DD")
    return text


def _score(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must be a finite number between 0 and 1")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError(f"{context} must be finite") from error
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError(f"{context} must be a finite number between 0 and 1")
    return result


def _positive_integer(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{context} must be a positive integer")
    return value


def _strict_json(raw: bytes, context: str) -> object:
    if not isinstance(raw, bytes):
        raise TypeError(f"{context} must be bytes")
    if not raw:
        raise ValueError(f"{context} must not be empty")
    if len(raw) > TAIWAN_FACILITY_MAX_ARTIFACT_BYTES:
        raise ValueError(f"{context} exceeds the byte limit")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError(f"{context} must be valid UTF-8") from error
    try:
        return json.loads(
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


def _query(
    connection: sqlite3.Connection,
    sql: str,
    parameters: Sequence[object] = (),
) -> list[dict[str, Any]]:
    cursor = connection.execute(sql, parameters)
    columns = tuple(item[0] for item in cursor.description or ())
    return [
        dict(row) if isinstance(row, sqlite3.Row) else dict(zip(columns, row, strict=True))
        for row in cursor.fetchall()
    ]


def _schema_identity(connection: sqlite3.Connection) -> tuple[int, str]:
    version = schema_version(connection)
    if version < TAIWAN_FACILITY_MIN_SCHEMA_VERSION:
        raise ValueError(
            "Taiwan facility identity requires database schema version 4 or later"
        )
    rows = _query(
        connection,
        "SELECT version, name, sha256 FROM schema_migrations ORDER BY version",
    )
    payload = [
        {
            "version": int(row["version"]),
            "name": str(row["name"]),
            "sha256": str(row["sha256"]),
        }
        for row in rows
    ]
    return version, hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _binding_payload(binding: TaiwanFacilitySourceBinding) -> dict[str, object]:
    return {
        "source_key": binding.source_key,
        "ingestion_run_id": binding.ingestion_run_id,
        "ingestion_run_sha256": binding.ingestion_run_sha256,
        "input_document_id": binding.input_document_id,
        "snapshot_manifest_sha256": binding.snapshot_manifest_sha256,
        "snapshot_candidate_sha256": binding.snapshot_candidate_sha256,
        "snapshot_raw_sha256": binding.snapshot_raw_sha256,
        "evidence_producer_ingestion_runs": [
            {
                "ingestion_run_id": item.ingestion_run_id,
                "ingestion_run_sha256": item.ingestion_run_sha256,
            }
            for item in binding.evidence_producer_ingestion_runs
        ],
    }


def _parse_binding(value: object, context: str) -> TaiwanFacilitySourceBinding:
    payload = _object(value, context)
    if set(payload) != _BINDING_FIELDS:
        raise ValueError(f"{context} must contain exactly the required fields")
    raw_producers = payload["evidence_producer_ingestion_runs"]
    if not isinstance(raw_producers, list) or not raw_producers:
        raise ValueError(
            f"{context}.evidence_producer_ingestion_runs must be non-empty"
        )
    producers: list[TaiwanFacilityIngestionIdentity] = []
    for index, item in enumerate(raw_producers):
        producer_context = f"{context}.evidence_producer_ingestion_runs[{index}]"
        producer = _object(item, producer_context)
        if set(producer) != _INGESTION_IDENTITY_FIELDS:
            raise ValueError(
                f"{producer_context} must contain exactly the required fields"
            )
        producers.append(
            TaiwanFacilityIngestionIdentity(
                ingestion_run_id=_clean_text(
                    producer["ingestion_run_id"],
                    f"{producer_context}.ingestion_run_id",
                ),
                ingestion_run_sha256=_sha256(
                    producer["ingestion_run_sha256"],
                    f"{producer_context}.ingestion_run_sha256",
                ),
            )
        )
    producer_ids = tuple(item.ingestion_run_id for item in producers)
    if producer_ids != tuple(sorted(set(producer_ids))):
        raise ValueError(
            f"{context}.evidence_producer_ingestion_runs must be sorted and unique"
        )
    ingestion_run_id = _clean_text(
        payload["ingestion_run_id"], f"{context}.ingestion_run_id"
    )
    if ingestion_run_id not in producer_ids:
        raise ValueError(f"{context} selected ingestion run must be a declared input")
    ingestion_run_sha256 = _sha256(
        payload["ingestion_run_sha256"], f"{context}.ingestion_run_sha256"
    )
    selected_identity = next(
        item for item in producers if item.ingestion_run_id == ingestion_run_id
    )
    if selected_identity.ingestion_run_sha256 != ingestion_run_sha256:
        raise ValueError(f"{context} selected ingestion run hash conflicts")
    return TaiwanFacilitySourceBinding(
        source_key=_clean_text(payload["source_key"], f"{context}.source_key"),
        ingestion_run_id=ingestion_run_id,
        ingestion_run_sha256=ingestion_run_sha256,
        input_document_id=_clean_text(
            payload["input_document_id"], f"{context}.input_document_id"
        ),
        snapshot_manifest_sha256=_sha256(
            payload["snapshot_manifest_sha256"],
            f"{context}.snapshot_manifest_sha256",
        ),
        snapshot_candidate_sha256=_sha256(
            payload["snapshot_candidate_sha256"],
            f"{context}.snapshot_candidate_sha256",
        ),
        snapshot_raw_sha256=_sha256(
            payload["snapshot_raw_sha256"],
            f"{context}.snapshot_raw_sha256",
        ),
        evidence_producer_ingestion_runs=tuple(producers),
    )


def _evidence_value_payload(
    value: TaiwanFacilityEvidenceValue,
) -> dict[str, str]:
    return {
        "claim_version_id": value.claim_version_id,
        "raw_value": value.raw_value,
        "source_record_id": value.source_record_id,
        "source_record_key": value.source_record_key,
    }


def _parse_evidence_value(
    value: object, context: str
) -> TaiwanFacilityEvidenceValue:
    payload = _object(value, context)
    if set(payload) != _EVIDENCE_VALUE_FIELDS:
        raise ValueError(f"{context} must contain exactly the required fields")
    return TaiwanFacilityEvidenceValue(
        raw_value=_raw_source_text(
            payload["raw_value"], f"{context}.raw_value"
        ),
        claim_version_id=_clean_text(
            payload["claim_version_id"], f"{context}.claim_version_id"
        ),
        source_record_id=_clean_text(
            payload["source_record_id"], f"{context}.source_record_id"
        ),
        source_record_key=_clean_text(
            payload["source_record_key"], f"{context}.source_record_key"
        ),
    )


def _evidence_values_payload(
    values: Iterable[TaiwanFacilityEvidenceValue],
) -> list[dict[str, str]]:
    return [_evidence_value_payload(value) for value in values]


def _parse_evidence_values(
    value: object, context: str
) -> tuple[TaiwanFacilityEvidenceValue, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be a JSON array")
    result = tuple(
        _parse_evidence_value(item, f"{context}[{index}]")
        for index, item in enumerate(value)
    )
    if result != tuple(sorted(result)):
        raise ValueError(
            f"{context} must be sorted by raw value, claim ID, and source record"
        )
    links = tuple(
        (item.claim_version_id, item.source_record_id) for item in result
    )
    if len(links) != len(set(links)):
        raise ValueError(f"{context} must not repeat an evidence link")
    return result


def _evidence_ids(values: Iterable[TaiwanFacilityEvidenceValue]) -> tuple[str, ...]:
    return tuple(sorted({item.claim_version_id for item in values}))


def _ids_array(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{context} must be a non-empty JSON array")
    result = tuple(
        _clean_text(item, f"{context}[{index}]")
        for index, item in enumerate(value)
    )
    if result != tuple(sorted(set(result))):
        raise ValueError(f"{context} must be sorted and unique")
    return result


def _candidate_payload(candidate: TaiwanFacilityCandidate) -> dict[str, object]:
    return {
        "candidate_id": candidate.candidate_id,
        "observed_source_record_id": candidate.observed_source_record_id,
        "observed_source_record_key": candidate.observed_source_record_key,
        "observed_entity_id": candidate.observed_entity_id,
        "observed_entity_stable_key": candidate.observed_entity_stable_key,
        "observed_entity_kind": candidate.observed_entity_kind,
        "target_source_record_id": candidate.target_source_record_id,
        "target_source_record_key": candidate.target_source_record_key,
        "target_entity_id": candidate.target_entity_id,
        "target_entity_stable_key": candidate.target_entity_stable_key,
        "target_entity_kind": candidate.target_entity_kind,
        "raw_factory_registration_number": (
            candidate.raw_factory_registration_number
        ),
        "normalized_factory_registration_number": (
            candidate.normalized_factory_registration_number
        ),
        "normalization_method": candidate.normalization_method,
        "observed_evidence": {
            "factory_registration_number": _evidence_value_payload(
                candidate.observed_factory_registration_number
            ),
            "names": _evidence_values_payload(candidate.observed_names),
            "addresses": _evidence_values_payload(candidate.observed_addresses),
            "unified_business_numbers": _evidence_values_payload(
                candidate.observed_unified_business_numbers
            ),
        },
        "target_evidence": {
            "factory_registration_number": _evidence_value_payload(
                candidate.target_factory_registration_number
            ),
            "names": _evidence_values_payload(candidate.target_names),
            "addresses": _evidence_values_payload(candidate.target_addresses),
            "unified_business_numbers": _evidence_values_payload(
                candidate.target_unified_business_numbers
            ),
            "registration_statuses": _evidence_values_payload(
                candidate.target_registration_statuses
            ),
        },
        "observed_evidence_claim_version_ids": list(
            candidate.observed_evidence_claim_version_ids
        ),
        "target_evidence_claim_version_ids": list(
            candidate.target_evidence_claim_version_ids
        ),
        "score": candidate.score,
        "candidate_rank": candidate.candidate_rank,
    }


def _candidate_stable_id(
    *,
    observed_source_record_id: str,
    observed_entity_id: str,
    target_source_record_id: str,
    target_entity_id: str,
    normalized_number: str,
    observed_number_claim_id: str,
    target_number_claim_id: str,
) -> str:
    return stable_id(
        "taiwan-facility-candidate-v1",
        observed_source_record_id,
        observed_entity_id,
        target_source_record_id,
        target_entity_id,
        normalized_number,
        observed_number_claim_id,
        target_number_claim_id,
    )


def _parse_candidate(value: object, index: int) -> TaiwanFacilityCandidate:
    context = f"candidates[{index}]"
    payload = _object(value, context)
    if set(payload) != _CANDIDATE_FIELDS:
        raise ValueError(f"{context} must contain exactly the required fields")
    if payload["observed_entity_kind"] != "facility":
        raise ValueError(f"{context}.observed_entity_kind must be facility")
    if payload["target_entity_kind"] != "facility":
        raise ValueError(f"{context}.target_entity_kind must be facility")

    raw_number = _clean_text(
        payload["raw_factory_registration_number"],
        f"{context}.raw_factory_registration_number",
    )
    normalized_number = _clean_text(
        payload["normalized_factory_registration_number"],
        f"{context}.normalized_factory_registration_number",
    )
    if not _FACTORY_NUMBER_RE.fullmatch(normalized_number):
        raise ValueError(
            f"{context}.normalized_factory_registration_number must have 8 uppercase characters"
        )
    if normalize_factory_registration_number(raw_number) != normalized_number:
        raise ValueError(f"{context} uses an undocumented registration normalization")
    method = _clean_text(
        payload["normalization_method"], f"{context}.normalization_method"
    )
    expected_method = (
        "exact_8_character_registration"
        if raw_number.upper() == normalized_number
        and _FACTORY_NUMBER_RE.fullmatch(raw_number.upper())
        else "documented_legacy_certificate"
    )
    if method not in _NORMALIZATION_METHODS or method != expected_method:
        raise ValueError(f"{context}.normalization_method conflicts with the raw number")

    observed = _object(payload["observed_evidence"], f"{context}.observed_evidence")
    target = _object(payload["target_evidence"], f"{context}.target_evidence")
    if set(observed) != _OBSERVED_EVIDENCE_FIELDS:
        raise ValueError(f"{context}.observed_evidence has unexpected fields")
    if set(target) != _TARGET_EVIDENCE_FIELDS:
        raise ValueError(f"{context}.target_evidence has unexpected fields")
    observed_number = _parse_evidence_value(
        observed["factory_registration_number"],
        f"{context}.observed_evidence.factory_registration_number",
    )
    target_number = _parse_evidence_value(
        target["factory_registration_number"],
        f"{context}.target_evidence.factory_registration_number",
    )
    if observed_number.raw_value != raw_number:
        raise ValueError(f"{context} raw observed registration evidence conflicts")
    if target_number.raw_value != normalized_number:
        raise ValueError(f"{context} target registration evidence must be exact")
    observed_names = _parse_evidence_values(
        observed["names"], f"{context}.observed_evidence.names"
    )
    observed_addresses = _parse_evidence_values(
        observed["addresses"], f"{context}.observed_evidence.addresses"
    )
    observed_ubns = _parse_evidence_values(
        observed["unified_business_numbers"],
        f"{context}.observed_evidence.unified_business_numbers",
    )
    target_names = _parse_evidence_values(
        target["names"], f"{context}.target_evidence.names"
    )
    target_addresses = _parse_evidence_values(
        target["addresses"], f"{context}.target_evidence.addresses"
    )
    target_ubns = _parse_evidence_values(
        target["unified_business_numbers"],
        f"{context}.target_evidence.unified_business_numbers",
    )
    target_statuses = _parse_evidence_values(
        target["registration_statuses"],
        f"{context}.target_evidence.registration_statuses",
    )
    observed_ids = _ids_array(
        payload["observed_evidence_claim_version_ids"],
        f"{context}.observed_evidence_claim_version_ids",
    )
    target_ids = _ids_array(
        payload["target_evidence_claim_version_ids"],
        f"{context}.target_evidence_claim_version_ids",
    )
    expected_observed_ids = _evidence_ids(
        (observed_number, *observed_names, *observed_addresses, *observed_ubns)
    )
    expected_target_ids = _evidence_ids(
        (target_number, *target_names, *target_addresses, *target_ubns, *target_statuses)
    )
    if observed_ids != expected_observed_ids or target_ids != expected_target_ids:
        raise ValueError(f"{context} evidence ID arrays do not exactly describe evidence")

    observed_record_id = _clean_text(
        payload["observed_source_record_id"],
        f"{context}.observed_source_record_id",
    )
    observed_entity_id = _clean_text(
        payload["observed_entity_id"], f"{context}.observed_entity_id"
    )
    target_record_id = _clean_text(
        payload["target_source_record_id"], f"{context}.target_source_record_id"
    )
    target_entity_id = _clean_text(
        payload["target_entity_id"], f"{context}.target_entity_id"
    )
    observed_record_key = _clean_text(
        payload["observed_source_record_key"],
        f"{context}.observed_source_record_key",
    )
    target_record_key = _clean_text(
        payload["target_source_record_key"],
        f"{context}.target_source_record_key",
    )
    if (
        observed_number.source_record_id != observed_record_id
        or observed_number.source_record_key != observed_record_key
    ):
        raise ValueError(
            f"{context} observed candidate record must be the registration evidence record"
        )
    if (
        target_number.source_record_id != target_record_id
        or target_number.source_record_key != target_record_key
    ):
        raise ValueError(
            f"{context} target candidate record must be the registration evidence record"
        )
    if observed_entity_id == target_entity_id:
        raise ValueError(f"{context} must compare distinct facility entities")
    candidate_id = _clean_text(payload["candidate_id"], f"{context}.candidate_id")
    expected_id = _candidate_stable_id(
        observed_source_record_id=observed_record_id,
        observed_entity_id=observed_entity_id,
        target_source_record_id=target_record_id,
        target_entity_id=target_entity_id,
        normalized_number=normalized_number,
        observed_number_claim_id=observed_number.claim_version_id,
        target_number_claim_id=target_number.claim_version_id,
    )
    if candidate_id != expected_id:
        raise ValueError(f"{context}.candidate_id is not deterministic")
    return TaiwanFacilityCandidate(
        candidate_id=candidate_id,
        observed_source_record_id=observed_record_id,
        observed_source_record_key=observed_record_key,
        observed_entity_id=observed_entity_id,
        observed_entity_stable_key=_clean_text(
            payload["observed_entity_stable_key"],
            f"{context}.observed_entity_stable_key",
        ),
        target_source_record_id=target_record_id,
        target_source_record_key=target_record_key,
        target_entity_id=target_entity_id,
        target_entity_stable_key=_clean_text(
            payload["target_entity_stable_key"],
            f"{context}.target_entity_stable_key",
        ),
        raw_factory_registration_number=raw_number,
        normalized_factory_registration_number=normalized_number,
        normalization_method=method,
        observed_factory_registration_number=observed_number,
        observed_names=observed_names,
        observed_addresses=observed_addresses,
        observed_unified_business_numbers=observed_ubns,
        target_factory_registration_number=target_number,
        target_names=target_names,
        target_addresses=target_addresses,
        target_unified_business_numbers=target_ubns,
        target_registration_statuses=target_statuses,
        observed_evidence_claim_version_ids=observed_ids,
        target_evidence_claim_version_ids=target_ids,
        score=_score(payload["score"], f"{context}.score"),
        candidate_rank=_positive_integer(
            payload["candidate_rank"], f"{context}.candidate_rank"
        ),
    )


def _candidate_artifact_payload(
    *,
    database_schema_version: int,
    database_schema_sha256: str,
    knowledge_cutoff_at: str,
    moenv: TaiwanFacilitySourceBinding,
    factory_registry: TaiwanFacilitySourceBinding,
    candidates: Sequence[TaiwanFacilityCandidate],
) -> dict[str, object]:
    return {
        "format": TAIWAN_FACILITY_CANDIDATE_FORMAT,
        "database_schema_version": database_schema_version,
        "database_schema_sha256": database_schema_sha256,
        "knowledge_cutoff_at": knowledge_cutoff_at,
        "moenv": _binding_payload(moenv),
        "factory_registry": _binding_payload(factory_registry),
        "candidates": [_candidate_payload(candidate) for candidate in candidates],
    }


def parse_taiwan_facility_candidate_bytes(
    raw: bytes,
) -> TaiwanFacilityCandidateArtifact:
    """Strictly parse and canonicalize one candidate artifact."""

    value = _strict_json(raw, "Taiwan facility candidate artifact")
    payload = _object(value, "Taiwan facility candidate artifact")
    if set(payload) != _CANDIDATE_TOP_LEVEL_FIELDS:
        raise ValueError(
            "Taiwan facility candidate artifact must contain exactly the required fields"
        )
    if payload["format"] != TAIWAN_FACILITY_CANDIDATE_FORMAT:
        raise ValueError(f"format must be {TAIWAN_FACILITY_CANDIDATE_FORMAT!r}")
    version = payload["database_schema_version"]
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version < TAIWAN_FACILITY_MIN_SCHEMA_VERSION
    ):
        raise ValueError("database_schema_version must be an integer at least 4")
    schema_sha = _sha256(
        payload["database_schema_sha256"], "database_schema_sha256"
    )
    cutoff = _canonical_timestamp(payload["knowledge_cutoff_at"], "knowledge_cutoff_at")
    moenv = _parse_binding(payload["moenv"], "moenv")
    factory = _parse_binding(payload["factory_registry"], "factory_registry")
    if moenv.source_key != MOENV_SOURCE_KEY:
        raise ValueError("moenv binding has the wrong source key")
    if factory.source_key != FACTORY_SOURCE_KEY:
        raise ValueError("factory_registry binding has the wrong source key")
    if moenv.ingestion_run_id == factory.ingestion_run_id:
        raise ValueError("candidate sides must use distinct ingestion runs")
    candidate_values = payload["candidates"]
    if not isinstance(candidate_values, list):
        raise ValueError("candidates must be a JSON array")
    if len(candidate_values) > TAIWAN_FACILITY_MAX_CANDIDATES:
        raise ValueError("candidate artifact exceeds the candidate-count limit")
    candidates = tuple(
        _parse_candidate(item, index)
        for index, item in enumerate(candidate_values)
    )
    ids = tuple(item.candidate_id for item in candidates)
    if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
        raise ValueError("candidates must be sorted by unique candidate_id")
    observed = tuple(item.observed_entity_id for item in candidates)
    if len(observed) != len(set(observed)):
        raise ValueError("candidate artifact must contain at most one target per observed facility")
    canonical = _canonical_json_bytes(
        _candidate_artifact_payload(
            database_schema_version=version,
            database_schema_sha256=schema_sha,
            knowledge_cutoff_at=cutoff,
            moenv=moenv,
            factory_registry=factory,
            candidates=candidates,
        )
    )
    return TaiwanFacilityCandidateArtifact(
        format=TAIWAN_FACILITY_CANDIDATE_FORMAT,
        database_schema_version=version,
        database_schema_sha256=schema_sha,
        knowledge_cutoff_at=cutoff,
        moenv=moenv,
        factory_registry=factory,
        candidates=candidates,
        raw_bytes=raw,
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        canonical_bytes=canonical,
        canonical_sha256=hashlib.sha256(canonical).hexdigest(),
    )


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


def _read_regular_file(path: str | Path, context: str) -> tuple[Path, bytes]:
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
        if before.st_size < 0 or before.st_size > TAIWAN_FACILITY_MAX_ARTIFACT_BYTES:
            raise ValueError(f"{context} exceeds the byte limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            remaining = TAIWAN_FACILITY_MAX_ARTIFACT_BYTES + 1 - total
            chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > TAIWAN_FACILITY_MAX_ARTIFACT_BYTES:
                raise ValueError(f"{context} exceeds the byte limit")
        after = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(after):
            raise ValueError(f"{context} changed while being read")
        raw = b"".join(chunks)
        if len(raw) != before.st_size:
            raise ValueError(f"{context} size changed while being read")
        try:
            final_path = os.stat(candidate, follow_symlinks=False)
        except OSError as error:
            raise ValueError(f"{context} path changed while being read") from error
        if _stat_identity(after) != _stat_identity(final_path):
            raise ValueError(f"{context} path changed while being read")
    finally:
        os.close(descriptor)
    return candidate, raw


def read_taiwan_facility_candidate_file(
    path: str | Path,
) -> TaiwanFacilityCandidateArtifact:
    """Read a regular, non-symlink candidate artifact with bounded I/O."""

    candidate, raw = _read_regular_file(path, "Taiwan facility candidate artifact")
    return replace(parse_taiwan_facility_candidate_bytes(raw), path=candidate)


def _verified_moenv_snapshot(
    value: str | Path | VerifiedMOENVSnapshot,
) -> VerifiedMOENVSnapshot:
    root = value.root if isinstance(value, VerifiedMOENVSnapshot) else value
    return verify_moenv_snapshot(root)


def _verified_factory_snapshot(
    value: str | Path | VerifiedTaiwanFactorySnapshot,
) -> VerifiedTaiwanFactorySnapshot:
    root = value.root if isinstance(value, VerifiedTaiwanFactorySnapshot) else value
    return verify_taiwan_factory_snapshot(root)


def _json_object_from_database(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, str):
        raise ValueError(f"{context} is not stored JSON text")
    try:
        parsed = json.loads(
            value,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, _DuplicateJSONKey, ValueError) as error:
        raise ValueError(f"{context} is not strict JSON") from error
    return _object(parsed, context)


def _nested_hash(
    parameters: Mapping[str, Any], key: str, context: str
) -> str | None:
    value = parameters.get(key)
    if not isinstance(value, dict):
        return None
    digest = value.get("sha256")
    return digest if isinstance(digest, str) else None


def _ingestion_binding(
    connection: sqlite3.Connection,
    *,
    ingestion_run_id: str,
    expected_source_key: str,
    snapshot_manifest_sha256: str,
    snapshot_candidate_sha256: str,
    snapshot_raw_sha256: str,
    cutoff: str,
) -> TaiwanFacilitySourceBinding:
    rows = _query(
        connection,
        """
        SELECT runs.id, runs.source_id, runs.input_document_id,
               runs.started_at, runs.completed_at, runs.status,
               runs.code_version, runs.parameters_json, runs.error,
               sources.stable_key AS source_key,
               documents.content_sha256 AS input_document_sha256
        FROM ingestion_runs AS runs
        JOIN sources ON sources.id = runs.source_id
        LEFT JOIN source_documents AS documents
          ON documents.id = runs.input_document_id
        WHERE runs.id = ?
        """,
        (ingestion_run_id,),
    )
    if len(rows) != 1:
        raise ValueError(f"unknown ingestion run: {ingestion_run_id}")
    row = rows[0]
    if row["source_key"] != expected_source_key:
        raise ValueError(f"ingestion run {ingestion_run_id} has the wrong source")
    if row["status"] != "succeeded" or row["completed_at"] is None:
        raise ValueError(f"ingestion run {ingestion_run_id} did not succeed")
    completed_at = _canonical_timestamp(
        row["completed_at"], f"ingestion run {ingestion_run_id}.completed_at"
    )
    if _timestamp(completed_at) > _timestamp(cutoff):
        raise ValueError(f"ingestion run {ingestion_run_id} postdates the cutoff")
    input_document_id = _clean_text(
        row["input_document_id"],
        f"ingestion run {ingestion_run_id}.input_document_id",
    )
    if row["input_document_sha256"] != snapshot_candidate_sha256:
        raise ValueError(
            f"ingestion run {ingestion_run_id} input document conflicts with snapshot"
        )
    parameters = _json_object_from_database(
        row["parameters_json"], f"ingestion run {ingestion_run_id}.parameters_json"
    )
    if parameters.get("manifest_sha256") != snapshot_manifest_sha256:
        raise ValueError(
            f"ingestion run {ingestion_run_id} manifest binding conflicts"
        )
    candidate_parameter = _nested_hash(parameters, "candidate_derivative", "candidate")
    raw_parameter = _nested_hash(parameters, "raw_archive", "raw")
    if candidate_parameter != snapshot_candidate_sha256:
        raise ValueError(
            f"ingestion run {ingestion_run_id} candidate derivative conflicts"
        )
    if raw_parameter != snapshot_raw_sha256:
        raise ValueError(f"ingestion run {ingestion_run_id} raw archive conflicts")
    run_payload = {
        "id": str(row["id"]),
        "source_id": str(row["source_id"]),
        "input_document_id": input_document_id,
        "started_at": _canonical_timestamp(
            row["started_at"], f"ingestion run {ingestion_run_id}.started_at"
        ),
        "completed_at": completed_at,
        "status": "succeeded",
        "code_version": row["code_version"],
        "parameters": parameters,
        "error": row["error"],
    }
    run_digest = hashlib.sha256(_canonical_json_bytes(run_payload)).hexdigest()
    return TaiwanFacilitySourceBinding(
        source_key=expected_source_key,
        ingestion_run_id=ingestion_run_id,
        ingestion_run_sha256=run_digest,
        input_document_id=input_document_id,
        snapshot_manifest_sha256=snapshot_manifest_sha256,
        snapshot_candidate_sha256=snapshot_candidate_sha256,
        snapshot_raw_sha256=snapshot_raw_sha256,
        evidence_producer_ingestion_runs=(
            TaiwanFacilityIngestionIdentity(ingestion_run_id, run_digest),
        ),
    )


def _ingestion_run_identity(
    connection: sqlite3.Connection,
    *,
    ingestion_run_id: str,
    expected_source_key: str,
    cutoff: str,
) -> TaiwanFacilityIngestionIdentity:
    rows = _query(
        connection,
        """
        SELECT runs.id, runs.source_id, runs.input_document_id,
               runs.started_at, runs.completed_at, runs.status,
               runs.code_version, runs.parameters_json, runs.error,
               sources.stable_key AS source_key
        FROM ingestion_runs AS runs
        JOIN sources ON sources.id = runs.source_id
        WHERE runs.id = ?
        """,
        (ingestion_run_id,),
    )
    if len(rows) != 1:
        raise ValueError(f"unknown evidence producer run: {ingestion_run_id}")
    row = rows[0]
    if row["source_key"] != expected_source_key:
        raise ValueError(f"evidence producer {ingestion_run_id} has the wrong source")
    if row["status"] != "succeeded" or row["completed_at"] is None:
        raise ValueError(f"evidence producer {ingestion_run_id} did not succeed")
    completed_at = _canonical_timestamp(
        row["completed_at"], f"evidence producer {ingestion_run_id}.completed_at"
    )
    if _timestamp(completed_at) > _timestamp(cutoff):
        raise ValueError(f"evidence producer {ingestion_run_id} postdates the cutoff")
    parameters = _json_object_from_database(
        row["parameters_json"],
        f"evidence producer {ingestion_run_id}.parameters_json",
    )
    payload = {
        "id": str(row["id"]),
        "source_id": str(row["source_id"]),
        "input_document_id": row["input_document_id"],
        "started_at": _canonical_timestamp(
            row["started_at"], f"evidence producer {ingestion_run_id}.started_at"
        ),
        "completed_at": completed_at,
        "status": "succeeded",
        "code_version": row["code_version"],
        "parameters": parameters,
        "error": row["error"],
    }
    return TaiwanFacilityIngestionIdentity(
        ingestion_run_id=ingestion_run_id,
        ingestion_run_sha256=hashlib.sha256(
            _canonical_json_bytes(payload)
        ).hexdigest(),
    )


def _with_evidence_producers(
    connection: sqlite3.Connection,
    *,
    binding: TaiwanFacilitySourceBinding,
    producer_ids: Iterable[str],
    cutoff: str,
) -> TaiwanFacilitySourceBinding:
    identities = tuple(
        _ingestion_run_identity(
            connection,
            ingestion_run_id=run_id,
            expected_source_key=binding.source_key,
            cutoff=cutoff,
        )
        for run_id in sorted(set(producer_ids) | {binding.ingestion_run_id})
    )
    return replace(binding, evidence_producer_ingestion_runs=identities)


def _current_source_scalars(
    connection: sqlite3.Connection,
    *,
    ingestion_run_id: str,
    cutoff: str,
    predicates: Mapping[str, str],
) -> dict[str, dict[str, Any]]:
    """Return selected-run entity state with exact mixed-record evidence.

    ``cutoff`` bounds the selected succeeded run itself.  Entity membership and
    state are then frozen at that run's acceptance timestamp, so later runs from
    the same source cannot leak into an older artifact.  Reused claims may retain
    evidence records from older producer runs.
    """

    placeholders = ",".join("?" for _ in predicates)
    rows = _query(
        connection,
        f"""
        WITH selected_state AS (
            SELECT id, source_id, started_at AS state_at
            FROM ingestion_runs
            WHERE id = ?
              AND status = 'succeeded'
              AND julianday(completed_at) <= julianday(?)
        ),
        selected_entities AS (
            SELECT records.id AS selected_source_record_id,
                   records.source_record_key AS selected_source_record_key,
                   records.observed_at AS selected_observed_at,
                   entities.id AS entity_id,
                   entities.stable_key AS entity_stable_key,
                   entities.kind AS entity_kind
            FROM selected_state
            JOIN source_records AS records
              ON records.ingestion_run_id = selected_state.id
            JOIN entities ON entities.stable_key = records.source_record_key
            WHERE entities.kind = 'facility'
        )
        SELECT DISTINCT selected_entities.selected_source_record_id,
               selected_entities.selected_source_record_key,
               entities.id AS entity_id,
               entities.stable_key AS entity_stable_key,
               entities.kind AS entity_kind,
               series.predicate,
               versions.id AS claim_version_id,
               versions.created_by_run_id AS producer_ingestion_run_id,
               evidence_records.id AS evidence_source_record_id,
               evidence_records.source_record_key AS evidence_source_record_key,
               evidence_records.ingestion_run_id AS evidence_record_ingestion_run_id,
               scalar_values.text_value AS raw_value
        FROM selected_state
        JOIN selected_entities ON 1 = 1
        JOIN entities ON entities.id = selected_entities.entity_id
        JOIN claim_series AS series
          ON series.subject_entity_id = selected_entities.entity_id
        JOIN claim_versions AS versions ON versions.series_id = series.id
        JOIN claim_evidence AS evidence
          ON evidence.claim_version_id = versions.id
         AND evidence.source_record_id IS NOT NULL
        JOIN source_records AS evidence_records
          ON evidence_records.id = evidence.source_record_id
         AND evidence_records.source_document_id = evidence.source_document_id
        JOIN scalar_values ON scalar_values.claim_version_id = versions.id
        JOIN ingestion_runs AS producer ON producer.id = versions.created_by_run_id
        JOIN ingestion_runs AS evidence_record_run
          ON evidence_record_run.id = evidence_records.ingestion_run_id
        WHERE producer.source_id = selected_state.source_id
          AND evidence_record_run.source_id = selected_state.source_id
          AND scalar_values.scalar_type IN ('text', 'date', 'timestamp')
          AND series.predicate IN ({placeholders})
          AND producer.status = 'succeeded'
          AND (
              producer.id = selected_state.id
              OR julianday(producer.completed_at) <= julianday(selected_state.state_at)
          )
          AND evidence_record_run.status = 'succeeded'
          AND (
              evidence_record_run.id = selected_state.id
              OR julianday(evidence_record_run.completed_at)
                  <= julianday(selected_state.state_at)
          )
          AND julianday(evidence_records.observed_at)
              <= julianday(selected_state.state_at)
          AND julianday(selected_entities.selected_observed_at)
              <= julianday(selected_state.state_at)
          AND julianday(entities.created_at) <= julianday(selected_state.state_at)
          AND julianday(versions.recorded_at) <= julianday(selected_state.state_at)
          AND (
              versions.superseded_at IS NULL
              OR julianday(versions.superseded_at) > julianday(selected_state.state_at)
          )
        ORDER BY entities.id, series.predicate, scalar_values.text_value,
                 versions.id, evidence_records.id
        """,
        (
            ingestion_run_id,
            cutoff,
            *tuple(predicates),
        ),
    )
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row["entity_id"])
        group = grouped.setdefault(
            key,
            {
                "selected_source_record_id": str(
                    row["selected_source_record_id"]
                ),
                "selected_source_record_key": str(
                    row["selected_source_record_key"]
                ),
                "entity_id": str(row["entity_id"]),
                "entity_stable_key": str(row["entity_stable_key"]),
                "entity_kind": str(row["entity_kind"]),
                "factory_registration_numbers": [],
                "names": [],
                "addresses": [],
                "unified_business_numbers": [],
                "registration_statuses": [],
                "producer_ingestion_run_ids": set(),
            },
        )
        raw_value = row["raw_value"]
        if not isinstance(raw_value, str) or not raw_value:
            continue
        field = predicates[str(row["predicate"])]
        evidence_value = TaiwanFacilityEvidenceValue(
            raw_value=raw_value,
            claim_version_id=str(row["claim_version_id"]),
            source_record_id=str(row["evidence_source_record_id"]),
            source_record_key=str(row["evidence_source_record_key"]),
        )
        if evidence_value not in group[field]:
            group[field].append(evidence_value)
        group["producer_ingestion_run_ids"].add(
            str(row["producer_ingestion_run_id"])
        )
        group["producer_ingestion_run_ids"].add(
            str(row["evidence_record_ingestion_run_id"])
        )
    for group in grouped.values():
        for field in (
            "factory_registration_numbers",
            "names",
            "addresses",
            "unified_business_numbers",
            "registration_statuses",
        ):
            group[field] = tuple(sorted(group[field]))
        group["producer_ingestion_run_ids"] = tuple(
            sorted(group["producer_ingestion_run_ids"])
        )
    return grouped


def _make_candidate(
    observed: Mapping[str, Any],
    target: Mapping[str, Any],
    observed_number: TaiwanFacilityEvidenceValue,
    normalized_number: str,
) -> TaiwanFacilityCandidate:
    target_numbers = target["factory_registration_numbers"]
    if len(target_numbers) != 1 or target_numbers[0].raw_value != normalized_number:
        raise ValueError(
            f"factory registry target {normalized_number} lacks one exact registration claim"
        )
    target_number = target_numbers[0]
    method = (
        "exact_8_character_registration"
        if observed_number.raw_value.upper() == normalized_number
        and _FACTORY_NUMBER_RE.fullmatch(observed_number.raw_value.upper())
        else "documented_legacy_certificate"
    )
    observed_values = (
        observed_number,
        *observed["names"],
        *observed["addresses"],
        *observed["unified_business_numbers"],
    )
    target_values = (
        target_number,
        *target["names"],
        *target["addresses"],
        *target["unified_business_numbers"],
        *target["registration_statuses"],
    )
    candidate_id = _candidate_stable_id(
        observed_source_record_id=observed_number.source_record_id,
        observed_entity_id=observed["entity_id"],
        target_source_record_id=target_number.source_record_id,
        target_entity_id=target["entity_id"],
        normalized_number=normalized_number,
        observed_number_claim_id=observed_number.claim_version_id,
        target_number_claim_id=target_number.claim_version_id,
    )
    return TaiwanFacilityCandidate(
        candidate_id=candidate_id,
        observed_source_record_id=observed_number.source_record_id,
        observed_source_record_key=observed_number.source_record_key,
        observed_entity_id=observed["entity_id"],
        observed_entity_stable_key=observed["entity_stable_key"],
        target_source_record_id=target_number.source_record_id,
        target_source_record_key=target_number.source_record_key,
        target_entity_id=target["entity_id"],
        target_entity_stable_key=target["entity_stable_key"],
        raw_factory_registration_number=observed_number.raw_value,
        normalized_factory_registration_number=normalized_number,
        normalization_method=method,
        observed_factory_registration_number=observed_number,
        observed_names=observed["names"],
        observed_addresses=observed["addresses"],
        observed_unified_business_numbers=observed["unified_business_numbers"],
        target_factory_registration_number=target_number,
        target_names=target["names"],
        target_addresses=target["addresses"],
        target_unified_business_numbers=target["unified_business_numbers"],
        target_registration_statuses=target["registration_statuses"],
        observed_evidence_claim_version_ids=_evidence_ids(observed_values),
        target_evidence_claim_version_ids=_evidence_ids(target_values),
        score=1.0 if method == "exact_8_character_registration" else 0.99,
        candidate_rank=1,
    )


def propose_taiwan_facility_candidates(
    connection: sqlite3.Connection,
    *,
    knowledge_cutoff_at: str,
    moenv_ingestion_run_id: str,
    factory_ingestion_run_id: str,
    moenv_snapshot: str | Path | VerifiedMOENVSnapshot,
    factory_snapshot: str | Path | VerifiedTaiwanFactorySnapshot,
) -> TaiwanFacilityCandidateArtifact:
    """Propose deterministic facility matches without writing to the database.

    Only one invariant, nonblank MOENV ``facno`` is eligible.  The adapter's
    documented normalizer must produce a registration number that exactly occurs
    once in the selected factory-registry ingestion.
    """

    cutoff = _canonical_timestamp(knowledge_cutoff_at, "knowledge_cutoff_at")
    moenv_verified = _verified_moenv_snapshot(moenv_snapshot)
    factory_verified = _verified_factory_snapshot(factory_snapshot)
    version, schema_sha = _schema_identity(connection)
    moenv_binding = _ingestion_binding(
        connection,
        ingestion_run_id=_clean_text(
            moenv_ingestion_run_id, "moenv_ingestion_run_id"
        ),
        expected_source_key=MOENV_SOURCE_KEY,
        snapshot_manifest_sha256=moenv_verified.manifest_sha256,
        snapshot_candidate_sha256=moenv_verified.candidate_sha256,
        snapshot_raw_sha256=moenv_verified.raw_sha256,
        cutoff=cutoff,
    )
    factory_binding = _ingestion_binding(
        connection,
        ingestion_run_id=_clean_text(
            factory_ingestion_run_id, "factory_ingestion_run_id"
        ),
        expected_source_key=FACTORY_SOURCE_KEY,
        snapshot_manifest_sha256=factory_verified.manifest_sha256,
        snapshot_candidate_sha256=factory_verified.candidate_sha256,
        snapshot_raw_sha256=factory_verified.raw_sha256,
        cutoff=cutoff,
    )
    if moenv_binding.ingestion_run_id == factory_binding.ingestion_run_id:
        raise ValueError("MOENV and factory-registry ingestion runs must be distinct")

    observed_groups = _current_source_scalars(
        connection,
        ingestion_run_id=moenv_binding.ingestion_run_id,
        cutoff=cutoff,
        predicates=_MOENV_PREDICATES,
    )
    target_groups = _current_source_scalars(
        connection,
        ingestion_run_id=factory_binding.ingestion_run_id,
        cutoff=cutoff,
        predicates=_FACTORY_PREDICATES,
    )
    targets: dict[str, Mapping[str, Any]] = {}
    for target in target_groups.values():
        numbers = target["factory_registration_numbers"]
        if len(numbers) != 1:
            continue
        raw = numbers[0].raw_value
        normalized = normalize_factory_registration_number(raw)
        if normalized != raw or not _FACTORY_NUMBER_RE.fullmatch(raw):
            raise ValueError(
                "factory-registry ingestion contains a non-exact registration number"
            )
        if raw in targets:
            raise ValueError(f"factory-registry registration {raw} is not unique")
        targets[raw] = target

    candidates: list[TaiwanFacilityCandidate] = []
    observed_producer_ids = {moenv_binding.ingestion_run_id}
    target_producer_ids = {factory_binding.ingestion_run_id}
    for observed in observed_groups.values():
        numbers = observed["factory_registration_numbers"]
        # Invariance is intentional: conflicting MOENV variants are evidence for
        # review, never a license to choose one registration number.
        if len(numbers) != 1:
            continue
        observed_number = numbers[0]
        normalized = normalize_factory_registration_number(observed_number.raw_value)
        if normalized is None:
            continue
        target = targets.get(normalized)
        if target is None:
            continue
        candidates.append(
            _make_candidate(observed, target, observed_number, normalized)
        )
        observed_producer_ids.update(observed["producer_ingestion_run_ids"])
        target_producer_ids.update(target["producer_ingestion_run_ids"])
    candidates.sort(key=lambda item: item.candidate_id)
    if len(candidates) > TAIWAN_FACILITY_MAX_CANDIDATES:
        raise ValueError("proposal exceeds the candidate-count limit")
    moenv_binding = _with_evidence_producers(
        connection,
        binding=moenv_binding,
        producer_ids=observed_producer_ids,
        cutoff=cutoff,
    )
    factory_binding = _with_evidence_producers(
        connection,
        binding=factory_binding,
        producer_ids=target_producer_ids,
        cutoff=cutoff,
    )
    artifact_payload = _candidate_artifact_payload(
        database_schema_version=version,
        database_schema_sha256=schema_sha,
        knowledge_cutoff_at=cutoff,
        moenv=moenv_binding,
        factory_registry=factory_binding,
        candidates=candidates,
    )
    canonical = _canonical_json_bytes(artifact_payload)
    # Parsing the generated form applies the same exact-field and ordering rules
    # used for an artifact supplied later to acceptance.
    return parse_taiwan_facility_candidate_bytes(canonical)


def _review_decision_payload(
    decision: TaiwanFacilityReviewDecision,
) -> dict[str, object]:
    return {
        "candidate_id": decision.candidate_id,
        "outcome": decision.outcome,
        "reason": decision.reason,
        "assignment_valid_from": decision.assignment_valid_from,
    }


def _parse_review_decision(
    value: object, index: int
) -> TaiwanFacilityReviewDecision:
    context = f"decisions[{index}]"
    payload = _object(value, context)
    if set(payload) != _REVIEW_DECISION_FIELDS:
        raise ValueError(f"{context} must contain exactly the required fields")
    outcome = _clean_text(payload["outcome"], f"{context}.outcome")
    if outcome not in _OUTCOMES:
        raise ValueError(f"{context}.outcome must be match, reject, or defer")
    assignment = payload["assignment_valid_from"]
    if outcome == "match":
        assignment = _canonical_date(
            assignment, f"{context}.assignment_valid_from"
        )
    elif assignment is not None:
        raise ValueError(
            f"{context}.assignment_valid_from must be null unless outcome is match"
        )
    return TaiwanFacilityReviewDecision(
        candidate_id=_clean_text(
            payload["candidate_id"], f"{context}.candidate_id"
        ),
        outcome=outcome,
        reason=_clean_text(payload["reason"], f"{context}.reason"),
        assignment_valid_from=assignment,
    )


def _review_payload(
    *,
    candidate_artifact_canonical_sha256: str,
    reviewed_by: str,
    reviewed_at: str,
    decisions: Sequence[TaiwanFacilityReviewDecision],
) -> dict[str, object]:
    return {
        "format": TAIWAN_FACILITY_REVIEW_FORMAT,
        "candidate_artifact_canonical_sha256": (
            candidate_artifact_canonical_sha256
        ),
        "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at,
        "decisions": [_review_decision_payload(item) for item in decisions],
    }


def parse_taiwan_facility_review_bytes(
    raw: bytes,
    *,
    candidate_artifact: TaiwanFacilityCandidateArtifact,
) -> TaiwanFacilityReviewArtifact:
    """Parse a strict, complete review bound to ``candidate_artifact``."""

    candidate_artifact = _reparse_candidate_artifact(candidate_artifact)
    value = _strict_json(raw, "Taiwan facility review artifact")
    payload = _object(value, "Taiwan facility review artifact")
    if set(payload) != _REVIEW_TOP_LEVEL_FIELDS:
        raise ValueError(
            "Taiwan facility review artifact must contain exactly the required fields"
        )
    if payload["format"] != TAIWAN_FACILITY_REVIEW_FORMAT:
        raise ValueError(f"format must be {TAIWAN_FACILITY_REVIEW_FORMAT!r}")
    candidate_sha = _sha256(
        payload["candidate_artifact_canonical_sha256"],
        "candidate_artifact_canonical_sha256",
    )
    if candidate_sha != candidate_artifact.canonical_sha256:
        raise ValueError("review is bound to a different candidate artifact")
    reviewed_by = _clean_text(payload["reviewed_by"], "reviewed_by")
    reviewed_at = _canonical_timestamp(payload["reviewed_at"], "reviewed_at")
    if _timestamp(reviewed_at) < _timestamp(candidate_artifact.knowledge_cutoff_at):
        raise ValueError("reviewed_at must not predate the candidate knowledge cutoff")
    raw_decisions = payload["decisions"]
    if not isinstance(raw_decisions, list):
        raise ValueError("decisions must be a JSON array")
    if len(raw_decisions) > TAIWAN_FACILITY_MAX_CANDIDATES:
        raise ValueError("review exceeds the decision-count limit")
    decisions = tuple(
        _parse_review_decision(item, index)
        for index, item in enumerate(raw_decisions)
    )
    ids = tuple(item.candidate_id for item in decisions)
    expected_ids = tuple(item.candidate_id for item in candidate_artifact.candidates)
    if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
        raise ValueError("review decisions must be sorted by unique candidate_id")
    if ids != expected_ids:
        raise ValueError("review must contain exactly one decision for every candidate")
    canonical = _canonical_json_bytes(
        _review_payload(
            candidate_artifact_canonical_sha256=candidate_sha,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
            decisions=decisions,
        )
    )
    return TaiwanFacilityReviewArtifact(
        format=TAIWAN_FACILITY_REVIEW_FORMAT,
        candidate_artifact_canonical_sha256=candidate_sha,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
        decisions=decisions,
        raw_bytes=raw,
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        canonical_bytes=canonical,
        canonical_sha256=hashlib.sha256(canonical).hexdigest(),
    )


def build_taiwan_facility_review(
    candidate_artifact: TaiwanFacilityCandidateArtifact,
    *,
    reviewed_by: str,
    reviewed_at: str,
    decisions: Sequence[TaiwanFacilityReviewDecision],
) -> TaiwanFacilityReviewArtifact:
    """Canonicalize programmatically supplied, complete review decisions."""

    candidate_artifact = _reparse_candidate_artifact(candidate_artifact)
    payload = _review_payload(
        candidate_artifact_canonical_sha256=candidate_artifact.canonical_sha256,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
        decisions=tuple(decisions),
    )
    return parse_taiwan_facility_review_bytes(
        _canonical_json_bytes(payload), candidate_artifact=candidate_artifact
    )


def read_taiwan_facility_review_file(
    path: str | Path,
    *,
    candidate_artifact: TaiwanFacilityCandidateArtifact,
) -> TaiwanFacilityReviewArtifact:
    """Read a regular, non-symlink complete review with bounded I/O."""

    review_path, raw = _read_regular_file(path, "Taiwan facility review artifact")
    return replace(
        parse_taiwan_facility_review_bytes(
            raw, candidate_artifact=candidate_artifact
        ),
        path=review_path,
    )


def _reparse_candidate_artifact(
    artifact: TaiwanFacilityCandidateArtifact,
) -> TaiwanFacilityCandidateArtifact:
    if not isinstance(artifact, TaiwanFacilityCandidateArtifact):
        raise TypeError("candidate_artifact must be a TaiwanFacilityCandidateArtifact")
    reparsed = parse_taiwan_facility_candidate_bytes(artifact.raw_bytes)
    if (
        hashlib.sha256(artifact.raw_bytes).hexdigest() != artifact.raw_sha256
        or reparsed.raw_bytes != artifact.raw_bytes
        or reparsed.raw_sha256 != artifact.raw_sha256
        or reparsed.canonical_sha256 != artifact.canonical_sha256
        or reparsed.canonical_bytes != artifact.canonical_bytes
    ):
        raise ValueError("candidate artifact object has inconsistent canonical identity")
    return replace(reparsed, path=artifact.path)


def _reparse_review_artifact(
    artifact: TaiwanFacilityReviewArtifact,
    candidate_artifact: TaiwanFacilityCandidateArtifact,
) -> TaiwanFacilityReviewArtifact:
    if not isinstance(artifact, TaiwanFacilityReviewArtifact):
        raise TypeError("review_artifact must be a TaiwanFacilityReviewArtifact")
    reparsed = parse_taiwan_facility_review_bytes(
        artifact.raw_bytes, candidate_artifact=candidate_artifact
    )
    if (
        hashlib.sha256(artifact.raw_bytes).hexdigest() != artifact.raw_sha256
        or reparsed.raw_bytes != artifact.raw_bytes
        or reparsed.raw_sha256 != artifact.raw_sha256
        or reparsed.canonical_sha256 != artifact.canonical_sha256
        or reparsed.canonical_bytes != artifact.canonical_bytes
    ):
        raise ValueError("review artifact object has inconsistent canonical identity")
    return replace(reparsed, path=artifact.path)


def _load_candidate_artifact(
    value: TaiwanFacilityCandidateArtifact | bytes | str | Path,
) -> TaiwanFacilityCandidateArtifact:
    if isinstance(value, TaiwanFacilityCandidateArtifact):
        return _reparse_candidate_artifact(value)
    if isinstance(value, bytes):
        return parse_taiwan_facility_candidate_bytes(value)
    if isinstance(value, (str, Path)):
        return read_taiwan_facility_candidate_file(value)
    raise TypeError("candidate_artifact must be an artifact, bytes, or file path")


def _load_review_artifact(
    value: TaiwanFacilityReviewArtifact | bytes | str | Path,
    *,
    candidate_artifact: TaiwanFacilityCandidateArtifact,
) -> TaiwanFacilityReviewArtifact:
    if isinstance(value, TaiwanFacilityReviewArtifact):
        return _reparse_review_artifact(value, candidate_artifact)
    if isinstance(value, bytes):
        return parse_taiwan_facility_review_bytes(
            value, candidate_artifact=candidate_artifact
        )
    if isinstance(value, (str, Path)):
        return read_taiwan_facility_review_file(
            value, candidate_artifact=candidate_artifact
        )
    raise TypeError("review_artifact must be an artifact, bytes, or file path")


def _snapshot_identity(value: object) -> tuple[object, ...]:
    return (
        getattr(value, "manifest_sha256"),
        getattr(value, "candidate_sha256"),
        getattr(value, "raw_sha256"),
        getattr(value, "retrieved_at"),
    )


def _assert_candidate_recomputed(
    connection: sqlite3.Connection,
    *,
    artifact: TaiwanFacilityCandidateArtifact,
    moenv_snapshot: VerifiedMOENVSnapshot,
    factory_snapshot: VerifiedTaiwanFactorySnapshot,
) -> None:
    actual = propose_taiwan_facility_candidates(
        connection,
        knowledge_cutoff_at=artifact.knowledge_cutoff_at,
        moenv_ingestion_run_id=artifact.moenv.ingestion_run_id,
        factory_ingestion_run_id=artifact.factory_registry.ingestion_run_id,
        moenv_snapshot=moenv_snapshot,
        factory_snapshot=factory_snapshot,
    )
    if (
        actual.canonical_sha256 != artifact.canonical_sha256
        or actual.canonical_bytes != artifact.canonical_bytes
    ):
        raise ValueError("candidate artifact is stale relative to the database")


def _assert_evidence_current(
    connection: sqlite3.Connection,
    *,
    artifact: TaiwanFacilityCandidateArtifact,
    recorded_at: str,
) -> None:
    """Re-verify every exact evidence value and its current source lineage."""

    for binding in (artifact.moenv, artifact.factory_registry):
        for expected_identity in binding.evidence_producer_ingestion_runs:
            actual_identity = _ingestion_run_identity(
                connection,
                ingestion_run_id=expected_identity.ingestion_run_id,
                expected_source_key=binding.source_key,
                cutoff=recorded_at,
            )
            if actual_identity != expected_identity:
                raise ValueError(
                    f"evidence producer {expected_identity.ingestion_run_id} changed"
                )

    for candidate in artifact.candidates:
        sides = (
            (
                candidate.observed_entity_id,
                frozenset(
                    item.ingestion_run_id
                    for item in artifact.moenv.evidence_producer_ingestion_runs
                ),
                (
                    candidate.observed_factory_registration_number,
                    *candidate.observed_names,
                    *candidate.observed_addresses,
                    *candidate.observed_unified_business_numbers,
                ),
            ),
            (
                candidate.target_entity_id,
                frozenset(
                    item.ingestion_run_id
                    for item in artifact.factory_registry.evidence_producer_ingestion_runs
                ),
                (
                    candidate.target_factory_registration_number,
                    *candidate.target_names,
                    *candidate.target_addresses,
                    *candidate.target_unified_business_numbers,
                    *candidate.target_registration_statuses,
                ),
            ),
        )
        for entity_id, producer_run_ids, evidence_values in sides:
            entity_rows = _query(
                connection,
                "SELECT kind FROM entities WHERE id = ?",
                (entity_id,),
            )
            if len(entity_rows) != 1 or entity_rows[0]["kind"] != "facility":
                raise ValueError("candidate entity kind changed or is not facility")
            for evidence_value in evidence_values:
                rows = _query(
                    connection,
                    """
                    SELECT DISTINCT versions.id, versions.recorded_at,
                           versions.superseded_at, versions.created_by_run_id,
                           series.subject_entity_id, series.value_kind,
                           scalar_values.text_value,
                           producer.status AS producer_status,
                           producer.completed_at AS producer_completed_at,
                           evidence_records.source_record_key,
                           evidence_records.ingestion_run_id
                    FROM claim_versions AS versions
                    JOIN claim_series AS series ON series.id = versions.series_id
                    JOIN scalar_values
                      ON scalar_values.claim_version_id = versions.id
                    JOIN claim_evidence AS evidence
                      ON evidence.claim_version_id = versions.id
                     AND evidence.source_record_id = ?
                    JOIN source_records AS evidence_records
                      ON evidence_records.id = evidence.source_record_id
                     AND evidence_records.source_document_id = evidence.source_document_id
                    LEFT JOIN ingestion_runs AS producer
                      ON producer.id = versions.created_by_run_id
                    WHERE versions.id = ?
                    """,
                    (
                        evidence_value.source_record_id,
                        evidence_value.claim_version_id,
                    ),
                )
                if len(rows) != 1:
                    raise ValueError(
                        f"missing evidence claim {evidence_value.claim_version_id}"
                    )
                row = rows[0]
                superseded_at = row["superseded_at"]
                if (
                    row["subject_entity_id"] != entity_id
                    or row["value_kind"] != "scalar"
                    or row["text_value"] != evidence_value.raw_value
                    or row["source_record_key"]
                    != evidence_value.source_record_key
                    or row["ingestion_run_id"] not in producer_run_ids
                    or row["created_by_run_id"] not in producer_run_ids
                    or row["producer_status"] != "succeeded"
                    or row["producer_completed_at"] is None
                    or _timestamp(
                        _canonical_timestamp(
                            row["recorded_at"],
                            f"evidence {evidence_value.claim_version_id}.recorded_at",
                        )
                    )
                    > _timestamp(recorded_at)
                    or _timestamp(
                        _canonical_timestamp(
                            row["producer_completed_at"],
                            f"evidence {evidence_value.claim_version_id}.producer_completed_at",
                        )
                    )
                    > _timestamp(recorded_at)
                    or (
                        superseded_at is not None
                        and _timestamp(
                            _canonical_timestamp(
                                superseded_at,
                                f"evidence {evidence_value.claim_version_id}.superseded_at",
                            )
                        )
                        <= _timestamp(recorded_at)
                    )
                ):
                    raise ValueError(
                        f"evidence claim {evidence_value.claim_version_id} is stale or conflicts"
                    )


def _candidate_features(
    candidate: TaiwanFacilityCandidate,
    candidate_artifact: TaiwanFacilityCandidateArtifact,
) -> dict[str, object]:
    return {
        "candidate_artifact_canonical_sha256": (
            candidate_artifact.canonical_sha256
        ),
        "normalization_method": candidate.normalization_method,
        "normalized_factory_registration_number": (
            candidate.normalized_factory_registration_number
        ),
        "observed_evidence_claim_version_ids": list(
            candidate.observed_evidence_claim_version_ids
        ),
        "raw_factory_registration_number": (
            candidate.raw_factory_registration_number
        ),
        "resolver_version": TAIWAN_FACILITY_RESOLVER_VERSION,
        "source_candidate_id": candidate.candidate_id,
        "target_evidence_claim_version_ids": list(
            candidate.target_evidence_claim_version_ids
        ),
        "target_source_record_id": candidate.target_source_record_id,
        "target_source_record_key": candidate.target_source_record_key,
    }


def _db_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _resolution_ids(
    candidate_artifact: TaiwanFacilityCandidateArtifact,
    review_artifact: TaiwanFacilityReviewArtifact,
    accepted_at: str,
    *,
    stale_correction: bool,
) -> tuple[
    str,
    tuple[str, ...],
    tuple[str, ...],
    tuple[str | None, ...],
]:
    run_id = stable_id(
        "entity-resolution-run",
        TAIWAN_FACILITY_RESOLVER_VERSION,
        candidate_artifact.canonical_sha256,
        review_artifact.canonical_sha256,
        accepted_at,
        str(stale_correction).casefold(),
    )
    candidate_ids = tuple(
        stable_id(
            "entity-resolution-candidate",
            TAIWAN_FACILITY_RESOLVER_VERSION,
            run_id,
            candidate.candidate_id,
        )
        for candidate in candidate_artifact.candidates
    )
    decision_ids = tuple(
        stable_id(
            "entity-resolution-decision",
            TAIWAN_FACILITY_RESOLVER_VERSION,
            candidate_id,
            review_artifact.canonical_sha256,
        )
        for candidate_id in candidate_ids
    )
    assignment_ids = tuple(
        stable_id(
            "source-entity-assignment",
            TAIWAN_FACILITY_RESOLVER_VERSION,
            decision_id,
            decision.assignment_valid_from,
            accepted_at,
        )
        if decision.outcome == "match" and not stale_correction
        else None
        for decision_id, decision in zip(
            decision_ids, review_artifact.decisions, strict=True
        )
    )
    return run_id, candidate_ids, decision_ids, assignment_ids


def _open_assignments(
    connection: sqlite3.Connection,
    observed_entity_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    if not observed_entity_ids:
        return {}
    placeholders = ",".join("?" for _ in observed_entity_ids)
    rows = _query(
        connection,
        f"""
        SELECT assignments.*, decisions.outcome,
               candidates.features_json,
               runs.status AS resolution_status,
               runs.id AS resolution_run_id
        FROM source_entity_assignments AS assignments
        JOIN entity_resolution_decisions AS decisions
          ON decisions.id = assignments.decision_id
        JOIN entity_resolution_candidates AS candidates
          ON candidates.id = decisions.candidate_id
        JOIN entity_resolution_runs AS runs
          ON runs.id = candidates.resolution_run_id
        WHERE assignments.observed_entity_id IN ({placeholders})
          AND assignments.valid_to IS NULL
          AND assignments.superseded_at IS NULL
        ORDER BY assignments.observed_entity_id
        """,
        tuple(observed_entity_ids),
    )
    return {str(row["observed_entity_id"]): row for row in rows}


def _resolution_parameters(
    *,
    candidate_artifact: TaiwanFacilityCandidateArtifact,
    review_artifact: TaiwanFacilityReviewArtifact,
    accepted_at: str,
    superseded_assignment_ids: Sequence[str],
    reaffirmed_assignment_ids: Sequence[str],
    stale_correction: bool,
) -> dict[str, object]:
    return {
        "accepted_at": accepted_at,
        "candidate_artifact_canonical_sha256": (
            candidate_artifact.canonical_sha256
        ),
        "candidate_artifact_raw_sha256": candidate_artifact.raw_sha256,
        "database_schema_sha256": candidate_artifact.database_schema_sha256,
        "database_schema_version": candidate_artifact.database_schema_version,
        "factory_registry": _binding_payload(candidate_artifact.factory_registry),
        "knowledge_cutoff_at": candidate_artifact.knowledge_cutoff_at,
        "moenv": _binding_payload(candidate_artifact.moenv),
        "review_artifact_canonical_sha256": review_artifact.canonical_sha256,
        "review_artifact_raw_sha256": review_artifact.raw_sha256,
        "reviewed_at": review_artifact.reviewed_at,
        "reaffirmed_assignment_ids": list(reaffirmed_assignment_ids),
        "stale_correction": stale_correction,
        "superseded_assignment_ids": list(superseded_assignment_ids),
    }


def _all_resolution_input_ids(
    candidate_artifact: TaiwanFacilityCandidateArtifact,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                item.ingestion_run_id
                for binding in (
                    candidate_artifact.moenv,
                    candidate_artifact.factory_registry,
                )
                for item in binding.evidence_producer_ingestion_runs
            }
        )
    )


def _expected_times(accepted_at: str) -> tuple[str, str, str, str]:
    started = _timestamp(accepted_at)
    completed = started + timedelta(seconds=1)
    decided = started + timedelta(seconds=2)
    recorded = started + timedelta(seconds=3)
    canonical = lambda item: item.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return (
        canonical(started),
        canonical(completed),
        canonical(decided),
        canonical(recorded),
    )


def _assignment_belongs_to_artifact(
    assignment: Mapping[str, Any],
    candidate: TaiwanFacilityCandidate,
    artifact: TaiwanFacilityCandidateArtifact,
) -> bool:
    try:
        features = _json_object_from_database(
            assignment["features_json"],
            f"assignment {assignment['id']}.candidate.features_json",
        )
    except (KeyError, ValueError):
        return False
    return (
        assignment.get("resolution_status") == "succeeded"
        and assignment.get("outcome") == "match"
        and assignment.get("source_record_id")
        == candidate.observed_source_record_id
        and assignment.get("observed_entity_id") == candidate.observed_entity_id
        and assignment.get("canonical_entity_id") == candidate.target_entity_id
        and features.get("candidate_artifact_canonical_sha256")
        == artifact.canonical_sha256
        and features.get("source_candidate_id") == candidate.candidate_id
    )


def _later_supersession_is_proven(
    connection: sqlite3.Connection,
    *,
    assignment_id: str,
    observed_entity_id: str,
    original_recorded_at: str,
    superseded_at: str,
) -> bool:
    if _timestamp(superseded_at) <= _timestamp(original_recorded_at):
        return False
    rows = _query(
        connection,
        """
        SELECT runs.id AS resolution_run_id, runs.started_at,
               runs.completed_at, runs.status, runs.parameters_json,
               decisions.decided_at, decisions.metadata_json,
               candidates.observed_entity_id
        FROM entity_resolution_runs AS runs
        JOIN entity_resolution_candidates AS candidates
          ON candidates.resolution_run_id = runs.id
        JOIN entity_resolution_decisions AS decisions
          ON decisions.candidate_id = candidates.id
        WHERE candidates.observed_entity_id = ?
          AND runs.status = 'succeeded'
          AND julianday(runs.started_at) > julianday(?)
        ORDER BY runs.started_at, runs.id
        """,
        (observed_entity_id, original_recorded_at),
    )
    for row in rows:
        try:
            parameters = _json_object_from_database(
                row["parameters_json"],
                f"resolution run {row['resolution_run_id']}.parameters_json",
            )
            metadata = _json_object_from_database(
                row["metadata_json"],
                f"resolution run {row['resolution_run_id']}.decision metadata",
            )
            accepted_at = _canonical_timestamp(
                parameters.get("accepted_at"),
                f"resolution run {row['resolution_run_id']}.accepted_at",
            )
            started, completed, decided, recorded = _expected_times(accepted_at)
        except ValueError:
            continue
        superseded_ids = parameters.get("superseded_assignment_ids")
        if (
            not isinstance(superseded_ids, list)
            or assignment_id not in superseded_ids
            or metadata.get("prior_assignment_id") != assignment_id
            or row["started_at"] != started
            or row["completed_at"] != completed
            or row["decided_at"] != decided
            or recorded != superseded_at
        ):
            continue
        return True
    return False


def _verify_exact_replay(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    candidate_artifact: TaiwanFacilityCandidateArtifact,
    review_artifact: TaiwanFacilityReviewArtifact,
    accepted_at: str,
    candidate_ids: Sequence[str],
    decision_ids: Sequence[str],
    assignment_ids: Sequence[str | None],
) -> TaiwanFacilityAcceptanceResult:
    rows = _query(
        connection, "SELECT * FROM entity_resolution_runs WHERE id = ?", (run_id,)
    )
    if len(rows) != 1:
        raise ValueError("resolution replay run disappeared")
    run = rows[0]
    parameters = _json_object_from_database(
        run["parameters_json"], f"resolution run {run_id}.parameters_json"
    )
    raw_superseded = parameters.get("superseded_assignment_ids")
    if not isinstance(raw_superseded, list) or any(
        not isinstance(item, str) or not item for item in raw_superseded
    ):
        raise ValueError("resolution replay has invalid supersession parameters")
    superseded_ids = tuple(raw_superseded)
    if superseded_ids != tuple(sorted(set(superseded_ids))):
        raise ValueError("resolution replay supersession IDs are not sorted and unique")
    raw_reaffirmed = parameters.get("reaffirmed_assignment_ids")
    if not isinstance(raw_reaffirmed, list) or any(
        not isinstance(item, str) or not item for item in raw_reaffirmed
    ):
        raise ValueError("resolution replay has invalid reaffirmation parameters")
    reaffirmed_ids = tuple(raw_reaffirmed)
    if reaffirmed_ids != tuple(sorted(set(reaffirmed_ids))):
        raise ValueError("resolution replay reaffirmation IDs are not sorted and unique")
    if set(reaffirmed_ids) & set(superseded_ids):
        raise ValueError("one prior assignment cannot be reaffirmed and superseded")
    stale_correction = parameters.get("stale_correction")
    if not isinstance(stale_correction, bool):
        raise ValueError("resolution replay has invalid stale-correction mode")
    expected_parameters = _resolution_parameters(
        candidate_artifact=candidate_artifact,
        review_artifact=review_artifact,
        accepted_at=accepted_at,
        superseded_assignment_ids=superseded_ids,
        reaffirmed_assignment_ids=reaffirmed_ids,
        stale_correction=stale_correction,
    )
    started_at, completed_at, decided_at, recorded_at = _expected_times(accepted_at)
    expected_run = {
        "started_at": started_at,
        "completed_at": completed_at,
        "status": "succeeded",
        "resolver_version": TAIWAN_FACILITY_RESOLVER_VERSION,
        "code_version": TAIWAN_FACILITY_RESOLVER_VERSION,
        "parameters_json": _db_json(expected_parameters),
        "error": None,
    }
    if any(run[key] != value for key, value in expected_run.items()):
        raise ValueError("existing resolution run is not an exact immutable replay")
    inputs = tuple(
        str(row["ingestion_run_id"])
        for row in _query(
            connection,
            """
            SELECT ingestion_run_id FROM entity_resolution_run_inputs
            WHERE resolution_run_id = ? ORDER BY ingestion_run_id
            """,
            (run_id,),
        )
    )
    expected_inputs = _all_resolution_input_ids(candidate_artifact)
    if inputs != expected_inputs:
        raise ValueError("resolution replay input lineage conflicts")

    persisted_candidates = _query(
        connection,
        """
        SELECT * FROM entity_resolution_candidates
        WHERE resolution_run_id = ? ORDER BY id
        """,
        (run_id,),
    )
    expected_candidate_rows: list[dict[str, object]] = []
    for persisted_id, candidate in zip(
        candidate_ids, candidate_artifact.candidates, strict=True
    ):
        expected_candidate_rows.append(
            {
                "id": persisted_id,
                "resolution_run_id": run_id,
                "source_record_id": candidate.observed_source_record_id,
                "observed_entity_id": candidate.observed_entity_id,
                "candidate_entity_id": candidate.target_entity_id,
                "features_json": _db_json(
                    _candidate_features(candidate, candidate_artifact)
                ),
                "score": float(candidate.score),
                "candidate_rank": candidate.candidate_rank,
                "created_at": started_at,
            }
        )
    expected_candidate_rows.sort(key=lambda item: str(item["id"]))
    if persisted_candidates != expected_candidate_rows:
        raise ValueError("resolution replay candidate ledger conflicts")

    persisted_decisions = _query(
        connection,
        """
        SELECT decisions.* FROM entity_resolution_decisions AS decisions
        JOIN entity_resolution_candidates AS candidates
          ON candidates.id = decisions.candidate_id
        WHERE candidates.resolution_run_id = ? ORDER BY decisions.id
        """,
        (run_id,),
    )
    prior_assignment_rows = _query(
        connection,
        """
        SELECT id, observed_entity_id, recorded_at, superseded_at
        FROM source_entity_assignments
        WHERE id IN (
            SELECT value FROM json_each(?)
        )
        ORDER BY id
        """,
        (
            json.dumps(
                list((*superseded_ids, *reaffirmed_ids)),
                separators=(",", ":"),
            ),
        ),
    ) if superseded_ids or reaffirmed_ids else []
    prior_by_observed = {
        str(row["observed_entity_id"]): str(row["id"])
        for row in prior_assignment_rows
    }
    if len(prior_assignment_rows) != len(superseded_ids) + len(reaffirmed_ids):
        raise ValueError("resolution replay prior-assignment ledger conflicts")
    expected_decision_rows: list[dict[str, object]] = []
    for decision_id, candidate_id, candidate, review_decision in zip(
        decision_ids,
        candidate_ids,
        candidate_artifact.candidates,
        review_artifact.decisions,
        strict=True,
    ):
        expected_decision_rows.append(
            {
                "id": decision_id,
                "candidate_id": candidate_id,
                "outcome": review_decision.outcome,
                "decided_at": decided_at,
                "decided_by": review_artifact.reviewed_by,
                "reason": review_decision.reason,
                "metadata_json": _db_json(
                    {
                        "candidate_artifact_canonical_sha256": (
                            candidate_artifact.canonical_sha256
                        ),
                        "review_artifact_canonical_sha256": (
                            review_artifact.canonical_sha256
                        ),
                        "prior_assignment_id": prior_by_observed.get(
                            candidate.observed_entity_id
                        ),
                        "reviewed_at": review_artifact.reviewed_at,
                        "stale_correction": stale_correction,
                    }
                ),
            }
        )
    expected_decision_rows.sort(key=lambda item: str(item["id"]))
    if persisted_decisions != expected_decision_rows:
        raise ValueError("resolution replay decision ledger conflicts")

    persisted_assignments = _query(
        connection,
        """
        SELECT assignments.* FROM source_entity_assignments AS assignments
        JOIN entity_resolution_decisions AS decisions
          ON decisions.id = assignments.decision_id
        JOIN entity_resolution_candidates AS candidates
          ON candidates.id = decisions.candidate_id
        WHERE candidates.resolution_run_id = ? ORDER BY assignments.id
        """,
        (run_id,),
    )
    expected_assignment_rows: list[dict[str, object]] = []
    for assignment_id, decision_id, candidate, review_decision in zip(
        assignment_ids,
        decision_ids,
        candidate_artifact.candidates,
        review_artifact.decisions,
        strict=True,
    ):
        if review_decision.outcome != "match" or stale_correction:
            if assignment_id is not None:
                raise AssertionError(
                    "non-creating decision unexpectedly received an assignment ID"
                )
            continue
        assert assignment_id is not None
        expected_assignment_rows.append(
            {
                "id": assignment_id,
                "source_record_id": candidate.observed_source_record_id,
                "observed_entity_id": candidate.observed_entity_id,
                "canonical_entity_id": candidate.target_entity_id,
                "decision_id": decision_id,
                "valid_from": review_decision.assignment_valid_from,
                "valid_to": None,
                "recorded_at": recorded_at,
                "superseded_at": None,
            }
        )
    expected_assignment_rows.sort(key=lambda item: str(item["id"]))
    if len(persisted_assignments) != len(expected_assignment_rows):
        raise ValueError("resolution replay assignment ledger conflicts")
    for actual, expected in zip(
        persisted_assignments, expected_assignment_rows, strict=True
    ):
        actual_superseded_at = actual["superseded_at"]
        if any(
            actual[key] != value
            for key, value in expected.items()
            if key != "superseded_at"
        ):
            raise ValueError("resolution replay assignment ledger conflicts")
        if actual_superseded_at is not None and not _later_supersession_is_proven(
            connection,
            assignment_id=str(actual["id"]),
            observed_entity_id=str(actual["observed_entity_id"]),
            original_recorded_at=str(actual["recorded_at"]),
            superseded_at=str(actual_superseded_at),
        ):
            raise ValueError(
                "resolution replay assignment has an unproven later supersession"
            )
    for superseded_id in superseded_ids:
        old = _query(
            connection,
            "SELECT superseded_at FROM source_entity_assignments WHERE id = ?",
            (superseded_id,),
        )
        if len(old) != 1 or old[0]["superseded_at"] != recorded_at:
            raise ValueError("resolution replay prior-assignment supersession conflicts")
    for reaffirmed_id in reaffirmed_ids:
        old = next(
            row for row in prior_assignment_rows if row["id"] == reaffirmed_id
        )
        later = old["superseded_at"]
        if later is not None and not _later_supersession_is_proven(
            connection,
            assignment_id=reaffirmed_id,
            observed_entity_id=str(old["observed_entity_id"]),
            original_recorded_at=str(old["recorded_at"]),
            superseded_at=str(later),
        ):
            raise ValueError(
                "resolution replay reaffirmed assignment has unproven supersession"
            )
    return TaiwanFacilityAcceptanceResult(
        resolution_run_id=run_id,
        candidate_ids=tuple(candidate_ids),
        decision_ids=tuple(decision_ids),
        assignment_ids=tuple(item for item in assignment_ids if item is not None),
        superseded_assignment_ids=superseded_ids,
        reaffirmed_assignment_ids=reaffirmed_ids,
        replayed=True,
        rows_written=0,
    )


def _begin_atomic(connection: sqlite3.Connection) -> tuple[str, str | None]:
    if connection.in_transaction:
        name = f"taiwan_facility_identity_{next(_SAVEPOINTS)}"
        connection.execute(f"SAVEPOINT {name}")
        return "savepoint", name
    connection.execute("BEGIN IMMEDIATE")
    return "transaction", None


def _commit_atomic(
    connection: sqlite3.Connection, mode: str, name: str | None
) -> None:
    if mode == "savepoint":
        assert name is not None
        connection.execute(f"RELEASE SAVEPOINT {name}")
    else:
        connection.commit()


def _rollback_atomic(
    connection: sqlite3.Connection, mode: str, name: str | None
) -> None:
    if mode == "savepoint":
        assert name is not None
        connection.execute(f"ROLLBACK TO SAVEPOINT {name}")
        connection.execute(f"RELEASE SAVEPOINT {name}")
    else:
        connection.rollback()


def _reverify_artifact_sources(
    candidate: TaiwanFacilityCandidateArtifact,
    review: TaiwanFacilityReviewArtifact,
) -> tuple[TaiwanFacilityCandidateArtifact, TaiwanFacilityReviewArtifact]:
    if candidate.path is not None:
        refreshed_candidate = read_taiwan_facility_candidate_file(candidate.path)
    else:
        refreshed_candidate = _reparse_candidate_artifact(candidate)
    if (
        refreshed_candidate.canonical_sha256 != candidate.canonical_sha256
        or refreshed_candidate.raw_sha256 != candidate.raw_sha256
    ):
        raise ValueError("candidate artifact changed during acceptance")
    if review.path is not None:
        refreshed_review = read_taiwan_facility_review_file(
            review.path, candidate_artifact=refreshed_candidate
        )
    else:
        refreshed_review = _reparse_review_artifact(review, refreshed_candidate)
    if (
        refreshed_review.canonical_sha256 != review.canonical_sha256
        or refreshed_review.raw_sha256 != review.raw_sha256
    ):
        raise ValueError("review artifact changed during acceptance")
    return refreshed_candidate, refreshed_review


def accept_taiwan_facility_review(
    connection: sqlite3.Connection,
    *,
    candidate_artifact: TaiwanFacilityCandidateArtifact | bytes | str | Path,
    review_artifact: TaiwanFacilityReviewArtifact | bytes | str | Path,
    moenv_snapshot: str | Path | VerifiedMOENVSnapshot,
    factory_snapshot: str | Path | VerifiedTaiwanFactorySnapshot,
    accepted_at: str,
    stale_correction: bool = False,
) -> TaiwanFacilityAcceptanceResult:
    """Atomically persist one complete reviewed facility-resolution artifact.

    The function re-verifies both snapshots, both artifacts, schema/cutoff
    lineage, and every evidence claim before writing.  An already-persisted exact
    run is verified through a read-only replay path and performs zero writes.
    ``stale_correction`` is a restricted retraction/reaffirmation path for an
    artifact that already created the currently open assignment but whose source
    evidence is no longer current.  It never creates a new assignment.
    """

    if not isinstance(stale_correction, bool):
        raise ValueError("stale_correction must be a boolean")

    candidates = _load_candidate_artifact(candidate_artifact)
    review = _load_review_artifact(
        review_artifact, candidate_artifact=candidates
    )
    acceptance = _canonical_timestamp(accepted_at, "accepted_at")
    if _timestamp(acceptance) < _timestamp(review.reviewed_at):
        raise ValueError("accepted_at must not predate reviewed_at")
    if _timestamp(acceptance) < _timestamp(candidates.knowledge_cutoff_at):
        raise ValueError("accepted_at must not predate the candidate cutoff")

    moenv_verified = _verified_moenv_snapshot(moenv_snapshot)
    factory_verified = _verified_factory_snapshot(factory_snapshot)
    if (
        moenv_verified.manifest_sha256
        != candidates.moenv.snapshot_manifest_sha256
        or moenv_verified.candidate_sha256
        != candidates.moenv.snapshot_candidate_sha256
        or moenv_verified.raw_sha256 != candidates.moenv.snapshot_raw_sha256
    ):
        raise ValueError("MOENV snapshot conflicts with the candidate artifact")
    if (
        factory_verified.manifest_sha256
        != candidates.factory_registry.snapshot_manifest_sha256
        or factory_verified.candidate_sha256
        != candidates.factory_registry.snapshot_candidate_sha256
        or factory_verified.raw_sha256
        != candidates.factory_registry.snapshot_raw_sha256
    ):
        raise ValueError("factory snapshot conflicts with the candidate artifact")
    version, schema_sha = _schema_identity(connection)
    if (
        version != candidates.database_schema_version
        or schema_sha != candidates.database_schema_sha256
    ):
        raise ValueError("database schema identity changed after candidate generation")
    _assert_candidate_recomputed(
        connection,
        artifact=candidates,
        moenv_snapshot=moenv_verified,
        factory_snapshot=factory_verified,
    )

    run_id, candidate_ids, decision_ids, assignment_ids = _resolution_ids(
        candidates,
        review,
        acceptance,
        stale_correction=stale_correction,
    )
    if _query(
        connection,
        "SELECT id FROM entity_resolution_runs WHERE id = ?",
        (run_id,),
    ):
        result = _verify_exact_replay(
            connection,
            run_id=run_id,
            candidate_artifact=candidates,
            review_artifact=review,
            accepted_at=acceptance,
            candidate_ids=candidate_ids,
            decision_ids=decision_ids,
            assignment_ids=assignment_ids,
        )
        _reverify_artifact_sources(candidates, review)
        if _snapshot_identity(_verified_moenv_snapshot(moenv_verified)) != _snapshot_identity(
            moenv_verified
        ):
            raise ValueError("MOENV snapshot changed during replay verification")
        if _snapshot_identity(
            _verified_factory_snapshot(factory_verified)
        ) != _snapshot_identity(factory_verified):
            raise ValueError("factory snapshot changed during replay verification")
        return result

    evidence_stale = False
    try:
        _assert_evidence_current(
            connection, artifact=candidates, recorded_at=acceptance
        )
    except ValueError:
        evidence_stale = True
        if not stale_correction:
            raise
    if stale_correction and not evidence_stale:
        raise ValueError(
            "stale_correction requires evidence that is no longer current"
        )

    observed_ids = tuple(item.observed_entity_id for item in candidates.candidates)
    open_assignments = _open_assignments(connection, observed_ids)
    prior_by_observed = {
        observed_id: str(assignment["id"])
        for observed_id, assignment in open_assignments.items()
    }
    if stale_correction:
        valid_open_count = 0
        superseded: list[str] = []
        reaffirmed: list[str] = []
        for candidate, review_decision in zip(
            candidates.candidates, review.decisions, strict=True
        ):
            assignment = open_assignments.get(candidate.observed_entity_id)
            if assignment is None:
                if review_decision.outcome == "match":
                    raise ValueError(
                        "stale correction cannot create or restore a missing assignment"
                    )
                continue
            if not _assignment_belongs_to_artifact(
                assignment, candidate, candidates
            ):
                raise ValueError(
                    "stale correction may touch only an open match created from this artifact"
                )
            valid_open_count += 1
            if review_decision.outcome == "match":
                if (
                    assignment["valid_from"]
                    != review_decision.assignment_valid_from
                ):
                    raise ValueError(
                        "stale correction match may only reaffirm the identical valid interval"
                    )
                reaffirmed.append(str(assignment["id"]))
            else:
                superseded.append(str(assignment["id"]))
        if valid_open_count == 0:
            raise ValueError(
                "stale correction requires a prior accepted open match from this artifact"
            )
        superseded_ids = tuple(sorted(superseded))
        reaffirmed_ids = tuple(sorted(reaffirmed))
    else:
        superseded_ids = tuple(
            sorted(str(item["id"]) for item in open_assignments.values())
        )
        reaffirmed_ids = ()
    parameters = _resolution_parameters(
        candidate_artifact=candidates,
        review_artifact=review,
        accepted_at=acceptance,
        superseded_assignment_ids=superseded_ids,
        reaffirmed_assignment_ids=reaffirmed_ids,
        stale_correction=stale_correction,
    )
    started_at, completed_at, decided_at, recorded_at = _expected_times(acceptance)
    resolution_input_ids = _all_resolution_input_ids(candidates)
    placeholders = ",".join("?" for _ in resolution_input_ids)
    input_completions = _query(
        connection,
        f"""
        SELECT id, completed_at FROM ingestion_runs
        WHERE id IN ({placeholders}) ORDER BY id
        """,
        resolution_input_ids,
    )
    if len(input_completions) != len(resolution_input_ids) or any(
        row["completed_at"] is None
        or _timestamp(
            _canonical_timestamp(
                row["completed_at"], f"ingestion run {row['id']}.completed_at"
            )
        )
        > _timestamp(started_at)
        for row in input_completions
    ):
        raise ValueError("resolution start must follow both succeeded input runs")
    for superseded_id in superseded_ids:
        old = _query(
            connection,
            "SELECT recorded_at FROM source_entity_assignments WHERE id = ?",
            (superseded_id,),
        )
        if len(old) != 1 or _timestamp(
            _canonical_timestamp(
                old[0]["recorded_at"],
                f"source assignment {superseded_id}.recorded_at",
            )
        ) >= _timestamp(recorded_at):
            raise ValueError("a prior assignment cannot be superseded at this acceptance time")

    mode, savepoint = _begin_atomic(connection)
    rows_written = 0
    try:
        rows_written += int(
            add_entity_resolution_run(
                connection,
                EntityResolutionRun(
                    run_id,
                    started_at,
                    TAIWAN_FACILITY_RESOLVER_VERSION,
                    code_version=TAIWAN_FACILITY_RESOLVER_VERSION,
                    parameters=parameters,
                ),
            )
        )
        for ingestion_run_id in resolution_input_ids:
            rows_written += int(
                add_entity_resolution_run_input(
                    connection,
                    EntityResolutionRunInput(run_id, ingestion_run_id),
                )
            )
        for persisted_id, candidate in zip(
            candidate_ids, candidates.candidates, strict=True
        ):
            rows_written += int(
                add_entity_resolution_candidate(
                    connection,
                    EntityResolutionCandidate(
                        persisted_id,
                        run_id,
                        candidate.observed_source_record_id,
                        candidate.observed_entity_id,
                        candidate.target_entity_id,
                        candidate.score,
                        candidate.candidate_rank,
                        started_at,
                        features=_candidate_features(candidate, candidates),
                    ),
                )
            )
        rows_written += int(
            finalize_entity_resolution_run(
                connection,
                run_id,
                status=EntityResolutionStatus.SUCCEEDED,
                completed_at=completed_at,
            )
        )
        # The run is sealed above.  Decisions and any assignments are appended
        # only after the immutable candidate ledger is terminal.
        for decision_id, candidate_id, candidate, review_decision in zip(
            decision_ids,
            candidate_ids,
            candidates.candidates,
            review.decisions,
            strict=True,
        ):
            rows_written += int(
                add_entity_resolution_decision(
                    connection,
                    EntityResolutionDecision(
                        decision_id,
                        candidate_id,
                        ResolutionDecisionOutcome(review_decision.outcome),
                        decided_at,
                        review.reviewed_by,
                        review_decision.reason,
                        metadata={
                            "candidate_artifact_canonical_sha256": (
                                candidates.canonical_sha256
                            ),
                            "review_artifact_canonical_sha256": review.canonical_sha256,
                            "prior_assignment_id": prior_by_observed.get(
                                candidate.observed_entity_id
                            ),
                            "reviewed_at": review.reviewed_at,
                            "stale_correction": stale_correction,
                        },
                    ),
                )
            )
        for superseded_id in superseded_ids:
            rows_written += int(
                supersede_source_entity_assignment(
                    connection, superseded_id, recorded_at
                )
            )
        for assignment_id, decision_id, candidate, review_decision in zip(
            assignment_ids,
            decision_ids,
            candidates.candidates,
            review.decisions,
            strict=True,
        ):
            if review_decision.outcome != "match" or stale_correction:
                continue
            assert assignment_id is not None
            assert review_decision.assignment_valid_from is not None
            rows_written += int(
                add_source_entity_assignment(
                    connection,
                    SourceEntityAssignment(
                        assignment_id,
                        candidate.observed_source_record_id,
                        candidate.observed_entity_id,
                        candidate.target_entity_id,
                        decision_id,
                        review_decision.assignment_valid_from,
                        recorded_at,
                    ),
                )
            )

        refreshed_candidates, refreshed_review = _reverify_artifact_sources(
            candidates, review
        )
        refreshed_moenv = _verified_moenv_snapshot(moenv_verified)
        refreshed_factory = _verified_factory_snapshot(factory_verified)
        if _snapshot_identity(refreshed_moenv) != _snapshot_identity(moenv_verified):
            raise ValueError("MOENV snapshot changed during acceptance")
        if _snapshot_identity(refreshed_factory) != _snapshot_identity(factory_verified):
            raise ValueError("factory snapshot changed during acceptance")
        _assert_candidate_recomputed(
            connection,
            artifact=refreshed_candidates,
            moenv_snapshot=refreshed_moenv,
            factory_snapshot=refreshed_factory,
        )
        if stale_correction:
            try:
                _assert_evidence_current(
                    connection,
                    artifact=refreshed_candidates,
                    recorded_at=recorded_at,
                )
            except ValueError:
                pass
            else:
                raise ValueError(
                    "stale-correction evidence unexpectedly became current"
                )
            for reaffirmed_id in reaffirmed_ids:
                row = _query(
                    connection,
                    """
                    SELECT superseded_at FROM source_entity_assignments
                    WHERE id = ?
                    """,
                    (reaffirmed_id,),
                )
                if len(row) != 1 or row[0]["superseded_at"] is not None:
                    raise ValueError(
                        "stale correction failed to preserve a reaffirmed assignment"
                    )
        else:
            _assert_evidence_current(
                connection, artifact=refreshed_candidates, recorded_at=recorded_at
            )
        if refreshed_review.canonical_sha256 != review.canonical_sha256:
            raise ValueError("review artifact changed during acceptance")
        errors = validate_database(connection)
        if errors:
            raise RuntimeError(
                "Taiwan facility acceptance failed database validation: "
                + "; ".join(errors)
            )
        _commit_atomic(connection, mode, savepoint)
    except BaseException:
        _rollback_atomic(connection, mode, savepoint)
        raise

    return TaiwanFacilityAcceptanceResult(
        resolution_run_id=run_id,
        candidate_ids=candidate_ids,
        decision_ids=decision_ids,
        assignment_ids=tuple(item for item in assignment_ids if item is not None),
        superseded_assignment_ids=superseded_ids,
        reaffirmed_assignment_ids=reaffirmed_ids,
        replayed=False,
        rows_written=rows_written,
    )


def _write_canonical_artifact(path: str | Path, raw: bytes) -> bool:
    """Create one private artifact atomically without overwriting any path."""

    destination = Path(path).absolute()
    if not destination.parent.is_dir():
        raise ValueError(f"artifact output parent does not exist: {destination.parent}")
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"artifact output already exists: {destination}")

    temporary = destination.parent / (
        f".{destination.name}.tmp-{os.getpid()}-{next(_SAVEPOINTS)}"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, flags, 0o600)
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short artifact write")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        try:
            os.link(temporary, destination, follow_symlinks=False)
        except FileExistsError:
            raise ValueError("artifact output appeared concurrently") from None
        return True
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m semiconductor_atlas.taiwan_facility_identity",
        description="Propose or accept reviewed Taiwan facility identity matches.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    propose = commands.add_parser(
        "propose", help="write a canonical, read-only candidate artifact"
    )
    propose.add_argument("--database", required=True)
    propose.add_argument("--moenv-snapshot", required=True)
    propose.add_argument("--factory-snapshot", required=True)
    propose.add_argument("--moenv-ingestion-run-id", required=True)
    propose.add_argument("--factory-ingestion-run-id", required=True)
    propose.add_argument("--knowledge-cutoff-at", required=True)
    propose.add_argument("--output", required=True)

    accept = commands.add_parser(
        "accept", help="atomically persist one complete reviewed artifact"
    )
    accept.add_argument("--database", required=True)
    accept.add_argument("--moenv-snapshot", required=True)
    accept.add_argument("--factory-snapshot", required=True)
    accept.add_argument("--candidates", required=True)
    accept.add_argument("--review", required=True)
    accept.add_argument("--accepted-at", required=True)
    accept.add_argument(
        "--stale-correction",
        action="store_true",
        help=(
            "retract or reaffirm only open assignments previously created from "
            "this now-stale artifact; never create assignments"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Operational module CLI kept inside the reviewed two-file scope."""

    arguments = _argument_parser().parse_args(argv)
    connection = connect(arguments.database)
    try:
        if arguments.command == "propose":
            artifact = propose_taiwan_facility_candidates(
                connection,
                knowledge_cutoff_at=arguments.knowledge_cutoff_at,
                moenv_ingestion_run_id=arguments.moenv_ingestion_run_id,
                factory_ingestion_run_id=arguments.factory_ingestion_run_id,
                moenv_snapshot=arguments.moenv_snapshot,
                factory_snapshot=arguments.factory_snapshot,
            )
            created = _write_canonical_artifact(
                arguments.output, artifact.canonical_bytes
            )
            summary: dict[str, object] = {
                "candidate_artifact_canonical_sha256": artifact.canonical_sha256,
                "candidate_count": len(artifact.candidates),
                "created": created,
                "output": str(Path(arguments.output).absolute()),
            }
        else:
            result = accept_taiwan_facility_review(
                connection,
                candidate_artifact=arguments.candidates,
                review_artifact=arguments.review,
                moenv_snapshot=arguments.moenv_snapshot,
                factory_snapshot=arguments.factory_snapshot,
                accepted_at=arguments.accepted_at,
                stale_correction=arguments.stale_correction,
            )
            summary = {
                "assignment_ids": list(result.assignment_ids),
                "candidate_ids": list(result.candidate_ids),
                "decision_ids": list(result.decision_ids),
                "replayed": result.replayed,
                "reaffirmed_assignment_ids": list(
                    result.reaffirmed_assignment_ids
                ),
                "resolution_run_id": result.resolution_run_id,
                "rows_written": result.rows_written,
                "superseded_assignment_ids": list(
                    result.superseded_assignment_ids
                ),
            }
    finally:
        connection.close()
    sys.stdout.write(_canonical_json_bytes(summary).decode("utf-8"))
    return 0


__all__ = [
    "FACTORY_NUMBER_PREDICATE",
    "FACTORY_SOURCE_KEY",
    "MOENV_FACTORY_NUMBER_PREDICATE",
    "MOENV_SOURCE_KEY",
    "TAIWAN_FACILITY_CANDIDATE_FORMAT",
    "TAIWAN_FACILITY_MAX_ARTIFACT_BYTES",
    "TAIWAN_FACILITY_MAX_CANDIDATES",
    "TAIWAN_FACILITY_MIN_SCHEMA_VERSION",
    "TAIWAN_FACILITY_RESOLVER_VERSION",
    "TAIWAN_FACILITY_REVIEW_FORMAT",
    "TaiwanFacilityAcceptanceResult",
    "TaiwanFacilityCandidate",
    "TaiwanFacilityCandidateArtifact",
    "TaiwanFacilityEvidenceValue",
    "TaiwanFacilityIngestionIdentity",
    "TaiwanFacilityReviewArtifact",
    "TaiwanFacilityReviewDecision",
    "TaiwanFacilitySourceBinding",
    "accept_taiwan_facility_review",
    "build_taiwan_facility_review",
    "main",
    "parse_taiwan_facility_candidate_bytes",
    "parse_taiwan_facility_review_bytes",
    "propose_taiwan_facility_candidates",
    "read_taiwan_facility_candidate_file",
    "read_taiwan_facility_review_file",
]


if __name__ == "__main__":
    raise SystemExit(main())
