"""Prespecified shadow predictions over retained checks, separate from outcome labels."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from pathlib import Path
import tempfile

from . import curated_observation_population as population, curated_poll as poll
from . import curated_capture as capture, source_statement_review as statements
from . import discovery_handoff as binding, source_vintage_review as vintage
from . import source_target_detector as detector
from .ai_critical_changes import _pretty_bytes, _strict_json
from .source_checks import _read


STUDY_FORMAT = "semiconductor-atlas-prospective-target-study-v1"
REGISTRATION_FORMAT = "semiconductor-atlas-prospective-target-registration-v1"
BATCH_FORMAT = "semiconductor-atlas-prospective-target-predictions-v1"
SEAL_FORMAT = "semiconductor-atlas-prospective-target-seal-v1"
RECEIPT_FORMAT = "semiconductor-atlas-prospective-target-receipt-v1"
REGISTRATION_RECEIPT_FORMAT = "semiconductor-atlas-prospective-target-registration-receipt-v1"
RULE_VERSION = "all-configured-documents-future-shadow-target-predictions-v1"
TARGET_SCOPE = "source_stated_calendar_manufacturing_targets"
STOPPING_RULE = "fixed_end_no_adaptive_stopping"
BOUNDARIES = {**vintage.BOUNDARIES, "external_timestamp_attestation": False,
    "scheduled_execution_proven": False, "outcome_adjudication": False}
_now, _instant, _hash = population._now, population._instant, population._hash


def _code_hashes() -> dict:
    root = Path(__file__).parent.parent
    paths = (Path(__file__), Path(detector.__file__), root / "scripts/shadow_source_targets.py")
    return {**vintage._code_hashes(), **{path.relative_to(root).as_posix(): _hash(_read(path)) for path in paths}}


def _study(raw: bytes) -> dict:
    study = binding._keys(_strict_json(raw, "prospective target study"), {"format", "study_id",
        "start", "end", "config", "baseline_population", "prediction_root", "prediction_deadline_seconds",
        "target_scope", "stopping_rule", "prior_exposure"}, "prospective target study")
    if (study["format"] != STUDY_FORMAT or study["target_scope"] != TARGET_SCOPE
            or study["stopping_rule"] != STOPPING_RULE or not _instant(study["start"]) < _instant(study["end"])):
        raise ValueError("unsupported prospective study or empty window")
    for key in ("study_id", "prior_exposure"):
        statements._text(study[key])
    seconds = study["prediction_deadline_seconds"]
    if type(seconds) is not int or not 1 <= seconds <= 86400:
        raise ValueError("prediction deadline must be between one second and one day")
    return study


def _inputs(study: dict, root: Path) -> dict:
    config_path, config_raw = binding._binding(root, study["config"])
    loaded = poll.load_config(config_path, repository_root=root)
    if loaded["config_sha256"] != _hash(config_raw):
        raise ValueError("poll configuration changed during registration replay")
    baseline_path, baseline_raw = binding._binding(root, study["baseline_population"])
    population.verify_population_sources(baseline_path, reference_root=root)
    baseline = _strict_json(baseline_raw, "baseline observation population")
    baseline_study = population._study(population.blobs._unblob(baseline["study"]))
    config = loaded["config"]
    if (baseline_study["queue_path"] != config["queue_path"] or
            {"capture_root": config["capture_root"], "poll_state_root": config["state_root"]} not in baseline_study["retention_roots"]):
        raise ValueError("registered poll configuration and baseline retention universe differ")
    documents, unmonitored, files = [], [], {}
    refs = [study["config"], study["baseline_population"], config["catalog"],
            loaded["catalog"]["catalog"]["baseline"]]
    for target in loaded["catalog"]["targets"]:
        entry, plan = target["entry"], target["plan"]
        if plan is None:
            unmonitored.append(entry)
            continue
        if not _instant(plan["reviewed_at"]) <= _instant(study["start"]) < _instant(study["end"]) <= _instant(plan["expires_at"]):
            raise ValueError("future study must fit within every configured plan review window")
        refs += [entry["plan"], plan["review_record"]]
        for document in plan["documents"]:
            documents.append({"facility_key": entry["facility_key"], "plan_sha256": entry["plan"]["sha256"],
                "document_id": document["id"], **{key: document[key] for key in
                    ("url", "scope", "company", "country_code", "source_family")}})
    if not documents or len({row["url"] for row in documents}) != len(documents):
        raise ValueError("configured document population is empty or has duplicate URLs")
    for ref in refs:
        path, raw = binding._binding(root, ref)
        files[path.relative_to(root).as_posix()] = {"bytes": len(raw), "sha256": _hash(raw)}
    prediction_root = population._path(root, study["prediction_root"])
    if not prediction_root.is_dir():
        raise ValueError("prediction root must already be a real directory")
    protected = {str(Path(name).parent) for name in baseline["snapshot"]["files"]}
    protected.update(baseline["snapshot"]["root_entries"])
    protected.update((config["capture_root"], config["state_root"]))
    exact_inputs = [population._path(root, name) for name in files] + [loaded["queue_path"]]
    if (any(prediction_root == path or prediction_root.is_relative_to(path) or path.is_relative_to(prediction_root)
            for path in [population._path(root, name) for name in protected])
            or any(path == prediction_root or path.is_relative_to(prediction_root) for path in exact_inputs)):
        raise ValueError("prediction root overlaps retained sources or registered inputs")
    return {"documents": sorted(documents, key=lambda row: row["url"]), "unmonitored_facilities": unmonitored,
        "parser_support": [{"url": row["url"], "route_implemented": row["url"] in detector.SUPPORTED_URLS}
                           for row in sorted(documents, key=lambda row: row["url"])],
        "files": files, "protected_directories": sorted(protected),
        "baseline_frozen_at": baseline["frozen_at"], "baseline_study": baseline_study,
        "poll_config": config, "poll_config_reference": study["config"]["path"],
        "manufacturing_baseline_source_bytes_verified": False,
        "baseline_source_files": baseline["snapshot"]["files"]}


def register(study_path: str | Path, *, reference_root: str | Path) -> dict:
    root, path = Path(reference_root).resolve(), Path(study_path).absolute()
    raw = binding._bounded_read(path)
    study = _study(raw)
    started, codes = _now(), _code_hashes()
    if not _instant(started) < _instant(study["start"]):
        raise ValueError("registration must precede the future window")
    inputs = _inputs(study, root)
    if any(_instant(clock) > _instant(started) for clock in
           (inputs["baseline_frozen_at"], inputs["poll_config"]["recorded_at"])):
        raise ValueError("registration predates its baseline knowledge")
    if any(population._path(root, study["prediction_root"]).iterdir()):
        raise ValueError("a new study requires an empty prediction root")
    if inputs != _inputs(study, root) or raw != _read(path) or codes != _code_hashes():
        raise ValueError("registration inputs changed")
    finished = _now()
    if not _instant(started) <= _instant(finished) < _instant(study["start"]):
        raise ValueError("registration clock crossed the start or moved backwards")
    return {"format": REGISTRATION_FORMAT, "rule_version": RULE_VERSION, "study": population.blobs._blob(raw),
        "started_at": started, "registered_at": finished, "inputs": inputs,
        "code_sha256": codes, "boundaries": BOUNDARIES.copy()}


def _validate_registration_draft(path: str | Path, *, reference_root: str | Path) -> tuple[dict, bytes]:
    root, path = Path(reference_root).resolve(), Path(path).absolute()
    raw = binding._bounded_read(path)
    result = binding._keys(_strict_json(raw, "target registration"), {"format", "rule_version", "study",
        "started_at", "registered_at", "inputs", "code_sha256", "boundaries"}, "target registration")
    study = _study(population.blobs._unblob(result["study"]))
    if (result["format"] != REGISTRATION_FORMAT or result["rule_version"] != RULE_VERSION
            or result["code_sha256"] != _code_hashes() or result["boundaries"] != BOUNDARIES
            or any(value is not False for value in result["boundaries"].values()) or raw != _pretty_bytes(result)
            or not _instant(result["started_at"]) <= _instant(result["registered_at"]) < _instant(study["start"])
            or _instant(result["registered_at"]) > _instant(_now())
            or _instant(result["inputs"]["poll_config"]["recorded_at"]) > _instant(result["started_at"])
            or _instant(result["inputs"]["baseline_frozen_at"]) > _instant(result["started_at"])):
        raise ValueError("registration bytes, code, boundaries or clocks differ")
    if result["inputs"] != _inputs(study, root) or result["inputs"] != _inputs(study, root):
        raise ValueError("registered inputs differ")
    if raw != _read(path) or result["code_sha256"] != _code_hashes():
        raise ValueError("registration changed during replay")
    return result, raw


def _registration_receipt_path(path: Path) -> Path:
    return path.with_name(path.name + ".receipt.json")


def accept_registration(study_path: str | Path, output: str | Path, *, reference_root: str | Path) -> dict:
    root, path = Path(reference_root).resolve(), Path(output).absolute()
    result = register(study_path, reference_root=root)
    study = _study(population.blobs._unblob(result["study"]))
    directory = population._path(root, study["prediction_root"])
    protection = [*result["inputs"]["protected_directories"], study["prediction_root"]]
    receipt_path = _registration_receipt_path(path)
    with poll._lock(directory):
        if any(directory.iterdir()) or receipt_path.exists():
            raise ValueError("registration output population or receipt already exists")
        written = vintage.write_new(path, result, reference_root=root, protected_directories=protection)
        retained, raw = _validate_registration_draft(path, reference_root=root)
        accepted = _now()
        if retained != result or not _instant(result["registered_at"]) <= _instant(accepted) < _instant(study["start"]):
            raise ValueError("registration acceptance crossed the start; unaccepted draft retained")
        receipt = {"format": REGISTRATION_RECEIPT_FORMAT, "registration_sha256": _hash(raw), "accepted_at": accepted}
        acceptance = vintage.write_new(receipt_path, receipt, reference_root=root, protected_directories=protection)
    return {**written, "receipt": acceptance, "accepted_at": accepted}


def validate_registration(path: str | Path, *, reference_root: str | Path) -> tuple[dict, bytes]:
    result, raw = _validate_registration_draft(path, reference_root=reference_root)
    receipt_path = _registration_receipt_path(Path(path).absolute())
    content = binding._bounded_read(receipt_path)
    receipt = binding._keys(_strict_json(content, "registration acceptance receipt"),
        {"format", "registration_sha256", "accepted_at"}, "registration acceptance receipt")
    study = _study(population.blobs._unblob(result["study"]))
    if (content != _pretty_bytes(receipt) or receipt["format"] != REGISTRATION_RECEIPT_FORMAT
            or receipt["registration_sha256"] != _hash(raw)
            or not _instant(result["registered_at"]) <= _instant(receipt["accepted_at"]) < _instant(study["start"])
            or _instant(receipt["accepted_at"]) > _instant(_now())
            or content != _read(receipt_path) or raw != _read(Path(path)) or result["code_sha256"] != _code_hashes()):
        raise ValueError("registration acceptance receipt, clock or registered bytes differ")
    return result, raw


def _freeze(registration: dict, root: Path, cutoff: str) -> dict:
    study = _study(population.blobs._unblob(registration["study"]))
    source_study = {**registration["inputs"]["baseline_study"], "study_id": study["study_id"] + "-observations",
                    "start": study["start"], "end": cutoff}
    with tempfile.TemporaryDirectory(prefix="atlas-shadow-census-") as temporary:
        path = Path(temporary).resolve() / "study.json"
        capture._write(path, _pretty_bytes(source_study))
        return population.freeze_population(path, reference_root=root)


def _verify_population(frozen: dict, root: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="atlas-shadow-replay-") as temporary:
        path = Path(temporary).resolve() / "population.json"
        capture._write(path, _pretty_bytes(frozen))
        population.verify_population_sources(path, reference_root=root)


def _verify_current_population(frozen: dict, root: Path) -> None:
    study = population._study(population.blobs._unblob(frozen["study"]))
    current = population._collect(study, root)
    population._validate_knowledge_clock(current, _now())
    if current != frozen["snapshot"]:
        raise ValueError("source retention population changed before seal acceptance")


def _verify_current_opportunities(frozen: dict, registration: dict, root: Path) -> None:
    study = population._study(population.blobs._unblob(frozen["study"]))
    snapshot = population._collect(study, root)
    population._validate_knowledge_clock(snapshot, _now())
    current = {"snapshot": snapshot, **population._projection(snapshot, study)}
    def identities(value):
        return {_input_id(case): _protocol(case, registration, value) for case in statements._cases(value).values()}
    if identities(current) != identities(frozen):
        raise ValueError("sealed in-window opportunity population differs from current retention; preserve seal and investigate")


def _protocol(case: dict, registration: dict, frozen: dict) -> tuple[bool, str]:
    expected = next((row for row in registration["inputs"]["documents"] if row["url"] == case["url"]), None)
    if expected is None:
        return False, "outside_registered_documents"
    completed = next((row for row in frozen["snapshot"]["captures"] if row["path"] == case["capture_path"]), None)
    unfinished = next((row for row in frozen["snapshot"]["incomplete_captures"] if row["path"] == case["capture_path"]), None)
    intent = completed["poll_intent"] if completed else unfinished["intent"]
    if not intent:
        return False, "non_poll_capture"
    tick_path = str(Path(intent["job_path"]).parents[1])
    invocation = next(row for row in frozen["snapshot"]["poll_invocations"] if row["path"] == tick_path)
    if invocation["request"]["forced"] is not False:
        return False, "forced_poll_capture"
    if (invocation["request"]["config_sha256"] != registration["inputs"]["files"]
            [registration["inputs"]["poll_config_reference"]]["sha256"]):
        return False, "unregistered_poll_configuration"
    if case["kind"] == "document_check":
        actual = {field: case[field] for field in expected}
    else:
        plan = intent["plan"]
        document = next(row for row in plan["documents"] if row["id"] == case["document_id"])
        actual = {"facility_key": plan["checked_facility_key"], "plan_sha256": intent["intent"]["plan_sha256"],
            "document_id": document["id"], **{key: document[key] for key in
                ("url", "scope", "company", "country_code", "source_family")}}
    return (True, "ordinary_registered_poll") if actual == expected else (False, "registered_document_or_plan_changed")


def _input_id(case: dict) -> str:
    return _hash({field: value for field, value in case.items() if field not in
                  {"queue_imported_at", "queue_admitted_before_end"}})


def _case_predictions(registration: dict, frozen: dict, root: Path) -> list:
    result, inputs = [], {}
    for key, case in statements._cases(frozen).items():
        status, reason, analysis = "abstain", "incomplete_document", None
        in_protocol, collection_mode = _protocol(case, registration, frozen)
        if not in_protocol:
            reason = collection_mode
        elif case["kind"] == "document_check":
            if not case["comparison_eligible"]:
                reason = "no_eligible_actual_predecessor_pair"
            else:
                before, _ = statements._body(case, "before", root, frozen, inputs)
                after, _ = statements._body(case, "after", root, frozen, inputs)
                try:
                    analysis = detector.analyze(case["url"], before, after)
                    status = analysis["result"]
                    if status not in {"revision_candidate", "no_candidate", "abstain", "error"}:
                        raise ValueError("invalid detector result")
                    reason = analysis["reason"]
                except Exception as error:
                    status, reason, analysis = "error", f"{type(error).__name__}: {error}", None
        opportunity = {"capture_path": case["capture_path"], "document_id": case["document_id"]}
        result.append({"case_id": key, "input_id": _input_id(case), "opportunity_id": _hash(opportunity),
                       "case": case, "in_protocol": in_protocol, "collection_mode": collection_mode,
                       "result": status, "reason": reason, "analysis": analysis})
    if any(_read(path) != raw for path, raw in inputs.items()):
        raise ValueError("detector source bytes changed during prediction")
    return result


def predict(registration_path: str | Path, *, reference_root: str | Path) -> dict:
    root = Path(reference_root).resolve()
    started = _now()
    registration, raw = validate_registration(registration_path, reference_root=root)
    study = _study(population.blobs._unblob(registration["study"]))
    if not _instant(study["start"]) < _instant(started):
        raise ValueError("prediction window has not started")
    cutoff = min((started, study["end"]), key=_instant)
    frozen = _freeze(registration, root, cutoff)
    predictions = _case_predictions(registration, frozen, root)
    finished = _now()
    if not _instant(started) <= _instant(frozen["started_at"]) <= _instant(frozen["frozen_at"]) <= _instant(finished):
        raise ValueError("invalid actual prediction clocks")
    result = {"format": BATCH_FORMAT, "rule_version": RULE_VERSION, "registration_sha256": _hash(raw),
        "started_at": started, "recorded_at": finished, "cutoff": cutoff, "population": frozen,
        "predictions": predictions, "code_sha256": registration["code_sha256"], "boundaries": BOUNDARIES.copy()}
    _verify_population(frozen, root)
    if validate_registration(registration_path, reference_root=root)[1] != raw:
        raise ValueError("registration changed across prediction")
    return result


def validate_batch(path: str | Path, registration: dict, registration_raw: bytes, root: Path) -> tuple[dict, bytes]:
    path = Path(path)
    raw = binding._bounded_read(path)
    batch = binding._keys(_strict_json(raw, "prediction batch"), {"format", "rule_version", "registration_sha256",
        "started_at", "recorded_at", "cutoff", "population", "predictions", "code_sha256", "boundaries"}, "prediction batch")
    study = _study(population.blobs._unblob(registration["study"]))
    frozen = batch["population"]
    source_study = population._study(population.blobs._unblob(frozen["study"]))
    expected_study = {**registration["inputs"]["baseline_study"], "study_id": study["study_id"] + "-observations",
                      "start": study["start"], "end": batch["cutoff"]}
    if (batch["format"] != BATCH_FORMAT or batch["rule_version"] != RULE_VERSION or raw != _pretty_bytes(batch)
            or batch["registration_sha256"] != _hash(registration_raw) or batch["code_sha256"] != registration["code_sha256"]
            or batch["boundaries"] != BOUNDARIES or any(value is not False for value in batch["boundaries"].values())
            or batch["cutoff"] != min((batch["started_at"], study["end"]), key=_instant)
            or not _instant(study["start"]) < _instant(batch["started_at"]) <= _instant(frozen["started_at"])
            or not _instant(frozen["frozen_at"]) <= _instant(batch["recorded_at"]) <= _instant(_now())
            or source_study != expected_study):
        raise ValueError("prediction batch bytes, inputs or clocks differ")
    _verify_population(frozen, root)
    expected = _case_predictions(registration, frozen, root)
    recorded = batch["predictions"]
    if not isinstance(recorded, list) or len(recorded) != len(expected):
        raise ValueError("recorded prediction population differs")
    for actual, replayed in zip(recorded, expected):
        if actual.get("result") == "error":
            statements._text(actual.get("reason"))
            if (actual.get("analysis") is not None or not actual.get("in_protocol")
                    or actual.get("case", {}).get("comparison_eligible") is not True):
                raise ValueError("invalid retained detector execution error")
            replayed = {**replayed, "result": "error", "reason": actual["reason"], "analysis": None}
        if actual != replayed:
            raise ValueError("recorded predictions do not replay")
    if raw != _read(path):
        raise ValueError("recorded predictions do not replay")
    return batch, raw


def _journal(directory: Path, registration: dict, registration_raw: bytes, root: Path, *, allow_seal=False) -> tuple[list, dict]:
    records, retained = [], {}
    entries = sorted(directory.iterdir())
    batch_paths = []
    for path in entries:
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            raise ValueError("unexpected or sealed prediction-root entry")
        if path.name == "seal.json":
            if not allow_seal:
                raise ValueError("prediction population is already sealed")
            continue
        if path.name.endswith(".receipt.json"):
            if not (directory / (path.name.removesuffix(".receipt.json") + ".json")).is_file():
                raise ValueError("acceptance receipt lacks its prediction batch")
        else:
            batch_paths.append(path)
    for path in batch_paths:
        batch, raw = validate_batch(path, registration, registration_raw, root)
        if path.stem != _hash(batch):
            raise ValueError("prediction filename differs from content identity")
        retained[path] = raw
        receipt_path = path.with_name(path.stem + ".receipt.json")
        receipt = None
        if receipt_path.exists():
            content = binding._bounded_read(receipt_path)
            receipt = binding._keys(_strict_json(content, "prediction acceptance receipt"),
                {"format", "registration_sha256", "batch_sha256", "accepted_at"}, "prediction acceptance receipt")
            if (content != _pretty_bytes(receipt) or receipt["format"] != RECEIPT_FORMAT
                    or receipt["registration_sha256"] != _hash(registration_raw) or receipt["batch_sha256"] != _hash(raw)
                    or not _instant(batch["recorded_at"]) <= _instant(receipt["accepted_at"]) <= _instant(_now())):
                raise ValueError("acceptance receipt bytes, binding or clock differs")
            retained[receipt_path] = content
        records.append({"batch": batch, "receipt": receipt, "path": path})
    return records, retained


def record(registration_path: str | Path, *, reference_root: str | Path) -> dict:
    root = Path(reference_root).resolve()
    registration, _ = validate_registration(registration_path, reference_root=root)
    study = _study(population.blobs._unblob(registration["study"]))
    with poll._lock(population._path(root, study["prediction_root"])):
        return _record(registration_path, reference_root=root)


def _record(registration_path: str | Path, *, reference_root: str | Path) -> dict:
    root = Path(reference_root).resolve()
    registration, raw = validate_registration(registration_path, reference_root=root)
    study = _study(population.blobs._unblob(registration["study"]))
    directory = population._path(root, study["prediction_root"])
    if (directory / "seal.json").exists():
        raise ValueError("sealed prediction population cannot accept new batches")
    if _instant(_now()) <= _instant(study["start"]):
        return {"status": "not_started", "start": study["start"], "network_requests": 0}
    if _instant(_now()) > _instant(study["end"]) + timedelta(seconds=study["prediction_deadline_seconds"]):
        return {"status": "window_closed_seal_required", "end": study["end"], "network_requests": 0}
    records, retained = _journal(directory, registration, raw, root)
    batch = predict(registration_path, reference_root=root)
    if batch["registration_sha256"] != _hash(raw):
        raise ValueError("registration changed between output protection and prediction")
    known = {prediction["input_id"] for record in records if record["receipt"]
             for prediction in record["batch"]["predictions"]}
    if any(_read(path) != content for path, content in retained.items()):
        raise ValueError("journal changed during prediction")
    if all(prediction["input_id"] in known for prediction in batch["predictions"]):
        return {"status": "no_new_opportunities", "known_inputs": len(known), "network_requests": 0}
    path = directory / (_hash(batch) + ".json")
    written = vintage.write_new(path, batch, reference_root=root,
        protected_directories=registration["inputs"]["protected_directories"])
    if validate_registration(registration_path, reference_root=root)[1] != raw:
        raise ValueError("registration changed before prediction acceptance; uncommitted batch retained")
    receipt = {"format": RECEIPT_FORMAT, "registration_sha256": _hash(raw),
               "batch_sha256": written["sha256"], "accepted_at": _now()}
    if _instant(receipt["accepted_at"]) < _instant(batch["recorded_at"]) or _hash(_read(path)) != written["sha256"]:
        raise ValueError("prediction durability clock or written bytes differ; uncommitted batch retained")
    acceptance = vintage.write_new(path.with_name(path.stem + ".receipt.json"), receipt, reference_root=root,
        protected_directories=registration["inputs"]["protected_directories"])
    return {"status": "recorded", **written, "receipt": acceptance,
        "counts": dict(sorted(Counter(row["result"] for row in batch["predictions"]).items()))}


def seal(registration_path: str | Path, *, reference_root: str | Path) -> dict:
    root, started = Path(reference_root).resolve(), _now()
    registration, raw = validate_registration(registration_path, reference_root=root)
    study = _study(population.blobs._unblob(registration["study"]))
    if _instant(started) < _instant(study["end"]) + timedelta(seconds=study["prediction_deadline_seconds"]):
        raise ValueError("seal requires the fixed window and prediction grace period to end")
    directory = population._path(root, study["prediction_root"])
    entries = sorted(directory.iterdir())
    if (directory / "seal.json").exists():
        raise ValueError("seal already exists; never overwrite")
    records, retained = _journal(directory, registration, raw, root)
    receipts = [{"path": record["path"].relative_to(root).as_posix(), "sha256": _hash(retained[record["path"]]),
        "receipt": record["receipt"]} for record in records]
    final = _freeze(registration, root, study["end"])
    result = {"format": SEAL_FORMAT, "rule_version": RULE_VERSION, "registration_sha256": _hash(raw),
        "started_at": started, "sealed_at": _now(), "batches": receipts, "final_population": final,
        **_reconcile(registration, final, records),
        "metrics": dict.fromkeys(("precision", "recall", "false_positive_burden", "publication_lag", "forecast_calibration")),
        "code_sha256": registration["code_sha256"], "boundaries": BOUNDARIES.copy()}
    _verify_population(final, root)
    _verify_current_population(final, root)
    if (sorted(directory.iterdir()) != entries or any(_read(path) != content for path, content in retained.items())
            or validate_registration(registration_path, reference_root=root)[1] != raw
            or not _instant(started) <= _instant(final["frozen_at"]) <= _instant(result["sealed_at"])):
        raise ValueError("seal inputs or clocks changed")
    return result


def _reconcile(registration: dict, final: dict, records: list) -> dict:
    study = _study(population.blobs._unblob(registration["study"]))
    batches = [record["batch"] for record in records]
    cases = statements._cases(final)
    reconciled = []
    for key, case in cases.items():
        input_id = _input_id(case)
        candidates = [(record["receipt"], prediction) for record in records if record["receipt"]
                      for prediction in record["batch"]["predictions"] if prediction["input_id"] == input_id]
        chosen = min(candidates, key=lambda item: _instant(item[0]["accepted_at"])) if candidates else None
        completed = case.get("assessment_at") or case.get("intent_started_at")
        lag = (_instant(chosen[0]["accepted_at"]) - _instant(completed)).total_seconds() if chosen else None
        reconciled.append({"case_id": key, "case": case, "prediction": chosen[1] if chosen else None,
            "in_protocol": _protocol(case, registration, final)[0],
            "collection_mode": _protocol(case, registration, final)[1],
            "opportunity_id": _hash({"capture_path": case["capture_path"], "document_id": case["document_id"]}),
            "first_recorded_at": chosen[0]["accepted_at"] if chosen else None,
            "assessment_to_prediction_seconds": lag,
            "recording_status": "missing" if chosen is None else "on_time" if 0 <= lag <= study["prediction_deadline_seconds"] else "late"})
    final_ids = set(cases)
    superseded = sorted({row["case_id"] for batch in batches for row in batch["predictions"]} - final_ids)
    return {"cases": reconciled, "superseded_interim_case_ids": superseded,
        "counts": {"final_document_opportunities": len(reconciled), "recorded_batches": len(batches),
            "uncommitted_batches": sum(record["receipt"] is None for record in records),
            "in_protocol_opportunities": sum(row["in_protocol"] for row in reconciled),
            "outside_protocol_opportunities": sum(not row["in_protocol"] for row in reconciled),
            "recording_statuses": dict(sorted(Counter(row["recording_status"] for row in reconciled).items())),
            "documents_without_checks": len({row["url"] for row in registration["inputs"]["documents"]}
                - {row["case"]["url"] for row in reconciled if row["in_protocol"]}), "superseded_interim_cases": len(superseded)}}


def validate_seal(registration_path: str | Path, *, reference_root: str | Path) -> dict:
    root = Path(reference_root).resolve()
    registration, registration_raw = validate_registration(registration_path, reference_root=root)
    study = _study(population.blobs._unblob(registration["study"]))
    directory = population._path(root, study["prediction_root"])
    path = directory / "seal.json"
    raw = binding._bounded_read(path)
    result = binding._keys(_strict_json(raw, "prediction seal"), {"format", "rule_version", "registration_sha256",
        "started_at", "sealed_at", "batches", "final_population", "cases", "superseded_interim_case_ids",
        "counts", "metrics", "code_sha256", "boundaries"}, "prediction seal")
    final = result["final_population"]
    expected_study = {**registration["inputs"]["baseline_study"], "study_id": study["study_id"] + "-observations",
                      "start": study["start"], "end": study["end"]}
    if (result["format"] != SEAL_FORMAT or result["rule_version"] != RULE_VERSION or raw != _pretty_bytes(result)
            or result["registration_sha256"] != _hash(registration_raw) or result["code_sha256"] != _code_hashes()
            or result["boundaries"] != BOUNDARIES or any(value is not False for value in result["boundaries"].values())
            or result["metrics"] != dict.fromkeys(("precision", "recall", "false_positive_burden", "publication_lag", "forecast_calibration"))
            or not _instant(study["end"]) + timedelta(seconds=study["prediction_deadline_seconds"]) <= _instant(result["started_at"])
                <= _instant(final["started_at"]) <= _instant(final["frozen_at"]) <= _instant(result["sealed_at"]) <= _instant(_now())
            or population._study(population.blobs._unblob(final["study"])) != expected_study):
        raise ValueError("sealed prediction contract, code or clocks differ")
    entries = sorted(directory.iterdir())
    records, retained = _journal(directory, registration, registration_raw, root, allow_seal=True)
    receipts = [{"path": record["path"].relative_to(root).as_posix(), "sha256": _hash(retained[record["path"]]),
                 "receipt": record["receipt"]} for record in records]
    if result["batches"] != receipts or any(result[key] != value for key, value in _reconcile(registration, final, records).items()):
        raise ValueError("sealed prediction population or reconciliation differs")
    _verify_population(final, root)
    _verify_current_opportunities(final, registration, root)
    if (entries != sorted(directory.iterdir()) or raw != _read(path) or any(_read(path) != value for path, value in retained.items())
            or registration_raw != validate_registration(registration_path, reference_root=root)[1]):
        raise ValueError("seal changed during replay")
    return {"seal_sha256": _hash(raw), "registration_sha256": _hash(registration_raw), "counts": result["counts"],
            "exact_source_and_prediction_replay": True, "current_in_window_population_matches": True,
            "retained_execution_errors_require_recurrence": False, "network_requests": 0}
