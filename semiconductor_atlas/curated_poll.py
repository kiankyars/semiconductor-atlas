"""One recoverable polling invocation; the app owns the recurring schedule."""

from __future__ import annotations

import fcntl
import os
import re
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path, PurePosixPath

from .ai_critical import ensure_real_directory
from .ai_critical_changes import _pretty_bytes, _strict_json
from .curated_capture import _content_error, _instant, _keys, _now, _sha, _write, capture_sources, curl_fetch, load_plan, validate_capture
from .curated_coverage import _bound_file, coverage_report, load_catalog
from .curated_review import ELIGIBLE, OPEN_STATUSES, export_events, import_capture, verify_queue
from .source_checks import _read, validate_source_checks


FORMAT = "semiconductor-atlas-curated-poll-config-v1"


def _path(root: Path, name: str) -> Path:
    if not isinstance(name, str):
        raise ValueError("poll paths must be repository-relative")
    relative = PurePosixPath(name)
    if not relative.parts or relative.is_absolute() or ".." in relative.parts or str(relative) != name:
        raise ValueError("poll paths must be canonical repository-relative paths")
    return root.joinpath(*relative.parts)


def load_config(path: str | Path, repository_root: str | Path | None = None) -> dict:
    path = Path(path).absolute()
    root = Path(repository_root).absolute() if repository_root is not None else path.parent.parent
    raw = _read(path)
    config = _keys(_strict_json(raw, "poll config"), {
        "format", "recorded_at", "catalog", "queue_path", "capture_root", "state_root", "interval_seconds", "notes",
    })
    if config["format"] != FORMAT or not isinstance(config["notes"], str) or not config["notes"].strip():
        raise ValueError("invalid poll configuration")
    if type(config["interval_seconds"]) is not int or not 3600 <= config["interval_seconds"] <= 31_536_000:
        raise ValueError("poll interval must be between one hour and one year")
    catalog_path, _ = _bound_file(root, config["catalog"])
    catalog = load_catalog(catalog_path, repository_root=root)
    if _instant(config["recorded_at"]) < _instant(catalog["catalog"]["recorded_at"]):
        raise ValueError("poll config predates its catalog")
    paths = {key: _path(root, config[key]) for key in ("queue_path", "capture_root", "state_root")}
    protected = [path.parent, catalog_path.parent, paths["queue_path"]]
    for output in (paths["capture_root"], paths["state_root"]):
        if any(output.resolve().is_relative_to(item.resolve()) or item.resolve().is_relative_to(output.resolve())
               for item in protected):
            raise ValueError("poll output roots overlap protected inputs")
    if (paths["capture_root"].resolve().is_relative_to(paths["state_root"].resolve())
            or paths["state_root"].resolve().is_relative_to(paths["capture_root"].resolve())):
        raise ValueError("poll state and capture roots must be separate")
    return {"config": config, "config_sha256": _sha(raw), "root": root,
            "catalog_path": catalog_path, "catalog": catalog, **paths}


@contextmanager
def _lock(queue: Path):
    path = queue.with_name(queue.name + ".poll.lock")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("another poll process holds the queue lock; no work started") from error
        yield
    finally:
        os.close(descriptor)


def _json(path: Path) -> dict:
    return _strict_json(_read(path), str(path))


def _history(state: Path, captures: Path) -> list[dict]:
    result = []
    for tick in sorted(state.iterdir()):
        if tick.is_symlink() or not tick.is_dir() or not re.fullmatch(r"[0-9a-f]{32}", tick.name):
            raise ValueError("unexpected entry in poll state root")
        jobs = tick / "jobs"
        if not jobs.exists():
            continue
        for job in sorted(jobs.iterdir()):
            if job.is_symlink() or not job.is_dir() or not re.fullmatch(r"[0-6]", job.name):
                raise ValueError("unexpected poll job")
            intent_path = job / "intent.json"
            if not intent_path.exists():
                continue  # Snapshot setup cannot issue requests before durable intent.
            intent = _json(intent_path)
            if intent["capture_name"] != f"{tick.name}-{job.name}":
                raise ValueError("poll intent capture name mismatch")
            result.append({"job": job, "intent": intent, "capture": captures / intent["capture_name"]})
    return result


def _reconcile(history: list[dict], queue: Path) -> tuple[list[dict], set[tuple]]:
    outcomes = []
    blocked = set()
    for row in history:
        intent, packet = row["intent"], row["capture"]
        key = intent["facility_key"], intent["plan_sha256"]
        result_path = row["job"] / "outcome.json"
        if not (packet / "manifest.json").exists():
            if not result_path.exists():
                _write(result_path, _pretty_bytes({"status": "interrupted_capture", "observed_at": _now(),
                                                  "note": "No completed manifest; any partial bytes remain retained."}))
            continue
        try:
            validate_capture(packet)
            plan, raw = load_plan(packet / "plan.json")
            run = _json(packet / "run.json")
            if (_sha(raw) != intent["plan_sha256"] or plan["checked_facility_key"] != intent["facility_key"]
                    or _instant(run["started_at"]) < _instant(intent["started_at"])):
                raise ValueError("completed packet does not match polling intent")
            imported = import_capture(queue, packet)
            if imported["imported"] or not result_path.exists():
                outcomes.append({"capture_name": intent["capture_name"], "status": "recovered_import",
                                 "run_id": imported["run_id"], "new_queue_import": imported["imported"]})
            if not result_path.exists():
                _write(result_path, _pretty_bytes({"status": "recovered_import", "observed_at": _now(),
                                                  "run_id": imported["run_id"]}))
        except (OSError, ValueError) as error:
            blocked.add(key)
            outcomes.append({"capture_name": intent["capture_name"], "status": "recovery_blocked",
                             "error": str(error)})
    return outcomes, blocked


def _prior(runs: list[dict], plan: dict) -> tuple[Path | None, Path | None]:
    if not runs:
        return None, None
    latest_clock = max(_instant(row["finished_at"]) for row in runs)
    latest = [row for row in runs if _instant(row["finished_at"]) == latest_clock]
    if len(latest) != 1:
        raise ValueError("ambiguous equal-time prior captures require review")
    root = Path(latest[0]["source_path"])
    ledger = root / "last_successful_checks.json"
    if not ledger.exists():
        ledger = root / "source_checks.json"
    validate_source_checks(ledger, root)
    history = {row["url"]: row for row in _json(ledger)["attempts"]}
    for document in plan["documents"]:
        eligible = [row for run in runs for row in run["documents"]
                    if row["url"] == document["url"] and row["status"] in ELIGIBLE]
        if not eligible:
            continue
        clock = max(_instant(row["response_finished_at"]) for row in eligible)
        expected = {row["current_sha256"] for row in eligible if _instant(row["response_finished_at"]) == clock}
        chosen = history.get(document["url"])
        if (len(expected) != 1 or chosen is None or chosen["sha256"] not in expected
                or _instant(chosen["finished_at"]) != clock
                or _content_error(document, chosen, root, plan) is not None):
            raise ValueError("latest prior packet loses or contradicts known eligible document history")
    return ledger, root


def _signal(report: dict, queue: dict, problems: list[str]) -> str:
    return _sha(_pretty_bytes({
        "catalog_sha256": report["catalog_sha256"],
        "facilities": [{"facility_key": row["facility_key"], "plan_state": row["plan_state"],
                        "documents": [{"id": doc["id"], "freshness": doc["freshness"], "health": doc["check_health"],
                                       "versions": sorted({item["current_text_sha256"] for item in doc["last_eligible_observations"]})}
                                      for doc in row["documents"]]} for row in report["facilities"]],
        "review": [{"id": row["id"], "status": row["status"], "requires_reopen": row["requires_reopen"]}
                   for row in queue["candidates"] if row["status"] in OPEN_STATUSES or row["requires_reopen"]],
        "problems": sorted(set(problems)),
    }))


def poll_once(
    config_path: str | Path, *, repository_root: str | Path | None = None,
    force: bool = False, transport=curl_fetch,
) -> dict:
    bound = load_config(config_path, repository_root)
    config, queue_path = bound["config"], bound["queue_path"]
    before = verify_queue(queue_path)
    started = _now()
    if _instant(started) < _instant(config["recorded_at"]):
        raise ValueError("poll configuration is not yet effective")
    for run in before["runs"]:
        for output in (bound["capture_root"], bound["state_root"]):
            if output.resolve().is_relative_to(Path(run["source_path"]).resolve()):
                raise ValueError("poll output root is inside an immutable capture")
    with _lock(queue_path):
        events = export_events(queue_path)["events"]
        started = _now()
        if events and _instant(events[-1]["recorded_at"]) > _instant(started):
            raise ValueError("poll clock predates existing queue knowledge")
        before = verify_queue(queue_path, as_of=started)
        state = ensure_real_directory(bound["state_root"], "poll state")
        captures = ensure_real_directory(bound["capture_root"], "poll captures")
        history = _history(state, captures)
        previous = [_json(path / "finished.json") for path in state.iterdir() if (path / "finished.json").exists()]
        previous.sort(key=lambda row: _instant(row["finished_at"]))
        initial_report = coverage_report(bound["catalog_path"], queue_path, as_of=started, repository_root=bound["root"])
        initial_signal = _signal(initial_report, before, [])
        tick = state / uuid.uuid4().hex
        tick.mkdir(mode=0o700)
        _write(tick / "request.json", _pretty_bytes({"format": "semiconductor-atlas-poll-request-v1",
               "started_at": started, "config_sha256": bound["config_sha256"],
               "catalog_sha256": bound["catalog"]["catalog_sha256"], "queue_head_before": before["head_event_id"],
               "forced": force, "runner_sha256": _sha(Path(__file__).read_bytes())}))
        recovered, blocked = _reconcile(history, queue_path)
        queue = verify_queue(queue_path)
        results = []
        problems = [f"{key[0]}:recovery_blocked" for key in blocked]
        (tick / "jobs").mkdir()
        for index, target in enumerate(bound["catalog"]["targets"]):
            plan, entry = target["plan"], target["entry"]
            if plan is None:
                continue
            key = entry["facility_key"], entry["plan"]["sha256"]
            item = {"facility_key": key[0], "plan_sha256": key[1]}
            now = _now()
            runs = [run for run in queue["runs"] if run["facility_key"] == key[0] and run["plan_sha256"] == key[1]]
            clocks = [run["finished_at"] for run in runs]
            clocks += [row["intent"]["started_at"] for row in history
                       if (row["intent"]["facility_key"], row["intent"]["plan_sha256"]) == key]
            if any(_instant(value) > _instant(now) for value in clocks):
                raise ValueError("poll clock predates recorded acquisition attempts")
            latest = max(clocks, key=_instant) if clocks else None
            due = (_instant(latest) + timedelta(seconds=config["interval_seconds"])) if latest else None
            item["due_at"] = due.isoformat().replace("+00:00", "Z") if due else None
            if key in blocked:
                results.append({**item, "status": "recovery_blocked"})
                continue
            if _instant(now) >= _instant(plan["expires_at"]):
                results.append({**item, "status": "review_expired"})
                continue
            if due is not None and _instant(now) < due and not force:
                incomplete = [row for row in history if (row["intent"]["facility_key"], row["intent"]["plan_sha256"]) == key
                              and not (row["capture"] / "manifest.json").exists()
                              and not any(_instant(run["finished_at"]) >= _instant(row["intent"]["started_at"]) for run in runs)]
                if incomplete:
                    problems.append(f"{key[0]}:capture_failed")
                results.append({**item, "status": "not_due", "unresolved_interrupted_capture": bool(incomplete)})
                continue
            try:
                prior_checks, prior_root = _prior(runs, plan)
            except (OSError, ValueError) as error:
                problems.append(f"{key[0]}:prior_history_requires_review")
                results.append({**item, "status": "prior_history_requires_review", "error": str(error)})
                continue
            job = tick / "jobs" / str(index)
            job.mkdir()
            # Capture consumes these pinned private bytes, never a mutable plan path.
            _, plan_raw = _bound_file(bound["root"], entry["plan"])
            _write(job / "plan.json", plan_raw)
            _, review_raw = _bound_file(bound["root"], plan["review_record"])
            review_path = job / "review" / plan["review_record"]["path"]
            review_path.parent.mkdir(parents=True)
            _write(review_path, review_raw)
            name = f"{tick.name}-{index}"
            intent = {**item, "started_at": _now(), "capture_name": name,
                      "prior_root": str(prior_root) if prior_root else None,
                      "prior_ledger_sha256": _sha(_read(prior_checks)) if prior_checks else None}
            _write(job / "intent.json", _pretty_bytes(intent))
            packet = captures / name
            stage = "capture"
            try:
                captured = capture_sources(job / "plan.json", packet, prior_checks=prior_checks, prior_root=prior_root,
                                           review_root=job / "review", transport=transport)
                stage = "queue_import"
                imported = import_capture(queue_path, packet)
                outcome = {"status": "captured_imported", "capture_attention_required": captured["attention_required"],
                           "run_id": imported["run_id"]}
            except (OSError, ValueError) as error:
                outcome = {"status": f"{stage}_failed", "error": str(error)}
                problems.append(f"{key[0]}:{outcome['status']}")
            _write(job / "outcome.json", _pretty_bytes({**outcome, "observed_at": _now()}))
            results.append({**item, **outcome, "capture_name": name})
        finished = _now()
        report = coverage_report(bound["catalog_path"], queue_path, as_of=finished, repository_root=bound["root"])
        queue = verify_queue(queue_path, as_of=finished)
        signal = _signal(report, queue, problems)
        _write(tick / "coverage.json", _pretty_bytes(report))
        _write(tick / "queue-events.json", _pretty_bytes(export_events(queue_path, as_of=finished)))
        result = {"format": "semiconductor-atlas-poll-result-v1", "invocation_path": str(tick),
                  "started_at": started, "finished_at": finished, "results": results, "recovery": recovered,
                  "signal_sha256": signal, "reportable_change": signal != (previous[-1]["signal_sha256"] if previous else initial_signal),
                  "attention_required": report["attention_required"] or bool(problems),
                  "coverage_summary": report["summary"], "queue_head_event_id": queue["head_event_id"],
                  "coverage_sha256": _sha(_pretty_bytes(report)), "continuous_operation_proven": False,
                  "claim_acceptance": False, "delivery_eligible": False}
        _write(tick / "finished.json", _pretty_bytes(result))
        return result
