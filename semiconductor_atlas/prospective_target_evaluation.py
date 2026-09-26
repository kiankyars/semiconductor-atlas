"""Source-only review packets and evidence-conditional document-triage diagnostics."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from . import prospective_target_review as shadow, source_statement_review as statements
from . import source_vintage_review as vintage, discovery_handoff as binding
from .ai_critical_changes import _pretty_bytes, _strict_json
from .source_checks import _read


POLICY_FORMAT = "semiconductor-atlas-prospective-target-evaluation-policy-v1"
RECEIPT_FORMAT = "semiconductor-atlas-prospective-target-evaluation-policy-receipt-v1"
PACKET_FORMAT = "semiconductor-atlas-prospective-target-source-only-packet-v1"
PACKET_RECEIPT_FORMAT = "semiconductor-atlas-prospective-target-source-only-packet-receipt-v1"
LABEL_FORMAT = "semiconductor-atlas-prospective-target-outcome-labels-v1"
REPORT_FORMAT = "semiconductor-atlas-prospective-target-evaluation-report-v1"
RULE_VERSION = "all-final-opportunities-source-only-document-triage-v1"
CONTRACT = {
    "unit": "final_in_protocol_collector_comparable_document_opportunity",
    "positive": "at_least_one_evidence_supported_aligned_scoped_calendar_target_revision",
    "negative": "reviewer_asserts_complete_two_sided_scope_review_with_no_revisions_or_no_relevant_target",
    "unknown": "missing_partial_unresolved_or_ambiguous_review_is_not_negative",
    "timely_positive": "on_time_revision_candidate_at_first_accepted_prediction",
    "timely_sensitivity_denominator": "all_resolved_positive_opportunities_including_abstain_error_late_missing",
    "workload_burden": "all_recorded_candidates_including_late_with_separate_timely_only_accounting",
    "repetition": "retain_all_actual_checks_and_report_exact_source_pair_repetition_without_event_deduplication",
    "target_scope": shadow.TARGET_SCOPE,
}
BOUNDARIES = {**shadow.BOUNDARIES, "reviewer_identity_authenticated": False,
    "independent_adjudication_verified": False, "reviewer_blinding_verified": False,
    "exact_subject_detection_accuracy": False, "generalization_established": False}
_now, _instant, _hash = shadow._now, shadow._instant, shadow._hash


def _code_hashes() -> dict:
    root = Path(__file__).parent.parent
    paths = (Path(__file__), root / "scripts/evaluate_prospective_source_targets.py")
    return {**shadow._code_hashes(), **{path.relative_to(root).as_posix(): _hash(_read(path)) for path in paths}}


def _read_json(path: Path, fields: set[str], context: str) -> tuple[dict, bytes]:
    raw = binding._bounded_read(path)
    data = binding._keys(_strict_json(raw, context), fields, context)
    if raw != _pretty_bytes(data):
        raise ValueError(f"{context} must use canonical exact bytes")
    return data, raw


def _receipt_path(path: Path) -> Path:
    return path.with_name(path.name + ".receipt.json")


def _registration(path: Path, root: Path) -> tuple[dict, bytes, dict, Path]:
    registration, raw = shadow.validate_registration(path, reference_root=root)
    study = shadow._study(shadow.population.blobs._unblob(registration["study"]))
    directory = shadow.population._path(root, study["prediction_root"])
    return registration, raw, study, directory


def _protection(registration: dict, study: dict) -> list[str]:
    return [*registration["inputs"]["protected_directories"], study["prediction_root"]]


def register_policy(registration_path: str | Path, output: str | Path, *, reference_root: str | Path) -> dict:
    root, path = Path(reference_root).resolve(), Path(output).absolute()
    started, codes = _now(), _code_hashes()
    registration, raw, study, directory = _registration(Path(registration_path), root)
    if not _instant(registration["registered_at"]) <= _instant(started) < _instant(study["start"]):
        raise ValueError("evaluation policy must be registered before the observation window")
    policy = {"format": POLICY_FORMAT, "rule_version": RULE_VERSION, "registration_sha256": _hash(raw),
        "started_at": started, "prepared_at": _now(), "contract": CONTRACT.copy(),
        "code_sha256": codes, "boundaries": BOUNDARIES.copy()}
    protection = _protection(registration, study)
    with shadow.poll._lock(directory):
        if _receipt_path(path).exists():
            raise ValueError("evaluation policy receipt already exists")
        written = vintage.write_new(path, policy, reference_root=root, protected_directories=protection)
        if (_code_hashes() != codes or shadow.validate_registration(registration_path, reference_root=root)[1] != raw
                or _read(path) != _pretty_bytes(policy)):
            raise ValueError("policy inputs changed before acceptance; unaccepted draft retained")
        accepted = _now()
        if not _instant(started) <= _instant(policy["prepared_at"]) <= _instant(accepted) < _instant(study["start"]):
            raise ValueError("evaluation policy acceptance crossed the start or clock moved backwards")
        receipt = {"format": RECEIPT_FORMAT, "policy_sha256": written["sha256"], "accepted_at": accepted}
        receipt_result = vintage.write_new(_receipt_path(path), receipt, reference_root=root, protected_directories=protection)
    return {**written, "receipt": receipt_result, "accepted_at": accepted}


def validate_policy(registration_path: str | Path, policy_path: str | Path, *, reference_root: str | Path) -> dict:
    root, path = Path(reference_root).resolve(), Path(policy_path).absolute()
    registration, registration_raw, study, directory = _registration(Path(registration_path), root)
    policy, raw = _read_json(path, {"format", "rule_version", "registration_sha256", "started_at",
        "prepared_at", "contract", "code_sha256", "boundaries"}, "evaluation policy")
    receipt_path = _receipt_path(path)
    receipt, receipt_raw = _read_json(receipt_path, {"format", "policy_sha256", "accepted_at"}, "evaluation policy receipt")
    if (policy["format"] != POLICY_FORMAT or policy["rule_version"] != RULE_VERSION
            or policy["registration_sha256"] != _hash(registration_raw) or policy["contract"] != CONTRACT
            or policy["code_sha256"] != _code_hashes() or policy["boundaries"] != BOUNDARIES
            or any(value is not False for value in policy["boundaries"].values())
            or receipt["format"] != RECEIPT_FORMAT or receipt["policy_sha256"] != _hash(raw)
            or not _instant(registration["registered_at"]) <= _instant(policy["started_at"]) <= _instant(policy["prepared_at"])
                <= _instant(receipt["accepted_at"]) < _instant(study["start"])
            or _instant(receipt["accepted_at"]) > _instant(_now())
            or _read(path) != raw or _read(receipt_path) != receipt_raw):
        raise ValueError("evaluation policy, receipt, code or clocks differ")
    return {"policy": policy, "policy_sha256": _hash(raw), "registration": registration,
        "registration_raw": registration_raw, "study": study, "directory": directory,
        "inputs": {path: raw, receipt_path: receipt_raw}}


def _sealed(registration_path: Path, root: Path, context: dict) -> tuple[dict, bytes, Path]:
    path = context["directory"] / "seal.json"
    raw = binding._bounded_read(path)
    verified = shadow.validate_seal(registration_path, reference_root=root)
    if verified["seal_sha256"] != _hash(raw):
        raise ValueError("seal changed during source-only evaluation")
    return _strict_json(raw, "shadow seal"), raw, path


def _source_payload(seal: dict, root: Path) -> tuple[dict, dict]:
    frozen, bodies, cases, inputs = seal["final_population"], {}, [], {}
    for case in statements._cases(frozen).values():
        sides = {}
        for side in ("before", "after"):
            source = case.get("predecessor") if side == "before" else case
            if not source or not source.get("body_path") or not source.get("body_sha256"):
                sides[side] = None
                continue
            body, reference = statements._body(case, side, root, frozen, inputs)
            bodies[reference["body_sha256"]] = shadow.population.blobs._blob(body)
            sides[side] = {"body_sha256": reference["body_sha256"],
                "response_finished_at": source.get("response_finished_at")}
        cases.append({"case_id": case["case_id"], "kind": case["kind"], "url": case["url"],
            "scope": case["scope"], "document_id": case["document_id"],
            "assessment_at": case.get("assessment_at"), "intent_started_at": case.get("intent_started_at"),
            "comparison_available": case["comparison_eligible"],
            "unavailable_reason": None if case["comparison_eligible"] else case["status"], **sides})
    return {"cases": cases, "bodies": dict(sorted(bodies.items()))}, inputs


def _packet_scope(context: dict) -> dict:
    return {"study": {key: context["study"][key] for key in ("study_id", "start", "end")},
        "selection_rule": "all_final_document_opportunities_no_prediction_filter",
        "registered_documents": [{key: document[key] for key in ("document_id", "url", "scope", "company", "country_code", "source_family")}
                                 for document in context["registration"]["inputs"]["documents"]]}


def _unchanged(context: dict, registration_path: Path, policy_path: Path, root: Path, inputs: dict) -> None:
    if any(_read(path) != raw for path, raw in {**context["inputs"], **inputs}.items()):
        raise ValueError("evaluation inputs changed")
    current = validate_policy(registration_path, policy_path, reference_root=root)
    if (current["policy_sha256"] != context["policy_sha256"]
            or current["registration_raw"] != context["registration_raw"]):
        raise ValueError("registered evaluation inputs changed")


def make_packet(registration_path: str | Path, policy_path: str | Path, *, reference_root: str | Path) -> dict:
    root, registration_path, policy_path = Path(reference_root).resolve(), Path(registration_path), Path(policy_path)
    started = _now()
    context = validate_policy(registration_path, policy_path, reference_root=root)
    seal, seal_raw, seal_path = _sealed(registration_path, root, context)
    payload, inputs = _source_payload(seal, root)
    finished = _now()
    if not _instant(seal["sealed_at"]) <= _instant(started) <= _instant(finished):
        raise ValueError("source-only packet must follow the seal")
    result = {"format": PACKET_FORMAT, "rule_version": RULE_VERSION,
        "registration_sha256": _hash(context["registration_raw"]), "policy_sha256": context["policy_sha256"],
        "seal_sha256": _hash(seal_raw), "started_at": started, "packaged_at": finished,
        "target_scope": shadow.TARGET_SCOPE, **_packet_scope(context), **payload,
        "boundaries": {"raw_redistribution": False, "reviewer_blinding_verified": False,
                       "independent_adjudication_verified": False}}
    if len(_pretty_bytes(result)) > shadow.population.MAX_BYTES:
        raise ValueError("complete source-only packet exceeds 20 MB; never truncate")
    if _sealed(registration_path, root, context)[1] != seal_raw:
        raise ValueError("seal changed across source-only packet creation")
    _unchanged(context, registration_path, policy_path, root, {seal_path: seal_raw, **inputs})
    return result


def accept_packet(registration_path: str | Path, policy_path: str | Path, output: str | Path,
                  *, reference_root: str | Path) -> dict:
    root, path = Path(reference_root).resolve(), Path(output).absolute()
    registration_path, policy_path = Path(registration_path), Path(policy_path)
    context = validate_policy(registration_path, policy_path, reference_root=root)
    if _receipt_path(path).exists():
        raise ValueError("source-only packet acceptance receipt already exists")
    packet = make_packet(registration_path, policy_path, reference_root=root)
    protection = _protection(context["registration"], context["study"])
    written = vintage.write_new(path, packet, reference_root=root, protected_directories=protection)
    _unchanged(context, registration_path, policy_path, root, {path: _pretty_bytes(packet)})
    if packet["seal_sha256"] != _hash(_sealed(registration_path, root, context)[1]):
        raise ValueError("seal changed before packet acceptance")
    accepted = _now()
    if _instant(accepted) < _instant(packet["packaged_at"]):
        raise ValueError("packet acceptance clock moved backwards; unaccepted draft retained")
    receipt = {"format": PACKET_RECEIPT_FORMAT, "packet_sha256": written["sha256"], "accepted_at": accepted}
    saved = vintage.write_new(_receipt_path(path), receipt, reference_root=root, protected_directories=protection)
    return {**written, "receipt": saved, "accepted_at": accepted}


def prepare_review(registration_path: str | Path, policy_path: str | Path, output: str | Path,
                   *, reference_root: str | Path) -> dict:
    context = validate_policy(registration_path, policy_path, reference_root=reference_root)
    if not (context["directory"] / "seal.json").exists():
        return {"status": "awaiting_sealed_population", "network_requests": 0, "labels_created": 0}
    if Path(output).exists():
        packet = validate_packet(registration_path, policy_path, output, reference_root=reference_root)
        return {"status": "source_only_packet_verified", "packet_sha256": _hash(packet["packet_raw"]),
                "cases": len(packet["packet"]["cases"]), "network_requests": 0, "labels_created": 0}
    return {"status": "source_only_packet_created", **accept_packet(registration_path, policy_path, output,
        reference_root=reference_root), "network_requests": 0, "labels_created": 0}


def validate_packet(registration_path: str | Path, policy_path: str | Path, packet_path: str | Path,
                    *, reference_root: str | Path) -> dict:
    root, registration_path, policy_path, packet_path = map(Path, (reference_root, registration_path, policy_path, packet_path))
    root = root.resolve()
    context = validate_policy(registration_path, policy_path, reference_root=root)
    seal, seal_raw, seal_path = _sealed(registration_path, root, context)
    packet, raw = _read_json(packet_path, {"format", "rule_version", "registration_sha256", "policy_sha256",
        "seal_sha256", "started_at", "packaged_at", "target_scope", "study", "selection_rule", "registered_documents", "cases", "bodies", "boundaries"}, "source-only packet")
    receipt_path = _receipt_path(packet_path)
    receipt, receipt_raw = _read_json(receipt_path, {"format", "packet_sha256", "accepted_at"}, "source-only packet receipt")
    payload, inputs = _source_payload(seal, root)
    if (packet["format"] != PACKET_FORMAT or packet["rule_version"] != RULE_VERSION
            or packet["registration_sha256"] != _hash(context["registration_raw"])
            or packet["policy_sha256"] != context["policy_sha256"] or packet["seal_sha256"] != _hash(seal_raw)
            or packet["target_scope"] != shadow.TARGET_SCOPE or any(packet[key] != value for key, value in {**_packet_scope(context), **payload}.items())
            or packet["boundaries"] != {"raw_redistribution": False, "reviewer_blinding_verified": False, "independent_adjudication_verified": False}
            or any(value is not False for value in packet["boundaries"].values())
            or receipt["format"] != PACKET_RECEIPT_FORMAT or receipt["packet_sha256"] != _hash(raw)
            or not _instant(seal["sealed_at"]) <= _instant(packet["started_at"]) <= _instant(packet["packaged_at"])
                <= _instant(receipt["accepted_at"]) <= _instant(_now())):
        raise ValueError("source-only packet population, bytes or clocks differ")
    inputs.update({packet_path: raw, seal_path: seal_raw, receipt_path: receipt_raw})
    _unchanged(context, registration_path, policy_path, root, inputs)
    return {**context, "packet": packet, "packet_raw": raw, "seal": seal, "seal_raw": seal_raw,
            "packet_accepted_at": receipt["accepted_at"],
            "inputs": {**context["inputs"], **inputs}}


def _labels(path: Path, context: dict, root: Path) -> tuple[dict, dict, list, dict]:
    labels, raw = _read_json(path, {"format", "packet_sha256", "seal_sha256", "policy_sha256", "reviewer",
        "reviewed_at", "prior_exposure", "coverage_statement", "target_scope", "supporting_reviews", "adjudications"}, "outcome labels")
    if (labels["format"] != LABEL_FORMAT or labels["packet_sha256"] != _hash(context["packet_raw"])
            or labels["seal_sha256"] != _hash(context["seal_raw"]) or labels["policy_sha256"] != context["policy_sha256"]
            or labels["target_scope"] != shadow.TARGET_SCOPE
            or not _instant(context["packet_accepted_at"]) < _instant(labels["reviewed_at"]) <= _instant(_now())):
        raise ValueError("outcome labels must bind policy, packet and seal and follow packet creation")
    for field in ("reviewer", "prior_exposure", "coverage_statement"):
        statements._text(labels[field])
    inputs, reviews = {path: raw}, {}
    for ref in statements._array(labels["supporting_reviews"], "supporting reviews"):
        review_path, content = binding._binding(path.parent, ref)
        if review_path in inputs or ref["sha256"] in reviews:
            raise ValueError("duplicate supporting review")
        inputs[review_path], reviews[ref["sha256"]] = content, ref
    adjudications, complete = [], {}
    for row in statements._array(labels["adjudications"], "outcome adjudications"):
        binding._keys(row, {"case_id", "disposition", "reason", "review_sha256", "locator", "reviewed_regions", "targets", "complete_scope_review"}, "outcome adjudication")
        if type(row["complete_scope_review"]) is not bool:
            raise ValueError("complete scope review must be an explicit boolean assertion")
        if row["complete_scope_review"] and row["disposition"] not in {"reviewed_targets", "no_relevant_scoped_target"}:
            raise ValueError("unresolved or uncomparable review cannot assert complete target coverage")
        complete[row["case_id"]] = row["complete_scope_review"]
        adjudications.append({key: value for key, value in row.items() if key != "complete_scope_review"})
    frozen = context["seal"]["final_population"]
    judgments, targets = statements._review_cases({"adjudications": adjudications}, statements._cases(frozen), reviews, root, frozen, inputs)
    judgments = {key: {**value, "complete_scope_review": complete[key]} for key, value in judgments.items()}
    return labels, judgments, targets, inputs


def _ratio(numerator: int, denominator: int) -> dict:
    return {"numerator": numerator, "denominator": denominator, "value": numerator / denominator if denominator else None}


def _truth(judgment: dict | None) -> str:
    if judgment is None:
        return "unlabelled"
    if any(target["verdict"] == "revision" for target in judgment["targets"]):
        return "positive"
    if (judgment["complete_scope_review"] and (judgment["disposition"] == "no_relevant_scoped_target"
            or judgment["disposition"] == "reviewed_targets" and all(target["verdict"] == "no_revision" for target in judgment["targets"]))):
        return "negative"
    return "uncomparable" if judgment["disposition"] == "uncomparable" else "unresolved"


def _decision(row: dict) -> str:
    if row["recording_status"] != "on_time":
        return row["recording_status"]
    return {"revision_candidate": "candidate", "no_candidate": "quiet", "abstain": "abstain", "error": "error"}[row["prediction"]["result"]]


def _metrics(rows: list[dict]) -> dict:
    eligible = [row for row in rows if row["evaluation_eligible"]]
    cells = Counter((row["truth"], row["decision"]) for row in eligible)
    tp, fp, tn, fn = (cells[truth, decision] for truth, decision in (("positive", "candidate"),
        ("negative", "candidate"), ("negative", "quiet"), ("positive", "quiet")))
    positives = sum(row["truth"] == "positive" for row in eligible)
    unknown_candidates = sum(row["truth"] not in {"positive", "negative"} and row["decision"] == "candidate" for row in eligible)
    candidate_count = sum(row["decision"] == "candidate" for row in eligible)
    known = sum(row["truth"] in {"positive", "negative"} for row in eligible)
    recorded_candidates = [row for row in eligible if row["recorded_result"] == "revision_candidate"]
    recorded_false = sum(row["truth"] == "negative" for row in recorded_candidates)
    recorded_unknown = sum(row["truth"] not in {"positive", "negative"} for row in recorded_candidates)
    recorded_true = sum(row["truth"] == "positive" for row in recorded_candidates)
    return {"total_retained_cases": len(rows), "eligible_document_opportunities": len(eligible),
        "excluded_cases": len(rows) - len(eligible),
        "truth_counts": dict(sorted(Counter(row["truth"] for row in eligible).items())),
        "decision_counts": dict(sorted(Counter(row["decision"] for row in eligible).items())),
        "recorded_result_counts": dict(sorted(Counter(row["recorded_result"] for row in eligible).items())),
        "confusion_on_resolved_timely_decisions": {"true_positive": tp, "false_positive": fp, "true_negative": tn, "false_negative": fn},
        "positive_without_timely_candidate": positives - tp,
        "positive_nondetection_by_decision": dict(sorted(Counter(row["decision"] for row in eligible if row["truth"] == "positive" and row["decision"] != "candidate").items())),
        "resolved_label_coverage": _ratio(known, len(eligible)),
        "timely_decision_coverage": _ratio(sum(row["decision"] in {"candidate", "quiet"} for row in eligible), len(eligible)),
        "candidate_document_support_among_resolved": _ratio(tp, tp + fp),
        "candidate_document_support_bounds": {"lower": _ratio(tp, candidate_count), "upper": _ratio(tp + unknown_candidates, candidate_count)},
        "timely_document_sensitivity_among_resolved_positives": _ratio(tp, positives),
        "confirmed_timely_false_positive_document_burden": _ratio(fp, len(eligible)),
        "timely_false_positive_document_burden_upper_with_unresolved_candidates": _ratio(fp + unknown_candidates, len(eligible)),
        "all_recorded_candidate_document_support_among_resolved": _ratio(recorded_true, recorded_true + recorded_false),
        "all_recorded_candidate_counts": {"total": len(recorded_candidates), "supported": recorded_true,
            "false_positive": recorded_false, "unknown_label": recorded_unknown},
        "confirmed_false_positive_document_burden": _ratio(recorded_false, len(eligible)),
        "false_positive_document_burden_upper_with_unresolved_candidates": _ratio(recorded_false + recorded_unknown, len(eligible)),
        "unknown_candidate_labels": unknown_candidates}


def evaluate(registration_path: str | Path, policy_path: str | Path, packet_path: str | Path,
             labels_path: str | Path, *, reference_root: str | Path) -> dict:
    root = Path(reference_root).resolve()
    registration_path, policy_path, packet_path, labels_path = map(Path, (registration_path, policy_path, packet_path, labels_path))
    codes = _code_hashes()
    context = validate_packet(registration_path, policy_path, packet_path, reference_root=root)
    labels, judgments, targets, inputs = _labels(labels_path, context, root)
    rows, pairs = [], Counter()
    for row in context["seal"]["cases"]:
        case = row["case"]
        pair = None
        if case["comparison_eligible"]:
            pair = _hash({"url": case["url"], "scope": case["scope"], "before_sha256": case["predecessor"]["body_sha256"], "after_sha256": case["body_sha256"]})
            pairs[pair] += 1
        judgment = judgments.get(row["case_id"])
        rows.append({"case_id": row["case_id"], "url": case["url"], "scope": case["scope"],
            "evaluation_eligible": row["in_protocol"] and case["comparison_eligible"],
            "exclusion_reason": None if row["in_protocol"] and case["comparison_eligible"] else
                row["collection_mode"] if not row["in_protocol"] else "no_actual_comparable_pair",
            "truth": _truth(judgment), "decision": _decision(row),
            "recorded_result": row["prediction"]["result"] if row["prediction"] else "missing",
            "recording_status": row["recording_status"], "assessment_to_prediction_seconds": row["assessment_to_prediction_seconds"],
            "source_pair_id": pair, "adjudication": judgment})
    result = {"format": REPORT_FORMAT, "rule_version": RULE_VERSION,
        "policy_sha256": context["policy_sha256"], "registration_sha256": _hash(context["registration_raw"]),
        "seal_sha256": _hash(context["seal_raw"]), "packet_sha256": _hash(context["packet_raw"]),
        "labels_sha256": _hash(inputs[labels_path]), "contract": CONTRACT.copy(),
        **{key: labels[key] for key in ("reviewer", "reviewed_at", "prior_exposure", "coverage_statement")},
        "cases": rows, "targets": targets, "metrics": _metrics(rows),
        "by_exact_url": [{"url": url, "metrics": _metrics([row for row in rows if row["url"] == url])}
            for url in sorted({row["url"] for row in context["registration"]["inputs"]["documents"]} | {row["url"] for row in rows})],
        "repetition": {"comparable_checks": sum(pairs.values()), "unique_exact_source_pairs": len(pairs),
            "repeated_exact_source_pairs": sum(value - 1 for value in pairs.values()), "event_count": None,
            "note": "Every actual check remains a workload unit; repeated source pairs and formulations are not independent events."},
        "unscored_metrics": dict.fromkeys(("publisher_recall", "exact_subject_precision", "publication_detection_lag", "forecast_calibration", "physical_outcome_accuracy")),
        "supporting_reviews": labels["supporting_reviews"], "code_sha256": codes, "boundaries": BOUNDARIES.copy(),
        "limitations": ["Scores are conditional on unauthenticated reviewer assertions, not independently established truth.",
            "A source-only packet does not prove that the reviewer was unexposed to predictions elsewhere.",
            "Document triage support does not establish correct subject, milestone, direction or magnitude extraction.",
            "Negative labels require an explicit complete-scope assertion; evidence containment does not prove semantic completeness.",
            "First, failed, blocked, incomplete and out-of-protocol cases remain reported but outside comparable-document accuracy.",
            "Timely sensitivity includes labelled positives with abstentions, errors, late and missing outputs.",
            "Zero denominators are null; repeated checks are correlated and no confidence intervals or event recall are inferred."]}
    if len(_pretty_bytes(result)) > shadow.population.MAX_BYTES:
        raise ValueError("complete evaluation exceeds 20 MB; never truncate")
    if validate_packet(registration_path, policy_path, packet_path, reference_root=root)["seal_raw"] != context["seal_raw"]:
        raise ValueError("sealed evaluation inputs changed")
    _unchanged(context, registration_path, policy_path, root, inputs)
    if codes != _code_hashes():
        raise ValueError("evaluation code changed")
    return result
