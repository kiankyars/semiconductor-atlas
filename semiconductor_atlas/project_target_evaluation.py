"""Retrospective source-statement diagnostics over an accepted comparison census."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from . import ai_critical_alert_review as legacy
from . import project_target_population as population
from .ai_critical_changes import _strict_json
from .source_checks import _read


LABEL_FORMAT = "semiconductor-atlas-project-target-evaluation-labels-v1"
REPORT_FORMAT = "semiconductor-atlas-project-target-evaluation-report-v1"
RULE_VERSION = "accepted-project-source-support-diagnostics-v1"
VERDICTS = {"supported_revision", "no_revision", "unsupported_comparison", "unresolved"}
UNKNOWN = {"unresolved", "unlabelled"}
_now = legacy._now


def _ratio(numerator: int, denominator: int) -> dict:
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None}


def _seconds(start: str, end: str) -> float:
    result = (legacy._instant(end) - legacy._instant(start)).total_seconds()
    if result < 0:
        raise ValueError("observed latency cannot precede its retained source clock")
    return result


def _code_hashes() -> dict:
    return population._code_hashes()


def _comparison_id(alert: dict) -> str:
    identifiers = {row["comparison_id"] for row in alert["observations"]}
    if len(identifiers) != 1:
        raise ValueError("one evaluated project episode must identify one accepted comparison")
    return next(iter(identifiers))


def _negative_bounds(negatives: int, false: int, unknown_alert: int, unknown_quiet: int) -> dict:
    # Bounds are conditional on at least one comparison being a no-revision comparison.
    lower = (_ratio(false, negatives + unknown_quiet) if negatives + unknown_quiet else
             _ratio(1, 1) if unknown_alert else _ratio(0, 0))
    upper = (_ratio(false + unknown_alert, negatives + unknown_alert) if negatives + unknown_alert else
             _ratio(0, 1) if unknown_quiet else _ratio(0, 0))
    return {"lower": lower, "upper": upper, "undefined_if_no_negatives_possible": negatives == 0,
            "unknown_with_prediction": unknown_alert, "unknown_without_prediction": unknown_quiet,
            "scope": "conditional_on_at_least_one_no_revision_opportunity"}


def _labels(path: Path, raw: bytes, frozen: dict, frozen_raw: bytes) -> tuple[dict, dict, list, dict]:
    if len(raw) > 20_000_000:
        raise ValueError("evaluation labels exceed the bounded 20 MB artifact limit")
    labels = legacy._keys(_strict_json(raw, "project evaluation labels"), (
        "format", "frozen_sha256", "adjudicator", "reviewed_at", "prior_exposure",
        "coverage_statement", "adjudications", "supporting_reviews"), "project evaluation labels")
    if labels["format"] != LABEL_FORMAT or labels["frozen_sha256"] != legacy._hash(frozen_raw):
        raise ValueError("labels must bind the exact frozen project population")
    if not legacy._instant(frozen["frozen_at"]) < legacy._instant(labels["reviewed_at"]) <= legacy._instant(_now()):
        raise ValueError("label review must follow population freeze and cannot be in the future")
    for key in ("adjudicator", "prior_exposure", "coverage_statement"):
        legacy._text(labels[key], key)
    for key in ("adjudications", "supporting_reviews"):
        if not isinstance(labels[key], list) or len(labels[key]) > 10_000:
            raise ValueError(f"{key} must be a bounded array")
    paths, reviews, hashes = {}, [], set()
    for ref in labels["supporting_reviews"]:
        legacy._keys(ref, ("path", "sha256"), "supporting review reference")
        relative = Path(legacy._text(ref["path"], "supporting review path"))
        target = relative if relative.is_absolute() else path.parent / relative
        target = target.absolute()
        digest = ref["sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("supporting review requires lowercase SHA-256")
        if target in paths or digest in hashes:
            raise ValueError("duplicate supporting review")
        review_raw = _read(target)
        if len(review_raw) > 20_000_000 or legacy._hash(review_raw) != digest:
            raise ValueError("supporting review bytes do not match their bounded reference")
        paths[target], hashes = review_raw, hashes | {digest}
        reviews.append({"reference": ref, "content": legacy._blob(review_raw)})
    eligible = {row["comparison_id"] for row in frozen["opportunities"]}
    eligible.update(_comparison_id(alert) for alert in frozen["predictions"])
    judgments = {}
    for row in labels["adjudications"]:
        legacy._keys(row, ("comparison_id", "verdict", "reason", "review_sha256", "locator"), "source comparison label")
        for key in ("comparison_id", "reason", "locator", "review_sha256", "verdict"):
            legacy._text(row[key], key)
        comparison = row["comparison_id"]
        if comparison not in eligible or comparison in judgments:
            raise ValueError("unknown, excluded, or duplicate comparison label")
        if row["verdict"] not in VERDICTS or row["review_sha256"] not in hashes:
            raise ValueError("unsupported verdict or unbound supporting review")
        judgments[comparison] = row
    return labels, judgments, reviews, paths


def _score_population(frozen: dict, judgments: dict) -> dict:
    packets = {packet["comparison_id"]: packet for packet in frozen["packets"]}
    if len(packets) != len(frozen["packets"]):
        raise ValueError("duplicate accepted comparison packet")
    matches = {identifier: [] for identifier in packets}
    predictions = []
    prediction_counts = Counter({key: 0 for key in (*sorted(VERDICTS), "unlabelled")})
    for alert in frozen["predictions"]:
        comparison = _comparison_id(alert)
        if alert["origin"] != "source_native_project" or comparison not in packets:
            raise ValueError("prediction lacks accepted source-project comparison")
        packet = packets[comparison]
        verdict = judgments.get(comparison, {}).get("verdict", "unlabelled")
        prediction_counts[verdict] += 1
        row = {"alert_id": alert["id"], "comparison_id": comparison,
               "entity_id": packet["subject"]["entity_id"], "verdict": verdict,
               "first_admitted_at": alert["first_recorded_at"], "status_at_cutoff": alert["status"],
               "claim_to_alert_seconds": _seconds(packet["accepted_at"], alert["first_recorded_at"]),
               "retrieval_to_claim_seconds": _seconds(packet["claims"]["after"]["document"]["retrieved_at"], packet["accepted_at"]),
               "publication_to_alert_seconds": None,
               "retraction_count": sum(item["action"] == "retract" for item in alert["decisions"])}
        matches[comparison].append(row)
        predictions.append(row)
    opportunities = []
    opportunity_counts = Counter({key: 0 for key in (*sorted(VERDICTS), "unlabelled")})
    for opportunity in frozen["opportunities"]:
        comparison = opportunity["comparison_id"]
        packet = packets[comparison]
        matched = matches[comparison]
        verdict = judgments.get(comparison, {}).get("verdict", "unlabelled")
        opportunity_counts[verdict] += 1
        expected = opportunity["expected_proposal"]
        opportunities.append({**opportunity, "verdict": verdict,
            "matched_alert_ids": sorted(row["alert_id"] for row in matched),
            "admission_status": "admitted_by_cutoff" if matched else "not_admitted_by_cutoff" if expected else "no_proposal_expected",
            "retrieval_to_claim_seconds": _seconds(packet["claims"]["after"]["document"]["retrieved_at"], packet["accepted_at"]),
            "claim_to_first_alert_seconds": min((row["claim_to_alert_seconds"] for row in matched), default=None),
            "publication_to_alert_seconds": None})
    expected = [row for row in opportunities if row["expected_proposal"]]
    admitted = sum(bool(row["matched_alert_ids"]) for row in expected)
    negatives = [row for row in opportunities if row["verdict"] == "no_revision"]
    false_positive_opportunities = sum(bool(row["matched_alert_ids"]) for row in negatives)
    unknown = [row for row in opportunities if row["verdict"] in UNKNOWN]
    unknown_alert = sum(bool(row["matched_alert_ids"]) for row in unknown)
    supported = prediction_counts["supported_revision"]
    unsupported = prediction_counts["no_revision"] + prediction_counts["unsupported_comparison"]
    unresolved = prediction_counts["unresolved"] + prediction_counts["unlabelled"]
    total = len(predictions)
    transition_groups = Counter(row["transition_group_id"] for row in opportunities)
    return {"counts": {
                "packets_before_end": len(packets), "accepted_opportunities_in_window": len(opportunities),
                "unique_opportunity_transition_groups": len(transition_groups),
                "repeated_transition_opportunities": sum(count - 1 for count in transition_groups.values()),
                "prior_window_comparisons": len(frozen["excluded_comparisons"]),
                "predictions_in_window": total, "prediction_labels": dict(prediction_counts),
                "opportunity_labels": dict(opportunity_counts),
                "expected_proposal_comparisons": len(expected), "admitted_expected_comparisons": admitted,
                "not_admitted_by_cutoff": len(expected) - admitted,
                "verified_no_revision_opportunities": len(negatives),
                "unsupported_comparisons_excluded_from_no_change_denominator": opportunity_counts["unsupported_comparison"],
                "unknown_opportunity_labels": len(unknown)},
            "metrics": {
                "conditional_supported_proposal_precision": _ratio(supported, supported + unsupported),
                "supported_proposal_precision_bounds": {"lower": _ratio(supported, total), "upper": _ratio(supported + unresolved, total)},
                "conditional_no_change_false_positive_burden": _ratio(false_positive_opportunities, len(negatives)),
                "no_change_false_positive_bounds": _negative_bounds(len(negatives), false_positive_opportunities, unknown_alert, len(unknown) - unknown_alert),
                "accepted_expected_comparison_admission_coverage": _ratio(admitted, len(expected)),
                "detection_recall": None, "publication_detection_lag": None,
                "retracted_predictions": sum(row["retraction_count"] > 0 for row in predictions)},
            "opportunities": opportunities, "predictions": predictions}


def evaluate(frozen_path: str | Path, labels_path: str | Path) -> dict:
    codes = _code_hashes()
    frozen_path, labels_path = Path(frozen_path).absolute(), Path(labels_path).absolute()
    frozen, frozen_raw = population.validate_population(frozen_path)
    label_raw = _read(labels_path)
    labels, judgments, reviews, paths = _labels(labels_path, label_raw, frozen, frozen_raw)
    scored = _score_population(frozen, judgments)
    report = {"format": REPORT_FORMAT, "rule_version": RULE_VERSION, "study_id": frozen["study_id"],
        "frozen_sha256": legacy._hash(frozen_raw), "labels_sha256": legacy._hash(label_raw),
        "start": frozen["start"], "end": frozen["end"], "frozen_at": frozen["frozen_at"],
        "adjudicator": labels["adjudicator"], "reviewed_at": labels["reviewed_at"],
        "prior_exposure": labels["prior_exposure"], "coverage_statement": labels["coverage_statement"],
        "design": frozen["design"], "cohort": frozen["cohort"], **scored,
        "excluded_comparisons": frozen["excluded_comparisons"], "excluded_alerts": frozen["excluded_alerts"],
        "adjudications": labels["adjudications"], "supporting_reviews": reviews, "code_sha256": codes,
        "blinding_verified": False, "independence_verified": False, "calibration_established": False,
        "phase3_gate_passed": False, "delivery_eligible": False,
        "limitations": [
            "Post-freeze retrospective labels assess source-statement support, not actual production or physical capacity.",
            "The core census covers accepted supported-route comparisons, not all external changes, facilities, or source publications.",
            "Missing and unresolved labels remain unknown; unsupported comparisons do not establish a no-change opportunity.",
            "Conditional precision and no-change burden do not establish blind performance or independent truth.",
            "Admission coverage is mechanical pipeline coverage at cutoff, not detection recall or compliance with a routing deadline.",
            "Observed retrieval-to-claim and claim-to-alert lags are not publisher first-availability or detection lead time.",
            "Prior-window comparisons can support in-window predictions but are excluded from in-window opportunity denominators.",
            "Bounds condition on the retained comparison population and reviewer label categories, not external completeness.",
            "Retraction counts describe recorded review actions, not correct physical conclusions or timely correction.",
            "Raw labels and the frozen population must accompany a report for exact-byte replay.",
            "Hash-bound reviews and replayability do not authenticate reviewer identity or grant redistribution rights."]}
    for path, raw in [(frozen_path, frozen_raw), (labels_path, label_raw), *paths.items()]:
        if _read(path) != raw:
            raise ValueError("evaluation inputs changed across the scoring boundary")
    if codes != _code_hashes():
        raise ValueError("evaluation code changed across the scoring boundary")
    return report
