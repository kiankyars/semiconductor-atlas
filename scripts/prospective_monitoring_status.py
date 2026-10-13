"""Read-only operational projection, run against the registered source runtime."""

from collections import Counter
import json
from pathlib import Path
import sys

from semiconductor_atlas import prospective_target_review as shadow


FORMAT = "semiconductor-atlas-prospective-monitoring-status-v2"
BOUNDARIES = {
    "final_population": False, "source_only_blinded_packet": False,
    "scheduler_trigger_authenticated": False, "source_acquisition": False,
    "prediction_or_label_writes": False, "policy_approval_or_repin": False,
    "manufacturing_truth_established": False, "alert_accuracy_established": False,
    "forecast_calibration_established": False, "absence_inference_allowed": False,
    "atomic_filesystem_snapshot": False,
    "continuous_monitoring_established": False, "missed_scheduled_runs_established": False,
    "source_content_freshness_established": False,
}


def ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None}


def summarize(rows):
    comparable = [row for row in rows if row["collector_comparable"]]
    timely = lambda row: row["recording_status"] == "on_time" and row["recorded_result"] in {
        "revision_candidate", "no_candidate"}
    return {
        "opportunities": len(rows), "collector_comparable_opportunities": len(comparable),
        "collector_uncomparable_opportunities": len(rows) - len(comparable),
        "recording_statuses": dict(sorted(Counter(row["recording_status"] for row in rows).items())),
        "recorded_results": dict(sorted(Counter(row["recorded_result"] for row in rows).items())),
        "collection_statuses": dict(sorted(Counter(row["collection_status"] for row in rows).items())),
        "timely_recording_coverage": ratio(sum(row["recording_status"] == "on_time" for row in rows), len(rows)),
        "timely_decision_coverage_all_opportunities": ratio(sum(map(timely, rows)), len(rows)),
        "timely_decision_coverage_comparable_opportunities": ratio(sum(map(timely, comparable)), len(comparable)),
    }


def activity(rows, *, study_start, observation_cutoff):
    start, cutoff = shadow._instant(study_start), shadow._instant(observation_cutoff)
    if not start < cutoff:
        raise ValueError("activity requires a positive observation window")
    completed = [row for row in rows if row["case_kind"] == "document_check"]
    attempted = [row for row in completed if row["source_request_attempted"]]

    def latest(items, field):
        if not items:
            return {"at": None, "age_seconds": None, "case_ids": [],
                    "collection_statuses": {}, "transport_outcomes": {}}
        clock = max(shadow._instant(row[field]) for row in items)
        tied = sorted((row for row in items if shadow._instant(row[field]) == clock),
                      key=lambda row: row["case_id"])
        return {"at": tied[0][field], "age_seconds": (cutoff - clock).total_seconds(),
                "case_ids": [row["case_id"] for row in tied],
                "collection_statuses": dict(sorted(Counter(row["collection_status"] for row in tied).items())),
                "transport_outcomes": dict(sorted(Counter(row["transport_outcome"] or "not_attempted"
                                                          for row in tied).items()))}

    assessment = latest(completed, "assessment_at")
    clocks = [start, *sorted({shadow._instant(row["assessment_at"]) for row in completed}), cutoff]
    return {"completed_assessments": len(completed),
        "source_attempts_in_completed_assessments": len(attempted),
        "last_completed_assessment": assessment,
        "last_source_attempt": latest(attempted, "source_attempt_finished_at"),
        "assessment_gap_to_cutoff_seconds": (cutoff - clocks[-2]).total_seconds(),
        "assessment_gap_basis": "last_completed_assessment" if completed else "study_start_no_completed_assessment",
        "largest_assessment_gap_seconds": max((right - left).total_seconds()
                                              for left, right in zip(clocks, clocks[1:]))}


def project(registration, reconciled, *, study_start, observation_cutoff):
    start, cutoff = shadow._instant(study_start), shadow._instant(observation_cutoff)
    if not start < cutoff:
        raise ValueError("activity requires a positive observation window")
    rows = []
    for item in reconciled["cases"]:
        case, prediction = item["case"], item["prediction"]
        if case["kind"] == "document_check":
            assessment, attempted = case["assessment_at"], case["attempted"]
            attempt_finished, outcome = case["response_finished_at"], case["transport_outcome"]
            if (not start <= shadow._instant(assessment) < cutoff or type(attempted) is not bool
                    or (attempted and (outcome not in {"succeeded", "failed"} or attempt_finished is None
                                       or shadow._instant(attempt_finished) > shadow._instant(assessment)))
                    or (not attempted and (attempt_finished is not None or outcome is not None))):
                raise ValueError("inconsistent completed assessment activity")
            intent = None
        elif case["kind"] == "incomplete_document":
            assessment = attempted = attempt_finished = outcome = None
            intent = case["intent_started_at"]
            if not start <= shadow._instant(intent) < cutoff:
                raise ValueError("incomplete intent is outside the observation window")
        else:
            raise ValueError("unknown monitoring case kind")
        rows.append({"case_id": item["case_id"], "opportunity_id": item["opportunity_id"],
            "url": case["url"], "document_id": case["document_id"],
            "capture_path": case["capture_path"], "case_kind": case["kind"],
            "assessment_at": assessment, "intent_started_at": intent,
            "source_request_attempted": attempted, "source_attempt_finished_at": attempt_finished,
            "transport_outcome": outcome,
            "collection_status": case.get("status", "incomplete_document"),
            "collector_comparable": case.get("comparison_eligible") is True,
            "in_protocol": item["in_protocol"], "collection_mode": item["collection_mode"],
            "recording_status": item["recording_status"], "first_recorded_at": item["first_recorded_at"],
            "assessment_to_prediction_seconds": item["assessment_to_prediction_seconds"],
            "recorded_result": prediction["result"] if prediction else "missing",
            "recorded_reason": ("detector_error" if prediction["result"] == "error" else prediction["reason"])
                               if prediction else None})
    rows.sort(key=lambda row: row["case_id"])
    documents = []
    support = {row["url"]: row["route_implemented"] for row in registration["inputs"]["parser_support"]}
    for document in registration["inputs"]["documents"]:
        scoped = [row for row in rows if row["in_protocol"] and row["url"] == document["url"]]
        documents.append({"url": document["url"], "document_id": document["document_id"],
            "facility_key": document["facility_key"], "parser_route_implemented": support[document["url"]],
            "has_in_protocol_opportunity": bool(scoped),
            "has_completed_collector_assessment": any(row["case_kind"] == "document_check" for row in scoped),
            "activity": activity(scoped, study_start=study_start, observation_cutoff=observation_cutoff),
            **summarize(scoped)})
    return {"cases": rows, "registered_documents": documents,
        "activity_basis": {"population": "in_protocol_assessments_completed_in_observation_window",
            "assessment_clock": "retained_capture_finished_at",
            "source_attempt_clock": "source_document_attempt_finished_at_in_completed_capture",
            "gaps_include_window_boundaries": True,
            "source_attempts_may_predate_window": True,
            "incomplete_intents_do_not_establish_request_execution": True},
        "unmonitored_facilities": registration["inputs"]["unmonitored_facilities"],
        "counts": {"registered_documents": len(documents),
            "unmonitored_facilities": len(registration["inputs"]["unmonitored_facilities"]),
            "interim_document_opportunities": len(rows),
            "registered_documents_without_in_protocol_opportunities": sum(not row["has_in_protocol_opportunity"] for row in documents),
            "registered_documents_without_completed_collector_assessments": sum(not row["has_completed_collector_assessment"] for row in documents),
            "registered_documents_without_source_attempts_in_completed_assessments": sum(
                row["activity"]["source_attempts_in_completed_assessments"] == 0 for row in documents),
            "superseded_interim_cases": len(reconciled["superseded_interim_case_ids"])},
        "in_protocol": summarize([row for row in rows if row["in_protocol"]]),
        "outside_protocol": summarize([row for row in rows if not row["in_protocol"]])}


def audit(registration_path, reference_root):
    root, path = Path(reference_root).resolve(), Path(registration_path).absolute()
    started = shadow._now()
    registration, raw = shadow.validate_registration(path, reference_root=root)
    receipt_path = shadow._registration_receipt_path(path)
    receipt_raw = shadow._read(receipt_path)
    study = shadow._study(shadow.population.blobs._unblob(registration["study"]))
    if shadow._instant(started) <= shadow._instant(study["start"]):
        raise ValueError("interim audit requires the registered window to have started")
    cutoff = min(started, study["end"], key=shadow._instant)
    directory = shadow.population._path(root, study["prediction_root"])
    entries = sorted(directory.iterdir())
    records, retained = shadow._journal(directory, registration, raw, root)
    for record in records:
        clocks = [record["batch"]["recorded_at"]]
        if record["receipt"]:
            clocks.append(record["receipt"]["accepted_at"])
        if any(shadow._instant(clock) > shadow._instant(started) for clock in clocks):
            raise ValueError("prediction journal crossed the audit start; rerun the read-only audit")
    frozen = shadow._freeze(registration, root, cutoff)
    result = project(registration, shadow._reconcile(registration, frozen, records),
                     study_start=study["start"], observation_cutoff=cutoff)
    shadow._verify_current_population(frozen, root)
    if (entries != sorted(directory.iterdir())
            or any(shadow._read(name) != content for name, content in retained.items())
            or shadow.validate_registration(path, reference_root=root)[1] != raw
            or shadow._read(receipt_path) != receipt_raw):
        raise ValueError("interim audit inputs changed; preserve records and rerun")
    finished = shadow._now()
    if not (shadow._instant(started) <= shadow._instant(frozen["started_at"])
            <= shadow._instant(frozen["frozen_at"]) <= shadow._instant(finished)):
        raise ValueError("interim audit clock moved backwards")
    inputs = {str(name): {"bytes": len(content), "sha256": shadow._hash(content)}
              for name, content in {path: raw, receipt_path: receipt_raw, **retained}.items()}
    protected = [*registration["inputs"]["protected_directories"], study["prediction_root"]]
    return {"format": FORMAT,
        "status": "open_window" if shadow._instant(started) < shadow._instant(study["end"]) else "closed_window_unsealed",
        "audit_started_at": started, "observation_cutoff": cutoff, "audited_at": finished,
        "study_start": study["start"], "study_end": study["end"],
        "prediction_deadline_seconds": study["prediction_deadline_seconds"],
        "registration_sha256": shadow._hash(raw), "registered_code_sha256": registration["code_sha256"],
        "source_snapshot_sha256": shadow._hash(frozen["snapshot"]),
        "source_snapshot_files": frozen["snapshot"]["files"],
        "journal_and_registration_files": inputs,
        "protected_directories": [str(shadow.population._path(root, name)) for name in protected],
        "protected_files": [str(shadow.population._path(root, name)) for name in registration["inputs"]["files"]],
        "batches": {"accepted": sum(record["receipt"] is not None for record in records),
                    "unaccepted": sum(record["receipt"] is None for record in records)},
        **result, "boundaries": BOUNDARIES.copy(),
        "unscored": dict.fromkeys(("precision", "recall", "false_positive_burden",
                                  "publication_detection_lag", "forecast_calibration"))}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("expected registration and reference-root absolute paths")
    print(json.dumps(audit(sys.argv[1], sys.argv[2]), sort_keys=True, indent=2))
