"""Durable, replayable source-version review; never manufacturing-claim acceptance."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from .ai_critical import ensure_real_directory
from .ai_critical_changes import _open_real_directory_fd, _pretty_bytes, _strict_json
from .curated_capture import _instant, _now, _text, load_plan, validate_capture
from .source_checks import _read


APPLICATION_ID = 0x53415131
QUEUE_VERSION = 1
RULE_VERSION = "source-version-review-v1"
OPEN_STATUSES = {"pending", "acknowledged", "deferred"}
ACTIONS = {"acknowledge": "acknowledged", "defer": "deferred", "dismiss": "dismissed",
           "handoff": "handed_off", "reopen": "pending"}
ELIGIBLE = {"unchanged", "raw_bytes_only", "first_observation_requires_review",
            "visible_text_changed_requires_review"}


def _hash(value: bytes | dict | list) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else _pretty_bytes(value)).hexdigest()


@contextmanager
def _connection(path_value: str | Path, *, write: bool = False):
    path = Path(path_value).absolute()
    _, descriptor = _open_real_directory_fd(path.parent, "queue parent", create=False)
    os.close(descriptor)
    if path.is_symlink() or not path.is_file():
        raise ValueError("queue must be an existing regular non-symlink database")
    connection = sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if (connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                or connection.execute("PRAGMA user_version").fetchone()[0] != QUEUE_VERSION):
            raise ValueError("not a supported curated review queue")
        connection.execute("PRAGMA busy_timeout = 5000")
        if write:
            connection.execute("BEGIN IMMEDIATE")
        yield connection
        if write:
            connection.commit()
    except sqlite3.DatabaseError as error:
        connection.rollback()
        raise ValueError(f"invalid review queue: {error}") from error
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_queue(path_value: str | Path) -> dict:
    path = Path(path_value).absolute()
    parent = ensure_real_directory(path.parent, "review queue parent")
    path = parent / path.name
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(f"""
            PRAGMA application_id = {APPLICATION_ID};
            PRAGMA user_version = {QUEUE_VERSION};
            CREATE TABLE review_events (
                sequence INTEGER PRIMARY KEY,
                event_id TEXT NOT NULL UNIQUE,
                previous_event_id TEXT,
                recorded_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TRIGGER no_event_update BEFORE UPDATE ON review_events
                BEGIN SELECT RAISE(ABORT, 'review events are append-only'); END;
            CREATE TRIGGER no_event_delete BEFORE DELETE ON review_events
                BEGIN SELECT RAISE(ABORT, 'review events are append-only'); END;
        """)
    return queue_report(path)


def _events(connection: sqlite3.Connection) -> list[dict]:
    events = [{**dict(row), "payload": _strict_json(row["payload"].encode("utf-8"), "review event")}
              for row in connection.execute("SELECT * FROM review_events ORDER BY sequence")]
    _validate_events(events)
    return events


def _validate_events(events: list[dict]) -> None:
    if not isinstance(events, list):
        raise ValueError("review events must be an array")
    previous = None
    last_clock = None
    for index, row in enumerate(events, 1):
        if not isinstance(row, dict) or set(row) != {"sequence", "event_id", "previous_event_id", "recorded_at", "payload"}:
            raise ValueError("invalid review event fields")
        event = {"sequence": index, "previous_event_id": previous,
                 "recorded_at": row["recorded_at"], "payload": row["payload"]}
        clock = _instant(event["recorded_at"])
        if type(row["sequence"]) is not int or row["sequence"] != index or row["previous_event_id"] != previous or row["event_id"] != _hash(event):
            raise ValueError("review event chain does not replay")
        if last_clock is not None and clock < last_clock:
            raise ValueError("review event clocks moved backwards")
        previous, last_clock = row["event_id"], clock


def _append(connection: sqlite3.Connection, events: list[dict], payload: dict, recorded_at: str) -> dict:
    clock = _instant(recorded_at)
    if events and clock < _instant(events[-1]["recorded_at"]):
        raise ValueError("review event clock cannot predate queue history")
    event = {"sequence": len(events) + 1, "previous_event_id": events[-1]["event_id"] if events else None,
             "recorded_at": recorded_at, "payload": payload}
    identifier = _hash(event)
    connection.execute("INSERT INTO review_events VALUES (?, ?, ?, ?, ?)", (
        event["sequence"], identifier, event["previous_event_id"], recorded_at,
        _pretty_bytes(payload).decode("utf-8"),
    ))
    return {**event, "event_id": identifier}


def _capture_payload(root_value: str | Path) -> dict:
    root = Path(root_value).absolute()
    manifest_before = _read(root / "manifest.json")
    result = validate_capture(root)
    plan, plan_raw = load_plan(root / "plan.json")
    run = _strict_json(_read(root / "run.json"), "capture run")
    checks = _strict_json(_read(root / "source_checks.json"), "capture checks")
    attempts = {item["id"]: item for item in checks["attempts"]}
    if _read(root / "manifest.json") != manifest_before:
        raise ValueError("capture manifest changed during review import")
    return {
        "kind": "capture_imported", "rule_version": RULE_VERSION,
        "run_id": _hash(manifest_before), "source_path": str(root),
        "plan_sha256": _hash(plan_raw), "plan_id": plan["plan_id"],
        "review_scope_id": plan["review_scope_id"], "facility_key": plan["checked_facility_key"],
        "started_at": run["started_at"], "finished_at": run["finished_at"],
        "prior_ledger_sha256": run["prior_ledger_sha256"],
        "capture_rule_version": result["rule_version"], "text_normalization": "html_visible_text_v1",
        "attention_required": result["attention_required"], "policies": result["policies"],
        "documents": [{**row, "response_finished_at": attempts.get(row["id"], {}).get("finished_at")}
                      for row in result["documents"]],
    }


def _candidate_id(payload: dict, document: dict) -> str:
    return _hash({"rule_version": RULE_VERSION, "facility_key": payload["facility_key"],
                  "url": document["url"], "text_normalization": payload["text_normalization"],
                  "current_text_sha256": document["current_text_sha256"]})


def _recurrences(observations: list[dict], version: str) -> set[tuple]:
    by_clock: dict[object, set[str]] = {}
    for observation in observations:
        if observation["status"] in ELIGIBLE:
            by_clock.setdefault(_instant(observation["captured_at"]), set()).add(observation["current_text_sha256"])
    result = set()
    previous: set[str] = set()
    for clock, versions in sorted(by_clock.items()):
        if version in versions and previous - {version}:
            result.add((clock, tuple(sorted(previous - {version}))))
        previous = versions
    return result


def _fold(events: list[dict], *, as_of: str | None = None) -> dict:
    cutoff = _instant(as_of) if as_of is not None else None
    candidates: dict[str, dict] = {}
    runs: dict[str, dict] = {}
    sources: dict[tuple[str, str], list[dict]] = {}
    resolved_recurrences: dict[str, set[tuple]] = {}
    selected_events = []
    for event in events:
        if cutoff is not None and _instant(event["recorded_at"]) > cutoff:
            continue
        selected_events.append(event)
        payload = event["payload"]
        if payload.get("rule_version") != RULE_VERSION:
            raise ValueError("unsupported review rule version")
        if payload.get("kind") == "capture_imported":
            run_id = payload["run_id"]
            if run_id in runs:
                raise ValueError("duplicate capture import in event stream")
            if _instant(payload["finished_at"]) > _instant(event["recorded_at"]):
                raise ValueError("capture import contains future knowledge")
            runs[run_id] = {**payload, "imported_at": event["recorded_at"], "event_id": event["event_id"]}
            for document in payload["documents"]:
                key = payload["facility_key"], document["url"]
                observation = {
                    "run_id": run_id, "document_id": document["id"],
                    "plan_sha256": payload["plan_sha256"], "review_scope_id": payload["review_scope_id"],
                    "captured_at": document["response_finished_at"] or payload["finished_at"],
                    "assessment_at": payload["finished_at"], "response_finished_at": document["response_finished_at"],
                    "imported_at": event["recorded_at"], "status": document["status"],
                    "current_sha256": document["current_sha256"], "prior_sha256": document["prior_sha256"],
                    "prior_success_at": document["prior_success_at"],
                    "current_text_sha256": document["current_text_sha256"],
                    "prior_text_sha256": document["prior_text_sha256"],
                }
                sources.setdefault(key, []).append(observation)
                if document["status"] not in ELIGIBLE:
                    continue
                identifier = _candidate_id(payload, document)
                if identifier not in candidates:
                    candidates[identifier] = {
                        "id": identifier, "facility_key": key[0], "source_url": key[1],
                        "company": document["company"], "country_code": document["country_code"],
                        "source_family": document["source_family"], "rule_version": RULE_VERSION,
                        "text_normalization": payload["text_normalization"],
                        "prior_text_sha256": document["prior_text_sha256"],
                        "current_text_sha256": document["current_text_sha256"],
                        "trigger": document["status"] if document["review_required"] else "unreviewed_version_at_queue_admission",
                        "first_seen_at": observation["captured_at"], "first_recorded_at": event["recorded_at"],
                        "last_seen_at": observation["captured_at"], "status": "pending",
                        "last_event_id": event["event_id"], "requires_reopen": False,
                        "observations": [], "decisions": [],
                    }
                candidate = candidates[identifier]
                if candidate["status"] not in OPEN_STATUSES and document["review_required"]:
                    candidate["requires_reopen"] = True
                candidate["observations"].append(observation)
                candidate["first_seen_at"] = min((candidate["first_seen_at"], observation["captured_at"]), key=_instant)
                candidate["last_seen_at"] = max((candidate["last_seen_at"], observation["captured_at"]), key=_instant)
            for identifier, candidate in candidates.items():
                if candidate["status"] not in OPEN_STATUSES:
                    key = candidate["facility_key"], candidate["source_url"]
                    known = _recurrences(sources[key], candidate["current_text_sha256"])
                    if known - resolved_recurrences[identifier]:
                        candidate["requires_reopen"] = True
        elif payload.get("kind") == "review_decision":
            candidate = candidates.get(payload["candidate_id"])
            if candidate is None or candidate["last_event_id"] != payload["expected_event_id"]:
                raise ValueError("review decision refers to an unknown or stale candidate")
            _validate_decision(candidate, payload)
            candidate["status"] = ACTIONS[payload["action"]]
            candidate["last_event_id"] = event["event_id"]
            candidate["requires_reopen"] = False
            candidate["decisions"].append({**payload, "recorded_at": event["recorded_at"], "event_id": event["event_id"]})
            if candidate["status"] not in OPEN_STATUSES:
                key = candidate["facility_key"], candidate["source_url"]
                resolved_recurrences[candidate["id"]] = _recurrences(sources[key], candidate["current_text_sha256"])
        else:
            raise ValueError("unknown review event kind")
    source_reports = []
    for (facility, url), observations in sorted(sources.items()):
        last_clock = max(_instant(item["captured_at"]) for item in observations)
        latest = [item for item in observations if _instant(item["captured_at"]) == last_clock]
        eligible = [item for item in observations if item["status"] in ELIGIBLE]
        eligible_clock = max((_instant(item["captured_at"]) for item in eligible), default=None)
        last_eligible = [item for item in eligible if _instant(item["captured_at"]) == eligible_clock]
        source_reports.append({"facility_key": facility, "source_url": url,
                               "latest_checks": latest, "last_eligible_observations": last_eligible,
                               "attention_required": any(item["status"] not in ELIGIBLE for item in latest)
                               or len({item["current_text_sha256"] for item in last_eligible}) > 1,
                               "observation_count": len(observations)})
    ordered = [candidates[key] for key in sorted(candidates)]
    pending = sum(item["status"] in OPEN_STATUSES for item in ordered)
    recheck = sum(item["requires_reopen"] for item in ordered)
    return {
        "format": "semiconductor-atlas-curated-review-report-v1", "rule_version": RULE_VERSION,
        "as_of": as_of, "event_count": len(selected_events), "run_count": len(runs),
        "head_event_id": selected_events[-1]["event_id"] if selected_events else None,
        "candidate_count": len(ordered), "pending_count": pending, "recheck_count": recheck,
        "attention_required": bool(pending or recheck or any(item["attention_required"] for item in source_reports)),
        "claim_acceptance": False, "delivery_eligible": False, "absence_inference_allowed": False,
        "coverage_complete": False,
        "coverage_note": "Imported exact-URL packets only; omitted packets and intermediate versions may be unknown.",
        "candidates": ordered, "sources": source_reports,
        "runs": [runs[key] for key in sorted(runs)],
    }


def _validate_decision(candidate: dict, payload: dict) -> None:
    action = payload["action"]
    if action not in ACTIONS:
        raise ValueError("unknown review action; queue decisions cannot accept claims")
    _text(payload["reviewer"])
    _text(payload["reason"])
    if action == "handoff":
        _text(payload["evidence_ref"])
    elif payload["evidence_ref"] is not None:
        _text(payload["evidence_ref"])
    if action == "reopen" and candidate["status"] in OPEN_STATUSES:
        raise ValueError("only a resolved candidate can be reopened")
    if action != "reopen" and candidate["status"] not in OPEN_STATUSES:
        raise ValueError("reopen a resolved candidate before another disposition")


def queue_report(path: str | Path, *, as_of: str | None = None) -> dict:
    with _connection(path) as connection:
        return _fold(_events(connection), as_of=as_of)


def import_capture(path: str | Path, capture_root: str | Path) -> dict:
    root = Path(capture_root).absolute()
    if Path(path).absolute().is_relative_to(root):
        raise ValueError("review queue must be outside the immutable capture")
    payload = _capture_payload(root)
    with _connection(path, write=True) as connection:
        events = _events(connection)
        before = _fold(events)
        if any(run["run_id"] == payload["run_id"] for run in before["runs"]):
            return {"imported": False, "run_id": payload["run_id"], "pending_count": before["pending_count"]}
        recorded_at = _now()
        event = _append(connection, events, payload, recorded_at)
        report = _fold([*events, event])
        if _capture_payload(root) != payload:
            raise ValueError("capture changed before queue commit")
    return {"imported": True, "run_id": payload["run_id"], "pending_count": report["pending_count"],
            "candidates_created": report["candidate_count"] - before["candidate_count"],
            "head_event_id": event["event_id"]}


def record_decision(
    path: str | Path, candidate_id: str, *, action: str, reviewer: str, reason: str,
    expected_event_id: str, evidence_ref: str | None = None,
) -> dict:
    payload = {"kind": "review_decision", "rule_version": RULE_VERSION,
               "candidate_id": candidate_id, "action": action, "reviewer": reviewer,
               "reason": reason, "expected_event_id": expected_event_id, "evidence_ref": evidence_ref}
    with _connection(path, write=True) as connection:
        events = _events(connection)
        before = _fold(events)
        candidate = next((item for item in before["candidates"] if item["id"] == candidate_id), None)
        if candidate is None or candidate["last_event_id"] != expected_event_id:
            raise ValueError("unknown candidate or stale expected_event_id")
        _validate_decision(candidate, payload)
        event = _append(connection, events, payload, _now())
        after = _fold([*events, event])
    return next(item for item in after["candidates"] if item["id"] == candidate_id)


def export_events(path: str | Path) -> dict:
    with _connection(path) as connection:
        events = _events(connection)
        _fold(events)
        return {"format": "semiconductor-atlas-curated-review-events-v1", "events": events}


def restore_queue(path: str | Path, events_path: str | Path) -> dict:
    artifact = _strict_json(_read(Path(events_path)), "review event export")
    if not isinstance(artifact, dict) or set(artifact) != {"format", "events"} or artifact["format"] != "semiconductor-atlas-curated-review-events-v1":
        raise ValueError("invalid review event export")
    events = artifact["events"]
    _validate_events(events)
    report = _fold(events)
    if any(Path(path).resolve().is_relative_to(Path(run["source_path"]).resolve()) for run in report["runs"]):
        raise ValueError("restored queue must be outside its immutable captures")
    initialize_queue(path)
    with _connection(path, write=True) as connection:
        for event in events:
            connection.execute("INSERT INTO review_events VALUES (?, ?, ?, ?, ?)", (
                event["sequence"], event["event_id"], event["previous_event_id"], event["recorded_at"],
                _pretty_bytes(event["payload"]).decode("utf-8"),
            ))
        if _events(connection) != events:
            raise ValueError("restored review events differ")
    return report


def verify_queue(path: str | Path, *, as_of: str | None = None) -> dict:
    """Verify event replay and every imported packet at its retained location."""
    with _connection(path) as connection:
        events = _events(connection)
        report = _fold(events, as_of=as_of)
        selected_runs = {run["run_id"] for run in report["runs"]}
        for event in events:
            payload = event["payload"]
            if (payload["kind"] == "capture_imported" and payload["run_id"] in selected_runs
                    and _capture_payload(payload["source_path"]) != payload):
                raise ValueError("retained capture no longer matches the queue event")
    return {**report, "verified_capture_count": report["run_count"]}
