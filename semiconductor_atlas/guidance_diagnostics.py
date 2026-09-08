"""Deterministic interval diagnostics, not evidence verification or calibration.

Callers must independently verify exact project/event scope, source support,
outcome availability at their assessment cutoff, and the complete frozen roster.
In particular, censoring means explicit nonoccurrence through an inclusive date;
an absent report is an unknown outcome. This helper makes no evidence-trust claim.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any


def _day(value: object, field: str) -> date:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError(f"{field} requires an exact ISO calendar date")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field} requires a valid calendar date") from error


def _shape(value: object, keys: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{field} requires exactly {sorted(keys)}")
    return value


def _relation(low: date, high: date, vintage: date, horizon: date) -> str:
    if high <= vintage:
        return "pre_vintage"
    if low <= vintage:
        return "vintage_straddling"
    if low > horizon:
        return "after_horizon"
    if high > horizon:
        return "horizon_straddling"
    return "within_horizon"


def evaluate_case(
    prediction: dict[str, Any] | None,
    outcome: dict[str, Any],
    vintage_day: str,
    horizon_end: str,
) -> dict[str, Any]:
    """Classify one retained case without creating a point-date or probability metric.

    Prediction keys are exactly ``low``, ``base`` and ``high``, with a null base.
    Outcomes have ``status`` plus ``low``/``high`` for observed or cancelled,
    ``through`` for right_censored, and no other fields for unknown.

    All bounds and the censor-through date are inclusive. Issued intervals must
    lie strictly after the vintage calendar day and within the declared horizon.
    Observed/cancelled intervals ending on the vintage are pre_vintage. Intervals
    spanning both boundaries are vintage_straddling first: their wholly future
    occurrence has not been established. Exact horizon-day outcomes are in range.

    Abstentions retain their outcome status and temporal classification. Other
    pre-vintage, straddling and after-horizon outcomes retain explicit categories
    without interval-scoring values. An explicitly censored case is an exception:
    nonoccurrence through the prediction's high date proves a window miss, even
    when the censor-through date exceeds the assessment horizon.

    ``window_miss`` is false only for guaranteed containment, true for disjoint
    observed intervals or a proven censored miss, and null when not established
    or outside the scoring scope. Overlap alone does not establish a hit or miss.
    Cancellation is kept distinct from production timing and is never a silently
    scored miss. Every call returns one result; callers must not drop categories
    when reporting roster, issued-prediction or resolved-outcome denominators.
    """
    vintage = _day(vintage_day, "vintage_day")
    horizon = _day(horizon_end, "horizon_end")
    if horizon <= vintage:
        raise ValueError("horizon_end must follow the vintage calendar day")

    predicted_low = predicted_high = None
    if prediction is not None:
        prediction = _shape(prediction, {"low", "base", "high"}, "prediction")
        if prediction["base"] is not None:
            raise ValueError("source-guidance prediction base must remain null")
        predicted_low = _day(prediction["low"], "prediction low")
        predicted_high = _day(prediction["high"], "prediction high")
        if not vintage < predicted_low <= predicted_high <= horizon:
            raise ValueError("prediction must be ordered, wholly after vintage, and within horizon")

    if not isinstance(outcome, dict) or not isinstance(outcome.get("status"), str):
        raise ValueError("outcome requires a supported status")
    status = outcome["status"]
    if status not in {"unknown", "observed", "right_censored", "cancelled"}:
        raise ValueError("outcome requires a supported status")
    keys = {"status"}
    if status in {"observed", "cancelled"}:
        keys |= {"low", "high"}
    elif status == "right_censored":
        keys.add("through")
    _shape(outcome, keys, "outcome")

    low = high = through = None
    temporal = None
    if status in {"observed", "cancelled"}:
        low = _day(outcome["low"], "outcome low")
        high = _day(outcome["high"], "outcome high")
        if low > high:
            raise ValueError("outcome interval must be ordered")
        temporal = _relation(low, high, vintage, horizon)
    elif status == "right_censored":
        through = _day(outcome["through"], "censor through")
        temporal = _relation(through, through, vintage, horizon)

    result = {
        "classification": "abstained" if prediction is None else status,
        "prediction_issued": prediction is not None,
        "outcome_status": status,
        "temporal_classification": temporal,
        "guaranteed_containment": None,
        "possible_overlap": None,
        "window_miss": None,
    }
    if prediction is None or status == "unknown":
        return result

    if status == "right_censored":
        if temporal == "pre_vintage":
            result["classification"] = "pre_vintage"
        elif through >= predicted_high:
            result["classification"] = "censored_window_miss"
            result["window_miss"] = True
        return result

    if temporal != "within_horizon":
        result["classification"] = temporal
        return result
    if status == "cancelled":
        return result

    contained = predicted_low <= low <= high <= predicted_high
    overlapping = predicted_low <= high and low <= predicted_high
    result["guaranteed_containment"] = contained
    result["possible_overlap"] = overlapping
    if contained:
        result["classification"] = "contained"
        result["window_miss"] = False
    elif overlapping:
        result["classification"] = "overlapping"
    else:
        result["classification"] = "missed_early" if high < predicted_low else "missed_late"
        result["window_miss"] = True
    return result
