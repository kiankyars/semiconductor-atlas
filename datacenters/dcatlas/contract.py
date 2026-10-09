"""Normative record contract for curated data center build projects.

Every published value is a sourced statement. The validator rejects records whose fields cannot be
traced to a listed source, and it never fills gaps: an absent metric means "not found in a cited
source", not zero.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Iterable

SCHEMA_VERSION = "1.0"

ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SOURCE_ID_PATTERN = re.compile(r"^s[1-9][0-9]*$")
DAY_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
PARTIAL_DATE_PATTERN = re.compile(
    r"^\d{4}(?:-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12]\d|3[01]))?|-Q[1-4]|-H[12])?$"
)
COUNTRY_PATTERN = re.compile(r"^[A-Z]{2}$")
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
URL_PATTERN = re.compile(r"^https?://[^\s/$.?#][^\s]*$")

STATUSES = (
    "proposed",
    "announced",
    "under_construction",
    "partially_operational",
    "operational",
    "paused",
    "cancelled",
)
STATUS_LABELS = {
    "proposed": "Proposed",
    "announced": "Announced",
    "under_construction": "Under construction",
    "partially_operational": "Partially operational",
    "operational": "Operational",
    "paused": "Paused",
    "cancelled": "Cancelled",
}

WORKLOADS = (
    "ai_training",
    "ai_inference",
    "ai_unspecified",
    "cloud",
    "colocation",
    "enterprise",
    "hpc",
    "crypto_mining",
)

PARTY_ROLES = (
    "developer",
    "owner",
    "operator",
    "tenant",
    "investor",
    "builder",
    "power_supplier",
    "utility",
    "hardware_supplier",
    "government",
)

PRECISIONS = ("site", "approximate_site", "locality", "admin2", "admin1")

METRIC_UNITS = {
    "power_capacity": ("MW",),
    "investment": None,  # ISO 4217 code, original currency; never converted.
    "accelerators": ("count",),
    "floor_area": ("sq_ft", "sq_m"),
    "land_area": ("acres", "hectares"),
    "buildings": ("count",),
    "jobs_construction": ("count",),
    "jobs_permanent": ("count",),
}
METRIC_BASES = {
    "power_capacity": ("planned", "contracted", "permitted", "under_construction", "operational"),
    "investment": ("announced", "incentive_agreement", "reported"),
    "accelerators": ("planned", "installed", "operational"),
    "floor_area": ("planned", "under_construction", "operational"),
    "land_area": ("planned", "acquired", "reported"),
    "buildings": ("planned", "under_construction", "operational"),
    "jobs_construction": ("planned", "reported"),
    "jobs_permanent": ("planned", "reported"),
}
POWER_SCOPES = ("it_load", "facility", "grid_connection", "onsite_generation", "unspecified")
QUALIFIERS = ("exact", "approximately", "up_to", "at_least", "more_than", "range")
APPLIES_TO = ("campus", "phase")

MILESTONE_EVENTS = (
    "announced",
    "site_acquired",
    "zoning_approved",
    "permit_approved",
    "construction_started",
    "first_operational",
    "fully_operational",
    "expansion_announced",
    "paused",
    "cancelled",
    "other",
)
MILESTONE_KINDS = ("actual", "planned")

POWER_SOURCE_TYPES = (
    "grid",
    "natural_gas_onsite",
    "nuclear",
    "solar",
    "wind",
    "hydro",
    "geothermal",
    "battery_storage",
    "fuel_cell",
    "diesel_backup",
    "other",
)

SOURCE_TYPES = (
    "company",
    "government",
    "regulatory_filing",
    "utility",
    "court",
    "news",
    "trade_press",
    "analyst",
    "other",
)

TOP_LEVEL_REQUIRED = (
    "schema_version",
    "id",
    "name",
    "summary",
    "location",
    "status",
    "status_as_of",
    "status_source_ids",
    "parties",
    "metrics",
    "milestones",
    "sources",
    "last_reviewed",
)
TOP_LEVEL_OPTIONAL = ("aliases", "workloads", "power_sources", "tags", "related_ids", "notes")

MAX_SUMMARY = 700
MAX_NOTE = 600


class ContractError(ValueError):
    """Raised when a record or collection violates the contract."""


def partial_date_bounds(value: str) -> tuple[_dt.date, _dt.date]:
    """Return the inclusive calendar interval named by a partial date."""
    if not PARTIAL_DATE_PATTERN.match(value):
        raise ContractError(f"invalid partial date {value!r}")
    year = int(value[:4])
    rest = value[5:]
    if not rest:
        return _dt.date(year, 1, 1), _dt.date(year, 12, 31)
    if rest.startswith("Q"):
        quarter = int(rest[1])
        start = _dt.date(year, 3 * quarter - 2, 1)
        return start, _month_end(year, 3 * quarter)
    if rest.startswith("H"):
        half = int(rest[1])
        return _dt.date(year, 6 * half - 5, 1), _month_end(year, 6 * half)
    parts = rest.split("-")
    month = int(parts[0])
    if len(parts) == 1:
        return _dt.date(year, month, 1), _month_end(year, month)
    day = _dt.date(year, month, int(parts[1]))
    return day, day


def _month_end(year: int, month: int) -> _dt.date:
    if month == 12:
        return _dt.date(year, 12, 31)
    return _dt.date(year, month + 1, 1) - _dt.timedelta(days=1)


def date_precision(value: str) -> str:
    """Name the calendar precision of a partial date literal."""
    rest = value[5:]
    if not rest:
        return "year"
    if rest.startswith("Q"):
        return "quarter"
    if rest.startswith("H"):
        return "half"
    return "month" if len(rest) == 2 else "day"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


def _check_text(errors: list[str], where: str, value: Any, *, max_len: int | None = None) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{where}: must be a non-empty string")
        return
    if value != value.strip():
        errors.append(f"{where}: must not have leading or trailing whitespace")
    if max_len is not None and len(value) > max_len:
        errors.append(f"{where}: longer than {max_len} characters")


def _check_day(errors: list[str], where: str, value: Any) -> _dt.date | None:
    if not isinstance(value, str) or not DAY_PATTERN.match(value):
        errors.append(f"{where}: must be a YYYY-MM-DD date")
        return None
    try:
        return _dt.date.fromisoformat(value)
    except ValueError:
        errors.append(f"{where}: not a real calendar date")
        return None


def _check_partial(errors: list[str], where: str, value: Any) -> tuple[_dt.date, _dt.date] | None:
    if not isinstance(value, str):
        errors.append(f"{where}: must be a partial date string")
        return None
    try:
        return partial_date_bounds(value)
    except (ContractError, ValueError):
        errors.append(f"{where}: must be YYYY, YYYY-MM, YYYY-MM-DD, YYYY-Qn or YYYY-Hn")
        return None


def _check_enum(errors: list[str], where: str, value: Any, allowed: Iterable[str]) -> bool:
    allowed = tuple(allowed)
    if value not in allowed:
        errors.append(f"{where}: {value!r} is not one of {', '.join(allowed)}")
        return False
    return True


def _check_keys(errors: list[str], where: str, obj: Any, required: Iterable[str],
                optional: Iterable[str] = ()) -> bool:
    if not isinstance(obj, dict):
        errors.append(f"{where}: must be an object")
        return False
    required = tuple(required)
    allowed = set(required) | set(optional)
    for key in required:
        if key not in obj:
            errors.append(f"{where}: missing required key {key!r}")
    for key in obj:
        if key not in allowed:
            errors.append(f"{where}: unknown key {key!r}")
    return True


def _check_source_ids(errors: list[str], where: str, value: Any, known: set[str],
                      used: set[str]) -> None:
    if not isinstance(value, list) or not value:
        errors.append(f"{where}: must be a non-empty list of source ids")
        return
    if len(set(value)) != len(value):
        errors.append(f"{where}: repeats a source id")
    for sid in value:
        if sid not in known:
            errors.append(f"{where}: unknown source id {sid!r}")
        else:
            used.add(sid)


def validate_project(record: Any) -> list[str]:
    """Return every contract violation in one curated project record."""
    errors: list[str] = []
    if not _check_keys(errors, "record", record, TOP_LEVEL_REQUIRED, TOP_LEVEL_OPTIONAL):
        return errors
    if record.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version: must be {SCHEMA_VERSION!r}")
    rid = record.get("id")
    if not isinstance(rid, str) or not ID_PATTERN.match(rid) or len(rid) > 64:
        errors.append("id: must be a lowercase kebab-case slug of at most 64 characters")
    _check_text(errors, "name", record.get("name"), max_len=160)
    _check_text(errors, "summary", record.get("summary"), max_len=MAX_SUMMARY)
    reviewed = _check_day(errors, "last_reviewed", record.get("last_reviewed"))

    known: set[str] = set()
    used: set[str] = set()
    sources = record.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append("sources: must be a non-empty list")
        sources = []
    seen_urls: set[str] = set()
    for i, source in enumerate(sources):
        where = f"sources[{i}]"
        if not _check_keys(errors, where, source,
                           ("id", "url", "title", "publisher", "published", "accessed", "type"),
                           ("archived_url",)):
            continue
        sid = source.get("id")
        if not isinstance(sid, str) or not SOURCE_ID_PATTERN.match(sid):
            errors.append(f"{where}.id: must look like s1, s2, ...")
        elif sid in known:
            errors.append(f"{where}.id: duplicate source id {sid!r}")
        else:
            known.add(sid)
        url = source.get("url")
        if not isinstance(url, str) or not URL_PATTERN.match(url):
            errors.append(f"{where}.url: must be an http(s) URL")
        elif url in seen_urls:
            errors.append(f"{where}.url: duplicate source URL")
        else:
            seen_urls.add(url)
        if "archived_url" in source and (
            not isinstance(source["archived_url"], str)
            or not URL_PATTERN.match(source["archived_url"])
        ):
            errors.append(f"{where}.archived_url: must be an http(s) URL")
        _check_text(errors, f"{where}.title", source.get("title"), max_len=300)
        _check_text(errors, f"{where}.publisher", source.get("publisher"), max_len=160)
        published = source.get("published")
        published_bounds = None
        if published is not None:
            published_bounds = _check_partial(errors, f"{where}.published", published)
        accessed = _check_day(errors, f"{where}.accessed", source.get("accessed"))
        if accessed and published_bounds and published_bounds[0] > accessed:
            errors.append(f"{where}: published after it was accessed")
        if accessed and reviewed and accessed > reviewed:
            errors.append(f"{where}.accessed: later than last_reviewed")
        _check_enum(errors, f"{where}.type", source.get("type"), SOURCE_TYPES)

    for key in ("aliases", "tags"):
        values = record.get(key, [])
        if not isinstance(values, list) or len(set(values)) != len(values):
            errors.append(f"{key}: must be a list without duplicates")
            continue
        for j, value in enumerate(values):
            _check_text(errors, f"{key}[{j}]", value, max_len=160)
    related = record.get("related_ids", [])
    if not isinstance(related, list) or any(
        not isinstance(v, str) or not ID_PATTERN.match(v) or v == rid for v in related
    ):
        errors.append("related_ids: must be a list of other project ids")
    workloads = record.get("workloads", [])
    if not isinstance(workloads, list) or len(set(workloads)) != len(workloads):
        errors.append("workloads: must be a list without duplicates")
    else:
        for j, value in enumerate(workloads):
            _check_enum(errors, f"workloads[{j}]", value, WORKLOADS)
    if "notes" in record:
        _check_text(errors, "notes", record["notes"], max_len=MAX_SUMMARY)

    location = record.get("location")
    if _check_keys(errors, "location", location,
                   ("country", "lat", "lon", "precision", "source_ids"),
                   ("admin1", "admin2", "locality", "note")):
        if not isinstance(location.get("country"), str) or not COUNTRY_PATTERN.match(
            location["country"]
        ):
            errors.append("location.country: must be an ISO 3166-1 alpha-2 code")
        for key in ("admin1", "admin2", "locality"):
            if key in location and location[key] is not None:
                _check_text(errors, f"location.{key}", location[key], max_len=120)
        lat, lon = location.get("lat"), location.get("lon")
        if not _is_number(lat) or not -90 <= lat <= 90:
            errors.append("location.lat: must be a number in [-90, 90]")
        if not _is_number(lon) or not -180 <= lon <= 180:
            errors.append("location.lon: must be a number in [-180, 180]")
        _check_enum(errors, "location.precision", location.get("precision"), PRECISIONS)
        _check_source_ids(errors, "location.source_ids", location.get("source_ids"), known, used)
        if "note" in location:
            _check_text(errors, "location.note", location["note"], max_len=MAX_NOTE)

    _check_enum(errors, "status", record.get("status"), STATUSES)
    status_day = _check_day(errors, "status_as_of", record.get("status_as_of"))
    if status_day and reviewed and status_day > reviewed:
        errors.append("status_as_of: later than last_reviewed")
    _check_source_ids(errors, "status_source_ids", record.get("status_source_ids"), known, used)

    parties = record.get("parties")
    if not isinstance(parties, list):
        errors.append("parties: must be a list")
        parties = []
    party_keys: set[tuple[str, str]] = set()
    for i, party in enumerate(parties):
        where = f"parties[{i}]"
        if not _check_keys(errors, where, party, ("name", "role", "source_ids"), ("note",)):
            continue
        _check_text(errors, f"{where}.name", party.get("name"), max_len=160)
        _check_enum(errors, f"{where}.role", party.get("role"), PARTY_ROLES)
        key = (party.get("name"), party.get("role"))
        if key in party_keys:
            errors.append(f"{where}: duplicate name and role")
        party_keys.add(key)
        _check_source_ids(errors, f"{where}.source_ids", party.get("source_ids"), known, used)
        if "note" in party:
            _check_text(errors, f"{where}.note", party["note"], max_len=MAX_NOTE)

    metrics = record.get("metrics")
    if not isinstance(metrics, list):
        errors.append("metrics: must be a list")
        metrics = []
    for i, metric in enumerate(metrics):
        _validate_metric(errors, f"metrics[{i}]", metric, known, used, reviewed)

    milestones = record.get("milestones")
    if not isinstance(milestones, list):
        errors.append("milestones: must be a list")
        milestones = []
    for i, milestone in enumerate(milestones):
        where = f"milestones[{i}]"
        if not _check_keys(errors, where, milestone, ("event", "date", "kind", "source_ids"),
                           ("note",)):
            continue
        _check_enum(errors, f"{where}.event", milestone.get("event"), MILESTONE_EVENTS)
        bounds = _check_partial(errors, f"{where}.date", milestone.get("date"))
        _check_enum(errors, f"{where}.kind", milestone.get("kind"), MILESTONE_KINDS)
        if (bounds and reviewed and milestone.get("kind") == "actual"
                and bounds[0] > reviewed):
            errors.append(f"{where}: an actual milestone cannot start after last_reviewed")
        _check_source_ids(errors, f"{where}.source_ids", milestone.get("source_ids"), known, used)
        if milestone.get("event") == "other" and "note" not in milestone:
            errors.append(f"{where}.note: required for event 'other'")
        if "note" in milestone:
            _check_text(errors, f"{where}.note", milestone["note"], max_len=MAX_NOTE)

    power_sources = record.get("power_sources", [])
    if not isinstance(power_sources, list):
        errors.append("power_sources: must be a list")
        power_sources = []
    for i, item in enumerate(power_sources):
        where = f"power_sources[{i}]"
        if not _check_keys(errors, where, item, ("type", "description", "source_ids")):
            continue
        _check_enum(errors, f"{where}.type", item.get("type"), POWER_SOURCE_TYPES)
        _check_text(errors, f"{where}.description", item.get("description"), max_len=MAX_NOTE)
        _check_source_ids(errors, f"{where}.source_ids", item.get("source_ids"), known, used)

    for sid in sorted(known - used):
        errors.append(f"sources: {sid!r} is not cited by any statement")
    return errors


def _validate_metric(errors: list[str], where: str, metric: Any, known: set[str], used: set[str],
                     reviewed: _dt.date | None) -> None:
    if not _check_keys(errors, where, metric,
                       ("metric", "value", "qualifier", "unit", "basis", "applies_to", "as_of",
                        "source_ids"),
                       ("value_high", "power_scope", "accelerator_model", "phase_label", "note")):
        return
    name = metric.get("metric")
    if not _check_enum(errors, f"{where}.metric", name, METRIC_UNITS):
        return
    value = metric.get("value")
    if not _is_number(value) or value <= 0:
        errors.append(f"{where}.value: must be a positive number")
    qualifier = metric.get("qualifier")
    _check_enum(errors, f"{where}.qualifier", qualifier, QUALIFIERS)
    if qualifier == "range":
        high = metric.get("value_high")
        if not _is_number(high) or not _is_number(value) or high <= value:
            errors.append(f"{where}.value_high: a range needs value_high greater than value")
    elif "value_high" in metric:
        errors.append(f"{where}.value_high: only allowed with qualifier 'range'")
    unit = metric.get("unit")
    units = METRIC_UNITS[name]
    if units is None:
        if not isinstance(unit, str) or not CURRENCY_PATTERN.match(unit):
            errors.append(f"{where}.unit: investment must use an ISO 4217 currency code")
    else:
        _check_enum(errors, f"{where}.unit", unit, units)
    _check_enum(errors, f"{where}.basis", metric.get("basis"), METRIC_BASES[name])
    if name == "power_capacity":
        _check_enum(errors, f"{where}.power_scope", metric.get("power_scope"), POWER_SCOPES)
    elif "power_scope" in metric:
        errors.append(f"{where}.power_scope: only allowed on power_capacity")
    if "accelerator_model" in metric:
        if name != "accelerators":
            errors.append(f"{where}.accelerator_model: only allowed on accelerators")
        _check_text(errors, f"{where}.accelerator_model", metric["accelerator_model"], max_len=120)
    applies_to = metric.get("applies_to")
    _check_enum(errors, f"{where}.applies_to", applies_to, APPLIES_TO)
    if "phase_label" in metric:
        if applies_to != "phase":
            errors.append(f"{where}.phase_label: only allowed when applies_to is 'phase'")
        _check_text(errors, f"{where}.phase_label", metric["phase_label"], max_len=120)
    bounds = _check_partial(errors, f"{where}.as_of", metric.get("as_of"))
    if bounds and reviewed and bounds[0] > reviewed:
        errors.append(f"{where}.as_of: later than last_reviewed")
    _check_source_ids(errors, f"{where}.source_ids", metric.get("source_ids"), known, used)
    if "note" in metric:
        _check_text(errors, f"{where}.note", metric["note"], max_len=MAX_NOTE)


def validate_collection(records: list[dict[str, Any]]) -> list[str]:
    """Validate every record plus cross-record identity and references."""
    errors: list[str] = []
    ids: dict[str, int] = {}
    for index, record in enumerate(records):
        label = record.get("id") if isinstance(record, dict) else None
        for message in validate_project(record):
            errors.append(f"{label or index}: {message}")
        if isinstance(label, str):
            if label in ids:
                errors.append(f"{label}: duplicate project id")
            ids[label] = index
    for record in records:
        if not isinstance(record, dict):
            continue
        for other in record.get("related_ids", []) or []:
            if isinstance(other, str) and other not in ids:
                errors.append(f"{record.get('id')}: related id {other!r} does not exist")
    return errors


def json_schema() -> dict[str, Any]:
    """JSON Schema (draft 2020-12) for one record, generated from the constants above.

    The Python validator is authoritative: it also checks cross-references, per-metric units and
    bases, and date ordering, which JSON Schema cannot express compactly.
    """
    text = {"type": "string", "minLength": 1}
    partial = {"type": "string", "pattern": PARTIAL_DATE_PATTERN.pattern}
    day = {"type": "string", "pattern": DAY_PATTERN.pattern}
    source_ids = {"type": "array", "minItems": 1, "uniqueItems": True,
                  "items": {"type": "string", "pattern": SOURCE_ID_PATTERN.pattern}}
    every_basis = sorted({b for bases in METRIC_BASES.values() for b in bases})
    every_unit = sorted({u for units in METRIC_UNITS.values() if units for u in units})

    def obj(required: tuple[str, ...], properties: dict[str, Any]) -> dict[str, Any]:
        return {"type": "object", "additionalProperties": False, "required": list(required),
                "properties": properties}

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://kiankyars.github.io/semiconductor-atlas/data/project.schema.json",
        "title": "Open Data Center Atlas project record",
        "description": "One curated data center build. Every statement cites record-local "
                       "source ids. See CONTRIBUTING.md for field semantics.",
        **obj(TOP_LEVEL_REQUIRED, {
            "schema_version": {"const": SCHEMA_VERSION},
            "id": {"type": "string", "pattern": ID_PATTERN.pattern, "maxLength": 64},
            "name": {**text, "maxLength": 160},
            "aliases": {"type": "array", "uniqueItems": True, "items": text},
            "summary": {**text, "maxLength": MAX_SUMMARY},
            "location": obj(("country", "lat", "lon", "precision", "source_ids"), {
                "country": {"type": "string", "pattern": COUNTRY_PATTERN.pattern},
                "admin1": {"type": ["string", "null"]},
                "admin2": {"type": ["string", "null"]},
                "locality": {"type": ["string", "null"]},
                "lat": {"type": "number", "minimum": -90, "maximum": 90},
                "lon": {"type": "number", "minimum": -180, "maximum": 180},
                "precision": {"enum": list(PRECISIONS)},
                "source_ids": source_ids,
                "note": {**text, "maxLength": MAX_NOTE},
            }),
            "status": {"enum": list(STATUSES)},
            "status_as_of": day,
            "status_source_ids": source_ids,
            "workloads": {"type": "array", "uniqueItems": True,
                          "items": {"enum": list(WORKLOADS)}},
            "parties": {"type": "array", "items": obj(("name", "role", "source_ids"), {
                "name": {**text, "maxLength": 160}, "role": {"enum": list(PARTY_ROLES)},
                "source_ids": source_ids, "note": {**text, "maxLength": MAX_NOTE}})},
            "metrics": {"type": "array", "items": obj(
                ("metric", "value", "qualifier", "unit", "basis", "applies_to", "as_of",
                 "source_ids"), {
                    "metric": {"enum": list(METRIC_UNITS)},
                    "value": {"type": "number", "exclusiveMinimum": 0},
                    "value_high": {"type": "number", "exclusiveMinimum": 0},
                    "qualifier": {"enum": list(QUALIFIERS)},
                    "unit": {"anyOf": [{"enum": every_unit},
                                       {"type": "string", "pattern": CURRENCY_PATTERN.pattern}]},
                    "basis": {"enum": every_basis},
                    "power_scope": {"enum": list(POWER_SCOPES)},
                    "applies_to": {"enum": list(APPLIES_TO)},
                    "phase_label": {**text, "maxLength": 120},
                    "accelerator_model": {**text, "maxLength": 120},
                    "as_of": partial,
                    "source_ids": source_ids,
                    "note": {**text, "maxLength": MAX_NOTE}})},
            "milestones": {"type": "array", "items": obj(("event", "date", "kind", "source_ids"), {
                "event": {"enum": list(MILESTONE_EVENTS)}, "date": partial,
                "kind": {"enum": list(MILESTONE_KINDS)}, "source_ids": source_ids,
                "note": {**text, "maxLength": MAX_NOTE}})},
            "power_sources": {"type": "array", "items": obj(("type", "description", "source_ids"), {
                "type": {"enum": list(POWER_SOURCE_TYPES)},
                "description": {**text, "maxLength": MAX_NOTE}, "source_ids": source_ids})},
            "sources": {"type": "array", "minItems": 1, "items": obj(
                ("id", "url", "title", "publisher", "published", "accessed", "type"), {
                    "id": {"type": "string", "pattern": SOURCE_ID_PATTERN.pattern},
                    "url": {"type": "string", "pattern": URL_PATTERN.pattern},
                    "title": {**text, "maxLength": 300},
                    "publisher": {**text, "maxLength": 160},
                    "published": {"anyOf": [partial, {"type": "null"}]},
                    "accessed": day,
                    "type": {"enum": list(SOURCE_TYPES)},
                    "archived_url": {"type": "string", "pattern": URL_PATTERN.pattern}})},
            "tags": {"type": "array", "uniqueItems": True, "items": text},
            "related_ids": {"type": "array", "uniqueItems": True,
                            "items": {"type": "string", "pattern": ID_PATTERN.pattern}},
            "notes": {**text, "maxLength": MAX_SUMMARY},
            "last_reviewed": day,
        }),
    }
