"""Portable, append-only review of release-bound alert proposals, never delivery."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from .ai_critical import ensure_real_directory
from .ai_critical_changes import (
    _assertion, _iso_timestamp, _open_real_directory_fd, _pretty_bytes,
    _require_keys, _snapshot_open_directory, _strict_json, validate_change_bundle,
)
from .source_checks import _read


APPLICATION_ID = 0x53414131
VERSION = 1
RULE_VERSION = "ai-critical-alert-review-v1"
EXPORT_FORMAT = "semiconductor-atlas-ai-critical-alert-review-events-v1"
ADMISSION_FORMAT = "semiconductor-atlas-ai-critical-alert-admission-v1"
ACTIONS = {"acknowledge": "acknowledged", "resolve": "resolved",
           "retract": "retracted", "reopen": "pending"}


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _instant(value: object) -> datetime:
    return datetime.fromisoformat(_iso_timestamp(value, "knowledge clock").replace("Z", "+00:00"))


def _hash(value: bytes | dict | list) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else _pretty_bytes(value)).hexdigest()


def _keys(value: object, fields: tuple[str, ...], context: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    _require_keys(value, fields, context)
    return value


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be nonempty text")
    return value


def _blob(raw: bytes) -> dict:
    return {"bytes": len(raw), "sha256": _hash(raw), "base64": base64.b64encode(raw).decode("ascii")}


def _unblob(value: object) -> bytes:
    item = _keys(value, ("bytes", "sha256", "base64"), "embedded file")
    if type(item["bytes"]) is not int or not 0 <= item["bytes"] <= 20_000_000:
        raise ValueError("invalid embedded file length")
    encoded = item["base64"]
    if not isinstance(encoded, str) or len(encoded) != 4 * ((item["bytes"] + 2) // 3):
        raise ValueError("embedded base64 length disagrees with bounded file length")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError, binascii.Error) as error:
        raise ValueError("invalid embedded base64") from error
    if _blob(raw) != item:
        raise ValueError("embedded file hash, length or encoding mismatch")
    return raw


def _directory(path: str | Path) -> dict:
    _, descriptor = _open_real_directory_fd(path, "bound release directory", create=False)
    try:
        return {name: _blob(raw) for name, raw in sorted(
            _snapshot_open_directory(descriptor, "bound release directory").items())}
    finally:
        os.close(descriptor)


def _files(value: object) -> dict[str, bytes]:
    if not isinstance(value, dict) or not value or len(value) > 100:
        raise ValueError("invalid embedded inventory")
    result = {}
    for name, blob in value.items():
        if not isinstance(name, str) or not name or Path(name).name != name or name in {".", ".."} or "\\" in name or "\x00" in name:
            raise ValueError("unsafe embedded filename")
        result[name] = _unblob(blob)
    return result


def _admission_fields(raw: bytes) -> dict:
    item = _keys(_strict_json(raw, "admission review"), (
        "format", "purpose", "reviewer", "reviewed_at", "reason",
        "prior_manifest_sha256", "current_manifest_sha256", "change_manifest_sha256",
        "supporting_reviews"), "admission review")
    if item["format"] != ADMISSION_FORMAT or item["purpose"] != "alert_review_only":
        raise ValueError("admission must authorize alert review only")
    _text(item["reviewer"], "reviewer")
    _text(item["reason"], "reason")
    _instant(item["reviewed_at"])
    refs = item["supporting_reviews"]
    if not isinstance(refs, list) or not refs or len(refs) > 100:
        raise ValueError("admission needs bounded supporting review references")
    seen = set()
    for ref in refs:
        _keys(ref, ("path", "sha256"), "supporting review reference")
        path = _text(ref["path"], "supporting review path")
        if "\x00" in path or path in seen:
            raise ValueError("invalid or duplicate supporting review path")
        seen.add(path)
        digest = ref["sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("supporting review must bind lowercase SHA-256")
    return item


def _admission(raw: bytes, supporting: list[dict], manifests: dict, admission_clock: str) -> dict:
    item = _admission_fields(raw)
    if not (_instant(manifests["current"]["recorded_at"]) <= _instant(item["reviewed_at"])
            <= _instant(admission_clock)):
        raise ValueError("admission review clock predates current release or contains future knowledge")
    for side in ("prior", "current", "change"):
        if item[f"{side}_manifest_sha256"] != manifests[f"{side}_hash"]:
            raise ValueError("admission review does not bind exact release and change manifests")
    refs = item["supporting_reviews"]
    if not isinstance(refs, list) or not refs or len(refs) != len(supporting):
        raise ValueError("admission needs retained supporting review bytes")
    seen = set()
    for ref, stored in zip(refs, supporting):
        _keys(ref, ("path", "sha256"), "supporting review reference")
        path = _text(ref["path"], "supporting review path")
        if path in seen:
            raise ValueError("duplicate supporting review")
        seen.add(path)
        if _hash(_unblob(stored)) != ref["sha256"]:
            raise ValueError("supporting review hash mismatch")
    return item


def _packet(payload: dict, clock: str, *, full: bool) -> dict:
    _keys(payload, ("kind", "rule_version", "packet_id", "inventories", "admission", "supporting_reviews"), "import event")
    inventories = _keys(payload["inventories"], ("prior", "current", "change"), "packet inventories")
    raw = {side: _files(inventory) for side, inventory in inventories.items()}
    if any("manifest.json" not in files for files in raw.values()):
        raise ValueError("bound inventory is missing manifest.json")
    manifests = {side: _strict_json(files["manifest.json"], "bound manifest") for side, files in raw.items()}
    if any(not isinstance(manifest, dict) for manifest in manifests.values()):
        raise ValueError("bound manifests must be objects")
    if any("recorded_at" not in manifests[side] for side in ("prior", "current")) or "bundle_id" not in manifests["change"]:
        raise ValueError("bound manifests are missing identity or clock fields")
    manifests.update({f"{side}_hash": _hash(files["manifest.json"]) for side, files in raw.items()})
    if payload["packet_id"] != manifests["change_hash"]:
        raise ValueError("packet ID does not match change manifest")
    if not isinstance(payload["supporting_reviews"], list):
        raise ValueError("supporting reviews must be an array")
    review = _admission(_unblob(payload["admission"]), payload["supporting_reviews"], manifests, clock)
    if full:
        with tempfile.TemporaryDirectory(prefix="atlas-alert-review-verify-") as temporary:
            root = Path(temporary).resolve()
            for side, files in raw.items():
                (root / side).mkdir()
                for name, content in files.items():
                    with (root / side / name).open("xb") as handle:
                        handle.write(content)
            validate_change_bundle(root / "change", root / "prior", root / "current")
    try:
        changes = [_strict_json(line, "bound change") for line in raw["change"]["changes.jsonl"].splitlines()]
        proposals = [_strict_json(line, "bound proposal") for line in raw["change"]["alert_proposals.jsonl"].splitlines()]
    except KeyError as error:
        raise ValueError("incomplete bound packet") from error
    return {"bundle_id": manifests["change"]["bundle_id"], "packet_id": payload["packet_id"],
            "current_recorded_at": manifests["current"]["recorded_at"], "review": review,
            "changes": changes, "proposals": proposals, "manifests": manifests}


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
            raise ValueError("not a supported AI-critical alert review queue")
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


def _events(connection: sqlite3.Connection) -> list[dict]:
    events = [{**dict(row), "payload": _strict_json(row["payload"].encode(), "alert event")}
              for row in connection.execute("SELECT * FROM alert_events ORDER BY sequence")]
    _validate_events(events)
    return events


def _validate_events(events: object) -> None:
    if not isinstance(events, list):
        raise ValueError("events must be an array")
    previous, prior_clock = None, None
    for sequence, event in enumerate(events, 1):
        _keys(event, ("sequence", "event_id", "previous_event_id", "recorded_at", "payload"), "alert event")
        clock = _instant(event["recorded_at"])
        if (type(event["sequence"]) is not int or event["sequence"] != sequence
                or event["previous_event_id"] != previous or prior_clock is not None and clock < prior_clock
                or event["event_id"] != _hash({key: value for key, value in event.items() if key != "event_id"})):
            raise ValueError("invalid alert event chain or chronology")
        previous, prior_clock = event["event_id"], clock


def _append(connection: sqlite3.Connection, events: list[dict], payload: dict, clock: str) -> dict:
    event = {"sequence": len(events) + 1, "previous_event_id": events[-1]["event_id"] if events else None,
             "recorded_at": clock, "payload": payload}
    event["event_id"] = _hash(event)
    _validate_events([*events, event])
    connection.execute("INSERT INTO alert_events VALUES (?, ?, ?, ?, ?)", (
        event["sequence"], event["event_id"], event["previous_event_id"], clock,
        _pretty_bytes(payload).decode()))
    return event


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


def _transition(proposal: dict, change: dict) -> str:
    sides = {}
    for side in ("prior", "current"):
        claim = change[f"{side}_claim"]
        sides[side] = None if claim is None else {
            "assertion": _assertion(claim), "evidence_links": claim["evidence_links"]}
    return _hash({"rule_id": proposal["rule_id"], "rule_version": proposal["rule_version"],
                  "series_id": proposal["series_id"], **sides})


def _bound_snapshots(packet: dict, proposal: dict) -> dict:
    snapshots = {}
    for side in ("prior", "current"):
        identifier = proposal[f"{side}_claim_id"]
        matches = [change[f"{side}_claim"] for change in packet["changes"]
                   if change[f"{side}_claim"] is not None
                   and change[f"{side}_claim"]["claim_id"] == identifier]
        if identifier is None:
            snapshot = None
            expected = None
        else:
            if len(matches) != 1:
                raise ValueError("proposal claim does not resolve uniquely in bound release")
            snapshot = matches[0]
            if (snapshot["subject_stable_key"] != proposal["subject_stable_key"]
                    or snapshot["subject_entity_id"] != proposal["subject_entity_id"]):
                raise ValueError("proposal claim has incompatible subject")
            expected = {"release_id": packet["manifests"][side]["release_id"],
                        "manifest_sha256": packet["manifests"][f"{side}_hash"],
                        "claim_id": identifier, "evidence_links": snapshot["evidence_links"]}
        if proposal["evidence_lineage"][side] != expected:
            raise ValueError("proposal evidence lineage does not match bound claim")
        snapshots[f"{side}_claim"] = snapshot
    return snapshots


def _decision(alert: dict, payload: dict, packets: dict) -> None:
    _keys(payload, ("kind", "rule_version", "alert_id", "action", "reviewer", "reason", "expected_event_id", "evidence_refs"), "review decision")
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
        _keys(ref, ("bundle_id", "side", "claim_id", "evidence_id", "fragment_sha256"), "decision evidence reference")
        if ref["side"] not in {"prior", "current"} or ref["bundle_id"] not in packets:
            raise ValueError("decision evidence is not admitted")
        if _hash(ref) in seen:
            raise ValueError("duplicate decision evidence reference")
        seen.add(_hash(ref))
        claims = [change[f"{ref['side']}_claim"] for change in packets[ref["bundle_id"]]["changes"]]
        match = next((claim for claim in claims if claim is not None and claim["claim_id"] == ref["claim_id"]), None)
        if (match is None or match["subject_stable_key"] != alert["subject_stable_key"]
                or not any(link["evidence_id"] == ref["evidence_id"] and link["fragment_sha256"] == ref["fragment_sha256"]
                           for link in match["evidence_links"])):
            raise ValueError("decision reference does not match bound facility evidence")


def _fold(events: list[dict], *, as_of: str | None = None, full: bool = False) -> dict:
    cutoff = _instant(as_of) if as_of is not None else None
    selected = [event for event in events if cutoff is None or _instant(event["recorded_at"]) <= cutoff]
    alerts, packets, latest = {}, {}, {}
    for event in selected:
        payload = event["payload"]
        if not isinstance(payload, dict) or payload.get("rule_version") != RULE_VERSION:
            raise ValueError("unsupported alert review rule version")
        if payload.get("kind") == "bundle_imported":
            packet = _packet(payload, event["recorded_at"], full=full)
            if packet["bundle_id"] in packets:
                raise ValueError("duplicate imported bundle")
            packets[packet["bundle_id"]] = packet
            changes = {change["series_id"]: change for change in packet["changes"]}
            proposal_episodes = {}
            for proposal in packet["proposals"]:
                change = _bound_snapshots(packet, proposal)
                signature = _transition(proposal, change)
                previous = latest.get(proposal["series_id"])
                existing = alerts.get(previous["episode_id"]) if previous else None
                prior_proves_return = (existing is not None and change["prior_claim"] is not None
                    and _instant(packet["manifests"]["prior"]["recorded_at"])
                        >= _instant(existing["observations"][0]["release_recorded_at"])
                    and _assertion(change["prior_claim"])
                        != _assertion(existing["observations"][0]["after"]))
                same_prior_backfill = (existing is not None
                    and proposal["evidence_lineage"]["prior"] is not None
                    and existing["observations"][0]["proposal"]["evidence_lineage"]["prior"] is not None
                    and proposal["evidence_lineage"]["prior"]["manifest_sha256"]
                        == existing["observations"][0]["proposal"]["evidence_lineage"]["prior"]["manifest_sha256"]
                    and _instant(packet["current_recorded_at"]) > _instant(existing["deduplication_floor"]))
                reusable = (existing is not None and existing["transition_signature"] == signature
                            and not prior_proves_return
                            and (_instant(packet["current_recorded_at"]) > _instant(previous["clock"])
                                 or same_prior_backfill))
                identifier = existing["id"] if reusable else _hash({"packet_id": packet["packet_id"], "fingerprint": proposal["fingerprint"]})
                if not reusable:
                    floor = packet["manifests"]["prior"]["recorded_at"]
                    if (previous and previous["assertion"] != _assertion(change["current_claim"])
                            and _instant(floor) < _instant(previous["clock"]) < _instant(packet["current_recorded_at"])):
                        floor = previous["clock"]
                    alerts[identifier] = {
                        "id": identifier, "transition_signature": signature, "series_id": proposal["series_id"],
                        "subject_stable_key": proposal["subject_stable_key"], "predicate": proposal["predicate"],
                        "rule_id": proposal["rule_id"], "rule_version": proposal["rule_version"],
                        "status": "pending", "first_recorded_at": event["recorded_at"],
                        "deduplication_floor": floor,
                        "last_event_id": event["event_id"], "confidence": None,
                        "confidence_scope": "unknown_not_calibrated", "delivery_eligible": False,
                        "severity": "unassessed", "observations": [], "decisions": [],
                    }
                alerts[identifier]["observations"].append({
                    "bundle_id": packet["bundle_id"], "packet_id": packet["packet_id"],
                    "recorded_at": event["recorded_at"], "release_recorded_at": packet["current_recorded_at"],
                    "event_id": event["event_id"], "proposal": deepcopy(proposal),
                    "before": deepcopy(change["prior_claim"]), "after": deepcopy(change["current_claim"]),
                })
                proposal_episodes[proposal["series_id"]] = identifier
            for series, change in changes.items():
                claim = change["current_claim"]
                if claim is None:
                    continue  # Omission never withdraws an assertion or an alert.
                previous = latest.get(series)
                if previous and _instant(packet["current_recorded_at"]) < _instant(previous["clock"]):
                    continue
                assertion = _assertion(claim)
                episode = proposal_episodes.get(series)
                if episode is None and previous and previous["assertion"] == assertion:
                    episode = previous["episode_id"]
                latest[series] = {"clock": packet["current_recorded_at"], "assertion": assertion, "episode_id": episode}
        elif payload.get("kind") == "decision":
            identifier = payload.get("alert_id")
            if identifier not in alerts:
                raise ValueError("decision names unknown alert")
            alert = alerts[identifier]
            _decision(alert, payload, packets)
            alert["status"] = ACTIONS[payload["action"]]
            alert["last_event_id"] = event["event_id"]
            alert["decisions"].append({**deepcopy(payload), "recorded_at": event["recorded_at"], "event_id": event["event_id"]})
        else:
            raise ValueError("unsupported alert event kind")
    rows = sorted(alerts.values(), key=lambda item: (item["first_recorded_at"], item["id"]))
    return {"format": "semiconductor-atlas-ai-critical-alert-review-report-v1", "rule_version": RULE_VERSION,
            "as_of": as_of, "event_count": len(selected), "packet_count": len(packets),
            "head_event_id": selected[-1]["event_id"] if selected else None,
            "alert_count": len(rows), "open_count": sum(row["status"] in {"pending", "acknowledged"} for row in rows),
            "delivery_eligible_count": 0, "alerts": rows,
            "packets": [{"bundle_id": packet["bundle_id"], "packet_id": packet["packet_id"],
                         "current_recorded_at": packet["current_recorded_at"], "review": packet["review"]}
                        for packet in packets.values()]}


def queue_report(path: str | Path, *, as_of: str | None = None) -> dict:
    with _connection(path) as connection:
        return _fold(_events(connection), as_of=as_of)


def import_bundle(path: str | Path, bundle_path: str | Path, prior_path: str | Path,
                  current_path: str | Path, admission_review_path: str | Path) -> dict:
    paths = {"prior": Path(prior_path), "current": Path(current_path), "change": Path(bundle_path)}
    if any(Path(path).absolute().is_relative_to(root.absolute()) for root in paths.values()):
        raise ValueError("alert queue must be outside bound release directories")
    review_path = Path(admission_review_path).absolute()
    review_raw = _read(review_path)
    review = _admission_fields(review_raw)
    supporting = []
    for ref in review.get("supporting_reviews", []):
        target = Path(ref["path"])
        supporting.append(_blob(_read(target if target.is_absolute() else review_path.parent / target)))
    inventories = {side: _directory(root) for side, root in paths.items()}
    payload = {"kind": "bundle_imported", "rule_version": RULE_VERSION,
               "packet_id": inventories["change"]["manifest.json"]["sha256"],
               "inventories": inventories, "admission": _blob(review_raw), "supporting_reviews": supporting}
    clock = _now()
    packet = _packet(payload, clock, full=True)
    with _connection(path, write=True) as connection:
        events = _events(connection)
        before = _fold(events)
        if any(item["packet_id"] == packet["packet_id"] for item in before["packets"]):
            return {"imported": False, "packet_id": packet["packet_id"], **before}
        # The admitted bytes are the private, fully validated snapshot, not a later path read.
        clock = _now()
        _admission(review_raw, supporting, packet["manifests"], clock)
        event = _append(connection, events, payload, clock)
        report = _fold([*events, event])
    return {"imported": True, "packet_id": packet["packet_id"], **report}


def record_decision(path: str | Path, alert_id: str, *, action: str, reviewer: str,
                    reason: str, expected_event_id: str, evidence_refs=()) -> dict:
    payload = {"kind": "decision", "rule_version": RULE_VERSION, "alert_id": alert_id,
               "action": action, "reviewer": reviewer, "reason": reason,
               "expected_event_id": expected_event_id, "evidence_refs": list(evidence_refs)}
    with _connection(path, write=True) as connection:
        events = _events(connection)
        event = _append(connection, events, payload, _now())
        report = _fold([*events, event])
    return next(alert for alert in report["alerts"] if alert["id"] == alert_id)


def export_queue(path: str | Path, *, as_of: str | None = None) -> dict:
    with _connection(path) as connection:
        events = _events(connection)
        _fold(events, full=True)
        if as_of is not None:
            events = [event for event in events if _instant(event["recorded_at"]) <= _instant(as_of)]
        return {"format": EXPORT_FORMAT, "events": events}


def restore_queue(path: str | Path, export_path: str | Path) -> dict:
    artifact = _keys(_strict_json(_read(Path(export_path)), "alert export"), ("format", "events"), "alert export")
    if artifact["format"] != EXPORT_FORMAT:
        raise ValueError("unsupported alert export format")
    events = artifact["events"]
    _validate_events(events)
    report = _fold(events, full=True)
    initialize_queue(path)
    with _connection(path, write=True) as connection:
        for event in events:
            connection.execute("INSERT INTO alert_events VALUES (?, ?, ?, ?, ?)", (
                event["sequence"], event["event_id"], event["previous_event_id"], event["recorded_at"],
                _pretty_bytes(event["payload"]).decode()))
        if _events(connection) != events:
            raise ValueError("restored alert events differ")
    return report


def verify_queue(path: str | Path, *, as_of: str | None = None) -> dict:
    with _connection(path) as connection:
        report = _fold(_events(connection), as_of=as_of, full=True)
    return {**report, "verified_packet_count": report["packet_count"]}
