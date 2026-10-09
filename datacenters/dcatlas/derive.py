"""Documented derivations from curated records to headline (one-row-per-project) values.

Headline values are conveniences for browsing. They select one statement each; they never add,
average or convert statements. The full statement tables keep every value with its basis, scope
and sources.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contract import date_precision, partial_date_bounds

PLANNED_BASES = ("planned", "contracted", "permitted")
DEMAND_SCOPES = ("facility", "grid_connection", "unspecified")

DERIVATION_RULES = {
    "planned_power_mw": (
        "Campus-level power_capacity statements with basis planned, contracted or permitted. "
        "Facility, grid-connection and unspecified scopes are preferred over IT load; on-site "
        "generation is excluded. Among the preferred statements the most recent as_of wins, then "
        "the largest value. The scope, basis, qualifier and date of the chosen statement are "
        "published alongside it."
    ),
    "operational_power_mw": (
        "Campus-level power_capacity statements with basis operational, same scope preference and "
        "selection order as planned_power_mw."
    ),
    "onsite_generation_mw": (
        "Campus-level power_capacity statements with scope onsite_generation, any basis; the "
        "most recent as_of wins, then the largest value. Reported separately because generation "
        "capacity is not data center demand."
    ),
    "investment": (
        "Campus-level investment statements; the most recent as_of wins, then the largest value. "
        "Values stay in the reported currency and are never converted or summed."
    ),
    "accelerators": (
        "Campus-level accelerator counts; the most recent as_of wins, then the largest value."
    ),
    "announced": "Earliest actual 'announced' milestone.",
    "construction_started": "Earliest actual 'construction_started' milestone.",
    "first_operational": "Earliest actual 'first_operational' milestone.",
    "first_operational_target": (
        "Latest-dated planned 'first_operational' milestone, only when no actual one exists."
    ),
}


def _sort_key(statement: dict[str, Any]) -> tuple:
    start, end = partial_date_bounds(statement["as_of"])
    return (start, end, statement["value"])


def _pick(statements: list[dict[str, Any]], *, prefer_demand: bool) -> dict[str, Any] | None:
    if not statements:
        return None
    if prefer_demand:
        preferred = [s for s in statements if s.get("power_scope") in DEMAND_SCOPES]
        fallback = [s for s in statements if s.get("power_scope") == "it_load"]
        statements = preferred or fallback
        if not statements:
            return None
    return max(statements, key=_sort_key)


def _milestone(record: dict[str, Any], event: str, kind: str, *, latest: bool = False):
    found = [m for m in record["milestones"] if m["event"] == event and m["kind"] == kind]
    if not found:
        return None
    key = lambda m: partial_date_bounds(m["date"])  # noqa: E731
    return max(found, key=key) if latest else min(found, key=key)


def _names(record: dict[str, Any], role: str) -> list[str]:
    return [p["name"] for p in record["parties"] if p["role"] == role]


def headline(record: dict[str, Any]) -> dict[str, Any]:
    """Return derived headline fields for one validated record."""
    campus = [m for m in record["metrics"] if m["applies_to"] == "campus"]
    power = [m for m in campus if m["metric"] == "power_capacity"]
    planned = _pick([m for m in power if m["basis"] in PLANNED_BASES], prefer_demand=True)
    operational = _pick([m for m in power if m["basis"] == "operational"], prefer_demand=True)
    generation = _pick([m for m in power if m.get("power_scope") == "onsite_generation"],
                       prefer_demand=False)
    investment = _pick([m for m in campus if m["metric"] == "investment"], prefer_demand=False)
    accelerators = _pick([m for m in campus if m["metric"] == "accelerators"],
                         prefer_demand=False)
    announced = _milestone(record, "announced", "actual")
    started = _milestone(record, "construction_started", "actual")
    first_op = _milestone(record, "first_operational", "actual")
    target = None if first_op else _milestone(record, "first_operational", "planned", latest=True)
    out: dict[str, Any] = {
        "developers": _names(record, "developer"),
        "owners": _names(record, "owner"),
        "operators": _names(record, "operator"),
        "tenants": _names(record, "tenant"),
        "power_source_types": sorted({p["type"] for p in record.get("power_sources", [])}),
        "announced": announced["date"] if announced else None,
        "construction_started": started["date"] if started else None,
        "first_operational": first_op["date"] if first_op else None,
        "first_operational_target": target["date"] if target else None,
        "source_count": len(record["sources"]),
        "statement_count": len(record["metrics"]) + len(record["milestones"])
        + len(record["parties"]) + len(record.get("power_sources", [])),
    }
    for prefix, statement in (("planned_power", planned), ("operational_power", operational)):
        out[f"{prefix}_mw"] = statement["value"] if statement else None
        out[f"{prefix}_mw_high"] = statement.get("value_high") if statement else None
        out[f"{prefix}_qualifier"] = statement["qualifier"] if statement else None
        out[f"{prefix}_scope"] = statement["power_scope"] if statement else None
        out[f"{prefix}_basis"] = statement["basis"] if statement else None
        out[f"{prefix}_as_of"] = statement["as_of"] if statement else None
        out[f"{prefix}_source_ids"] = statement["source_ids"] if statement else None
    out["onsite_generation_mw"] = generation["value"] if generation else None
    out["onsite_generation_basis"] = generation["basis"] if generation else None
    out["onsite_generation_as_of"] = generation["as_of"] if generation else None
    out["onsite_generation_source_ids"] = generation["source_ids"] if generation else None
    out["investment_value"] = investment["value"] if investment else None
    out["investment_value_high"] = investment.get("value_high") if investment else None
    out["investment_currency"] = investment["unit"] if investment else None
    out["investment_qualifier"] = investment["qualifier"] if investment else None
    out["investment_as_of"] = investment["as_of"] if investment else None
    out["investment_source_ids"] = investment["source_ids"] if investment else None
    out["accelerators"] = accelerators["value"] if accelerators else None
    out["accelerator_model"] = accelerators.get("accelerator_model") if accelerators else None
    out["accelerators_basis"] = accelerators["basis"] if accelerators else None
    out["accelerators_source_ids"] = accelerators["source_ids"] if accelerators else None
    return out


def describe_date(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    start, end = partial_date_bounds(value)
    return {"value": value, "precision": date_precision(value), "start": start.isoformat(),
            "end": end.isoformat()}


class Countries:
    """ISO 3166-1 alpha-2 code to display name and UN region, from Natural Earth."""

    def __init__(self, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        best: dict[str, dict[str, Any]] = {}
        for country in data["countries"]:
            size = sum(len(ring) for polygon in country["polygons"] for ring in polygon)
            current = best.get(country["iso_a2"])
            if current is None or size > current["size"]:
                best[country["iso_a2"]] = {**country, "size": size}
        self._by_code = best

    def name(self, code: str | None) -> str | None:
        if code is None:
            return None
        entry = self._by_code.get(code)
        return entry["name"] if entry else code

    def region(self, code: str | None) -> str | None:
        entry = self._by_code.get(code) if code else None
        return entry["region"] if entry else None

    def subregion(self, code: str | None) -> str | None:
        entry = self._by_code.get(code) if code else None
        return entry["subregion"] if entry else None
