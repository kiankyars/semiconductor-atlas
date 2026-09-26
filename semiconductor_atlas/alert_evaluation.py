"""Frozen, offline alert-history diagnostics; never a claim of blind calibration."""

from __future__ import annotations

from pathlib import Path

from . import ai_critical_alert_review as ledger
from . import ai_critical, ai_critical_changes, ai_critical_change_claims, curated_capture, source_checks
from .ai_critical import load_baseline
from .ai_critical_changes import ALERT_RULE_IDS, _pretty_bytes, _strict_json
from .curated_capture import _write
from .source_checks import _read


FROZEN_FORMAT = "semiconductor-atlas-frozen-alert-evaluation-v1"
LABEL_FORMAT = "semiconductor-atlas-alert-evaluation-labels-v1"
REPORT_FORMAT = "semiconductor-atlas-alert-evaluation-report-v1"
RULE_VERSION = "alert-evaluation-diagnostics-v1"


def _code_hashes() -> dict:
    modules = (ledger, ai_critical, ai_critical_changes, ai_critical_change_claims,
               curated_capture, source_checks)
    return {"evaluator": ledger._hash(_read(Path(__file__))),
            **{module.__name__.rsplit(".", 1)[-1]: ledger._hash(_read(Path(module.__file__)))
               for module in modules}}


def _keys(value: object, fields: tuple[str, ...], context: str) -> dict:
    return ledger._keys(value, fields, context)


def _ratio(numerator: int, denominator: int) -> dict:
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None}


def _seconds(start: str, end: str) -> float:
    return (ledger._instant(end) - ledger._instant(start)).total_seconds()


def _retained_blob(raw: bytes) -> dict:
    if len(raw) > 20_000_000:
        raise ValueError("retained evaluation input exceeds the portable 20 MB file limit")
    return ledger._blob(raw)


def _cohort(raw: bytes, frozen_at: str) -> list[dict]:
    # Validate the actual baseline contract without requiring its private raw source corpus.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="atlas-evaluation-baseline-") as temporary:
        root = Path(temporary).resolve()
        path = root / "baseline.json"
        _write(path, raw)
        baseline = load_baseline(path, root, verify_source_bytes=False)
    if ledger._instant(baseline.spec["recorded_at"]) > ledger._instant(frozen_at):
        raise ValueError("cohort baseline contains future knowledge at freeze")
    return [{"facility_key": item["facility_key"], "company": item["company"],
             "country_code": item["geography"]["country_code"]} for item in baseline.facilities]


def _history(raw: bytes, end: str, frozen_at: str) -> dict:
    artifact = _keys(_strict_json(raw, "alert history"), ("format", "events"), "alert history")
    if artifact["format"] != ledger.EXPORT_FORMAT:
        raise ValueError("unsupported alert history")
    try:
        ledger._validate_events(artifact["events"])
        if any(ledger._instant(row["recorded_at"]) > ledger._instant(frozen_at) for row in artifact["events"]):
            raise ValueError("alert history contains future knowledge at freeze")
        ledger._fold(artifact["events"], full=True)
        selected = [row for row in artifact["events"] if ledger._instant(row["recorded_at"]) < ledger._instant(end)]
        return ledger._fold(selected, full=True)
    except (KeyError, TypeError, IndexError) as error:
        raise ValueError("malformed retained alert history") from error


def _prediction_rows(report: dict, cohort: list[dict], start: str, end: str) -> tuple[list[dict], list[dict]]:
    facilities = {row["facility_key"] for row in cohort}
    included, excluded = [], []
    for alert in report["alerts"]:
        reasons = []
        if alert["subject_stable_key"] not in facilities:
            reasons.append("outside_cohort")
        if not ledger._instant(start) <= ledger._instant(alert["first_recorded_at"]) < ledger._instant(end):
            reasons.append("admitted_before_window")
        if reasons:
            excluded.append({"alert_id": alert["id"], "reasons": reasons})
        else:
            included.append(alert)
    return included, excluded


def freeze_predictions(history_path: str | Path, baseline_path: str | Path,
                       output: str | Path, *, study_id: str, start: str, end: str) -> dict:
    """Seal all cohort episodes in a completed half-open admission window."""
    ledger._text(study_id, "study id")
    frozen_at = ledger._now()
    if not ledger._instant(start) < ledger._instant(end) <= ledger._instant(frozen_at):
        raise ValueError("freeze requires a completed, nonempty UTC window")
    history_raw, baseline_raw = _read(Path(history_path)), _read(Path(baseline_path))
    cohort = _cohort(baseline_raw, frozen_at)
    report = _history(history_raw, end, frozen_at)
    predictions, excluded = _prediction_rows(report, cohort, start, end)
    artifact = {
        "format": FROZEN_FORMAT, "rule_version": RULE_VERSION, "study_id": study_id,
        "start": start, "end": end, "window_semantics": "start_inclusive_end_exclusive",
        "frozen_at": frozen_at, "selection": "all_cohort_episodes_first_admitted_in_window",
        "design": "retrospective_diagnostic_not_preregistered",
        "baseline": _retained_blob(baseline_raw), "history": _retained_blob(history_raw),
        "cohort": cohort, "history_head_at_cutoff": report["head_event_id"],
        "predictions": predictions, "excluded": excluded,
        "code_sha256": _code_hashes(),
    }
    _write(Path(output), _pretty_bytes(artifact))
    return {"output": str(output), "sha256": ledger._hash(_pretty_bytes(artifact)),
            "frozen_at": frozen_at, "prediction_count": len(predictions), "cohort_count": len(cohort)}


def validate_frozen(path: str | Path) -> tuple[dict, bytes]:
    raw = _read(Path(path))
    artifact = _keys(_strict_json(raw, "frozen evaluation"), (
        "format", "rule_version", "study_id", "start", "end", "window_semantics", "frozen_at",
        "selection", "design", "baseline", "history", "cohort", "history_head_at_cutoff",
        "predictions", "excluded", "code_sha256"), "frozen evaluation")
    if (artifact["format"] != FROZEN_FORMAT or artifact["rule_version"] != RULE_VERSION
            or artifact["window_semantics"] != "start_inclusive_end_exclusive"
            or artifact["selection"] != "all_cohort_episodes_first_admitted_in_window"
            or artifact["design"] != "retrospective_diagnostic_not_preregistered"):
        raise ValueError("unsupported evaluation contract")
    ledger._text(artifact["study_id"], "study id")
    if not (ledger._instant(artifact["start"]) < ledger._instant(artifact["end"])
            <= ledger._instant(artifact["frozen_at"]) <= ledger._instant(ledger._now())):
        raise ValueError("invalid frozen evaluation clocks")
    if artifact["code_sha256"] != _code_hashes():
        raise ValueError("frozen evaluation requires its pinned evaluator and ledger versions")
    cohort = _cohort(ledger._unblob(artifact["baseline"]), artifact["frozen_at"])
    report = _history(ledger._unblob(artifact["history"]), artifact["end"], artifact["frozen_at"])
    predictions, excluded = _prediction_rows(report, cohort, artifact["start"], artifact["end"])
    if (_pretty_bytes([artifact["cohort"], artifact["predictions"], artifact["excluded"], artifact["history_head_at_cutoff"]])
            != _pretty_bytes([cohort, predictions, excluded, report["head_event_id"]])):
        raise ValueError("frozen predictions or cohort do not replay from the retained history")
    if raw != _pretty_bytes(artifact):
        raise ValueError("frozen evaluation must use exact canonical artifact bytes")
    return artifact, raw


def _labels(path: Path, raw: bytes, frozen: dict, frozen_raw: bytes) -> tuple[dict, dict, dict, list[dict]]:
    labels = _keys(_strict_json(raw, "evaluation labels"), (
        "format", "frozen_sha256", "adjudicator", "reviewed_at", "blinding",
        "truth_inventory_complete", "coverage_statement", "truth_events", "adjudications",
        "supporting_reviews"), "evaluation labels")
    if labels["format"] != LABEL_FORMAT or labels["frozen_sha256"] != ledger._hash(frozen_raw):
        raise ValueError("labels do not bind the frozen predictions")
    if not ledger._instant(frozen["frozen_at"]) < ledger._instant(labels["reviewed_at"]) <= ledger._instant(ledger._now()):
        raise ValueError("label review must follow freeze and cannot be in the future")
    for name in ("adjudicator", "coverage_statement"):
        ledger._text(labels[name], name)
    if labels["blinding"] not in ("not_blinded", "reviewer_declares_blinded"):
        raise ValueError("invalid blinding declaration")
    if type(labels["truth_inventory_complete"]) is not bool:
        raise ValueError("truth completeness must be explicitly declared")
    for field in ("truth_events", "adjudications", "supporting_reviews"):
        if not isinstance(labels[field], list) or len(labels[field]) > 10000:
            raise ValueError(f"{field} must be a bounded array")
    reviews = []
    review_hashes = set()
    for item in labels["supporting_reviews"]:
        _keys(item, ("path", "sha256"), "supporting label review")
        digest = ledger._text(item["sha256"], "review SHA-256")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("supporting review must bind lowercase SHA-256")
        target = Path(ledger._text(item["path"], "review path"))
        raw = _read(target if target.is_absolute() else path.parent / target)
        if ledger._hash(raw) != item["sha256"] or item["sha256"] in review_hashes:
            raise ValueError("supporting label review mismatch or duplicate")
        review_hashes.add(item["sha256"])
        reviews.append({"reference": item, "content": _retained_blob(raw)})
    facilities = {row["facility_key"] for row in frozen["cohort"]}
    truths = {}
    for truth in labels["truth_events"]:
        _keys(truth, ("id", "facility_key", "rule_id", "available_at", "deadline_at", "review_sha256", "locator"), "truth event")
        identifier = ledger._text(truth["id"], "truth id")
        for field in ("locator", "facility_key", "rule_id", "review_sha256"):
            ledger._text(truth[field], f"truth {field}")
        if identifier in truths or truth["facility_key"] not in facilities or truth["review_sha256"] not in review_hashes:
            raise ValueError("duplicate or unsupported truth event")
        if truth["rule_id"] not in ALERT_RULE_IDS:
            raise ValueError("unsupported truth rule")
        if not (ledger._instant(truth["available_at"]) < ledger._instant(frozen["end"])
                and ledger._instant(truth["available_at"]) <= ledger._instant(truth["deadline_at"])):
            raise ValueError("truth observability or deadline is outside the evaluation contract")
        truths[identifier] = truth
    predictions = {row["id"]: row for row in frozen["predictions"]}
    judgments = {}
    for item in labels["adjudications"]:
        _keys(item, ("alert_id", "verdict", "truth_event_id", "reason", "review_sha256"), "adjudication")
        identifier = ledger._text(item["alert_id"], "adjudication alert id")
        for field in ("reason", "verdict", "review_sha256"):
            ledger._text(item[field], f"adjudication {field}")
        if identifier not in predictions or identifier in judgments or item["review_sha256"] not in review_hashes:
            raise ValueError("unknown, duplicate or unsupported adjudication")
        if item["verdict"] not in {"supported", "false_positive", "unresolved"}:
            raise ValueError("invalid adjudication verdict")
        if item["verdict"] == "supported":
            ledger._text(item["truth_event_id"], "matched truth id")
            truth = truths.get(item["truth_event_id"])
            prediction = predictions[identifier]
            if (truth is None or truth["facility_key"] != prediction["subject_stable_key"]
                    or truth["rule_id"] != prediction["rule_id"]
                    or ledger._instant(prediction["first_recorded_at"]) < ledger._instant(truth["available_at"])):
                raise ValueError("supported match has incompatible scope or precedes source observability")
        elif item["truth_event_id"] is not None:
            raise ValueError("unsupported adjudication cannot match a truth event")
        judgments[identifier] = item
    return labels, truths, judgments, reviews


def evaluate(frozen_path: str | Path, labels_path: str | Path) -> dict:
    frozen, frozen_raw = validate_frozen(frozen_path)
    labels_path = Path(labels_path).absolute()
    label_raw = _read(labels_path)
    labels, truths, judgments, reviews = _labels(labels_path, label_raw, frozen, frozen_raw)
    if _read(labels_path) != label_raw:
        raise ValueError("evaluation labels changed during scoring")
    counts = {"supported": 0, "false_positive": 0, "unresolved": 0, "unlabelled": 0}
    matches: dict[str, list[dict]] = {key: [] for key in truths}
    rows = []
    for alert in frozen["predictions"]:
        judgment = judgments.get(alert["id"])
        verdict = judgment["verdict"] if judgment else "unlabelled"
        counts[verdict] += 1
        truth = truths.get(judgment["truth_event_id"]) if judgment else None
        timely = truth is not None and ledger._instant(alert["first_recorded_at"]) <= ledger._instant(truth["deadline_at"])
        retractions = [item for item in alert["decisions"] if item["action"] == "retract"]
        row = {"alert_id": alert["id"], "facility_key": alert["subject_stable_key"], "rule_id": alert["rule_id"],
               "first_admitted_at": alert["first_recorded_at"], "verdict": verdict,
               "truth_event_id": truth["id"] if truth else None, "timely": timely if truth else None,
               "source_to_admission_seconds": _seconds(truth["available_at"], alert["first_recorded_at"]) if truth else None,
               "status_at_cutoff": alert["status"], "retraction_count": len(retractions),
               "first_admission_to_retraction_seconds": _seconds(alert["first_recorded_at"], retractions[0]["recorded_at"]) if retractions else None}
        rows.append(row)
        if truth:
            matches[truth["id"]].append(row)
    outcomes = []
    for truth in truths.values():
        detected = matches[truth["id"]]
        in_window = ledger._instant(frozen["start"]) <= ledger._instant(truth["available_at"])
        mature = ledger._instant(truth["deadline_at"]) < ledger._instant(frozen["end"])
        timely = any(row["timely"] for row in detected)
        potential = [row for row in rows if row["verdict"] in {"unresolved", "unlabelled"}
                     and row["facility_key"] == truth["facility_key"] and row["rule_id"] == truth["rule_id"]
                     and ledger._instant(truth["available_at"]) <= ledger._instant(row["first_admitted_at"])
                     <= ledger._instant(truth["deadline_at"])]
        status = ("prior_window_event" if not in_window else "right_censored" if not mature
                  else "timely_detected" if timely else "unresolved_matching" if potential else "missed_deadline")
        outcomes.append({"truth_event_id": truth["id"], "facility_key": truth["facility_key"],
                         "matched_alert_count": len(detected), "timely_detected": timely,
                         "possible_timely_match_count": len(potential), "recall_eligible": in_window and mature,
                         "status": status})
    supported, false = counts["supported"], counts["false_positive"]
    unresolved = counts["unresolved"] + counts["unlabelled"]
    total = len(rows)
    mature_outcomes = [row for row in outcomes if row["recall_eligible"]]
    recalled = sum(row["timely_detected"] for row in mature_outcomes)
    possible_recalled = sum(row["timely_detected"] or row["possible_timely_match_count"] > 0 for row in mature_outcomes)
    strata = [{**facility, "admitted_alert_count": sum(row["facility_key"] == facility["facility_key"] for row in rows),
               "labelled_truth_count": sum(row["facility_key"] == facility["facility_key"] for row in truths.values()),
               "monitoring_completeness": "not_established_by_alert_ledger"} for facility in frozen["cohort"]]
    return {"format": REPORT_FORMAT, "rule_version": RULE_VERSION, "study_id": frozen["study_id"],
            "frozen_sha256": ledger._hash(frozen_raw), "labels_sha256": ledger._hash(label_raw),
            "start": frozen["start"], "end": frozen["end"], "frozen_at": frozen["frozen_at"],
            "adjudicator": labels["adjudicator"], "reviewed_at": labels["reviewed_at"],
            "blinding_declaration": labels["blinding"], "independence_verified": False,
            "design": frozen["design"], "truth_inventory_complete_declared": labels["truth_inventory_complete"],
            "coverage_statement": labels["coverage_statement"], "cohort": strata,
            "counts": {**counts, "predictions": total, "truth_events": len(truths),
                       "mature_truth_events": len(mature_outcomes), "right_censored": sum(row["status"] == "right_censored" for row in outcomes),
                       "prior_window_truth_events": sum(row["status"] == "prior_window_event" for row in outcomes),
                       "unique_supported_truth_events": sum(bool(value) for value in matches.values()),
                       "duplicate_supported_matches": sum(max(len(value) - 1, 0) for value in matches.values())},
            "metrics": {"assessed_episode_precision": _ratio(supported, supported + false),
                        "precision_bounds_with_unresolved": {"lower": _ratio(supported, total), "upper": _ratio(supported + unresolved, total)},
                        "confirmed_false_positive_review_burden": _ratio(false, total),
                        "false_positive_review_burden_bounds": {"lower": _ratio(false, total), "upper": _ratio(false + unresolved, total)},
                        "timely_event_recall": _ratio(recalled, len(mature_outcomes)) if labels["truth_inventory_complete"] and not unresolved else None,
                        "timely_event_recall_bounds": {"lower": _ratio(recalled, len(mature_outcomes)),
                                                       "upper": _ratio(possible_recalled, len(mature_outcomes))} if labels["truth_inventory_complete"] else None,
                        "false_positive_rate_over_no_change_opportunities": None,
                        "retracted_episodes": sum(row["retraction_count"] > 0 for row in rows),
                        "false_positive_episodes_retracted": sum(row["verdict"] == "false_positive" and row["retraction_count"] > 0 for row in rows),
                        "retraction_latency": None},
            "predictions": rows, "truth_outcomes": outcomes, "supporting_reviews": reviews,
            "phase3_gate_passed": False, "delivery_eligible": False,
            "limitations": ["Retrospective selection is not a preregistered or independently verified blind evaluation.",
                            "Window, cohort and deadlines can be chosen after seeing outcomes; freeze clocks and hashes are not external attestations.",
                            "The retained history may contain later events, but cutoff reconstruction excludes them from predictions and decisions.",
                            "Reviewer labels and claimed source observability are evidence-linked judgments, not machine-proven truth.",
                            "Missing or unresolved judgments remain in the prediction denominator and uncertainty bounds.",
                            "Unmonitored facilities and absent truth labels do not mean no real events occurred.",
                            "Conditional precision does not establish population accuracy, calibrated confidence or early detection.",
                            "Point recall requires declared complete truth inventory and resolved prediction labels; source coverage completeness remains unverified.",
                            "Older source events may support in-window backfills but are excluded from the in-window recall denominator.",
                            "Cohort selection is validated as of freeze, not proven knowable at the start of the retrospective window.",
                            "Raw labels, frozen artifact and referenced review files must accompany a report for replay; embedding reviews does not grant redistribution rights.",
                            "Retraction counts describe recorded actions, not correct or timely retraction behavior."]}
