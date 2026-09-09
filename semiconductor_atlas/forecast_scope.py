"""Hash-bound, cutoff-aware semantic attestations for capacity/event scope.

A reviewer asserts the exact project, phase and event relationship. Mechanical
validation authenticates the supplied bytes against a separately trusted digest
and binds them to the database evidence; it cannot prove those semantic labels,
reviewer identity, source truth, calibration or forecast eligibility. Shared
documents and dependencies alone are not semantic scope attestations.

The evidence digest covers the exact-subject dossier except its top-level query
``recorded_at``. The review separately binds that evidence cutoff. Excluding only
the request envelope permits later queries with identical evidence while still
binding every underlying claim, provenance and ingestion clock. Raw source text
omitted by the dossier is neither republished nor independently reverified here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime

from .evidence_dossier import build_evidence_dossier
from .service import claim_records


FORMAT = "semiconductor-atlas-capacity-scope-review-v1"
MAX_REVIEW_BYTES = 1_000_000
MAX_DECISIONS = 1_000
_TOP_FIELDS = {
    "format", "as_of", "evidence_cutoff_at", "reviewed_at", "reviewer", "decisions",
}
_DECISION_FIELDS = {
    "capacity_claim_id", "milestone_claim_id", "project_scope", "phase_scope",
    "event_type", "decision", "rationale",
}
_BINDING_FIELDS = {"entity_id", "evidence_dossier_sha256"}
_PRODUCTION_EVENTS = {"mass_production", "production_start", "ramp_start", "volume_production"}
_ACTIVE_STATUSES = {"expected", "started", "completed", "delayed"}
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_CLOCK = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z")


def canonical_bytes(value) -> bytes:
    """Encode a strict finite JSON value without implicit key/type conversion."""
    def check(item, depth=0):
        if depth > 64:
            raise ValueError("JSON nesting exceeds the scope-review bound")
        if item is None or type(item) in (bool, int, str):
            return
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError("JSON numbers must be finite")
            return
        if type(item) is list:
            for child in item:
                check(child, depth + 1)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child, depth + 1)
            return
        raise ValueError("scope-review canonicalization requires strict JSON values")

    check(value)
    try:
        return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2,
                           sort_keys=True) + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeError, OverflowError) as error:
        raise ValueError("invalid scope-review JSON value") from error


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _clock(value, name: str) -> datetime:
    if not isinstance(value, str) or not _CLOCK.fullmatch(value):
        raise ValueError(f"{name} must be a timezone-aware ISO timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, OverflowError) as error:
        raise ValueError(f"invalid {name}") from error


def _clock_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _day(value) -> str:
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError("invalid date")
    except (TypeError, ValueError) as error:
        raise ValueError("scope review as_of must use YYYY-MM-DD") from error
    return value


def _text(value, name: str, limit: int) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(ord(character) < 32 for character in value)):
        raise ValueError(f"{name} must be nonempty bounded single-line text")
    return value


def _digest(value, name: str) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _decisions(value, *, bound: bool) -> list[dict]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_DECISIONS:
        raise ValueError(f"scope review needs 1 to {MAX_DECISIONS} decisions")
    fields = _DECISION_FIELDS | (_BINDING_FIELDS if bound else set())
    identifiers = []
    result = []
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != fields:
            raise ValueError("scope decision has missing or unexpected fields")
        for field in ("capacity_claim_id", "milestone_claim_id", "event_type"):
            _text(entry[field], field, 256)
        for field in ("project_scope", "phase_scope"):
            _text(entry[field], field, 4_096)
        _text(entry["rationale"], "rationale", 16_384)
        if entry["decision"] not in ("accepted", "deferred"):
            raise ValueError("scope decision must be accepted or deferred")
        if bound:
            _text(entry["entity_id"], "entity_id", 256)
            _digest(entry["evidence_dossier_sha256"], "evidence_dossier_sha256")
        identifiers.append(entry["capacity_claim_id"])
        result.append(dict(entry))
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("scope decisions contain duplicate capacity claim IDs")
    if bound and identifiers != sorted(identifiers):
        raise ValueError("scope decisions must be sorted by capacity claim ID")
    return sorted(result, key=lambda entry: entry["capacity_claim_id"])


@contextmanager
def _snapshot(connection: sqlite3.Connection):
    connection.execute("SAVEPOINT atlas_forecast_scope_read")
    try:
        yield
    finally:
        connection.execute("ROLLBACK TO SAVEPOINT atlas_forecast_scope_read")
        connection.execute("RELEASE SAVEPOINT atlas_forecast_scope_read")


def _entity_id(connection, claim_id: str) -> str:
    row = connection.execute("""SELECT s.subject_entity_id FROM claim_versions v
        JOIN claim_series s ON s.id=v.series_id WHERE v.id=?""", (claim_id,)).fetchone()
    if row is None:
        raise ValueError("scope review references an unknown claim ID")
    return str(row[0])


def _bind(connection, entries: list[dict], *, as_of: str, recorded_at: str) -> list[dict]:
    views, digests = {}, {}
    result = []
    for entry in entries:
        entity = _entity_id(connection, entry["capacity_claim_id"])
        if _entity_id(connection, entry["milestone_claim_id"]) != entity:
            raise ValueError("scope pair must have the same exact entity")
        if entity not in views:
            views[entity] = {claim["id"]: claim for claim in claim_records(
                connection, as_of=as_of, recorded_at=recorded_at, subject_entity_id=entity)}
        current = views[entity]
        if entry["capacity_claim_id"] not in current or entry["milestone_claim_id"] not in current:
            raise ValueError("scope roots are not in the authoritative physical-current view at both cutoffs")
        capacity = current[entry["capacity_claim_id"]]
        milestone = current[entry["milestone_claim_id"]]
        if capacity["value_kind"] != "capacity" or milestone["value_kind"] != "milestone":
            raise ValueError("scope pair requires a numeric capacity and a typed milestone")
        if any(type(capacity["value"].get(key)) not in (int, float)
               or not math.isfinite(capacity["value"][key]) for key in ("low", "base", "high")):
            raise ValueError("scope capacity must have finite numeric bounds")
        if milestone["value"].get("milestone_type") != entry["event_type"]:
            raise ValueError("scope event_type must match the exact typed milestone event")
        if entry["decision"] == "accepted":
            if (entry["event_type"] not in _PRODUCTION_EVENTS
                    or milestone["value"].get("status") not in _ACTIVE_STATUSES
                    or milestone.get("confidence") is None
                    or milestone.get("valid_from") is None
                    or milestone["value"].get("date_base") is None):
                raise ValueError("accepted scope requires an active usable production milestone")
        if entity not in digests:
            dossier = build_evidence_dossier(connection, subject_entity_id=entity,
                as_of=as_of, recorded_at=recorded_at)
            evidence = {key: value for key, value in dossier.items() if key != "recorded_at"}
            digests[entity] = hashlib.sha256(canonical_bytes(evidence)).hexdigest()
        semantic = {key: entry[key] for key in _DECISION_FIELDS}
        result.append(semantic | {"entity_id": entity, "evidence_dossier_sha256": digests[entity]})
    return result


def create_scope_review(connection: sqlite3.Connection, *, as_of: str, recorded_at: str,
                        reviewer: str, decisions: list[dict]) -> dict:
    """Bind supplied semantic decisions to one coherent, actually available view."""
    as_of = _day(as_of)
    cutoff = _clock(recorded_at, "evidence cutoff")
    _text(reviewer, "reviewer", 256)
    entries = _decisions(decisions, bound=False)
    if cutoff > _clock(_now(), "actual review clock"):
        raise ValueError("scope evidence cutoff cannot be in the future")
    with _snapshot(connection):
        bound = _bind(connection, entries, as_of=as_of, recorded_at=_clock_text(cutoff))
        reviewed_at = _now()
        if _clock(reviewed_at, "actual review clock") < cutoff:
            raise ValueError("scope review clock precedes its evidence cutoff")
        review = {"format": FORMAT, "as_of": as_of, "evidence_cutoff_at": _clock_text(cutoff),
                  "reviewed_at": _clock_text(_clock(reviewed_at, "actual review clock")),
                  "reviewer": reviewer, "decisions": bound}
        if len(canonical_bytes(review)) > MAX_REVIEW_BYTES:
            raise ValueError("scope review exceeds the byte bound")
        return review


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("scope review JSON contains duplicate object keys")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("scope review JSON numbers must be finite")


def validate_scope_review(connection: sqlite3.Connection, *, review: bytes,
                          expected_sha256: str, as_of: str, recorded_at: str) -> dict:
    """Return bound decisions, rejecting untrusted, unavailable or stale evidence."""
    _digest(expected_sha256, "expected_sha256")
    if type(review) is not bytes or not 0 < len(review) <= MAX_REVIEW_BYTES:
        raise ValueError("scope review must be bounded exact bytes")
    if not hmac.compare_digest(hashlib.sha256(review).hexdigest(), expected_sha256):
        raise ValueError("scope review SHA-256 mismatch")
    try:
        value = json.loads(review.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=_invalid_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError("invalid scope review JSON: " + str(error)) from error
    if not isinstance(value, dict) or set(value) != _TOP_FIELDS or value.get("format") != FORMAT:
        raise ValueError("scope review has an invalid format or fields")
    if canonical_bytes(value) != review:
        raise ValueError("scope review bytes must use canonical JSON")
    if _day(value["as_of"]) != _day(as_of):
        raise ValueError("scope review as_of differs from the requested world cutoff")
    _text(value["reviewer"], "reviewer", 256)
    cutoff = _clock(value["evidence_cutoff_at"], "evidence cutoff")
    reviewed = _clock(value["reviewed_at"], "reviewed_at")
    requested = _clock(recorded_at, "requested recorded_at")
    if cutoff > reviewed or reviewed > requested or reviewed > _clock(_now(), "actual validation clock"):
        raise ValueError("scope review clocks are unavailable or out of order")
    if (value["evidence_cutoff_at"] != _clock_text(cutoff)
            or value["reviewed_at"] != _clock_text(reviewed)):
        raise ValueError("scope review clocks must use normalized UTC timestamps")
    entries = _decisions(value["decisions"], bound=True)
    with _snapshot(connection):
        old = _bind(connection, entries, as_of=as_of, recorded_at=_clock_text(cutoff))
        if old != entries:
            raise ValueError("scope review evidence binding differs from its reviewed snapshot")
        current = _bind(connection, entries, as_of=as_of, recorded_at=_clock_text(requested))
        if current != entries:
            raise ValueError("scope review is stale: evidence changed since the reviewed snapshot")
    return {entry["capacity_claim_id"]: entry for entry in entries}
