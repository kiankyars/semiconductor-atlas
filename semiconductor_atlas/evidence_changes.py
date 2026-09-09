"""A read-only knowledge-event inventory, not a manufacturing-event detector."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import date

from . import database, service
from .evidence_dossier import _check_support, _clock, _public_claim
from .models import EntityKind


FORMAT = "semiconductor-atlas-evidence-changes-v1"
EVENT_TYPES = ("claim_admitted", "claim_superseded")
BOUNDARIES = [
    "Events are stored claim admissions and supersessions, not physical manufacturing events, corrections certified as true, or deliverable alerts.",
    "The knowledge window is exclusive of since and inclusive of recorded_at; physical validity, source publication and retrieval do not select events.",
    "Supersession is bookkeeping, not evidence of cancellation, closure, capacity loss or retraction; no replacement or physical interpretation is inferred.",
    "Exact subjects remain separate. Event, claim and subject counts are different denominators, not independent observations.",
    "Page claims and their transitive support are checked at the upper cutoff, not certified as fully available at each original event time. Incomplete lineage rejects the page; do not advance a window after an error.",
    "Population counts inventory all selected event metadata; unseen pages have not yet passed typed-value and lineage checks.",
    "Each response uses one read snapshot. The population hash binds selectors and event metadata, not all database bytes or provenance; retain a frozen database or outer read transaction for identical cross-page evidence.",
    "Advancing since cannot discover later backdated writes. Recheck overlapping windows by event ID, or use a frozen input; this is not an append-only delivery cursor.",
    "Absence, physical expiry, entity edits, evidence-link additions and raw deletion are not manufactured into events. Empty means no selected stored events, not no real-world change.",
    "No source check, original-source-byte verification, confidence calibration, forecast, delivery or database mutation. Source excerpts, record payloads, claim notes and run parameters are omitted.",
]
_CLAIM_COLUMNS = (
    "id", "series_id", "value_kind", "value_sha256", "valid_from", "valid_to", "recorded_at",
    "superseded_at", "claim_kind", "method", "confidence", "created_by_run_id",
    "subject_entity_id", "series_stable_key", "predicate",
)
_ENTITY_COLUMNS = ("id", "kind", "stable_key", "display_name", "created_at", "created_by_run_id")


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _population(connection, *, lower: int, upper: int, kind: str | None,
                subject_entity_id: str | None) -> list[dict]:
    clock = database.knowledge_clock_sql(connection)
    rows = connection.execute(f"""
        WITH clocks AS MATERIALIZED (
            SELECT v.*, {clock}(v.recorded_at) AS admission_us,
                   {clock}(v.superseded_at) AS supersession_us
            FROM claim_versions v
        )
        SELECT v.id,v.series_id,v.value_kind,v.value_sha256,v.valid_from,v.valid_to,
               v.recorded_at,v.superseded_at,v.claim_kind,v.method,v.confidence,v.created_by_run_id,
               v.admission_us,v.supersession_us,
               s.subject_entity_id,s.stable_key AS series_stable_key,s.predicate,
               e.id AS entity_id,e.kind AS entity_kind,e.stable_key AS entity_stable_key,
               e.display_name AS entity_display_name,e.created_at AS entity_created_at,
               e.created_by_run_id AS entity_created_by_run_id
        FROM clocks v LEFT JOIN claim_series s ON s.id=v.series_id
        LEFT JOIN entities e ON e.id=s.subject_entity_id
        WHERE v.admission_us IS NULL OR (v.superseded_at IS NOT NULL AND v.supersession_us IS NULL)
           OR (v.admission_us>:lower AND v.admission_us<=:upper)
           OR (v.supersession_us>:lower AND v.supersession_us<=:upper)
        """, {"lower": lower, "upper": upper}).fetchall()
    result = []
    for row in rows:
        if row["admission_us"] is None or (row["superseded_at"] is not None
                                            and row["supersession_us"] is None):
            raise ValueError("invalid stored claim clock prevents a complete event inventory")
        if row["entity_id"] is None:
            raise ValueError("selected event has no exact subject or claim series")
        if row["supersession_us"] is not None and row["supersession_us"] <= row["admission_us"]:
            raise ValueError("selected claim supersession must follow admission")
        if kind is not None and row["entity_kind"] != kind:
            continue
        if subject_entity_id is not None and row["subject_entity_id"] != subject_entity_id:
            continue
        claim = {key: row[key] for key in _CLAIM_COLUMNS}
        if row["supersession_us"] is None or row["supersession_us"] > upper:
            claim["superseded_at"] = None
        entity = {key: row["entity_" + key] for key in _ENTITY_COLUMNS}
        for event_type, stamp, micros in (
                ("claim_admitted", row["recorded_at"], row["admission_us"]),
                ("claim_superseded", claim["superseded_at"], row["supersession_us"])):
            if micros is not None and lower < micros <= upper:
                result.append({
                    "event_id": "evidence-event:" + _digest([FORMAT, event_type, row["id"], micros]),
                    "event_type": event_type, "event_at": stamp,
                    "entity": entity, "claim": claim, "_event_us": micros,
                })
    result.sort(key=lambda e: (e["_event_us"], e["event_type"], e["claim"]["id"]))
    for event in result:
        del event["_event_us"]
    return result


def _page_claims(connection, identifiers: list[str], recorded_at: str) -> list[dict]:
    clock = database.knowledge_clock_sql(connection)
    rows = connection.execute(f"""
        WITH RECURSIVE selected(id) AS (
            SELECT value FROM json_each(?)
            UNION
            SELECT d.depends_on_claim_version_id FROM claim_dependencies d
            JOIN selected ON selected.id=d.claim_version_id
        )
        SELECT v.id,v.series_id,v.value_kind,v.value_sha256,v.valid_from,v.valid_to,
               v.recorded_at,
               CASE WHEN {clock}(v.superseded_at)<={clock}(?) THEN v.superseded_at
                    ELSE NULL END AS superseded_at,
               v.claim_kind,v.method,v.confidence,v.created_by_run_id,
               s.subject_entity_id,s.stable_key AS series_stable_key,s.predicate,
               e.id AS lineage_entity_id
        FROM selected LEFT JOIN claim_versions v ON v.id=selected.id
        LEFT JOIN claim_series s ON s.id=v.series_id
        LEFT JOIN entities e ON e.id=s.subject_entity_id
        ORDER BY v.id
        """, (json.dumps(identifiers), recorded_at)).fetchall()
    if any(row["id"] is None or row["subject_entity_id"] is None or row["lineage_entity_id"] is None for row in rows):
        raise ValueError("selected event lineage has a missing claim, series or entity")
    claims = service._hydrate_claim_rows(connection, rows)
    declared_evidence = {row[0] for row in connection.execute("""
        SELECT id FROM claim_evidence WHERE claim_version_id IN
            (SELECT value FROM json_each(?))
        """, (json.dumps([claim["id"] for claim in claims]),))}
    hydrated_evidence = {link["id"] for claim in claims for link in claim["evidence"]}
    if declared_evidence != hydrated_evidence:
        raise ValueError("selected event lineage has missing evidence documents, sources or families")
    return claims


def evidence_changes(connection: sqlite3.Connection, *, since: str, recorded_at: str,
                     as_of: str, kind: str | None = None, subject_entity_id: str | None = None,
                     limit: int = 50, offset: int = 0, population_sha256: str | None = None) -> dict:
    """Inventory the whole window, validate the requested page, and preserve caller transactions."""
    try:
        lower_clock, upper_clock = _clock(since), _clock(recorded_at)
    except OverflowError as error:
        raise ValueError("knowledge window is outside the supported UTC date range") from error
    if lower_clock >= upper_clock:
        raise ValueError("since must precede recorded_at")
    if not isinstance(as_of, str) or date.fromisoformat(as_of).isoformat() != as_of:
        raise ValueError("as_of must use YYYY-MM-DD; it selects dossier context only")
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("limit must be an integer between 1 and 200")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if kind is not None and (not isinstance(kind, str) or kind not in {item.value for item in EntityKind}):
        raise ValueError("unsupported entity kind")
    if subject_entity_id is not None and (not isinstance(subject_entity_id, str) or not subject_entity_id.strip()):
        raise ValueError("subject_entity_id must be an exact nonempty ID")
    if population_sha256 is not None and (not isinstance(population_sha256, str)
                                          or re.fullmatch(r"[0-9a-f]{64}", population_sha256) is None):
        raise ValueError("population_sha256 must be a lowercase SHA-256")
    if offset and population_sha256 is None:
        raise ValueError("pagination requires the first page's population_sha256")
    since = lower_clock.isoformat().replace("+00:00", "Z")
    recorded_at = upper_clock.isoformat().replace("+00:00", "Z")
    selectors = dict(since=since, recorded_at=recorded_at, as_of=as_of,
                     kind=kind, subject_entity_id=subject_entity_id)
    connection.execute("SAVEPOINT evidence_changes_read")
    try:
        version = database.schema_version(connection)
        if version < 5:
            raise ValueError("evidence changes requires existing schema 5 or later; no migration performed")
        events = _population(connection, lower=database._timestamp_microseconds(since),
            upper=database._timestamp_microseconds(recorded_at), kind=kind, subject_entity_id=subject_entity_id)
        digest = _digest({"format": FORMAT, "schema_version": version, **selectors, "events": events})
        if population_sha256 is not None and population_sha256 != digest:
            raise ValueError("event inventory or selectors changed; restart pagination from offset zero")
        page = events[offset:offset + limit]
        root_ids = {event["claim"]["id"] for event in page}
        claims = _page_claims(connection, sorted(root_ids), recorded_at) if page else []
        runs = _check_support(connection, claims, upper_clock, page[0]["entity"]) if page else []
        public = {claim["id"]: _public_claim(claim) for claim in claims}
        for event in page:
            event["claim"] = public[event["claim"]["id"]]
            event["dossier_selection"] = dict(entity_id=event["entity"]["id"],
                                               as_of=as_of, recorded_at=recorded_at)
        counts = Counter(event["event_type"] for event in events)
        return {
            "format": FORMAT, "schema_version": version, **selectors,
            "population_sha256": digest, "total_events": len(events),
            "event_counts": {key: counts[key] for key in EVENT_TYPES},
            "distinct_claims": len({event["claim"]["id"] for event in events}),
            "distinct_subjects": len({event["entity"]["id"] for event in events}),
            "offset": offset, "limit": limit, "returned_events": len(page),
            "next_offset": offset + len(page) if offset + len(page) < len(events) else None,
            "ordering": "exact event instant, then event type (admission before supersession), then claim ID; ascending",
            "events": page,
            "dependency_context": [public[key] for key in sorted(public.keys() - root_ids)],
            "ingestion_runs": runs,
            "page_validation_cutoff": recorded_at,
            "boundaries": BOUNDARIES.copy(),
        }
    finally:
        connection.execute("RELEASE SAVEPOINT evidence_changes_read")
