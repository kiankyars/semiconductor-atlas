"""One recoverable, cadence-guarded publisher discovery invocation."""

from __future__ import annotations

import re
import uuid
from datetime import timedelta
from pathlib import Path

from .ai_critical import ensure_real_directory
from .ai_critical_changes import _pretty_bytes, _strict_json
from .curated_capture import _instant, _keys, _now, _sha, _write, curl_fetch
from .curated_coverage import _bound_file
from .curated_poll import _lock, _path
from .discovery_review import OPEN_STATUSES, export_events, import_capture, verify_queue
from .nist_discovery import capture_indexes, load_plan, validate_capture
from .source_checks import _read


FORMAT = "semiconductor-atlas-discovery-poll-config-v1"


def _json(path: Path) -> dict:
    return _strict_json(_read(path), str(path))


def load_config(path: str | Path, repository_root: str | Path | None = None) -> dict:
    path = Path(path).absolute()
    root = Path(repository_root).absolute() if repository_root is not None else path.parent.parent
    raw = _read(path)
    config = _keys(_strict_json(raw, "discovery poll config"), {
        "format", "recorded_at", "plan", "queue_path", "capture_root", "state_root", "interval_seconds", "notes"})
    if config["format"] != FORMAT or not isinstance(config["notes"], str) or not config["notes"].strip():
        raise ValueError("invalid discovery poll config")
    if type(config["interval_seconds"]) is not int or not 3600 <= config["interval_seconds"] <= 31_536_000:
        raise ValueError("discovery poll interval must be between one hour and one year")
    plan_path, plan_raw = _bound_file(root, config["plan"])
    plan, _ = load_plan(plan_path, repository_root=root)
    if _instant(config["recorded_at"]) < _instant(plan["reviewed_at"]):
        raise ValueError("discovery poll config predates review")
    paths = {key: _path(root, config[key]) for key in ("queue_path", "capture_root", "state_root")}
    protected = [path.parent, plan_path.parent, paths["queue_path"],
                 root / plan["review_record"]["path"], root / plan["baseline"]["path"]]
    for output in (paths["capture_root"], paths["state_root"]):
        if any(output.resolve().is_relative_to(item.resolve()) or item.resolve().is_relative_to(output.resolve()) for item in protected):
            raise ValueError("discovery poll roots overlap protected inputs")
    if (paths["capture_root"].resolve().is_relative_to(paths["state_root"].resolve())
            or paths["state_root"].resolve().is_relative_to(paths["capture_root"].resolve())):
        raise ValueError("discovery poll state and capture roots must be separate")
    return {"config": config, "config_sha256": _sha(raw), "root": root, "plan_path": plan_path,
            "plan": plan, "plan_raw": plan_raw, **paths}


def _history(state: Path, captures: Path) -> tuple[list[dict], list[dict]]:
    history, previous = [], []
    for tick in sorted(state.iterdir()):
        if tick.is_symlink() or not tick.is_dir() or not re.fullmatch(r"[0-9a-f]{32}", tick.name):
            raise ValueError("unexpected discovery poll state entry")
        if (tick / "finished.json").exists():
            previous.append(_json(tick / "finished.json"))
        if not (tick / "intent.json").exists():
            continue
        intent = _keys(_json(tick / "intent.json"), {"capture_name", "checked_source_id", "plan_sha256", "started_at"})
        if intent["capture_name"] != tick.name or intent["checked_source_id"] != "nist:chips-public-indexes":
            raise ValueError("discovery intent identity mismatch")
        if not isinstance(intent["plan_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", intent["plan_sha256"]):
            raise ValueError("invalid discovery intent plan hash")
        _instant(intent["started_at"])
        history.append({"tick": tick, "intent": intent, "capture": captures / tick.name})
    previous.sort(key=lambda row: _instant(row["finished_at"]))
    return history, previous


def _reconcile(history: list[dict], queue: Path) -> tuple[list[dict], bool]:
    recovered, blocked = [], False
    for row in history:
        packet, intent = row["capture"], row["intent"]
        outcome = row["tick"] / "outcome.json"
        if not (packet / "manifest.json").exists():
            if not outcome.exists():
                _write(outcome, _pretty_bytes({"status": "interrupted_capture", "observed_at": _now()}))
            continue
        try:
            result = validate_capture(packet)
            run = _json(packet / "run.json")
            if (run["plan_sha256"] != intent["plan_sha256"] or result["checked_source_id"] != intent["checked_source_id"]
                    or _instant(run["started_at"]) < _instant(intent["started_at"])):
                raise ValueError("discovery packet differs from durable intent")
            admitted = import_capture(queue, packet)
            if admitted["imported"] or not outcome.exists():
                recovered.append({"capture_name": intent["capture_name"], "status": "recovered_import",
                                  "new_queue_import": admitted["imported"], "run_id": admitted["run_id"]})
            if not outcome.exists():
                _write(outcome, _pretty_bytes({"status": "recovered_import", "run_id": admitted["run_id"], "observed_at": _now()}))
        except (OSError, ValueError) as error:
            blocked = True
            recovered.append({"capture_name": intent["capture_name"], "status": "recovery_blocked", "error": str(error)})
    return recovered, blocked


def _health(queue: dict, plan: dict, now: str, interval: int) -> dict:
    latest = [row for row in queue["runs"] if row["run_id"] in queue["latest_run_ids"]]
    if not latest:
        freshness = "never_observed"
    else:
        age = (_instant(now) - _instant(latest[0]["finished_at"])).total_seconds()
        freshness = "stale" if age >= interval * 2 else "recent"
    return {"review_state": "expired" if _instant(now) >= _instant(plan["expires_at"]) else "current",
            "capture_freshness": freshness, "capture_attention_required": queue["latest_capture_attention_required"],
            "index_statuses": sorted({(index["kind"], index["status"]) for run in latest for index in run["result"]["indexes"]})}


def _signal(queue: dict, health: dict, problems: list[str], plan_hash: str) -> str:
    return _sha(_pretty_bytes({"plan_sha256": plan_hash, "health": health, "problems": sorted(set(problems)),
        "review": [{key: row[key] for key in ("id", "status", "requires_reopen", "review_fingerprint")}
                   for row in queue["candidates"] if row["matched_companies"]
                   and (row["status"] in OPEN_STATUSES or row["requires_reopen"])]}))


def poll_once(config_path: str | Path, *, repository_root: str | Path | None = None,
              force: bool = False, transport=curl_fetch) -> dict:
    bound = load_config(config_path, repository_root)
    config, plan, queue_path = bound["config"], bound["plan"], bound["queue_path"]
    plan_hash = _sha(bound["plan_raw"])
    before = verify_queue(queue_path)
    for run in before["runs"]:
        if any(output.resolve().is_relative_to(Path(run["source_path"]).resolve()) for output in (bound["state_root"], bound["capture_root"])):
            raise ValueError("discovery poll output is inside an immutable packet")
    with _lock(queue_path):
        started = _now()
        if _instant(started) < _instant(config["recorded_at"]):
            raise ValueError("discovery poll config is not yet effective")
        events = export_events(queue_path)["events"]
        if events and _instant(events[-1]["recorded_at"]) > _instant(started):
            raise ValueError("discovery poll predates queue knowledge")
        before = verify_queue(queue_path, as_of=started)
        initial_signal = _signal(before, _health(before, plan, started, config["interval_seconds"]), [], plan_hash)
        state = ensure_real_directory(bound["state_root"], "discovery poll state")
        captures = ensure_real_directory(bound["capture_root"], "discovery poll captures")
        history, previous = _history(state, captures)
        clocks = [row["finished_at"] for row in previous] + [row["intent"]["started_at"] for row in history]
        if any(_instant(clock) > _instant(started) for clock in clocks):
            raise ValueError("discovery poll clock predates invocation history")
        tick = state / uuid.uuid4().hex
        tick.mkdir(mode=0o700)
        _write(tick / "request.json", _pretty_bytes({"started_at": started, "config_sha256": bound["config_sha256"],
               "plan_sha256": plan_hash, "forced": force, "queue_head_before": before["head_event_id"],
               "runner_sha256": _sha(_read(Path(__file__)))}))
        recovered, blocked = _reconcile(history, queue_path)
        queue = verify_queue(queue_path)
        clocks = [row["finished_at"] for row in queue["runs"]] + [row["intent"]["started_at"] for row in history]
        latest = max(clocks, key=_instant) if clocks else None
        due = _instant(latest) + timedelta(seconds=config["interval_seconds"]) if latest else None
        now = _now()
        incomplete = [row for row in history if not (row["capture"] / "manifest.json").exists()
                      and not any(_instant(run["finished_at"]) >= _instant(row["intent"]["started_at"]) for run in queue["runs"])]
        problems = ["recovery_blocked"] if blocked else (["capture_failed"] if incomplete else [])
        if blocked:
            outcome = {"status": "recovery_blocked"}
        elif _instant(now) >= _instant(plan["expires_at"]):
            outcome = {"status": "review_expired"}
        elif due is not None and _instant(now) < due and not force:
            outcome = {"status": "not_due", "unresolved_interrupted_capture": bool(incomplete)}
        else:
            inputs = tick / "inputs"
            private_plan = inputs / "plans" / "discovery.json"
            private_plan.parent.mkdir(parents=True)
            _write(private_plan, bound["plan_raw"])
            for field in ("review_record", "baseline"):
                _, raw = _bound_file(bound["root"], plan[field])
                destination = inputs / plan[field]["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                _write(destination, raw)
            intent = {"capture_name": tick.name, "checked_source_id": plan["checked_source_id"],
                      "plan_sha256": plan_hash, "started_at": _now()}
            _write(tick / "intent.json", _pretty_bytes(intent))
            stage = "capture"
            try:
                captured = capture_indexes(private_plan, captures / tick.name, repository_root=inputs, transport=transport)
                stage = "queue_import"
                admitted = import_capture(queue_path, captures / tick.name)
                outcome = {"status": "captured_imported", "run_id": admitted["run_id"],
                           "capture_attention_required": captured["attention_required"]}
                problems = []
            except (OSError, ValueError) as error:
                outcome = {"status": f"{stage}_failed", "error": str(error)}
                problems = ["capture_failed" if stage == "capture" else "queue_import_failed"]
            _write(tick / "outcome.json", _pretty_bytes({**outcome, "observed_at": _now()}))
            outcome["capture_name"] = tick.name
        finished = _now()
        queue = verify_queue(queue_path, as_of=finished)
        health = _health(queue, plan, finished, config["interval_seconds"])
        signal = _signal(queue, health, problems, plan_hash)
        due_clocks = [row["finished_at"] for row in queue["runs"]] + [row["intent"]["started_at"] for row in history]
        if (tick / "intent.json").exists():
            due_clocks.append(_json(tick / "intent.json")["started_at"])
        due = _instant(max(due_clocks, key=_instant)) + timedelta(seconds=config["interval_seconds"]) if due_clocks else None
        report = {"format": "semiconductor-atlas-discovery-poll-result-v1", "invocation_path": str(tick),
                  "started_at": started, "finished_at": finished, "result": outcome, "recovery": recovered,
                  "due_at": due.isoformat().replace("+00:00", "Z") if due else None, "health": health,
                  "signal_sha256": signal, "reportable_change": signal != (previous[-1]["signal_sha256"] if previous else initial_signal)
                      or any(row["status"] == "recovered_import" for row in recovered)
                      or (not previous and (bool(problems) or health["review_state"] == "expired"
                                            or health["capture_attention_required"] or health["capture_freshness"] != "recent")),
                  "attention_required": bool(problems) or health["review_state"] == "expired" or health["capture_freshness"] != "recent"
                                        or health["capture_attention_required"] or bool(queue["pending_count"] or queue["recheck_count"]),
                  "queue_summary": {key: queue[key] for key in ("head_event_id", "run_count", "candidate_count", "pending_count", "unrouted_pending_count", "recheck_count")},
                  "continuous_operation_proven": False, "claim_acceptance": False, "delivery_eligible": False,
                  "linked_document_acquisition_allowed": False, "absence_inference_allowed": False}
        _write(tick / "queue-report.json", _pretty_bytes(queue))
        _write(tick / "queue-events.json", _pretty_bytes(export_events(queue_path, as_of=finished)))
        _write(tick / "finished.json", _pretty_bytes(report))
        return report
