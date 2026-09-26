"""One versioned proposal-review history for baseline facilities and source projects.

Legacy events retain their exact bytes, identities and clocks. Project packets are
derivatives of accepted core claims, not a second canonical claim store.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from . import ai_critical_alert_review as legacy
from . import project_target_changes as projects
from .ai_critical import ensure_real_directory
from .ai_critical_changes import _open_real_directory_fd, _pretty_bytes, _strict_json
from .source_checks import _read


APPLICATION_ID = legacy.APPLICATION_ID
VERSION = 2
RULE_VERSION = "unified-alert-review-v2"
EXPORT_FORMAT = "semiconductor-atlas-alert-review-events-v2"
ADMISSION_FORMAT = "semiconductor-atlas-project-alert-admission-v1"
ACTIONS = legacy.ACTIONS
_now, _instant, _hash, _keys, _text = legacy._now, legacy._instant, legacy._hash, legacy._keys, legacy._text


@contextmanager
def _connection(path_value: str | Path, *, write: bool = False):
    path = Path(path_value).absolute()
    _, descriptor = _open_real_directory_fd(path.parent, "alert queue parent", create=False)
    os.close(descriptor)
    if path.is_symlink() or not path.is_file():
        raise ValueError("alert queue must be a regular non-symlink file")
    connection = sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if (connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                or connection.execute("PRAGMA user_version").fetchone()[0] != VERSION):
            raise ValueError("not a supported version-2 alert review queue")
        if write:
            connection.execute("BEGIN IMMEDIATE")
        yield connection
        if write:
            connection.commit()
    except sqlite3.DatabaseError as error:
        connection.rollback()
        raise ValueError(f"invalid alert queue: {error}") from error
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_queue(path_value: str | Path) -> dict:
    path = Path(path_value).absolute()
    path = ensure_real_directory(path.parent, "alert queue parent") / path.name
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(f"""
            PRAGMA application_id = {APPLICATION_ID};
            PRAGMA user_version = {VERSION};
            CREATE TABLE alert_events (
                sequence INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
                previous_event_id TEXT, recorded_at TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TRIGGER no_alert_update BEFORE UPDATE ON alert_events
                BEGIN SELECT RAISE(ABORT, 'alert events are append-only'); END;
            CREATE TRIGGER no_alert_delete BEFORE DELETE ON alert_events
                BEGIN SELECT RAISE(ABORT, 'alert events are append-only'); END;
        """)
    finally:
        connection.close()
    return queue_report(path)


def _project_packet(payload: dict, clock: str) -> dict:
    _keys(payload, ("kind", "rule_version", "packet_id", "packet", "admission"), "project import")
    raw = legacy._unblob(payload["packet"])
    packet = projects.validate_packet(_strict_json(raw, "project change packet"), clock=clock)
    if payload["packet_id"] != _hash(raw):
        raise ValueError("project packet hash differs")
    review = _keys(_strict_json(legacy._unblob(payload["admission"]), "project alert admission"),
        ("format", "purpose", "reviewer", "reviewed_at", "reason", "packet_sha256", "expected_head_event_id"), "project alert admission")
    if review["format"] != ADMISSION_FORMAT or review["purpose"] != "alert_review_only":
        raise ValueError("separate admission must authorize alert review only")
    _text(review["reviewer"], "reviewer")
    _text(review["reason"], "reason")
    if review["packet_sha256"] != payload["packet_id"]:
        raise ValueError("review does not bind the exact project packet")
    if not (_instant(packet["generated_at"]) <= _instant(review["reviewed_at"]) <= _instant(clock)):
        raise ValueError("project alert review clock predates packet or follows admission")
    return {"packet": packet, "review": review, "packet_id": payload["packet_id"]}


def _project_decision(alert: dict, payload: dict, packets: dict) -> None:
    _keys(payload, ("kind", "rule_version", "alert_id", "action", "reviewer", "reason",
                    "expected_event_id", "evidence_refs"), "project alert decision")
    action = payload["action"]
    if action not in ACTIONS:
        raise ValueError("unsupported alert review action")
    _text(payload["reviewer"], "reviewer")
    _text(payload["reason"], "reason")
    if payload["expected_event_id"] != alert["last_event_id"]:
        raise ValueError("stale expected_event_id")
    if action == "reopen":
        if alert["status"] not in {"resolved", "retracted"}:
            raise ValueError("only closed alert review can be reopened")
    elif alert["status"] not in {"pending", "acknowledged"}:
        raise ValueError("reopen closed alert review before another disposition")
    refs = payload["evidence_refs"]
    if not isinstance(refs, list) or action in {"resolve", "retract"} and not refs:
        raise ValueError("resolve/retract require bound evidence references")
    seen = set()
    for ref in refs:
        _keys(ref, ("packet_id", "side", "claim_id", "evidence_id", "fragment_sha256"), "project evidence reference")
        if _hash(ref) in seen:
            raise ValueError("duplicate decision evidence reference")
        seen.add(_hash(ref))
        bound = packets.get(ref["packet_id"])
        if bound is None or ref["side"] not in {"before", "after"}:
            raise ValueError("decision evidence is not admitted")
        packet = bound["packet"]
        if packet["subject"]["entity_id"] != alert["subject_entity_id"]:
            raise ValueError("decision evidence belongs to another project")
        claim = packet["claims"][ref["side"]]
        if ref["claim_id"] != claim["id"] or not any(
                link["evidence_id"] == ref["evidence_id"] and link["fragment_sha256"] == ref["fragment_sha256"]
                for link in claim["evidence"]):
            raise ValueError("decision evidence does not bind an exact admitted claim fragment")


def _fold_unchecked(events: list[dict], *, as_of: str | None = None, full: bool = True) -> dict:
    legacy._validate_events(events)
    cutoff = _instant(as_of) if as_of is not None else None
    selected = [event for event in events if cutoff is None or _instant(event["recorded_at"]) <= cutoff]
    for event in selected:
        if not isinstance(event["payload"], dict) or event["payload"].get("rule_version") not in {legacy.RULE_VERSION, RULE_VERSION}:
            raise ValueError("unsupported alert review rule version")
    old_events = [event for event in selected if event["payload"]["rule_version"] == legacy.RULE_VERSION]
    old = legacy._fold(old_events, as_of=as_of, full=full)
    alerts, packets, comparisons = {}, {}, {}
    for event in selected:
        payload = event["payload"]
        if payload["rule_version"] == legacy.RULE_VERSION:
            continue
        if payload.get("kind") == "project_packet_imported":
            bound = _project_packet(payload, event["recorded_at"])
            packet, packet_id = bound["packet"], bound["packet_id"]
            if bound["review"]["expected_head_event_id"] != event["previous_event_id"]:
                raise ValueError("stale expected alert ledger head")
            if event["sequence"] > 1:
                prior = events[event["sequence"] - 2]
                if _instant(prior["recorded_at"]) > _instant(bound["review"]["reviewed_at"]):
                    raise ValueError("admission review binds a future ledger head")
            if packet_id in packets or packet["comparison_id"] in comparisons:
                raise ValueError("duplicate project comparison admission")
            packets[packet_id] = bound
            comparisons[packet["comparison_id"]] = packet_id
            for proposal in packet["proposals"]:
                identifier = _hash({"origin": "source_native_project", "comparison_id": packet["comparison_id"],
                                    "fingerprint": proposal["fingerprint"]})
                subject = packet["subject"]
                alerts[identifier] = {
                    "id": identifier, "origin": "source_native_project", "subject_entity_id": subject["entity_id"],
                    "subject_stable_key": subject["stable_key"], "subject": deepcopy(subject),
                    "rule_id": proposal["rule_id"], "rule_version": proposal["rule_version"],
                    "status": "pending", "first_recorded_at": event["recorded_at"], "last_event_id": event["event_id"],
                    "confidence": None, "confidence_scope": "unknown_not_calibrated", "severity": "unassessed",
                    "delivery_eligible": False, "observations": [{"packet_id": packet_id,
                        "comparison_id": packet["comparison_id"], "event_id": event["event_id"],
                        "recorded_at": event["recorded_at"], "proposal": deepcopy(proposal),
                        "before": deepcopy(packet["claims"]["before"]), "after": deepcopy(packet["claims"]["after"])}],
                    "decisions": [],
                }
        elif payload.get("kind") == "decision":
            alert = alerts.get(payload.get("alert_id"))
            if alert is None:
                raise ValueError("decision names unknown project alert")
            _project_decision(alert, payload, packets)
            alert["status"] = ACTIONS[payload["action"]]
            alert["last_event_id"] = event["event_id"]
            alert["decisions"].append({**deepcopy(payload), "recorded_at": event["recorded_at"], "event_id": event["event_id"]})
        else:
            raise ValueError("unsupported version-2 alert event kind")
    rows = [{"origin": "baseline_facility", **alert} for alert in old["alerts"]] + list(alerts.values())
    rows.sort(key=lambda item: (item["first_recorded_at"], item["id"]))
    return {"format": "semiconductor-atlas-alert-review-report-v2", "rule_version": RULE_VERSION,
        "as_of": as_of, "event_count": len(selected), "head_event_id": selected[-1]["event_id"] if selected else None,
        "packet_count": old["packet_count"] + len(packets), "alert_count": len(rows),
        "open_count": sum(row["status"] in {"pending", "acknowledged"} for row in rows),
        "delivery_eligible_count": 0, "alerts": rows, "legacy_event_count": len(old_events),
        "project_packets": [{"packet_id": key, "comparison_id": value["packet"]["comparison_id"],
                             "review": value["review"]} for key, value in packets.items()]}


def _fold(events: list[dict], *, as_of: str | None = None, full: bool = True) -> dict:
    try:
        return _fold_unchecked(events, as_of=as_of, full=full)
    except (KeyError, TypeError, IndexError, AttributeError) as error:
        raise ValueError("malformed unified alert review history") from error


def queue_report(path: str | Path, *, as_of: str | None = None) -> dict:
    with _connection(path) as connection:
        return _fold(legacy._events(connection), as_of=as_of)


def import_project_packet(path: str | Path, packet_path: str | Path, admission_review_path: str | Path) -> dict:
    raw, admission = _read(Path(packet_path)), _read(Path(admission_review_path))
    payload = {"kind": "project_packet_imported", "rule_version": RULE_VERSION, "packet_id": _hash(raw),
               "packet": legacy._blob(raw), "admission": legacy._blob(admission)}
    bound = _project_packet(payload, _now())
    with _connection(path, write=True) as connection:
        events = legacy._events(connection)
        before = _fold(events)
        comparison_id = bound["packet"]["comparison_id"]
        existing = next((row for row in before["project_packets"] if row["comparison_id"] == comparison_id), None)
        if existing is not None:
            original_event = next(event for event in events if event["payload"].get("kind") == "project_packet_imported"
                                  and event["payload"]["packet_id"] == existing["packet_id"])
            original = _project_packet(original_event["payload"], original_event["recorded_at"])["packet"]
            content = lambda packet: {key: value for key, value in packet.items() if key not in {"generated_at", "packet_id"}}
            if content(original) != content(bound["packet"]):
                raise ValueError("comparison is already admitted with different retained content")
            return {"imported": False, "packet_id": existing["packet_id"], **before}
        clock = _now()
        _project_packet(payload, clock)
        event = legacy._append(connection, events, payload, clock)
        report = _fold([*events, event])
    return {"imported": True, "packet_id": payload["packet_id"], **report}


def import_bundle(path: str | Path, bundle_path: str | Path, prior_path: str | Path,
                  current_path: str | Path, admission_review_path: str | Path) -> dict:
    paths = {"prior": Path(prior_path), "current": Path(current_path), "change": Path(bundle_path)}
    if any(Path(path).absolute().is_relative_to(root.absolute()) for root in paths.values()):
        raise ValueError("alert queue must be outside bound release directories")
    review_path = Path(admission_review_path).absolute()
    raw = _read(review_path)
    review = legacy._admission_fields(raw)
    supporting = []
    for ref in review["supporting_reviews"]:
        target = Path(ref["path"])
        supporting.append(legacy._blob(_read(target if target.is_absolute() else review_path.parent / target)))
    inventories = {side: legacy._directory(root) for side, root in paths.items()}
    payload = {"kind": "bundle_imported", "rule_version": legacy.RULE_VERSION,
        "packet_id": inventories["change"]["manifest.json"]["sha256"], "inventories": inventories,
        "admission": legacy._blob(raw), "supporting_reviews": supporting}
    packet = legacy._packet(payload, _now(), full=True)
    with _connection(path, write=True) as connection:
        events = legacy._events(connection)
        before = _fold(events)
        if any(event["payload"].get("kind") == "bundle_imported"
               and event["payload"].get("packet_id") == packet["packet_id"] for event in events):
            return {"imported": False, "packet_id": packet["packet_id"], **before}
        clock = _now()
        legacy._admission(raw, supporting, packet["manifests"], clock)
        event = legacy._append(connection, events, payload, clock)
        report = _fold([*events, event])
    return {"imported": True, "packet_id": packet["packet_id"], **report}


def record_decision(path: str | Path, alert_id: str, *, action: str, reviewer: str,
                    reason: str, expected_event_id: str, evidence_refs=()) -> dict:
    with _connection(path, write=True) as connection:
        events = legacy._events(connection)
        before = _fold(events)
        alert = next((row for row in before["alerts"] if row["id"] == alert_id), None)
        if alert is None:
            raise ValueError("decision names unknown alert")
        version = legacy.RULE_VERSION if alert["origin"] == "baseline_facility" else RULE_VERSION
        payload = {"kind": "decision", "rule_version": version, "alert_id": alert_id,
                   "action": action, "reviewer": reviewer, "reason": reason,
                   "expected_event_id": expected_event_id, "evidence_refs": list(evidence_refs)}
        event = legacy._append(connection, events, payload, _now())
        report = _fold([*events, event])
    return next(row for row in report["alerts"] if row["id"] == alert_id)


def export_queue(path: str | Path, *, as_of: str | None = None) -> dict:
    with _connection(path) as connection:
        events = legacy._events(connection)
        _fold(events)
        if as_of is not None:
            events = [event for event in events if _instant(event["recorded_at"]) <= _instant(as_of)]
        return {"format": EXPORT_FORMAT, "events": events}


def restore_queue(path: str | Path, export_path: str | Path) -> dict:
    artifact = _keys(_strict_json(_read(Path(export_path)), "alert export"), ("format", "events"), "alert export")
    if not isinstance(artifact["format"], str) or artifact["format"] not in {EXPORT_FORMAT, legacy.EXPORT_FORMAT}:
        raise ValueError("unsupported alert export format")
    events = artifact["events"]
    legacy._validate_events(events)
    if artifact["format"] == legacy.EXPORT_FORMAT:
        legacy._fold(events, full=True)
    report = _fold(events)
    initialize_queue(path)
    with _connection(path, write=True) as connection:
        for event in events:
            connection.execute("INSERT INTO alert_events VALUES (?, ?, ?, ?, ?)", (
                event["sequence"], event["event_id"], event["previous_event_id"], event["recorded_at"],
                _pretty_bytes(event["payload"]).decode()))
        if legacy._events(connection) != events:
            raise ValueError("restored alert events differ")
    return report


def verify_queue(path: str | Path, *, as_of: str | None = None) -> dict:
    report = queue_report(path, as_of=as_of)
    return {**report, "verified_packet_count": report["packet_count"]}
