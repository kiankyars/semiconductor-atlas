"""Strict parser for manually reviewed GLEIF entity-resolution decisions."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any


GLEIF_REVIEW_FORMAT = "semiconductor-atlas-gleif-review-v1"
GLEIF_REVIEW_MAX_DECISIONS = 500
GLEIF_REVIEW_MAX_BYTES = 2 * 1024 * 1024

_LEI_RE = re.compile(r"^[0-9A-Z]{20}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_OUTCOMES = frozenset({"match", "reject", "defer"})
_READ_CHUNK_BYTES = 64 * 1024
_TOP_LEVEL_FIELDS = {
    "format",
    "reviewed_by",
    "reviewed_at",
    "snapshot_manifest_sha256",
    "golden_copy_publish_date",
    "decisions",
}
_DECISION_FIELDS = {
    "lei",
    "target_entity_id",
    "target_entity_stable_key",
    "target_evidence_claim_version_ids",
    "outcome",
    "reason",
    "score",
    "candidate_rank",
    "assignment_valid_from",
}


@dataclass(frozen=True, slots=True)
class GLEIFReviewDecision:
    """One reviewed disposition for one LEI."""

    lei: str
    target_entity_id: str
    target_entity_stable_key: str
    target_evidence_claim_version_ids: tuple[str, ...]
    outcome: str
    reason: str
    score: float
    candidate_rank: int
    assignment_valid_from: str | None


@dataclass(frozen=True, slots=True)
class GLEIFReviewManifest:
    """Validated review data together with its raw and canonical identities."""

    format: str
    reviewed_by: str
    reviewed_at: str
    snapshot_manifest_sha256: str
    golden_copy_publish_date: str
    decisions: tuple[GLEIFReviewDecision, ...]
    raw_bytes: bytes
    raw_sha256: str
    canonical_bytes: bytes
    canonical_sha256: str
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


def _aware_timestamp(value: object, context: str) -> tuple[str, datetime]:
    text = _clean_text(value, context)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise ValueError(f"{context} must be an ISO 8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{context} must include a timezone")
    return text, parsed


def _canonical_utc_timestamp(value: object, context: str) -> str:
    text, parsed = _aware_timestamp(value, context)
    canonical = parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if text != canonical:
        raise ValueError(f"{context} must be a canonical UTC timestamp")
    return text


def _sha256(value: object, context: str) -> str:
    text = _clean_text(value, context)
    if not _SHA256_RE.fullmatch(text):
        raise ValueError(f"{context} must be a lowercase SHA-256 digest")
    return text


def _lei_checksum_is_valid(lei: str) -> bool:
    numeric = "".join(str(int(character, 36)) for character in lei)
    remainder = 0
    for character in numeric:
        remainder = (remainder * 10 + int(character)) % 97
    return remainder == 1


def _lei(value: object, context: str) -> str:
    text = _clean_text(value, context)
    if not _LEI_RE.fullmatch(text) or not _lei_checksum_is_valid(text):
        raise ValueError(f"{context} must be a valid 20-character uppercase LEI")
    return text


def _score(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must be a finite number between 0 and 1")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError(
            f"{context} must be a finite number between 0 and 1"
        ) from error
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError(f"{context} must be a finite number between 0 and 1")
    return result


def _positive_integer(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{context} must be a positive integer")
    return value


def _assignment_date(value: object, outcome: str, context: str) -> str | None:
    if outcome != "match":
        if value is not None:
            raise ValueError(f"{context} must be null unless outcome is 'match'")
        return None
    text = _clean_text(value, context)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"{context} must be a YYYY-MM-DD date") from error
    if parsed.isoformat() != text:
        raise ValueError(f"{context} must be a YYYY-MM-DD date")
    return text


def _evidence_ids(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{context} must be a non-empty JSON array")
    result = tuple(
        _clean_text(item, f"{context}[{index}]")
        for index, item in enumerate(value)
    )
    if len(set(result)) != len(result):
        raise ValueError(f"{context} must not contain duplicates")
    if tuple(sorted(result)) != result:
        raise ValueError(f"{context} must be sorted")
    return result


def _parse_decision(value: object, index: int) -> GLEIFReviewDecision:
    context = f"decisions[{index}]"
    payload = _object(value, context)
    if set(payload) != _DECISION_FIELDS:
        raise ValueError(f"{context} must contain exactly the required fields")
    outcome = _clean_text(payload["outcome"], f"{context}.outcome")
    if outcome not in _OUTCOMES:
        raise ValueError(f"{context}.outcome must be match, reject, or defer")
    return GLEIFReviewDecision(
        lei=_lei(payload["lei"], f"{context}.lei"),
        target_entity_id=_clean_text(
            payload["target_entity_id"], f"{context}.target_entity_id"
        ),
        target_entity_stable_key=_clean_text(
            payload["target_entity_stable_key"],
            f"{context}.target_entity_stable_key",
        ),
        target_evidence_claim_version_ids=_evidence_ids(
            payload["target_evidence_claim_version_ids"],
            f"{context}.target_evidence_claim_version_ids",
        ),
        outcome=outcome,
        reason=_clean_text(payload["reason"], f"{context}.reason"),
        score=_score(payload["score"], f"{context}.score"),
        candidate_rank=_positive_integer(
            payload["candidate_rank"], f"{context}.candidate_rank"
        ),
        assignment_valid_from=_assignment_date(
            payload["assignment_valid_from"],
            outcome,
            f"{context}.assignment_valid_from",
        ),
    )


def _decision_payload(decision: GLEIFReviewDecision) -> dict[str, object]:
    return {
        "lei": decision.lei,
        "target_entity_id": decision.target_entity_id,
        "target_entity_stable_key": decision.target_entity_stable_key,
        "target_evidence_claim_version_ids": list(
            decision.target_evidence_claim_version_ids
        ),
        "outcome": decision.outcome,
        "reason": decision.reason,
        "score": decision.score,
        "candidate_rank": decision.candidate_rank,
        "assignment_valid_from": decision.assignment_valid_from,
    }


def _canonical_bytes(
    *,
    reviewed_by: str,
    reviewed_at: str,
    snapshot_manifest_sha256: str,
    golden_copy_publish_date: str,
    decisions: tuple[GLEIFReviewDecision, ...],
) -> bytes:
    payload = {
        "format": GLEIF_REVIEW_FORMAT,
        "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at,
        "snapshot_manifest_sha256": snapshot_manifest_sha256,
        "golden_copy_publish_date": golden_copy_publish_date,
        "decisions": [_decision_payload(decision) for decision in decisions],
    }
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def parse_gleif_review_bytes(raw: bytes) -> GLEIFReviewManifest:
    """Parse and canonicalize one bounded reviewed-decision manifest."""

    if not isinstance(raw, bytes):
        raise TypeError("GLEIF review manifest must be bytes")
    if not raw:
        raise ValueError("GLEIF review manifest must not be empty")
    if len(raw) > GLEIF_REVIEW_MAX_BYTES:
        raise ValueError("GLEIF review manifest exceeds the byte limit")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("GLEIF review manifest must be valid UTF-8") from error
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except _DuplicateJSONKey as error:
        raise ValueError(f"GLEIF review manifest contains {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError("GLEIF review manifest is not valid JSON") from error
    except ValueError as error:
        raise ValueError("GLEIF review manifest is not strict JSON") from error

    payload = _object(value, "GLEIF review manifest")
    if set(payload) != _TOP_LEVEL_FIELDS:
        raise ValueError(
            "GLEIF review manifest must contain exactly the required fields"
        )
    if payload["format"] != GLEIF_REVIEW_FORMAT:
        raise ValueError(f"format must be {GLEIF_REVIEW_FORMAT!r}")
    reviewed_by = _clean_text(payload["reviewed_by"], "reviewed_by")
    reviewed_at = _canonical_utc_timestamp(payload["reviewed_at"], "reviewed_at")
    snapshot_manifest_sha256 = _sha256(
        payload["snapshot_manifest_sha256"], "snapshot_manifest_sha256"
    )
    golden_copy_publish_date, _ = _aware_timestamp(
        payload["golden_copy_publish_date"], "golden_copy_publish_date"
    )
    decision_values = payload["decisions"]
    if not isinstance(decision_values, list) or not decision_values:
        raise ValueError("decisions must be a non-empty JSON array")
    if len(decision_values) > GLEIF_REVIEW_MAX_DECISIONS:
        raise ValueError(
            f"decisions must not exceed {GLEIF_REVIEW_MAX_DECISIONS} entries"
        )
    decisions = tuple(
        _parse_decision(item, index)
        for index, item in enumerate(decision_values)
    )
    leis = tuple(decision.lei for decision in decisions)
    if len(set(leis)) != len(leis):
        raise ValueError("decisions must contain exactly one decision per LEI")
    if tuple(sorted(leis)) != leis:
        raise ValueError("decisions must be sorted by LEI")

    canonical = _canonical_bytes(
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
        snapshot_manifest_sha256=snapshot_manifest_sha256,
        golden_copy_publish_date=golden_copy_publish_date,
        decisions=decisions,
    )
    return GLEIFReviewManifest(
        format=GLEIF_REVIEW_FORMAT,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
        snapshot_manifest_sha256=snapshot_manifest_sha256,
        golden_copy_publish_date=golden_copy_publish_date,
        decisions=decisions,
        raw_bytes=raw,
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        canonical_bytes=canonical,
        canonical_sha256=hashlib.sha256(canonical).hexdigest(),
        path=None,
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


def read_gleif_review_file(path: str | Path) -> GLEIFReviewManifest:
    """Safely read and parse one regular, non-symlink review file."""

    candidate = Path(path).absolute()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError as error:
        raise ValueError(
            f"GLEIF review manifest is missing or unsafe: {candidate}"
        ) from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("GLEIF review manifest must be a regular file")
        if before.st_size < 0 or before.st_size > GLEIF_REVIEW_MAX_BYTES:
            raise ValueError("GLEIF review manifest exceeds the byte limit")

        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(
                descriptor,
                min(_READ_CHUNK_BYTES, GLEIF_REVIEW_MAX_BYTES + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > GLEIF_REVIEW_MAX_BYTES:
                raise ValueError("GLEIF review manifest exceeds the byte limit")

        after = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(after):
            raise ValueError("GLEIF review manifest changed while being read")
        raw = b"".join(chunks)
        if len(raw) != before.st_size:
            raise ValueError("GLEIF review manifest size changed while being read")
        try:
            final_path = os.stat(candidate, follow_symlinks=False)
        except OSError as error:
            raise ValueError(
                "GLEIF review manifest path changed while being read"
            ) from error
        if _stat_identity(after) != _stat_identity(final_path):
            raise ValueError("GLEIF review manifest path changed while being read")
    finally:
        os.close(descriptor)
    return replace(parse_gleif_review_bytes(raw), path=candidate)


__all__ = [
    "GLEIFReviewDecision",
    "GLEIFReviewManifest",
    "GLEIF_REVIEW_FORMAT",
    "GLEIF_REVIEW_MAX_BYTES",
    "GLEIF_REVIEW_MAX_DECISIONS",
    "parse_gleif_review_bytes",
    "read_gleif_review_file",
]
