"""Inventory retained source observations before claim acceptance, without network or writes."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import tempfile

from . import curated_capture as capture, curated_poll as poll, curated_review as queue
from . import discovery_handoff as binding, source_checks
from . import ai_critical_alert_review as blobs
from .ai_critical_changes import _pretty_bytes, _strict_json
from .source_checks import _read


STUDY_FORMAT = "semiconductor-atlas-curated-observation-study-v1"
FORMAT = "semiconductor-atlas-curated-observation-population-v1"
RULE_VERSION = "retained-exact-document-observation-census-v1"
DESIGN = "retrospective_retained_source_observation_census_not_preregistered"
SCOPE = "declared_retention_roots_plus_all_queue_captures; not complete publisher or manual-acquisition inventory"
MAX_BYTES = 20_000_000
_now, _instant, _hash = capture._now, capture._instant, queue._hash
BOUNDARIES = dict.fromkeys(("publisher_complete", "blind_evaluation_passed", "independence_verified",
    "claim_acceptance", "production_outcome_verified", "delivery_eligible", "calibrated_forecast"), False)


def _path(root: Path, value: str) -> Path:
    path = poll._path(root, value)
    if path == root or path.resolve() != path.absolute():
        raise ValueError("retention references cannot be the repository root or traverse symlinks")
    return path


def _json(path: Path) -> dict:
    raw = _read(path)
    if len(raw) > MAX_BYTES:
        raise ValueError("observation input exceeds 20 MB")
    result = _strict_json(raw, "observation input")
    if not isinstance(result, dict):
        raise ValueError("observation input must be an object")
    return result


def _files(root: Path, directory: Path) -> dict:
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("retention root must be an existing real directory")
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("retention inventory contains a symlink")
        if path.is_dir():
            continue
        raw = _read(path)
        result[path.relative_to(root).as_posix()] = {"bytes": len(raw), "sha256": _hash(raw)}
    return result


def _code_hashes() -> dict:
    paths = set(binding._code_paths().values()) | {
        Path(__file__), Path(poll.__file__), Path(blobs.__file__),
        Path(__file__).with_name("curated_coverage.py")}
    root = Path(__file__).parent.parent
    return {path.relative_to(root).as_posix(): _hash(_read(path)) for path in sorted(paths)}


def _study(raw: bytes) -> dict:
    study = binding._keys(_strict_json(raw, "observation study"),
        {"format", "study_id", "start", "end", "queue_path", "retention_roots", "seed_ledgers"}, "observation study")
    if study["format"] != STUDY_FORMAT or not _instant(study["start"]) < _instant(study["end"]):
        raise ValueError("unsupported study or empty half-open window")
    capture._text(study["study_id"])
    if not isinstance(study["retention_roots"], list) or not study["retention_roots"]:
        raise ValueError("study needs explicit retention roots")
    for row in study["retention_roots"]:
        binding._keys(row, {"capture_root", "poll_state_root"}, "retention root pair")
    if not isinstance(study["seed_ledgers"], list):
        raise ValueError("seed ledger references must be an array")
    return study


def _selected(clock: str, study: dict) -> bool:
    return _instant(study["start"]) <= _instant(clock) < _instant(study["end"])


def _document_rows(records: list[dict], study: dict) -> list[dict]:
    rows = []
    for record in records:
        plan, payload = record["plan"], record["payload"]
        entries = {row["id"]: row for row in plan["documents"]}
        attempts = {row["id"]: row for row in record["checks"]["attempts"]}
        prior = record["prior_checks"]
        for document in payload["documents"]:
            entry = entries[document["id"]]
            attempt = attempts.get(document["id"])
            previous = [row for row in prior["attempts"] if row["purpose"] == "source_document"
                and row["url"] == document["url"] and row["sha256"] == document["prior_sha256"]
                and row["finished_at"] == document["prior_success_at"]] if prior and document["prior_sha256"] else []
            if document["prior_sha256"] and len(previous) != 1:
                raise ValueError("selected embedded predecessor is missing or ambiguous")
            predecessor = None if not previous else {
                "ledger_sha256": record["run"]["prior_ledger_sha256"],
                "body_path": record["path"] + "/prior/" + previous[0]["path"],
                "body_sha256": previous[0]["sha256"], "response_finished_at": previous[0]["finished_at"],
                "text_sha256": document["prior_text_sha256"], "url": previous[0]["url"],
                "scope": "actual_embedded_predecessor_not_nearest_capture"}
            completed = payload["finished_at"]
            imported = record["queue_imported_at"]
            row = {"observation_id": _hash({"run_id": payload["run_id"], "document_id": document["id"]}),
                "capture_path": record["path"], "run_id": payload["run_id"], "document_id": document["id"],
                "url": document["url"], "facility_key": payload["facility_key"],
                "company": entry["company"], "country_code": entry["country_code"],
                "source_family": entry["source_family"], "scope": entry["scope"],
                "plan_sha256": payload["plan_sha256"], "status": document["status"],
                "assessment_at": completed, "response_finished_at": document["response_finished_at"],
                "queue_imported_at": imported,
                "queue_admitted_before_end": imported is not None and _instant(imported) < _instant(study["end"]),
                "attempted": attempt is not None, "transport_outcome": attempt["outcome"] if attempt else None,
                "body_path": record["path"] + "/" + attempt["path"] if attempt and attempt["path"] else None,
                "body_sha256": document["current_sha256"], "text_sha256": document["current_text_sha256"],
                "predecessor": predecessor, "target_revision_verdict": None,
                "window_membership": "in_window" if _selected(completed, study) else
                    "before_window" if _instant(completed) < _instant(study["start"]) else "at_or_after_end",
                "comparison_eligible": document["status"] in {"unchanged", "raw_bytes_only", "visible_text_changed_requires_review"}}
            rows.append(row)
    return sorted(rows, key=lambda row: row["observation_id"])


def _collect(study: dict, root: Path, *, history_override: dict | None = None,
             source_reference_root: Path | None = None) -> dict:
    reference = root if source_reference_root is None else source_reference_root
    history = queue.export_events(_path(root, study["queue_path"])) if history_override is None else history_override
    queue._validate_events(history["events"])
    queue._fold(history["events"])
    imported = {event["payload"]["source_path"]: event for event in history["events"]
                if event["payload"]["kind"] == "capture_imported"}
    captures, files, intents, invocations = set(), {}, {}, []
    directories, root_names = {}, set()
    for pair in study["retention_roots"]:
        capture_root, state_root = (_path(root, pair[key]) for key in ("capture_root", "poll_state_root"))
        for directory in (capture_root, state_root):
            if any(directory.is_relative_to(old) or old.is_relative_to(directory) for old in root_names):
                raise ValueError("retention roots overlap or repeat")
            root_names.add(directory)
            files.update(_files(root, directory))
            directories[directory.relative_to(root).as_posix()] = sorted(item.name for item in directory.iterdir())
        for path in capture_root.iterdir():
            if not path.is_dir():
                raise ValueError("capture container has an unexpected non-directory entry")
            captures.add(path)
        for row in poll._history(state_root, capture_root):
            path, intent = row["capture"], row["intent"]
            binding._keys(intent, {"facility_key", "plan_sha256", "started_at", "capture_name",
                "prior_root", "prior_ledger_sha256", "due_at"}, "poll intent")
            if path in intents:
                raise ValueError("duplicate capture intent")
            plan, raw = capture.load_plan(row["job"] / "plan.json")
            if _hash(raw) != intent["plan_sha256"] or plan["checked_facility_key"] != intent["facility_key"]:
                raise ValueError("poll intent does not bind its plan")
            if not _instant(plan["reviewed_at"]) <= _instant(intent["started_at"]) < _instant(plan["expires_at"]):
                raise ValueError("poll intent is outside its approved plan window")
            if _hash(_read(row["job"] / "review" / plan["review_record"]["path"])) != plan["review_record"]["sha256"]:
                raise ValueError("poll intent review bytes differ")
            intents[path] = {"job_path": row["job"].relative_to(root).as_posix(), "intent": intent,
                             "plan": plan, "outcome": _json(row["job"] / "outcome.json") if (row["job"] / "outcome.json").exists() else None}
            captures.add(path)
        for tick in sorted(state_root.iterdir()):
            request = _json(tick / "request.json")
            finished = _json(tick / "finished.json") if (tick / "finished.json").exists() else None
            if request.get("format") != "semiconductor-atlas-poll-request-v1":
                raise ValueError("unsupported polling request")
            if finished and (finished.get("format") != "semiconductor-atlas-poll-result-v1"
                    or finished["started_at"] != request["started_at"]
                    or _instant(finished["finished_at"]) < _instant(request["started_at"])):
                raise ValueError("poll completion does not match its request")
            invocations.append({"path": tick.relative_to(root).as_posix(), "request": request, "finished": finished})
    for name in imported:
        path = Path(name)
        relative = path.relative_to(reference).as_posix()
        captures.add(_path(root, relative))
    records, incomplete, seen = [], [], set()
    for path in sorted(captures):
        relative = path.relative_to(root).as_posix()
        if path.exists():
            files.update(_files(root, path))
        if not (path / "manifest.json").exists():
            if str(reference / relative) in imported:
                raise ValueError("queue-admitted capture is missing its completed manifest")
            intent = intents.get(path)
            plan = intent["plan"] if intent else capture.load_plan(path / "plan.json")[0]
            incomplete.append({"path": relative, "intent": intent,
                "planned_documents": plan["documents"], "status": "incomplete_or_unverifiable_capture",
                "window_membership": "in_window" if intent and _selected(intent["intent"]["started_at"], study)
                    else "outside_window" if intent else "unknown",
                "request_outcome": "unknown_without_completed_capture"})
            continue
        payload = queue._capture_payload(path)
        payload["source_path"] = str(reference / relative)
        if payload["run_id"] in seen:
            raise ValueError("same completed capture appears at multiple retained paths")
        seen.add(payload["run_id"])
        event = imported.get(str(reference / relative))
        if event and event["payload"] != payload:
            raise ValueError("completed capture differs from its admitted queue payload")
        if path in intents and (payload["plan_sha256"] != intents[path]["intent"]["plan_sha256"]
                or _instant(payload["started_at"]) < _instant(intents[path]["intent"]["started_at"])):
            raise ValueError("completed capture contradicts its polling intent")
        records.append({"path": relative, "payload": payload, "plan": _json(path / "plan.json"),
            "run": _json(path / "run.json"), "checks": _json(path / "source_checks.json"),
            "prior_checks": _json(path / "prior/source_checks.json") if payload["prior_ledger_sha256"] else None,
            "queue_imported_at": event["recorded_at"] if event else None,
            "poll_intent": intents.get(path)})
    seeds, seed_paths, seed_hashes = [], set(), set()
    for ref in study["seed_ledgers"]:
        path, raw = binding._binding(root, ref)
        _path(root, ref["path"])
        if path in seed_paths or _hash(raw) in seed_hashes:
            raise ValueError("duplicate predecessor seed ledger")
        seed_paths.add(path)
        seed_hashes.add(_hash(raw))
        source_checks.validate_source_checks(path, path.parent)
        ledger = _json(path)
        references = [path] + [path.parent / row["path"] for row in ledger["attempts"] if row["path"]]
        for source in references:
            content = _read(source)
            files[source.relative_to(root).as_posix()] = {"bytes": len(content), "sha256": _hash(content)}
        seeds.append({"reference": ref, "ledger": ledger,
            "used_by_captures": [record["path"] for record in records if record["payload"]["prior_ledger_sha256"] == _hash(raw)]})
    return {"source_reference_root": str(reference), "queue_history": history, "files": files, "root_entries": directories,
        "captures": records, "incomplete_captures": incomplete, "poll_invocations": invocations,
        "seed_ledgers": seeds}


def _projection(snapshot: dict, study: dict) -> dict:
    observations = _document_rows(snapshot["captures"], study)
    selected = [row for row in observations if row["window_membership"] == "in_window"]
    invocations = [row for row in snapshot["poll_invocations"] if _selected(row["request"]["started_at"], study)]
    outcomes = [item for row in invocations if row["finished"] and _instant(row["finished"]["finished_at"]) < _instant(study["end"])
                for item in row["finished"]["results"]]
    current_attempts = [attempt for record in snapshot["captures"] if _selected(record["payload"]["finished_at"], study)
                        for attempt in record["checks"]["attempts"]]
    pending = []
    for record in snapshot["captures"]:
        intent = record["poll_intent"]
        if (intent and _selected(intent["intent"]["started_at"], study)
                and _instant(record["payload"]["finished_at"]) >= _instant(study["end"])):
            pending.append({"capture_path": record["path"], "intent_started_at": intent["intent"]["started_at"],
                "planned_documents": record["plan"]["documents"], "status": "not_completed_by_cutoff"})
    for record in snapshot["incomplete_captures"]:
        if record["window_membership"] == "in_window":
            pending.append({"capture_path": record["path"], "intent_started_at": record["intent"]["intent"]["started_at"],
                "planned_documents": record["planned_documents"], "status": "not_completed_by_cutoff"})
    return {"observations": observations, "incomplete_at_cutoff": sorted(pending, key=lambda row: row["capture_path"]), "counts": {
        "retained_completed_captures": len(snapshot["captures"]), "retained_incomplete_captures": len(snapshot["incomplete_captures"]),
        "document_checks_in_window": len(selected), "document_requests_in_window": sum(row["attempted"] for row in selected),
        "document_statuses_in_window": dict(sorted(Counter(row["status"] for row in selected).items())),
        "paired_byte_text_comparisons_in_window": sum(row["comparison_eligible"] for row in selected),
        "queue_unadmitted_checks_by_end": sum(not row["queue_admitted_before_end"] for row in selected),
        "exact_urls_in_window": len({row["url"] for row in selected}),
        "facility_scopes_in_window": len({row["facility_key"] for row in selected}),
        "poll_invocations_started_in_window": len(invocations),
        "forced_poll_invocations_in_window": sum(row["request"]["forced"] is True for row in invocations),
        "poll_invocations_not_completed_by_end": sum(not row["finished"] or _instant(row["finished"]["finished_at"]) >= _instant(study["end"]) for row in invocations),
        "incomplete_document_opportunities_at_cutoff": sum(len(row["planned_documents"]) for row in pending),
        "completed_poll_plan_outcomes_by_end": dict(sorted(Counter(row["status"] for row in outcomes).items())),
        "seed_attempts_retained_separately": sum(len(row["ledger"]["attempts"]) for row in snapshot["seed_ledgers"]),
        "capture_request_outcomes_in_window": dict(sorted(Counter(row["outcome"] for row in current_attempts).items())),
        "seed_request_outcomes_retained_separately": dict(sorted(Counter(row["outcome"] for seed in snapshot["seed_ledgers"] for row in seed["ledger"]["attempts"]).items())),
        "target_revision_labels": 0},
        "window_basis": "completed_capture_assessment; incomplete_intents_and_invocation_gaps_retained_separately"}


def _validate_knowledge_clock(snapshot: dict, frozen_at: str) -> None:
    clocks = [row["recorded_at"] for row in snapshot["queue_history"]["events"]]
    clocks += [row["payload"]["finished_at"] for row in snapshot["captures"]]
    clocks += [row["request"]["started_at"] for row in snapshot["poll_invocations"]]
    clocks += [row["finished"]["finished_at"] for row in snapshot["poll_invocations"] if row["finished"]]
    intents = [row["poll_intent"] for row in snapshot["captures"] if row["poll_intent"]]
    intents += [row["intent"] for row in snapshot["incomplete_captures"] if row["intent"]]
    clocks += [row["intent"]["started_at"] for row in intents]
    clocks += [row["outcome"]["observed_at"] for row in intents if row["outcome"]]
    clocks += [row.get("finished_at") or row["observed_at"] for seed in snapshot["seed_ledgers"] for row in seed["ledger"]["attempts"]]
    if any(_instant(clock) > _instant(frozen_at) for clock in clocks):
        raise ValueError("retained source population contains knowledge after freeze")


def freeze_population(study_path: str | Path, *, reference_root: str | Path) -> dict:
    root, path = Path(reference_root).resolve(), Path(study_path).absolute()
    raw = _read(path)
    study = _study(raw)
    started = _now()
    if _instant(study["end"]) > _instant(started):
        raise ValueError("source observation window must end before freeze starts")
    codes = _code_hashes()
    snapshot = _collect(study, root)
    _validate_knowledge_clock(snapshot, _now())
    result = {"format": FORMAT, "rule_version": RULE_VERSION, "study": blobs._blob(raw),
        "started_at": started, "frozen_at": _now(), "snapshot": snapshot,
        "snapshot_sha256": _hash(snapshot), **_projection(snapshot, study),
        "code_sha256": codes, "boundaries": BOUNDARIES.copy(),
        "design": DESIGN, "scope": SCOPE}
    if len(_pretty_bytes(result)) > MAX_BYTES:
        raise ValueError("complete observation population exceeds 20 MB; never truncate")
    if snapshot != _collect(study, root) or raw != _read(path) or codes != _code_hashes():
        raise ValueError("source retention inventory or inputs changed across freeze")
    result["frozen_at"] = _now()
    return result


def verify_population_sources(frozen_path: str | Path, *, reference_root: str | Path) -> dict:
    path, root = Path(frozen_path), Path(reference_root).resolve()
    raw = _read(path)
    frozen = _json(path)
    binding._keys(frozen, {"format", "rule_version", "study", "started_at", "frozen_at", "snapshot",
        "snapshot_sha256", "observations", "incomplete_at_cutoff", "counts", "window_basis", "code_sha256",
        "boundaries", "design", "scope"}, "frozen observation population")
    if (frozen["format"] != FORMAT or frozen["rule_version"] != RULE_VERSION
            or frozen["code_sha256"] != _code_hashes() or frozen["boundaries"] != BOUNDARIES
            or any(value is not False for value in frozen["boundaries"].values())
            or frozen["design"] != DESIGN or frozen["scope"] != SCOPE
            or frozen["snapshot_sha256"] != _hash(frozen["snapshot"]) or raw != _pretty_bytes(frozen)):
        raise ValueError("frozen source population contract, bytes or pinned code differs")
    study = _study(blobs._unblob(frozen["study"]))
    if not _instant(study["end"]) <= _instant(frozen["started_at"]) <= _instant(frozen["frozen_at"]) <= _instant(_now()):
        raise ValueError("invalid actual population freeze clocks")
    snapshot = frozen["snapshot"]
    _validate_knowledge_clock(snapshot, frozen["frozen_at"])
    original_events = snapshot["queue_history"]["events"]
    current_events = queue.export_events(_path(root, study["queue_path"]))["events"]
    if current_events[:len(original_events)] != original_events:
        raise ValueError("original source queue history changed or disappeared")
    with tempfile.TemporaryDirectory(prefix="atlas-observation-replay-") as temporary:
        replay = Path(temporary).resolve()
        for relative, names in snapshot["root_entries"].items():
            directory = _path(replay, relative)
            directory.mkdir(parents=True, exist_ok=True)
            for name in names:
                child = _path(replay, relative + "/" + name)
                if child.parent != directory:
                    raise ValueError("invalid retained root entry")
                child.mkdir(parents=True, exist_ok=True)
        for relative, expected in snapshot["files"].items():
            source = _path(root, relative)
            content = _read(source)
            if {"bytes": len(content), "sha256": _hash(content)} != expected:
                raise ValueError("original retention input changed or disappeared")
            destination = _path(replay, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            capture._write(destination, content)
        current = _collect(study, replay, history_override=snapshot["queue_history"],
                           source_reference_root=Path(snapshot["source_reference_root"]))
    if current != snapshot:
        raise ValueError("original retained source population does not replay")
    if any(frozen[key] != value for key, value in _projection(current, study).items()):
        raise ValueError("observation population does not replay")
    changed = any({"bytes": len(content := _read(_path(root, relative))), "sha256": _hash(content)} != expected
                  for relative, expected in snapshot["files"].items())
    current_events = queue.export_events(_path(root, study["queue_path"]))["events"]
    if (changed or current_events[:len(original_events)] != original_events or _read(path) != raw
            or frozen["code_sha256"] != _code_hashes()):
        raise ValueError("frozen inputs changed across source verification")
    return {"frozen_sha256": _hash(raw), "counts": frozen["counts"],
        "exact_retention_snapshot_replayed": True, "original_queue_prefix_verified": True,
        "later_retained_additions_allowed": True, "network_requests": 0,
        "publisher_complete": False, "claim_acceptance": False, "delivery_eligible": False}
