"""Freeze the accepted-core comparison denominator, independently of queue success.

This census covers one supported importer route in one coherent core snapshot.
It cannot establish complete publisher coverage or authenticate a portable history.
"""

from __future__ import annotations

import copy
import sqlite3
import tempfile
from pathlib import Path

from . import alert_review_v2 as ledger, ai_critical_alert_review as legacy
from . import database, project_target_changes as producer, project_target_review as acceptance, repository
from .ai_critical_changes import _pretty_bytes, _strict_json
from .curated_capture import _write
from .discovery_handoff import _binding, _digest, _keys
from .source_checks import _read


STUDY_FORMAT = "semiconductor-atlas-project-target-study-v1"
FROZEN_FORMAT = "semiconductor-atlas-project-target-population-v1"
RULE_VERSION = "accepted-project-comparison-population-v1"
SELECTION = "all_accepted_project_target_reviews_before_end"
DESIGN = "retrospective_diagnostic_not_preregistered"
MAX_BYTES = 20_000_000
BOUNDARIES = dict.fromkeys(("publisher_complete", "independence_verified", "blind_evaluation_passed",
                           "production_outcome_verified", "delivery_eligible", "calibrated_forecast"), False)
_now, _instant, _hash = legacy._now, legacy._instant, legacy._hash


def _bounded(path: Path) -> bytes:
    raw = _read(path)
    if len(raw) > MAX_BYTES:
        raise ValueError("population input exceeds the bounded 20 MB artifact limit")
    return raw


def _code_hashes() -> dict:
    paths = acceptance._code_files() | {Path(__file__), Path(producer.__file__), Path(ledger.__file__),
                                       Path(legacy.__file__), Path(__file__).with_name("project_target_evaluation.py")}
    root = Path(__file__).parent.parent
    return {path.relative_to(root).as_posix(): _hash(_read(path)) for path in sorted(paths)}


def _study(raw: bytes) -> dict:
    item = _keys(_strict_json(raw, "project population study"),
        {"format", "study_id", "start", "end", "selection", "reviews"}, "population study")
    if item["format"] != STUDY_FORMAT or item["selection"] != SELECTION:
        raise ValueError("unsupported accepted-project census selection")
    legacy._text(item["study_id"], "study id")
    if not _instant(item["start"]) < _instant(item["end"]):
        raise ValueError("population window must be nonempty and half-open")
    if not isinstance(item["reviews"], list):
        raise ValueError("study reviews must be an array")
    seen, hashes = set(), set()
    for ref in item["reviews"]:
        _keys(ref, {"run_id", "path", "sha256"}, "accepted review reference")
        relative = Path(legacy._text(ref["path"], "review path"))
        if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != ref["path"]:
            raise ValueError("review references must be canonical repository-relative paths")
        sha = _digest(ref["sha256"])
        expected = repository.stable_id(acceptance.RULE_VERSION, sha)
        if ref["run_id"] != expected or expected in seen or sha in hashes:
            raise ValueError("duplicate or mismatched accepted review identity")
        seen.add(expected); hashes.add(sha)
    return item


def _inventory(connection: sqlite3.Connection) -> list[dict]:
    method_runs = {row[0] for row in connection.execute(
        "SELECT DISTINCT created_by_run_id FROM claim_versions WHERE method=?", (acceptance.RULE_VERSION,))}
    code = _hash(_read(Path(acceptance.__file__)))
    result = []
    for run in connection.execute("""
            SELECT r.*, f.stable_key AS source_family
            FROM ingestion_runs r JOIN sources s ON s.id=r.source_id
            JOIN source_families f ON f.id=s.family_id ORDER BY r.id
            """):
        params = _strict_json(run["parameters_json"].encode(), "core ingestion parameters")
        if not isinstance(params, dict):
            raise ValueError("core ingestion parameters must be objects")
        markers = {
            "parameters_rule": params.get("rule_version") == acceptance.RULE_VERSION,
            "claim_method": run["id"] in method_runs,
            "importer_code": run["code_version"] == code,
            "source_family": run["source_family"] == "reviewed-source-project-targets",
        }
        route = any(markers.values())
        admitted, entity_id, stable_key = None, None, None
        if route:
            if not all(markers.values()) or run["status"] != "succeeded":
                raise ValueError("project route markers or accepted run status disagree")
            admitted = params.get("accepted_at")
            if (admitted != run["completed_at"] or params.get("acceptance_timestamp_basis") != "actual_database_admission"
                    or not _instant(run["started_at"]) < _instant(admitted)):
                raise ValueError("project census requires an exact actual core admission clock")
            ids = acceptance._ids({"review": params["review"], "review_sha256": params["review_sha256"]})
            if ids["run_id"] != run["id"]:
                raise ValueError("project review digest does not identify its core admission")
            subjects = [dict(row) for row in connection.execute("""
                SELECT DISTINCT e.id, e.kind, e.stable_key FROM claim_versions c
                JOIN claim_series s ON s.id=c.series_id JOIN entities e ON e.id=s.subject_entity_id
                WHERE c.created_by_run_id=?
                """, (run["id"],))]
            if subjects != [{"id": ids["entity_id"], "kind": "project", "stable_key": ids["project_key"]}]:
                raise ValueError("core run does not own exactly its reviewed source-native project")
            entity_id, stable_key = ids["entity_id"], ids["project_key"]
        result.append({"run_id": run["id"], "source_id": run["source_id"], "status": run["status"],
            "code_version": run["code_version"], "parameters_sha256": _hash(run["parameters_json"].encode()),
            "markers": markers, "project_route": route, "accepted_at": admitted,
            "entity_id": entity_id, "stable_key": stable_key})
    if method_runs - {row["run_id"] for row in result}:
        raise ValueError("project claims have an unaccounted ingestion owner")
    if len(result) != connection.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[0]:
        raise ValueError("ingestion inventory lost a source or source-family join")
    return result


def _packet_content(packet: dict) -> dict:
    return {key: value for key, value in packet.items() if key not in {"generated_at", "packet_id"}}


def _views(study: dict, inventory: list[dict], packets: list[dict], history: dict, frozen_at: str) -> dict:
    if history.get("format") != ledger.EXPORT_FORMAT:
        raise ValueError("project population requires the unified version-2 alert history")
    events = history.get("events")
    ledger._fold(events)
    if any(_instant(row["recorded_at"]) > _instant(frozen_at) for row in events):
        raise ValueError("alert history contains knowledge after freeze")
    selected_events = [row for row in events if _instant(row["recorded_at"]) < _instant(study["end"])]
    report = ledger._fold(selected_events)
    packet_map = {row["comparison_id"]: row for row in packets}
    expected = {row["run_id"] for row in inventory if row["project_route"]
                and _instant(row["accepted_at"]) < _instant(study["end"])}
    if len(packet_map) != len(packets) or set(packet_map) != expected:
        raise ValueError("packet set does not equal all accepted project reviews before end")
    references = {ref["run_id"]: ref for ref in study["reviews"]}
    if set(references) != expected:
        raise ValueError("study review manifest must exactly cover the accepted core census")
    for packet in packets:
        producer.validate_packet(packet, clock=frozen_at)
        row = next(row for row in inventory if row["run_id"] == packet["comparison_id"])
        if (packet["accepted_at"] != row["accepted_at"] or packet["subject"]["entity_id"] != row["entity_id"]
                or packet["subject"]["stable_key"] != row["stable_key"]
                or packet["provenance"]["review"]["sha256"] != references[row["run_id"]]["sha256"]
                or packet["provenance"]["source"]["id"] != row["source_id"]
                or packet["provenance"]["acceptance"]["code_sha256"] != row["code_version"]):
            raise ValueError("packet scope, clock or review differs from the core census")
    admitted = {}
    for event in selected_events:
        if event["payload"].get("kind") != "project_packet_imported":
            continue
        bound = ledger._project_packet(event["payload"], event["recorded_at"])
        packet = bound["packet"]
        core_packet = packet_map.get(packet["comparison_id"])
        if core_packet is None or _packet_content(core_packet) != _packet_content(packet):
            raise ValueError("queue comparison is absent from or differs from the accepted core census")
        admitted[packet["comparison_id"]] = event["recorded_at"]
    opportunities, excluded = [], []
    for packet in packets:
        identifier = packet["comparison_id"]
        group = _hash({"series_id": packet["series_id"], "documents": {
            side: {"body_sha256": packet["claims"][side]["document"]["content_sha256"],
                   "value": packet["claims"][side]["value"]} for side in ("before", "after")}})
        row = {"comparison_id": identifier, "entity_id": packet["subject"]["entity_id"],
            "accepted_at": packet["accepted_at"], "classification": packet["change"]["classification"],
            "expected_proposal": bool(packet["proposals"]), "transition_group_id": group,
            "queue_admitted_at": admitted.get(identifier), "queue_admitted_before_end": identifier in admitted}
        if _instant(packet["accepted_at"]) >= _instant(study["start"]):
            opportunities.append(row)
        else:
            excluded.append({**row, "reason": "accepted_before_window"})
    predictions, excluded_alerts = [], []
    for alert in report["alerts"]:
        if alert["origin"] != "source_native_project":
            excluded_alerts.append({"alert_id": alert["id"], "reason": "baseline_facility_not_project"})
        elif _instant(alert["first_recorded_at"]) < _instant(study["start"]):
            excluded_alerts.append({"alert_id": alert["id"], "reason": "alert_admitted_before_window"})
        else:
            predictions.append(alert)
    cohort = {packet["subject"]["entity_id"]: {key: packet["subject"][key]
              for key in ("entity_id", "stable_key", "display_name")} for packet in packets}
    return {"cohort": sorted(cohort.values(), key=lambda row: row["entity_id"]),
        "opportunities": opportunities, "excluded_comparisons": excluded,
        "predictions": predictions, "excluded_alerts": excluded_alerts,
        "history_head_at_cutoff": report["head_event_id"]}


def _validate_frozen(artifact: object) -> dict:
    row = _keys(artifact, {"format", "rule_version", "study_id", "start", "end", "selection", "design",
        "snapshot_started_at", "frozen_at", "study", "history", "core_inventory", "core_inventory_sha256",
        "packets", "cohort", "opportunities", "excluded_comparisons", "predictions", "excluded_alerts",
        "history_head_at_cutoff", "code_sha256", "boundaries", "validation_scope"}, "frozen project population")
    if (row["format"] != FROZEN_FORMAT or row["rule_version"] != RULE_VERSION or row["design"] != DESIGN
            or row["selection"] != SELECTION or row["validation_scope"] != "retained_consistency_not_independent_core_or_source_authentication"
            or row["boundaries"] != BOUNDARIES or any(value is not False for value in row["boundaries"].values())):
        raise ValueError("unsupported project population scope or performance boundary")
    study = _study(legacy._unblob(row["study"]))
    if any(row[key] != study[key] for key in ("study_id", "start", "end", "selection")):
        raise ValueError("frozen selection differs from exact study bytes")
    if not _instant(row["end"]) <= _instant(row["snapshot_started_at"]) <= _instant(row["frozen_at"]) <= _instant(_now()):
        raise ValueError("population needs a completed window and an actual final freeze clock")
    if row["code_sha256"] != _code_hashes():
        raise ValueError("population requires its pinned census, evaluator and source/review versions")
    inventory = row["core_inventory"]
    if not isinstance(inventory, list) or _hash(inventory) != _digest(row["core_inventory_sha256"]):
        raise ValueError("core ingestion inventory digest differs")
    seen = set()
    for item in inventory:
        _keys(item, {"run_id", "source_id", "status", "code_version", "parameters_sha256", "markers",
                    "project_route", "accepted_at", "entity_id", "stable_key"}, "core inventory row")
        legacy._text(item["run_id"], "run id")
        if item["run_id"] in seen:
            raise ValueError("duplicate core ingestion inventory entry")
        seen.add(item["run_id"])
        _digest(item["parameters_sha256"])
        markers = _keys(item["markers"], {"parameters_rule", "claim_method", "importer_code", "source_family"}, "route markers")
        if any(type(value) is not bool for value in markers.values()) or type(item["project_route"]) is not bool:
            raise ValueError("route markers must be booleans")
        if item["project_route"] != any(markers.values()) or item["project_route"] and not all(markers.values()):
            raise ValueError("inconsistent retained route markers")
        if markers["importer_code"] != (item["code_version"] == row["code_sha256"]["semiconductor_atlas/project_target_review.py"]):
            raise ValueError("retained importer code contradicts the route marker")
        if item["project_route"]:
            if item["status"] != "succeeded" or _instant(item["accepted_at"]) > _instant(row["frozen_at"]):
                raise ValueError("invalid accepted project run in inventory")
            legacy._text(item["entity_id"], "project id")
            legacy._text(item["stable_key"], "project stable key")
        elif any(item[key] is not None for key in ("accepted_at", "entity_id", "stable_key")):
            raise ValueError("other importer routes do not define this project population")
    if not isinstance(row["packets"], list):
        raise ValueError("population packets must be an array")
    if (inventory != sorted(inventory, key=lambda item: item["run_id"])
            or row["packets"] != sorted(row["packets"], key=lambda item: item["comparison_id"])):
        raise ValueError("population inventory and packets must retain canonical run ordering")
    history = _strict_json(legacy._unblob(row["history"]), "unified alert history")
    views = _views(study, inventory, row["packets"], history, row["frozen_at"])
    if any(row[key] != value for key, value in views.items()):
        raise ValueError("frozen opportunities or predictions do not replay from retained inputs")
    return copy.deepcopy(row)


def validate_population(path: str | Path) -> tuple[dict, bytes]:
    raw = _bounded(Path(path))
    try:
        artifact = _validate_frozen(_strict_json(raw, "frozen project population"))
    except (KeyError, TypeError, IndexError, AttributeError) as error:
        raise ValueError("malformed frozen project population") from error
    if raw != _pretty_bytes(artifact):
        raise ValueError("frozen population must retain its exact canonical bytes")
    return artifact, raw


def freeze_population(connection: sqlite3.Connection, study_path: str | Path, history_path: str | Path,
                      *, reference_root: str | Path, source_queue: str | Path) -> dict:
    if connection.in_transaction:
        raise ValueError("population requires ownership of a coherent read-only core snapshot")
    study_raw, history_raw = _bounded(Path(study_path)), _bounded(Path(history_path))
    study = _study(study_raw)
    snapshot_started_at = _now()
    if _instant(study["end"]) > _instant(snapshot_started_at):
        raise ValueError("population window must be completed before the snapshot begins")
    codes = _code_hashes()
    snapshot = sqlite3.connect(":memory:")
    snapshot.row_factory = sqlite3.Row
    try:
        connection.backup(snapshot)
        if database.schema_version(snapshot) != 5:
            raise ValueError("project population supports the existing schema-5 core contract only")
        errors = repository.validate_database(snapshot)
        if errors:
            raise ValueError("invalid core snapshot: " + "; ".join(errors))
        inventory = _inventory(snapshot)
        selected = {row["run_id"] for row in inventory if row["project_route"]
                    and _instant(row["accepted_at"]) < _instant(study["end"])}
        if selected != {ref["run_id"] for ref in study["reviews"]}:
            raise ValueError("study review manifest must exactly cover all accepted project reviews before end")
        packets, bindings = [], []
        for ref in sorted(study["reviews"], key=lambda row: row["run_id"]):
            path, raw = _binding(Path(reference_root), {key: ref[key] for key in ("path", "sha256")})
            bindings.append((path, raw))
            packets.append(producer.build_packet(snapshot, path, reference_root=reference_root, source_queue=source_queue))
        if (_bounded(Path(study_path)) != study_raw or _bounded(Path(history_path)) != history_raw
                or any(_bounded(path) != raw for path, raw in bindings) or _code_hashes() != codes):
            raise ValueError("population inputs or implementation changed across the freeze boundary")
        history = _strict_json(history_raw, "unified alert history")
        checked_at = _now()
        views = _views(study, inventory, packets, history, checked_at)
        artifact = {"format": FROZEN_FORMAT, "rule_version": RULE_VERSION,
            **{key: study[key] for key in ("study_id", "start", "end", "selection")}, "design": DESIGN,
            "snapshot_started_at": snapshot_started_at, "frozen_at": _now(),
            "study": legacy._blob(study_raw), "history": legacy._blob(history_raw),
            "core_inventory": inventory, "core_inventory_sha256": _hash(inventory), "packets": packets,
            **views, "code_sha256": codes, "boundaries": copy.deepcopy(BOUNDARIES),
            "validation_scope": "retained_consistency_not_independent_core_or_source_authentication"}
        if len(_pretty_bytes(artifact)) > MAX_BYTES:
            raise ValueError("complete population exceeds artifact limit; never truncate the denominator")
        result = _validate_frozen(artifact)
        if (_bounded(Path(study_path)) != study_raw or _bounded(Path(history_path)) != history_raw
                or any(_bounded(path) != raw for path, raw in bindings) or _code_hashes() != codes):
            raise ValueError("population inputs or implementation changed before final seal")
        return result
    except (KeyError, TypeError, IndexError, AttributeError) as error:
        raise ValueError("malformed project population inputs") from error
    finally:
        snapshot.close()


def verify_population_sources(connection: sqlite3.Connection, frozen_path: str | Path,
                              *, reference_root: str | Path, source_queue: str | Path) -> dict:
    """Replay original acceptance externally; new build clocks are not old detections."""
    frozen, raw = validate_population(frozen_path)
    with tempfile.TemporaryDirectory(prefix="atlas-project-population-replay-") as temporary:
        root = Path(temporary).resolve()
        _write(root / "study.json", legacy._unblob(frozen["study"]))
        _write(root / "history.json", legacy._unblob(frozen["history"]))
        rebuilt = freeze_population(connection, root / "study.json", root / "history.json",
                                    reference_root=reference_root, source_queue=source_queue)
    original = {row["run_id"]: row for row in frozen["core_inventory"]}
    current = {row["run_id"]: row for row in rebuilt["core_inventory"]}
    if any(current.get(key) != value for key, value in original.items()):
        raise ValueError("original core ingestion inventory has changed or disappeared")
    if ({row["comparison_id"]: _packet_content(row) for row in frozen["packets"]}
            != {row["comparison_id"]: _packet_content(row) for row in rebuilt["packets"]}):
        raise ValueError("accepted pre-cutoff comparison population no longer matches")
    for field in ("cohort", "opportunities", "excluded_comparisons", "predictions", "excluded_alerts", "history_head_at_cutoff"):
        if frozen[field] != rebuilt[field]:
            raise ValueError("historical population projection differs from external replay")
    if _bounded(Path(frozen_path)) != raw:
        raise ValueError("frozen population changed during external source replay")
    return {"format": "semiconductor-atlas-project-target-population-source-verification-v1",
        "frozen_sha256": _hash(raw), "original_ingestion_rows_verified": len(original),
        "accepted_comparisons_replayed": len(frozen["packets"]),
        "opportunities_replayed": len(frozen["opportunities"]), "source_acceptance_replayed": True,
        "retained_cutoff_projection_matches": True, "independence_verified": False,
        "publisher_complete": False, "delivery_eligible": False}
