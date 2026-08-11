"""Deterministic claim-revision alerts with explicit before/after lineage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from .service import claim_value


@dataclass(frozen=True, slots=True)
class Alert:
    fingerprint: str
    alert_type: str
    severity: str
    entity_id: str
    predicate: str
    prior_claim_id: str | None
    current_claim_id: str
    effective_date: str
    confidence: float
    summary: str
    reasoning: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fingerprint(*parts: object) -> str:
    raw = json.dumps(
        [str(part) for part in parts],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _severity(value: float, medium: float, high: float) -> str:
    if value >= high:
        return "high"
    if value >= medium:
        return "medium"
    return "low"


def _revision_pairs(
    connection: sqlite3.Connection,
    *,
    as_of: str | None,
    recorded_at: str | None,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT prior.id AS prior_id, current.id AS current_id,
               prior.value_kind, prior.confidence AS prior_confidence,
               current.confidence AS current_confidence,
               current.valid_from, current.recorded_at,
               series.subject_entity_id, series.predicate
        FROM claim_versions AS prior
        JOIN claim_versions AS current
          ON current.series_id = prior.series_id
         AND current.valid_from = prior.valid_from
         AND current.recorded_at = prior.superseded_at
        JOIN claim_series AS series ON series.id = current.series_id
        WHERE (? IS NULL OR (
                 current.valid_from <= ?
                 AND (current.valid_to IS NULL OR ? < current.valid_to)
               ))
          AND (? IS NULL OR julianday(current.recorded_at) <= julianday(?))
        ORDER BY current.recorded_at, current.id, prior.id
        """,
        (as_of, as_of, as_of, recorded_at, recorded_at),
    ).fetchall()


def detect_revision_alerts(
    connection: sqlite3.Connection,
    *,
    capacity_change_threshold: float = 0.15,
    milestone_shift_days: int = 90,
    as_of: str | None = None,
    recorded_at: str | None = None,
) -> list[Alert]:
    if capacity_change_threshold <= 0:
        raise ValueError("capacity_change_threshold must be positive")
    if milestone_shift_days <= 0:
        raise ValueError("milestone_shift_days must be positive")
    alerts = []
    for row in _revision_pairs(connection, as_of=as_of, recorded_at=recorded_at):
        prior = claim_value(connection, row["prior_id"], row["value_kind"])
        current = claim_value(connection, row["current_id"], row["value_kind"])
        confidence = min(float(row["prior_confidence"]), float(row["current_confidence"]))
        if row["value_kind"] == "capacity":
            prior_base = float(prior["base"])
            current_base = float(current["base"])
            denominator = prior_base if prior_base else max(current_base, 1.0)
            change = (current_base - prior_base) / denominator
            if abs(change) < capacity_change_threshold:
                continue
            direction = "increase" if change > 0 else "decrease"
            alert_type = "capacity_revision"
            summary = (
                f"{current['metric']} {current['basis']} capacity {direction}d "
                f"from {prior_base:g} to {current_base:g} {current['unit']}"
            )
            reasoning = {
                "relative_change": change,
                "prior": prior,
                "current": current,
                "threshold": capacity_change_threshold,
            }
            magnitude = abs(change)
            severity = _severity(magnitude, capacity_change_threshold * 2, capacity_change_threshold * 4)
        elif row["value_kind"] == "milestone":
            prior_status = str(prior.get("status"))
            current_status = str(current.get("status"))
            if current_status == "cancelled" and prior_status != "cancelled":
                alert_type = "project_cancellation"
                severity = "high"
                summary = f"{current['milestone_type']} changed to cancelled"
                reasoning = {
                    "status_transition": [prior_status, current_status],
                    "prior": prior,
                    "current": current,
                }
            elif prior_status == "cancelled" and current_status != "cancelled":
                alert_type = "project_reactivation"
                severity = "medium"
                summary = (
                    f"{current['milestone_type']} changed from cancelled to {current_status}"
                )
                reasoning = {
                    "status_transition": [prior_status, current_status],
                    "prior": prior,
                    "current": current,
                }
            elif current_status == "delayed" and prior_status != "delayed":
                alert_type = "completion_delay"
                severity = "medium"
                summary = f"{current['milestone_type']} status changed to delayed"
                reasoning = {
                    "status_transition": [prior_status, current_status],
                    "prior": prior,
                    "current": current,
                }
            elif current_status == "cancelled":
                continue
            else:
                prior_date = date.fromisoformat(prior["date_base"])
                current_date = date.fromisoformat(current["date_base"])
                shift = (current_date - prior_date).days
                if abs(shift) < milestone_shift_days:
                    continue
                alert_type = "completion_delay" if shift > 0 else "completion_acceleration"
                summary = (
                    f"{current['milestone_type']} moved "
                    f"{'later' if shift > 0 else 'earlier'} by {abs(shift)} days"
                )
                reasoning = {
                    "shift_days": shift,
                    "status_transition": [prior_status, current_status],
                    "prior": prior,
                    "current": current,
                    "threshold_days": milestone_shift_days,
                }
                severity = _severity(
                    abs(shift), milestone_shift_days * 2, milestone_shift_days * 4
                )
        elif row["value_kind"] == "scalar":
            current_text = str(current.get("value") or "").lower()
            if not any(token in current_text for token in ("cancelled", "canceled", "redesigned", "paused")):
                continue
            if "cancel" in current_text:
                alert_type, severity = "project_cancellation", "high"
            elif "redesign" in current_text:
                alert_type, severity = "project_redesign", "medium"
            else:
                alert_type, severity = "project_pause", "medium"
            summary = f"{row['predicate']} changed from {prior.get('value')} to {current.get('value')}"
            reasoning = {"prior": prior, "current": current}
        else:
            continue
        fingerprint = _fingerprint(
            alert_type,
            row["subject_entity_id"],
            row["prior_id"],
            row["current_id"],
        )
        alerts.append(
            Alert(
                fingerprint=fingerprint,
                alert_type=alert_type,
                severity=severity,
                entity_id=row["subject_entity_id"],
                predicate=row["predicate"],
                prior_claim_id=row["prior_id"],
                current_claim_id=row["current_id"],
                effective_date=row["valid_from"],
                confidence=confidence,
                summary=summary,
                reasoning=reasoning,
            )
        )
    return sorted(alerts, key=lambda item: (item.alert_type, item.entity_id, item.fingerprint))


def serialize_alerts(alerts: list[Alert]) -> bytes:
    return (
        "".join(
            json.dumps(alert.as_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
            for alert in alerts
        )
    ).encode("utf-8")
