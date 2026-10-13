"""Pure exposed-retrospective source-statement comparisons, not Atlas forecasts.

The caller must separately retain and verify evidence, source-reference identity,
exact project/phase/event correspondence, date wording and the complete screening
population. Source-document dates provide context only: they neither authenticate
historical content availability nor become historical database knowledge clocks.

An interval target requires occurrence inside that interval; a deadline has only
an inclusive upper bound. No lower bound, midpoint, probability or issue time is
invented. ``scored_cases`` counts only established Boolean target results, never
overlap, partial censoring, cancellation, missing reports or screening exclusions.
"""

from __future__ import annotations

import copy
import re
from collections import Counter
from datetime import date


FORMAT = "semiconductor-atlas-exposed-source-guidance-outcomes-v1"
FORMAT_V2 = "semiconductor-atlas-exposed-source-guidance-outcomes-v2"
IDENTITY = {"case_id", "project_id", "project_group", "event_type", "scope_id"}
BOUNDARIES = {
    "evidence_verified": False,
    "historical_availability_verified": False,
    "historical_atlas_forecasts": False,
    "independent_or_blind_evaluation": False,
    "source_document_dates_are_issue_times": False,
    "population_completeness_independently_verified": False,
    "capacity_forecasts": False,
    "probabilistic_calibration": False,
}


def _keys(value, expected, name):
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{name} requires exactly {sorted(expected)}")


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 4_000:
        raise ValueError(f"{name} requires bounded nonempty text")


def _day(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError("dates require exact ISO calendar days")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("dates require valid ISO calendar days") from error


def _interval(value):
    low, high = _day(value["low"]), _day(value["high"])
    if low > high:
        raise ValueError("interval bounds must be ordered")
    return low, high


def _target(value):
    if value is None:
        return None, None, None
    if (not isinstance(value, dict) or not isinstance(value.get("kind"), str)
            or value["kind"] not in {"interval", "deadline"}):
        raise ValueError("target requires interval or deadline semantics")
    if value["kind"] == "interval":
        _keys(value, {"kind", "low", "high"}, "interval target")
        low, high = _interval(value)
        return "interval", low, high
    _keys(value, {"kind", "through"}, "deadline target")
    return "deadline", None, _day(value["through"])


def _outcome(value):
    if not isinstance(value, dict) or not isinstance(value.get("status"), str) or value["status"] not in {
        "observed", "observed_by", "explicit_nonoccurrence_through", "cancelled", "unknown"
    }:
        raise ValueError("outcome requires a supported explicit status")
    status = value["status"]
    if status == "observed":
        _keys(value, {"status", "low", "high"}, "observed outcome")
        low, high = _interval(value)
        return status, low, high
    if status == "observed_by":
        _keys(value, {"status", "through"}, "observed-by outcome")
        return status, None, _day(value["through"])
    if status == "explicit_nonoccurrence_through":
        _keys(value, {"status", "through"}, "nonoccurrence outcome")
        through = _day(value["through"])
        return status, through, through
    _keys(value, {"status"}, "cancelled or unknown outcome")
    return status, None, None


def evaluate_case(target, outcome):
    """Compare source bounds only; the caller independently judges admissibility.

    ``target`` is null, ``{kind: interval, low, high}``, or
    ``{kind: deadline, through}``. ``outcome`` is ``{status: observed, low,
    high}``, ``{status: observed_by, through}``,
    ``{status: explicit_nonoccurrence_through, through}``, or has
    only a ``status`` of ``cancelled`` or ``unknown``. All dates are inclusive.
    Missing evidence must be unknown, never explicit nonoccurrence.
    """
    kind, target_low, target_high = _target(target)
    status, low, high = _outcome(outcome)
    result = {"target_kind": kind, "outcome_status": status,
              "classification": "no_target" if target is None else status,
              "target_met": None, "possible_satisfaction": None}
    if target is None or status in {"cancelled", "unknown"}:
        return result
    if status == "explicit_nonoccurrence_through":
        if high >= target_high:
            result.update(classification=f"censored_{kind}_miss", target_met=False,
                          possible_satisfaction=False)
        return result
    if status == "observed_by":
        if kind == "deadline":
            if high <= target_high:
                result.update(classification="deadline_met", target_met=True, possible_satisfaction=True)
            else:
                result.update(classification="deadline_upper_bound_unresolved", possible_satisfaction=True)
        elif high < target_low:
            result.update(classification="interval_missed_early", target_met=False, possible_satisfaction=False)
        else:
            result.update(classification="interval_upper_bound_unresolved", possible_satisfaction=True)
        return result
    if kind == "deadline":
        if high <= target_high:
            result.update(classification="deadline_met", target_met=True, possible_satisfaction=True)
        elif low > target_high:
            result.update(classification="deadline_missed_late", target_met=False, possible_satisfaction=False)
        else:
            result.update(classification="deadline_straddling", possible_satisfaction=True)
        return result
    contained = target_low <= low <= high <= target_high
    overlapping = target_low <= high and low <= target_high
    if contained:
        result.update(classification="interval_contained", target_met=True, possible_satisfaction=True)
    elif overlapping:
        result.update(classification="interval_overlapping", possible_satisfaction=True)
    else:
        result.update(classification="interval_missed_early" if high < target_low else "interval_missed_late",
                      target_met=False, possible_satisfaction=False)
    return result


def _references(value, *, required):
    if not isinstance(value, list) or len(value) > 100 or (required and not value):
        raise ValueError("source reference IDs require a bounded array and substantive evidence")
    for item in value:
        _text(item, "source reference ID")
    if value != sorted(set(value)):
        raise ValueError("source reference IDs must be unique and sorted")


def _context(guidance_day, outcome_day, outcome):
    order = "unknown"
    if guidance_day is not None and outcome_day is not None:
        order = "guidance_before_outcome_document" if guidance_day < outcome_day else (
            "same_reported_document_day" if guidance_day == outcome_day else "outcome_document_before_guidance")
    status, low, high = _outcome(outcome)
    def relation(day):
        if status not in {"observed", "observed_by"} or day is None or high is None:
            return "unknown"
        if high < day:
            return "before_reported_document_day"
        if low is None:
            return "on_or_before_reported_document_day" if high == day else "unknown"
        if low > day:
            return "after_reported_document_day"
        if low == high == day:
            return "on_reported_document_day"
        return "spans_reported_document_day"
    return {"source_document_order": order,
            "outcome_relative_to_guidance_document": relation(guidance_day),
            "outcome_relative_to_outcome_document": relation(outcome_day)}


def score(roster, outcomes):
    """Keep each supplied screened case exactly once, without authenticating its roster.

    Both arrays repeat case_id, project_id, project_group, event_type and scope_id
    and must be in the same sorted case-ID order. Roster rows additionally have
    disposition (eligible/unresolved/excluded), reason, target, source_reference_ids
    and source_document_date. Outcome rows instead add reason, outcome,
    source_reference_ids and source_document_date. Document dates may be null.
    Unknown outcomes may have no evidence references; all other outcomes and all
    screened cases require references to the separate reviewed evidence manifest.

    Source-date chronology is reported even when reversed or uncertain. It does
    not create a historical issue date or silently remove a screened case. The
    arithmetic remains descriptive of reviewed retained source statements only.
    """
    if not isinstance(roster, list) or not 1 <= len(roster) <= 1_000:
        raise ValueError("roster requires a bounded nonempty complete case array")
    if not isinstance(outcomes, list) or len(outcomes) != len(roster):
        raise ValueError("outcomes must retain every screened case exactly once")
    for row in roster:
        _keys(row, IDENTITY | {"disposition", "reason", "target", "source_reference_ids", "source_document_date"}, "roster case")
        for field in IDENTITY | {"reason"}:
            _text(row[field], field)
    ids = [row["case_id"] for row in roster]
    if ids != sorted(set(ids)):
        raise ValueError("roster case IDs must be unique and sorted")
    rows = []
    for case, reviewed in zip(roster, outcomes, strict=True):
        _keys(reviewed, IDENTITY | {"reason", "outcome", "source_reference_ids", "source_document_date"}, "outcome case")
        if any(case[field] != reviewed[field] for field in IDENTITY):
            raise ValueError("outcome must match each exact screened case/project/group/event/scope")
        _text(reviewed["reason"], "outcome reason")
        if not isinstance(case["disposition"], str) or case["disposition"] not in {"eligible", "unresolved", "excluded"}:
            raise ValueError("case disposition must be eligible, unresolved or excluded")
        if case["disposition"] == "eligible" and case["target"] is None:
            raise ValueError("eligible cases require a supported source target")
        diagnostic = evaluate_case(case["target"], reviewed["outcome"])
        _references(case["source_reference_ids"], required=True)
        _references(reviewed["source_reference_ids"], required=diagnostic["outcome_status"] != "unknown")
        guidance_day = _day(case["source_document_date"]) if case["source_document_date"] is not None else None
        outcome_day = _day(reviewed["source_document_date"]) if reviewed["source_document_date"] is not None else None
        if case["disposition"] != "eligible":
            diagnostic.update(classification="screened_" + case["disposition"], target_met=None,
                              possible_satisfaction=None)
        rows.append({"case": copy.deepcopy(case), "review": copy.deepcopy(reviewed),
                     "source_date_context": _context(guidance_day, outcome_day, reviewed["outcome"]),
                     **diagnostic})
    eligible = [row for row in rows if row["case"]["disposition"] == "eligible"]
    scored = [row for row in eligible if row["target_met"] is not None]
    denominators = {"screened_cases": len(rows), "eligible_cases": len(eligible),
                    "unresolved_cases": sum(row["case"]["disposition"] == "unresolved" for row in rows),
                    "excluded_cases": sum(row["case"]["disposition"] == "excluded" for row in rows),
                    "scored_cases": len(scored), "eligible_result_unresolved_cases": len(eligible) - len(scored),
                    "scored_interval_cases": sum(row["target_kind"] == "interval" for row in scored),
                    "scored_deadline_cases": sum(row["target_kind"] == "deadline" for row in scored)}
    for name, members in (("screened", rows), ("eligible", eligible), ("scored", scored)):
        denominators[name + "_projects"] = len({row["case"]["project_id"] for row in members})
        denominators[name + "_project_groups"] = len({row["case"]["project_group"] for row in members})
    format_name = FORMAT_V2 if any(row['outcome_status'] == 'observed_by' for row in rows) else FORMAT
    return {"format": format_name, "design": "exposed_retrospective_source_statement_comparison",
            "boundaries": dict(BOUNDARIES), "denominators": denominators, "cases": rows,
            "classification_counts": dict(sorted(Counter(row["classification"] for row in rows).items())),
            "screened_outcome_status_counts": dict(sorted(Counter(row["outcome_status"] for row in rows).items())),
            "eligible_outcome_status_counts": dict(sorted(Counter(row["outcome_status"] for row in eligible).items())),
            "scored_outcome_status_counts": dict(sorted(Counter(row["outcome_status"] for row in scored).items()))}
