"""Append-only review of discovered URLs, separate from acquired source text."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from .ai_critical import ensure_real_directory
from .ai_critical_changes import _open_real_directory_fd, _pretty_bytes, _strict_json
from .curated_capture import _instant, _now, _text
from .curated_review import _append, _events, _hash, _validate_events
from .nist_discovery import validate_capture
from .source_checks import _read


APPLICATION_ID = 0x53414E31
RULE_VERSION = "nist-discovery-url-review-v1"
EXPORT_FORMAT = "semiconductor-atlas-discovery-review-events-v1"
OPEN_STATUSES = {"pending", "acknowledged", "deferred"}
ACTIONS = {"acknowledge": "acknowledged", "defer": "deferred", "dismiss": "dismissed",
           "handoff": "handed_off", "reopen": "pending"}


@contextmanager
def _connection(path_value: str | Path, *, write: bool = False):
    path = Path(path_value).absolute()
    _, descriptor = _open_real_directory_fd(path.parent, "discovery queue parent", create=False)
    os.close(descriptor)
    if path.is_symlink() or not path.is_file():
        raise ValueError("discovery queue must be an existing regular database")
    connection = sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if (connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                or connection.execute("PRAGMA user_version").fetchone()[0] != 1):
            raise ValueError("not a discovery review queue")
        connection.execute("PRAGMA busy_timeout = 5000")
        if write:
            connection.execute("BEGIN IMMEDIATE")
        yield connection
        if write:
            connection.commit()
    except sqlite3.DatabaseError as error:
        connection.rollback()
        raise ValueError(f"invalid discovery queue: {error}") from error
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_queue(path_value: str | Path) -> dict:
    path = Path(path_value).absolute()
    parent = ensure_real_directory(path.parent, "discovery queue parent")
    path = parent / path.name
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(f"""
            PRAGMA application_id = {APPLICATION_ID};
            PRAGMA user_version = 1;
            CREATE TABLE review_events (
                sequence INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
                previous_event_id TEXT, recorded_at TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TRIGGER no_event_update BEFORE UPDATE ON review_events
                BEGIN SELECT RAISE(ABORT, 'discovery events are append-only'); END;
            CREATE TRIGGER no_event_delete BEFORE DELETE ON review_events
                BEGIN SELECT RAISE(ABORT, 'discovery events are append-only'); END;
        """)
    return queue_report(path)


def _capture_payload(root_value: str | Path) -> dict:
    root = Path(root_value).absolute()
    manifest = _read(root / "manifest.json")
    result = validate_capture(root)
    run = _strict_json(_read(root / "run.json"), "discovery run")
    plan = _strict_json(_read(root / "plan.json"), "discovery plan")
    if _read(root / "manifest.json") != manifest:
        raise ValueError("discovery packet changed during import")
    return {"kind": "discovery_capture_imported", "rule_version": RULE_VERSION,
            "run_id": _hash(manifest), "source_path": str(root), "plan_sha256": run["plan_sha256"],
            "baseline_sha256": plan["baseline"]["sha256"], "checked_source_id": result["checked_source_id"],
            "started_at": run["started_at"], "finished_at": run["finished_at"], "result": result}


def _metadata(entry: dict) -> dict:
    return {key: entry[key] for key in ("url", "title", "index_date", "summary", "source_native_scope", "matched_companies")}


def _signature(observations: list[dict]) -> str:
    by_clock = {}
    for row in observations:
        by_clock.setdefault(_instant(row["observed_at"]), set()).add(row["metadata_sha256"])
    transitions = []
    for _, values in sorted(by_clock.items()):
        value = sorted(values)
        if not transitions or transitions[-1] != value:
            transitions.append(value)
    return _hash(transitions)


def _validate_decision(candidate: dict, payload: dict) -> None:
    if payload["action"] not in ACTIONS:
        raise ValueError("unsupported discovery review action")
    for field in ("reviewer", "reason"):
        _text(payload[field])
    if payload["evidence_ref"] is not None:
        _text(payload["evidence_ref"])
    if payload["action"] == "handoff" and payload["evidence_ref"] is None:
        raise ValueError("discovery handoff requires a scope/access review reference")
    if candidate["status"] not in OPEN_STATUSES and payload["action"] != "reopen":
        raise ValueError("resolved discovery must be explicitly reopened before another decision")
    if payload["action"] == "reopen" and candidate["status"] in OPEN_STATUSES:
        raise ValueError("discovery is already open")


def _fold(events: list[dict], *, as_of: str | None = None) -> dict:
    _validate_events(events)
    cutoff = _instant(as_of) if as_of is not None else None
    candidates, runs, resolved = {}, {}, {}
    selected = []
    for event in events:
        if cutoff is not None and _instant(event["recorded_at"]) > cutoff:
            continue
        selected.append(event)
        payload = event["payload"]
        if not isinstance(payload, dict) or payload.get("rule_version") != RULE_VERSION:
            raise ValueError("unsupported discovery review rule")
        if payload["kind"] == "discovery_capture_imported":
            if payload["run_id"] in runs or _instant(payload["finished_at"]) > _instant(event["recorded_at"]):
                raise ValueError("duplicate discovery import or future capture knowledge")
            if payload["checked_source_id"] != "nist:chips-public-indexes":
                raise ValueError("unsupported discovery publisher")
            result = payload["result"]
            if any(result[key] is not False for key in ("publisher_complete", "facility_coverage_complete", "claim_acceptance", "linked_document_acquisition_allowed", "absence_inference_allowed")):
                raise ValueError("discovery cannot accept claims or grant permissions")
            runs[payload["run_id"]] = {**payload, "imported_at": event["recorded_at"], "event_id": event["event_id"]}
            for index in result["indexes"]:
                for entry in index["entries"]:
                    if not _instant(payload["started_at"]) <= _instant(entry["observed_at"]) <= _instant(payload["finished_at"]):
                        raise ValueError("discovery observation outside capture clocks")
                    identity = _hash({"checked_source_id": payload["checked_source_id"], "url": entry["url"]})
                    candidate = candidates.setdefault(identity, {
                        "id": identity, "checked_source_id": payload["checked_source_id"], "source_url": entry["url"],
                        "status": "pending", "requires_reopen": False, "last_event_id": event["event_id"],
                        "first_recorded_at": event["recorded_at"], "observations": [], "decisions": [],
                    })
                    before = _signature(candidate["observations"])
                    candidate["observations"].append({**entry, "metadata_sha256": _hash(_metadata(entry)),
                        "run_id": payload["run_id"], "plan_sha256": payload["plan_sha256"],
                        "imported_at": event["recorded_at"]})
                    after = _signature(candidate["observations"])
                    if before != after:
                        candidate["last_event_id"] = event["event_id"]
                    if identity in resolved and after != resolved[identity]:
                        candidate["requires_reopen"] = True
        elif payload["kind"] == "discovery_review_decision":
            candidate = candidates.get(payload["candidate_id"])
            if candidate is None or candidate["last_event_id"] != payload["expected_event_id"]:
                raise ValueError("unknown discovery candidate or stale expected_event_id")
            _validate_decision(candidate, payload)
            candidate["status"] = ACTIONS[payload["action"]]
            candidate["last_event_id"] = event["event_id"]
            candidate["requires_reopen"] = False
            candidate["decisions"].append({**payload, "recorded_at": event["recorded_at"], "event_id": event["event_id"]})
            if candidate["status"] not in OPEN_STATUSES:
                resolved[candidate["id"]] = _signature(candidate["observations"])
            else:
                resolved.pop(candidate["id"], None)
        else:
            raise ValueError("unsupported discovery event")
    rows = sorted(candidates.values(), key=lambda row: row["id"])
    for row in rows:
        row["observations"].sort(key=lambda item: (_instant(item["observed_at"]), item["run_id"], item["index_url"]))
        row["first_seen_at"] = row["observations"][0]["observed_at"]
        row["last_seen_at"] = row["observations"][-1]["observed_at"]
        row["matched_companies"] = sorted({company for item in row["observations"] for company in item["matched_companies"]})
        row["metadata_version_count"] = len({item["metadata_sha256"] for item in row["observations"]})
        row["review_fingerprint"] = _signature(row["observations"])
    run_rows = sorted(runs.values(), key=lambda row: (_instant(row["finished_at"]), row["run_id"]))
    latest = [row for row in run_rows if _instant(row["finished_at"]) == _instant(run_rows[-1]["finished_at"])] if run_rows else []
    pending = [row for row in rows if row["status"] in OPEN_STATUSES]
    return {"format": "semiconductor-atlas-discovery-review-report-v1", "rule_version": RULE_VERSION,
            "as_of": as_of, "event_count": len(selected), "head_event_id": selected[-1]["event_id"] if selected else None,
            "run_count": len(run_rows), "runs": run_rows, "candidate_count": len(rows), "candidates": rows,
            "open_candidate_count": len(pending), "pending_count": sum(bool(row["matched_companies"]) for row in pending),
            "unrouted_pending_count": sum(not row["matched_companies"] for row in pending),
            "recheck_count": sum(row["requires_reopen"] for row in rows),
            "latest_run_ids": [row["run_id"] for row in latest],
            "latest_capture_attention_required": not latest or any(row["result"]["attention_required"] for row in latest),
            "claim_acceptance": False, "linked_document_acquisition_allowed": False,
            "publisher_complete": False, "facility_coverage_complete": False, "absence_inference_allowed": False,
            "scope_note": "URL review by admission-time history. All discovered links are retained; pending_count counts routed company matches only. Handoff records a review reference, never permission, facility identity or accepted evidence."}


def queue_report(path: str | Path, *, as_of: str | None = None) -> dict:
    with _connection(path) as connection:
        return _fold(_events(connection), as_of=as_of)


def import_capture(path: str | Path, capture_root: str | Path) -> dict:
    root = Path(capture_root).absolute()
    if Path(path).resolve().is_relative_to(root.resolve()):
        raise ValueError("discovery queue must be outside immutable capture")
    payload = _capture_payload(root)
    with _connection(path, write=True) as connection:
        events = _events(connection)
        before = _fold(events)
        if any(run["run_id"] == payload["run_id"] for run in before["runs"]):
            return {"imported": False, "run_id": payload["run_id"], "pending_count": before["pending_count"]}
        event = _append(connection, events, payload, _now())
        report = _fold([*events, event])
        if _capture_payload(root) != payload:
            raise ValueError("discovery capture changed before commit")
    return {"imported": True, "run_id": payload["run_id"], "pending_count": report["pending_count"],
            "candidates_created": report["candidate_count"] - before["candidate_count"], "head_event_id": event["event_id"]}


def record_decision(path: str | Path, candidate_id: str, *, action: str, reviewer: str, reason: str,
                    expected_event_id: str, evidence_ref: str | None = None) -> dict:
    payload = {"kind": "discovery_review_decision", "rule_version": RULE_VERSION, "candidate_id": candidate_id,
               "action": action, "reviewer": reviewer, "reason": reason,
               "expected_event_id": expected_event_id, "evidence_ref": evidence_ref}
    with _connection(path, write=True) as connection:
        events = _events(connection)
        before = _fold(events)
        candidate = next((row for row in before["candidates"] if row["id"] == candidate_id), None)
        if candidate is None or candidate["last_event_id"] != expected_event_id:
            raise ValueError("unknown discovery candidate or stale expected_event_id")
        _validate_decision(candidate, payload)
        event = _append(connection, events, payload, _now())
        report = _fold([*events, event])
    return next(row for row in report["candidates"] if row["id"] == candidate_id)


def export_events(path: str | Path, *, as_of: str | None = None) -> dict:
    with _connection(path) as connection:
        events = _events(connection)
        _fold(events)
        if as_of is not None:
            events = [row for row in events if _instant(row["recorded_at"]) <= _instant(as_of)]
        return {"format": EXPORT_FORMAT, "events": events}


def restore_queue(path: str | Path, events_path: str | Path) -> dict:
    data = _strict_json(_read(Path(events_path)), "discovery events")
    if not isinstance(data, dict) or set(data) != {"format", "events"} or data["format"] != EXPORT_FORMAT:
        raise ValueError("invalid discovery event export")
    events = data["events"]
    report = _fold(events)
    if any(Path(path).resolve().is_relative_to(Path(run["source_path"]).resolve()) for run in report["runs"]):
        raise ValueError("restored queue must be outside retained packets")
    initialize_queue(path)
    with _connection(path, write=True) as connection:
        for event in events:
            connection.execute("INSERT INTO review_events VALUES (?, ?, ?, ?, ?)", (
                event["sequence"], event["event_id"], event["previous_event_id"], event["recorded_at"],
                _pretty_bytes(event["payload"]).decode("utf-8")))
        if _events(connection) != events:
            raise ValueError("restored discovery events differ")
    return report


def verify_queue(path: str | Path, *, as_of: str | None = None) -> dict:
    with _connection(path) as connection:
        report = _fold(_events(connection), as_of=as_of)
        for run in report["runs"]:
            expected = {key: value for key, value in run.items() if key not in {"imported_at", "event_id"}}
            if _capture_payload(run["source_path"]) != expected:
                raise ValueError("retained discovery packet no longer matches admission")
    return {**report, "verified_capture_count": report["run_count"]}
