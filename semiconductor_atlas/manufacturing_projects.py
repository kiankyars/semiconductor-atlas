"""Validate source-linked semiconductor manufacturing project records.

Each record in ``manufacturing_projects/records/<id>.json`` describes one wafer fab, packaging
or test facility, or a multi-fab project as its operator announced it. Records are compiled from
linked public sources. Every statement cites the sources that support it, all text is written by
the maintainers (sources are linked, not copied), and a separate review re-opens each source.

Unlike the evidence-archived release in ``web/data/2026-09-07``, these records do not archive
source bytes. They keep each source's URL, publisher, publication date and access date, so a
reader can check every statement against the live source.

The validator is deliberately strict about structure (enumerations, dates, cross-references and
status consistency) and leaves judgments about source support to the review.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "1.0"
RECORDS_DIR = Path("manufacturing_projects/records")
ARCHIVED_FACILITIES = Path("web/data/2026-09-07/facilities.csv")
REGISTRY_RECORDS = Path("web/data/registry-2026-07-20/records.jsonl")

FACILITY_TYPES = {
    "wafer_fab": "Wafer fab",
    "packaging_test": "Packaging and test",
    "wafer_fab_and_packaging": "Wafer fab and packaging",
}
IDENTITY_SCOPES = {
    "named_facility": "Named facility",
    "campus": "Multi-fab campus",
    "project": "Announced project",
}
SEGMENTS = {
    "logic": "Logic",
    "dram": "DRAM",
    "hbm": "HBM",
    "nand": "NAND flash",
    "other_memory": "Other memory",
    "analog_mixed_signal": "Analog and mixed-signal",
    "power": "Power semiconductors",
    "silicon_carbide": "Silicon carbide",
    "gallium_nitride": "Gallium nitride",
    "rf": "RF",
    "image_sensor": "Image sensors",
    "mems_sensors": "MEMS and sensors",
    "photonics": "Photonics",
    "advanced_packaging": "Advanced packaging",
    "assembly_test": "Assembly and test",
}
STATUSES = {
    "announced": "Announced",
    "under_construction": "Under construction",
    "partially_operating": "Partly operating",
    "operating": "Operating",
    "paused": "Paused",
    "cancelled": "Cancelled",
    "closed": "Closed",
    "transferred": "Transferred",
}
MILESTONE_EVENTS = {
    "announced": "Announced",
    "site_selected": "Site selected",
    "groundbreaking": "Groundbreaking",
    "construction_start": "Construction started",
    "building_complete": "Building completed",
    "tool_move_in": "Equipment move-in",
    "first_production": "First production",
    "volume_production": "Volume production",
    "opening": "Opening",
    "paused": "Paused",
    "resumed": "Resumed",
    "cancelled": "Cancelled",
    "closed": "Closed",
    "acquired": "Acquired",
    "sold": "Sold",
}
PRODUCTION_EVENTS = {"first_production", "volume_production", "opening"}
TERMINAL_EVENTS = {"paused", "cancelled", "closed"}
CONSTRUCTION_EVENTS = {"groundbreaking", "construction_start", "building_complete", "tool_move_in"}
CURRENCIES = {"USD", "EUR", "JPY", "KRW", "TWD", "CNY", "INR", "SGD", "GBP", "ILS", "MYR",
              "VND", "PHP", "THB", "CHF", "CZK", "PLN", "HUF", "CAD", "MXN"}
METRIC_UNITS = {
    "investment": CURRENCIES,
    "government_incentive": CURRENCIES,
    "capacity": {"wafers_per_month", "wafers_per_year", "units_per_day", "units_per_month",
                 "units_per_year"},
    "cleanroom_area": {"m2", "sq_ft"},
    "site_area": {"m2", "sq_ft", "hectares", "acres"},
    "jobs": {"jobs"},
}
METRIC_LABELS = {
    "investment": "Investment",
    "government_incentive": "Government incentive",
    "capacity": "Capacity",
    "cleanroom_area": "Cleanroom area",
    "site_area": "Site area",
    "jobs": "Jobs",
}
QUALIFIERS = {"exact", "approximately", "up_to", "at_least", "more_than", "range"}
BASES = {
    "planned": "Planned",
    "committed": "Committed",
    "preliminary_award": "Preliminary award",
    "awarded": "Awarded",
    "reported": "Reported",
}
APPLIES_TO = {"facility", "phase", "project", "multi_site"}
SOURCE_TYPES = {
    "company_announcement": "Company announcement",
    "company_filing": "Company filing",
    "company_report": "Company report",
    "government": "Government",
    "regulatory_filing": "Regulatory filing",
    "news": "News",
    "trade_press": "Trade press",
    "reference": "Reference",
}
PRIMARY_SOURCE_TYPES = {"company_announcement", "company_filing", "company_report",
                        "government", "regulatory_filing"}
PARTY_ROLES = {"operator", "owner", "joint_venture_partner", "technology_partner", "government", "customer", "builder"}
PRECISIONS = {"site", "locality", "admin1"}
WAFER_SIZES = {100, 150, 200, 300}
RELATIONS = {"same_campus", "phase_of", "has_phase", "successor_of", "predecessor_of"}
REGISTRY_RELATIONS = {
    "award_page": "CHIPS award page covering this project",
    "same_facility": "Registry record for this facility",
    "same_site": "Registry record for the site that includes this facility",
}
INVERSE_RELATIONS = {"same_campus": "same_campus", "phase_of": "has_phase",
                     "has_phase": "phase_of", "successor_of": "predecessor_of",
                     "predecessor_of": "successor_of"}

ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SOURCE_ID_PATTERN = re.compile(r"^s[1-9][0-9]*$")
DATE_PATTERN = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?|-(Q[1-4])|-(H[12]))?$")
COUNTRY_PATTERN = re.compile(r"^[A-Z]{2}$")
QUOTE_PAIRS = (re.compile(r"“([^“”]*)”"), re.compile(r"‘([^‘’]*)’"), re.compile(r"「([^「」]*)」"),
               re.compile(r"『([^『』]*)』"), re.compile(r"«([^«»]*)»"))
MAX_QUOTE = 80

TOP_LEVEL = {
    "schema_version": True, "id": True, "name": True, "aliases": True, "company": True, "operator": True,
    "parties": True, "facility_type": True, "identity_scope": True, "segments": True,
    "process_nodes": True, "wafer_sizes": True, "summary": True, "location": True,
    "status": True, "status_as_of": True, "status_source_ids": True, "metrics": True,
    "milestones": True, "sources": True, "related": True, "notes": False,
    "archived_record": False, "registry_records": False, "review": False,
}
MAX_SUMMARY = 900
MAX_NOTE = 1500


class ValidationError(ValueError):
    """Raised when a record or the record set is invalid."""


def parse_date(value: str) -> tuple[date, date, str]:
    """Return the first and last day covered by a dated value and its precision."""
    match = DATE_PATTERN.match(value) if isinstance(value, str) else None
    if not match:
        raise ValueError(f"invalid date {value!r}")
    year = int(match.group(1))
    month, day, quarter, half = match.group(2), match.group(3), match.group(4), match.group(5)
    if day:
        start = date(year, int(month), int(day))
        return start, start, "day"
    if month:
        first = date(year, int(month), 1)
        following = date(year + (first.month == 12), first.month % 12 + 1, 1)
        return first, date.fromordinal(following.toordinal() - 1), "month"
    if quarter:
        number = int(quarter[1])
        first = date(year, 3 * number - 2, 1)
        last_month = 3 * number
        following = date(year + (last_month == 12), last_month % 12 + 1, 1)
        return first, date.fromordinal(following.toordinal() - 1), "quarter"
    if half:
        number = int(half[1])
        return (date(year, 1, 1), date(year, 6, 30), "half") if number == 1 else (
            date(year, 7, 1), date(year, 12, 31), "half")
    return date(year, 1, 1), date(year, 12, 31), "year"


def _text(errors: list[str], where: str, value: Any, *, required: bool = True,
          limit: int = 300) -> None:
    if value is None and not required:
        return
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{where}: must be a non-empty string")
        return
    if value != value.strip() or "\n" in value or "  " in value:
        errors.append(f"{where}: must not have leading/trailing whitespace, newlines or double spaces")
    if len(value) > limit:
        errors.append(f"{where}: longer than {limit} characters")


def _source_refs(errors: list[str], where: str, value: Any, sources: set[str],
                 used: set[str], *, required: bool = True) -> None:
    if not isinstance(value, list) or (required and not value) or not all(isinstance(x, str) for x in value):
        errors.append(f"{where}: must be a non-empty list of source ids")
        return
    if len(set(value)) != len(value):
        errors.append(f"{where}: repeats a source id")
    for source_id in value:
        if source_id not in sources:
            errors.append(f"{where}: unknown source id {source_id!r}")
        else:
            used.add(source_id)


def _date(errors: list[str], where: str, value: Any, *, today: date,
          future_ok: bool = False) -> tuple[date, date, str] | None:
    try:
        parsed = parse_date(value)
    except (ValueError, TypeError):
        errors.append(f"{where}: invalid date {value!r} (use YYYY, YYYY-MM, YYYY-MM-DD, YYYY-Qn or YYYY-Hn)")
        return None
    if not future_ok and parsed[0] > today:
        errors.append(f"{where}: {value} is after the access date {today.isoformat()}")
    return parsed


def quoted_passages(text: str) -> list[str]:
    """Every passage inside a pair of quotation marks (straight, curly, CJK corner or guillemets)."""
    passages = text.split('"')[1::2] if text.count('"') >= 2 else []
    for pattern in QUOTE_PAIRS:
        passages.extend(pattern.findall(text))
    return passages


def _no_quotes(errors: list[str], where: str, value: Any) -> None:
    if isinstance(value, str) and any(len(x) >= MAX_QUOTE for x in quoted_passages(value)):
        errors.append(f"{where}: contains a long quoted passage; write it in your own words")


def _one_of(value: Any, allowed: Any) -> bool:
    """Membership that never raises on unhashable values."""
    return isinstance(value, (str, int)) and not isinstance(value, bool) and value in allowed


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def latest_calendar_date() -> date:
    """The latest date currently in use anywhere (UTC+14).

    Researchers record dates in their own time zone, so a check run in UTC must not call a date
    that has already begun elsewhere "in the future".
    """
    return (datetime.now(timezone.utc) + timedelta(hours=14)).date()


def validate_record(record: Any, *, stem: str | None = None,
                    today: date | None = None) -> list[str]:
    """Return every structural problem in one record (an empty list means valid).

    ``today`` bounds every date (sources cannot be read, nor events reported, in the future);
    it defaults to the latest calendar date anywhere, so a check run in UTC accepts a source
    read on a date that has already begun in the researcher's time zone.
    """
    errors: list[str] = []
    today = today or latest_calendar_date()
    if not isinstance(record, dict):
        return ["record: must be a JSON object"]
    for key, required in TOP_LEVEL.items():
        if required and key not in record:
            errors.append(f"record: missing {key!r}")
    for key in record:
        if key not in TOP_LEVEL:
            errors.append(f"record: unknown field {key!r}")
    if errors:
        return errors

    if record["schema_version"] != SCHEMA_VERSION:
        errors.append(f"schema_version: must be {SCHEMA_VERSION!r}")
    if not isinstance(record["id"], str) or not ID_PATTERN.match(record["id"]):
        errors.append("id: must be lowercase kebab-case")
    elif stem is not None and record["id"] != stem:
        errors.append(f"id: {record['id']!r} does not match the file name {stem!r}")

    # Sources first, so every reference can be checked against them.
    source_ids: set[str] = set()
    accessed_dates: list[date] = []
    sources = record["sources"]
    urls: set[str] = set()
    if not isinstance(sources, list) or not sources:
        errors.append("sources: must be a non-empty list")
        sources = []
    for index, source in enumerate(sources):
        where = f"sources[{index}]"
        if not isinstance(source, dict):
            errors.append(f"{where}: must be an object")
            continue
        expected = {"id", "title", "publisher", "url", "type", "published", "accessed"}
        if set(source) != expected:
            errors.append(f"{where}: fields must be exactly {sorted(expected)}")
            continue
        if not isinstance(source["id"], str) or not SOURCE_ID_PATTERN.match(source["id"]):
            errors.append(f"{where}.id: must look like s1, s2, ...")
        elif source["id"] in source_ids:
            errors.append(f"{where}.id: duplicate {source['id']!r}")
        else:
            source_ids.add(source["id"])
        _text(errors, f"{where}.title", source["title"], limit=300)
        _text(errors, f"{where}.publisher", source["publisher"], limit=120)
        url = source["url"]
        if not isinstance(url, str) or not re.match(r"^https?://[^\s/]+\.[^\s/]+", url) or " " in url:
            errors.append(f"{where}.url: must be an absolute http(s) URL")
        elif url in urls:
            errors.append(f"{where}.url: listed twice")
        else:
            urls.add(url)
        if not _one_of(source["type"], SOURCE_TYPES):
            errors.append(f"{where}.type: must be one of {sorted(SOURCE_TYPES)}")
        accessed = None
        if not isinstance(source["accessed"], str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", source["accessed"]):
            errors.append(f"{where}.accessed: must be YYYY-MM-DD")
        else:
            try:
                accessed = date.fromisoformat(source["accessed"])
                accessed_dates.append(accessed)
            except ValueError:
                errors.append(f"{where}.accessed: invalid date")
            if accessed and accessed > today:
                errors.append(f"{where}.accessed: {source['accessed']} is in the future")
                accessed = today
        if source["published"] is not None:
            published = _date(errors, f"{where}.published", source["published"],
                              today=accessed or today)
            if published and accessed and published[0] > accessed:
                errors.append(f"{where}.published: after its access date")
    limit_date = min(today, max(accessed_dates)) if accessed_dates else today
    read_on = {source["id"]: source for source in sources
               if isinstance(source, dict) and isinstance(source.get("id"), str)}
    used: set[str] = set()

    _text(errors, "name", record["name"], limit=160)
    if not isinstance(record["aliases"], list) or not all(isinstance(x, str) for x in record["aliases"]):
        errors.append("aliases: must be a list")
    else:
        for index, alias in enumerate(record["aliases"]):
            _text(errors, f"aliases[{index}]", alias, limit=160)
        if len(set(record["aliases"])) != len(record["aliases"]) or record["name"] in record["aliases"]:
            errors.append("aliases: must be unique and differ from the name")
    _text(errors, "company", record["company"], limit=80)
    _text(errors, "operator", record["operator"], limit=120)

    if not isinstance(record["parties"], list):
        errors.append("parties: must be a list")
    else:
        for index, party in enumerate(record["parties"]):
            where = f"parties[{index}]"
            if not isinstance(party, dict) or set(party) != {"name", "role", "share_pct", "source_ids", "note"}:
                errors.append(f"{where}: fields must be exactly name, role, share_pct, source_ids, note")
                continue
            _text(errors, f"{where}.name", party["name"], limit=160)
            if not _one_of(party["role"], PARTY_ROLES):
                errors.append(f"{where}.role: must be one of {sorted(PARTY_ROLES)}")
            share = party["share_pct"]
            if share is not None and (not _number(share) or not 0 < share <= 100):
                errors.append(f"{where}.share_pct: must be null or a number in (0, 100]")
            _source_refs(errors, f"{where}.source_ids", party["source_ids"], source_ids, used)
            _text(errors, f"{where}.note", party["note"], required=False, limit=400)
            _no_quotes(errors, f"{where}.note", party["note"])

    if not _one_of(record["facility_type"], FACILITY_TYPES):
        errors.append(f"facility_type: must be one of {sorted(FACILITY_TYPES)}")
    if not _one_of(record["identity_scope"], IDENTITY_SCOPES):
        errors.append(f"identity_scope: must be one of {sorted(IDENTITY_SCOPES)}")
    segments = record["segments"]
    if not isinstance(segments, list) or not segments or any(not _one_of(x, SEGMENTS) for x in segments) \
            or len(set(segments)) != len(segments):
        errors.append(f"segments: must be a non-empty list of distinct values from {sorted(SEGMENTS)}")

    if not isinstance(record["process_nodes"], list):
        errors.append("process_nodes: must be a list")
    else:
        for index, node in enumerate(record["process_nodes"]):
            where = f"process_nodes[{index}]"
            if not isinstance(node, dict) or set(node) != {"label", "source_ids", "note"}:
                errors.append(f"{where}: fields must be exactly label, source_ids, note")
                continue
            _text(errors, f"{where}.label", node["label"], limit=80)
            _source_refs(errors, f"{where}.source_ids", node["source_ids"], source_ids, used)
            _text(errors, f"{where}.note", node["note"], required=False, limit=400)
            _no_quotes(errors, f"{where}.note", node["note"])
    if not isinstance(record["wafer_sizes"], list):
        errors.append("wafer_sizes: must be a list")
    else:
        seen_sizes = set()
        for index, size in enumerate(record["wafer_sizes"]):
            where = f"wafer_sizes[{index}]"
            if not isinstance(size, dict) or set(size) != {"mm", "source_ids"}:
                errors.append(f"{where}: fields must be exactly mm, source_ids")
                continue
            if not _one_of(size["mm"], WAFER_SIZES):
                errors.append(f"{where}.mm: must be one of {sorted(WAFER_SIZES)}")
            elif size["mm"] in seen_sizes:
                errors.append(f"{where}.mm: listed twice")
            seen_sizes.add(size["mm"])
            _source_refs(errors, f"{where}.source_ids", size["source_ids"], source_ids, used)

    _text(errors, "summary", record["summary"], limit=MAX_SUMMARY)
    _no_quotes(errors, "summary", record["summary"])
    _text(errors, "notes", record.get("notes"), required=False, limit=MAX_NOTE)
    _no_quotes(errors, "notes", record.get("notes"))

    location = record["location"]
    expected_location = {"country", "admin1", "locality", "site", "address", "lat", "lon",
                         "coordinate_source_ids", "precision", "source_ids", "note"}
    if not isinstance(location, dict) or set(location) != expected_location:
        errors.append(f"location: fields must be exactly {sorted(expected_location)}")
    else:
        if not isinstance(location["country"], str) or not COUNTRY_PATTERN.match(location["country"]):
            errors.append("location.country: must be an ISO 3166-1 alpha-2 code")
        _text(errors, "location.admin1", location["admin1"], required=False, limit=120)
        _text(errors, "location.locality", location["locality"], required=location["precision"] != "admin1",
              limit=120)
        _text(errors, "location.site", location["site"], required=False, limit=160)
        _text(errors, "location.address", location["address"], required=False, limit=240)
        if not _one_of(location["precision"], PRECISIONS):
            errors.append(f"location.precision: must be one of {sorted(PRECISIONS)}")
        if location["precision"] == "admin1" and not location["admin1"]:
            errors.append("location.precision: 'admin1' needs admin1")
        if location["precision"] == "site" and not (location["site"] or location["address"]):
            errors.append("location.precision: 'site' needs a site name or a published address")
        _source_refs(errors, "location.source_ids", location["source_ids"], source_ids, used)
        lat, lon = location["lat"], location["lon"]
        if (lat is None) != (lon is None):
            errors.append("location: lat and lon must both be null or both be numbers")
        elif lat is not None:
            if not _number(lat) or not _number(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
                errors.append("location: lat/lon out of range")
            _source_refs(errors, "location.coordinate_source_ids", location["coordinate_source_ids"],
                         source_ids, used)
        elif location["coordinate_source_ids"] != []:
            errors.append("location.coordinate_source_ids: must be empty without coordinates")
        _text(errors, "location.note", location["note"], required=False, limit=400)
        _no_quotes(errors, "location.note", location["note"])

    status = record["status"]
    if not _one_of(status, STATUSES):
        errors.append(f"status: must be one of {sorted(STATUSES)}")
        status = None
    _date(errors, "status_as_of", record["status_as_of"], today=limit_date)
    _source_refs(errors, "status_source_ids", record["status_source_ids"], source_ids, used)

    if not isinstance(record["metrics"], list):
        errors.append("metrics: must be a list")
    else:
        expected_metric = {"metric", "value", "value_max", "unit", "qualifier", "basis", "applies_to",
                           "phase_label", "as_of", "source_ids", "note"}
        for index, metric in enumerate(record["metrics"]):
            where = f"metrics[{index}]"
            if not isinstance(metric, dict) or set(metric) != expected_metric:
                errors.append(f"{where}: fields must be exactly {sorted(expected_metric)}")
                continue
            kind = metric["metric"]
            if not _one_of(kind, METRIC_UNITS):
                errors.append(f"{where}.metric: must be one of {sorted(METRIC_UNITS)}")
            elif not _one_of(metric["unit"], METRIC_UNITS[kind]):
                errors.append(f"{where}.unit: {metric['unit']!r} is not valid for {kind}")
            value = metric["value"]
            if not _number(value) or value <= 0:
                errors.append(f"{where}.value: must be a positive number")
            if not _one_of(metric["qualifier"], QUALIFIERS):
                errors.append(f"{where}.qualifier: must be one of {sorted(QUALIFIERS)}")
            maximum = metric["value_max"]
            if metric["qualifier"] == "range":
                if not _number(maximum) or not _number(value) or maximum <= value:
                    errors.append(f"{where}.value_max: a range needs value_max greater than value")
            elif maximum is not None:
                errors.append(f"{where}.value_max: only a range has value_max")
            if not _one_of(metric["basis"], BASES):
                errors.append(f"{where}.basis: must be one of {sorted(BASES)}")
            if kind == "government_incentive" and metric["basis"] == "reported":
                errors.append(f"{where}.basis: use planned, preliminary_award, awarded or committed for incentives")
            if not _one_of(metric["applies_to"], APPLIES_TO):
                errors.append(f"{where}.applies_to: must be one of {sorted(APPLIES_TO)}")
            if metric["applies_to"] == "phase":
                _text(errors, f"{where}.phase_label", metric["phase_label"], limit=80)
            elif metric["phase_label"] is not None:
                errors.append(f"{where}.phase_label: only a phase metric has a phase label")
            if metric["applies_to"] == "multi_site" and not metric["note"]:
                errors.append(f"{where}.note: a multi-site amount must say what it covers")
            if kind == "capacity" and not metric["note"]:
                errors.append(f"{where}.note: capacity must state the wafer size or unit basis")
            _date(errors, f"{where}.as_of", metric["as_of"], today=limit_date)
            _source_refs(errors, f"{where}.source_ids", metric["source_ids"], source_ids, used)
            _text(errors, f"{where}.note", metric["note"], required=False, limit=400)
            _no_quotes(errors, f"{where}.note", metric["note"])

    actual_events: set[str] = set()
    completed: list[tuple[date, str, Any]] = []
    if not isinstance(record["milestones"], list):
        errors.append("milestones: must be a list")
    else:
        expected_milestone = {"event", "date", "planned", "phase_label", "source_ids", "note"}
        for index, milestone in enumerate(record["milestones"]):
            where = f"milestones[{index}]"
            if not isinstance(milestone, dict) or set(milestone) != expected_milestone:
                errors.append(f"{where}: fields must be exactly {sorted(expected_milestone)}")
                continue
            if not _one_of(milestone["event"], MILESTONE_EVENTS):
                errors.append(f"{where}.event: must be one of {sorted(MILESTONE_EVENTS)}")
            if not isinstance(milestone["planned"], bool):
                errors.append(f"{where}.planned: must be true or false")
            parsed = _date(errors, f"{where}.date", milestone["date"], today=limit_date,
                           future_ok=milestone["planned"] is True)
            if milestone["planned"] is False and _one_of(milestone["event"], MILESTONE_EVENTS):
                actual_events.add(milestone["event"])
                if parsed:
                    completed.append((parsed[0], milestone["event"], milestone["phase_label"]))
                    cited = [read_on[x] for x in milestone["source_ids"] or [] if isinstance(x, str) and x in read_on]
                    reads = [x["accessed"] for x in cited if isinstance(x.get("accessed"), str)]
                    if reads and parsed[0].isoformat() > max(reads):
                        errors.append(f"{where}.date: reported as happened after its sources were read")
            _text(errors, f"{where}.phase_label", milestone["phase_label"], required=False, limit=80)
            _source_refs(errors, f"{where}.source_ids", milestone["source_ids"], source_ids, used)
            _text(errors, f"{where}.note", milestone["note"], required=False, limit=400)
            _no_quotes(errors, f"{where}.note", milestone["note"])

    if status in {"operating", "partially_operating"} and not actual_events & PRODUCTION_EVENTS:
        errors.append(f"status: {status} needs a completed first_production, volume_production or opening milestone")
    if status == "under_construction" and not actual_events & CONSTRUCTION_EVENTS:
        errors.append("status: under_construction needs a completed groundbreaking, construction or tool milestone")
    for state in ("paused", "cancelled", "closed"):
        if status == state and state not in actual_events:
            errors.append(f"status: {state} needs a completed {state} milestone")
    if status == "transferred" and "sold" not in actual_events:
        errors.append("status: transferred needs a completed sold milestone")
    if status == "announced" and actual_events & (CONSTRUCTION_EVENTS | PRODUCTION_EVENTS):
        errors.append("status: announced cannot have completed construction or production milestones")
    if status == "under_construction" and actual_events & PRODUCTION_EVENTS:
        errors.append("status: under_construction cannot have a completed production milestone "
                      "(use partially_operating)")
    # The latest facility-wide pause, cancellation or closure must match the status unless a later
    # completed event (a resumption, new construction, production or a change of owner) supersedes it.
    terminal = [x for x in completed if x[1] in TERMINAL_EVENTS and x[2] is None]
    if terminal:
        stop = max(terminal, key=lambda x: x[0])
        later = [x for x in completed if x[0] > stop[0] and x[1] not in TERMINAL_EVENTS]
        if not later and status != stop[1]:
            errors.append(f"status: {status} contradicts the completed {stop[1]} milestone of {stop[0].isoformat()}")
        if later and status == stop[1]:
            errors.append(f"status: {status} contradicts later completed milestones")

    if not isinstance(record["related"], list):
        errors.append("related: must be a list")
    else:
        for index, link in enumerate(record["related"]):
            where = f"related[{index}]"
            if not isinstance(link, dict) or set(link) != {"id", "relation"}:
                errors.append(f"{where}: fields must be exactly id, relation")
                continue
            if not isinstance(link["id"], str) or not ID_PATTERN.match(link["id"]) or link["id"] == record["id"]:
                errors.append(f"{where}.id: must be another record id")
            if not _one_of(link["relation"], RELATIONS):
                errors.append(f"{where}.relation: must be one of {sorted(RELATIONS)}")
    if "archived_record" in record and (not isinstance(record["archived_record"], str)
                                        or not record["archived_record"]):
        errors.append("archived_record: must be a stable key from the archived release")
    if "registry_records" in record:
        links = record["registry_records"]
        if not isinstance(links, list) or not links:
            errors.append("registry_records: must be a non-empty list when present")
        else:
            seen_links = set()
            for index, link in enumerate(links):
                where = f"registry_records[{index}]"
                if not isinstance(link, dict) or set(link) != {"record_id", "relation", "note"}:
                    errors.append(f"{where}: fields must be exactly record_id, relation, note")
                    continue
                _text(errors, f"{where}.record_id", link["record_id"], limit=200)
                if not isinstance(link["record_id"], str):
                    continue
                if link["record_id"] in seen_links:
                    errors.append(f"{where}.record_id: listed twice")
                seen_links.add(link["record_id"])
                if not _one_of(link["relation"], REGISTRY_RELATIONS):
                    errors.append(f"{where}.relation: must be one of {sorted(REGISTRY_RELATIONS)}")
                _text(errors, f"{where}.note", link["note"], required=False, limit=400)
                _no_quotes(errors, f"{where}.note", link["note"])
    if "review" in record:
        review = record["review"]
        if not isinstance(review, dict) or set(review) != {"reviewed_on", "method", "changes"}:
            errors.append("review: fields must be exactly reviewed_on, method, changes")
        else:
            if not isinstance(review["reviewed_on"], str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", review["reviewed_on"]):
                errors.append("review.reviewed_on: must be YYYY-MM-DD")
            else:
                _date(errors, "review.reviewed_on", review["reviewed_on"], today=today)
            _text(errors, "review.method", review["method"], limit=300)
            _text(errors, "review.changes", review["changes"], required=False, limit=MAX_NOTE)
            _no_quotes(errors, "review.changes", review["changes"])

    unused = sorted(source_ids - used, key=lambda x: int(x[1:]))
    if unused:
        errors.append(f"sources: {', '.join(unused)} not cited by any statement")
    return errors


def load_records(directory: Path) -> list[dict]:
    """Load every record in a directory, sorted by id, raising on any invalid record."""
    problems: list[str] = []
    records = []
    paths = sorted(directory.glob("*.json"))
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            problems.append(f"{path.name}: {error}")
            continue
        problems.extend(f"{path.name}: {problem}" for problem in validate_record(record, stem=path.stem))
        records.append(record)
    problems.extend(validate_collection(records))
    if problems:
        raise ValidationError("\n".join(problems))
    return sorted(records, key=lambda record: record["id"])


def validate_collection(records: Iterable[dict]) -> list[str]:
    """Check identifiers and links across records."""
    errors: list[str] = []
    by_id: dict[str, dict] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            continue
        if record["id"] in by_id:
            errors.append(f"{record['id']}: duplicate id")
        by_id[record["id"]] = record
    archived: dict[str, str] = {}
    for record in by_id.values():
        links = record.get("related")
        for link in links if isinstance(links, list) else []:
            if not isinstance(link, dict) or not isinstance(link.get("id"), str) or link["id"] not in by_id:
                errors.append(f"{record['id']}: related id {link.get('id') if isinstance(link, dict) else link!r} does not exist")
                continue
            inverse = INVERSE_RELATIONS.get(link["relation"]) if isinstance(link.get("relation"), str) else None
            back = by_id[link["id"]].get("related")
            back = back if isinstance(back, list) else []
            if inverse and {"id": record["id"], "relation": inverse} not in back:
                errors.append(f"{record['id']}: {link['id']} lacks the inverse {inverse!r} link")
        key = record.get("archived_record")
        if isinstance(key, str) and key:
            if key in archived:
                errors.append(f"{record['id']}: archived record {key} is already linked from {archived[key]}")
            archived[key] = record["id"]
    return errors


PAGE_ROUTE = "manufacturing/"
BUNDLE_FORMAT = "semiconductor-atlas-manufacturing-projects-v1"
PROJECT_COLUMNS = (
    "id", "name", "company", "operator", "facility_type", "identity_scope", "segments", "process_nodes",
    "wafer_sizes_mm", "country", "admin1", "locality", "site", "address", "latitude", "longitude",
    "status", "status_as_of", "announced", "production_started", "production_planned",
    "investment_value", "investment_value_max", "investment_currency", "investment_qualifier",
    "investment_basis", "investment_applies_to", "investment_as_of", "source_count",
    "primary_source_count", "archived_record", "reviewed_on", "page_path",
)
FIGURE_COLUMNS = ("record_id", "metric", "value", "value_max", "unit", "qualifier", "basis",
                  "applies_to", "phase_label", "as_of", "note", "source_ids", "source_urls")
MILESTONE_COLUMNS = ("record_id", "event", "date", "date_precision", "planned", "phase_label",
                     "note", "source_ids", "source_urls")
SOURCE_COLUMNS = ("record_id", "source_id", "title", "publisher", "url", "type", "published",
                  "accessed")


def _earliest(record: dict, events: set[str], *, planned: bool) -> str | None:
    dated = [(parse_date(m["date"])[0], m["date"]) for m in record["milestones"]
             if m["event"] in events and m["planned"] is planned]
    return min(dated)[1] if dated else None


def _newest_source(record: dict, ids: list[str]) -> date:
    published = {source["id"]: source["published"] for source in record["sources"]}
    dated = [parse_date(published[x])[1] for x in ids if published.get(x)]
    return max(dated) if dated else date.min


def headline_investment(record: dict) -> dict | None:
    """The most recent investment figure covering the whole facility or project.

    Among figures with the latest ``as_of`` date, a USD figure is preferred (sources that give one
    amount in two currencies). If the remaining figures still disagree, for example two parties'
    separate contributions, there is no single headline and None is returned. Phase-only and
    multi-site amounts are never headlines.
    """
    figures = [m for m in record["metrics"] if m["metric"] == "investment"
               and m["applies_to"] in {"facility", "project"}]
    if not figures:
        return None
    latest = max(parse_date(m["as_of"])[0] for m in figures)
    figures = [m for m in figures if parse_date(m["as_of"])[0] == latest]
    figures = [m for m in figures if m["unit"] == "USD"] or figures
    if len({(m["unit"], m["value"], m["value_max"], m["qualifier"]) for m in figures}) > 1:
        return None
    return figures[0]


def current_production_target(record: dict) -> str | None:
    """The production target stated by the newest source, ignoring targets later sources replaced.

    When the newest source gives several targets (one per phase), the earliest is the facility's
    first production target.
    """
    targets = [m for m in record["milestones"] if m["event"] in PRODUCTION_EVENTS and m["planned"] is True]
    if not targets:
        return None
    newest = max(_newest_source(record, m["source_ids"]) for m in targets)
    return min((m for m in targets if _newest_source(record, m["source_ids"]) == newest),
               key=lambda m: parse_date(m["date"])[0])["date"]


def production_dates(record: dict) -> tuple[str | None, str | None]:
    """Earliest reported production event, or else the current production target."""
    started = _earliest(record, PRODUCTION_EVENTS, planned=False)
    planned = None if started else current_production_target(record)
    return started, planned


def _urls(record: dict, ids: list[str]) -> list[str]:
    by_id = {source["id"]: source["url"] for source in record["sources"]}
    return [by_id[x] for x in ids]


def _flat(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "; ".join(_flat(x) for x in value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _csv_bytes(columns: tuple[str, ...], rows: Iterable[dict]) -> bytes:
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        cells = []
        for column in columns:
            text = _flat(row.get(column))
            if text[:1] in ("=", "+", "-", "@", "\t", "\r") and column not in ("latitude", "longitude"):
                text = "'" + text  # keep spreadsheets from evaluating source text
            cells.append(text)
        writer.writerow(cells)
    return buffer.getvalue().encode("utf-8")


def project_rows(records: list[dict]) -> list[dict]:
    rows = []
    for record in records:
        location = record["location"]
        investment = headline_investment(record) or {}
        started, planned = production_dates(record)
        rows.append({
            "id": record["id"], "name": record["name"], "company": record["company"],
            "operator": record["operator"],
            "facility_type": record["facility_type"], "identity_scope": record["identity_scope"],
            "segments": record["segments"],
            "process_nodes": [node["label"] for node in record["process_nodes"]],
            "wafer_sizes_mm": [size["mm"] for size in record["wafer_sizes"]],
            "country": location["country"], "admin1": location["admin1"],
            "locality": location["locality"], "site": location["site"],
            "address": location["address"], "latitude": location["lat"], "longitude": location["lon"],
            "status": record["status"], "status_as_of": record["status_as_of"],
            "announced": _earliest(record, {"announced"}, planned=False),
            "production_started": started, "production_planned": planned,
            "investment_value": investment.get("value"),
            "investment_value_max": investment.get("value_max"),
            "investment_currency": investment.get("unit"),
            "investment_qualifier": investment.get("qualifier"),
            "investment_basis": investment.get("basis"),
            "investment_applies_to": investment.get("applies_to"),
            "investment_as_of": investment.get("as_of"),
            "source_count": len(record["sources"]),
            "primary_source_count": sum(s["type"] in PRIMARY_SOURCE_TYPES for s in record["sources"]),
            "archived_record": record.get("archived_record"),
            "reviewed_on": (record.get("review") or {}).get("reviewed_on"),
            "page_path": PAGE_ROUTE + record["id"] + "/",
        })
    return rows


def has_primary_source(record: dict) -> bool:
    """Whether at least one cited source is a company, government or regulatory source."""
    return any(source["type"] in PRIMARY_SOURCE_TYPES for source in record["sources"])


def counts(records: list[dict]) -> dict:
    def tally(values: Iterable[str]) -> dict[str, int]:
        result: dict[str, int] = {}
        for value in values:
            result[value] = result.get(value, 0) + 1
        return dict(sorted(result.items()))

    sources = [source for record in records for source in record["sources"]]
    return {
        "records": len(records),
        "companies": len({record["company"] for record in records}),
        "operators": len({record["operator"] for record in records}),
        "countries": tally(record["location"]["country"] for record in records),
        "statuses": tally(record["status"] for record in records),
        "facility_types": tally(record["facility_type"] for record in records),
        "segments": tally(segment for record in records for segment in record["segments"]),
        "source_citations": len(sources),
        "distinct_source_urls": len({source["url"] for source in sources}),
        "primary_source_citations": sum(source["type"] in PRIMARY_SOURCE_TYPES for source in sources),
        "press_only_records": sum(not has_primary_source(record) for record in records),
        "reviewed_records": sum("review" in record for record in records),
        "with_coordinates": sum(record["location"]["lat"] is not None for record in records),
    }


def bundle_readme(summary: dict, as_of: str) -> str:
    statuses = ", ".join(f"{STATUSES[k].lower()} {v}" for k, v in summary["statuses"].items())
    return f"""# Semiconductor manufacturing projects

Source-linked records of {summary['records']} semiconductor wafer fabs, fab expansions and packaging
and test plants announced, built, opened, paused, cancelled or closed since 2020, from
{summary['companies']} companies in {len(summary['countries'])} countries. Sources were last read
on {as_of}. Status counts: {statuses}.

Every statement cites the public sources that support it. All text is written by the
maintainers; sources are linked, not copied, so check each statement against its linked source.
Records were compiled with AI-assisted research (Claude) and checked in a separate AI-assisted
review that re-opened every cited source. Coverage is a selection, not a census.

| File | Contents |
| --- | --- |
| `manufacturing_projects.jsonl` | Complete records: location, status, technology, figures, milestones, parties and sources |
| `manufacturing_projects.csv` | One row per record with its headline facts |
| `figures.csv` | Investment, incentives, capacity, area and jobs, with basis, scope and sources |
| `milestones.csv` | Dated events; `planned` marks targets rather than reported events |
| `sources.csv` | Every cited source with publisher, type, publication and access dates |
| `manifest.json` | File sizes and SHA-256 hashes |

Amounts keep the source currency. A figure's `basis` says whether it is planned, committed, a
preliminary or final award, or reported; `applies_to` says whether it covers the facility, one
phase, the whole project or several sites. Coordinates are given only when a cited source states
them for the facility; nothing is geocoded.

Field definitions and research rules: {SOURCE_URL}manufacturing_projects/README.md.
Each record links to its sources, which keep their own copyright and terms. Report errors at
{SOURCE_URL.replace('/blob/main/', '/')}issues.
"""


SOURCE_URL = "https://github.com/kiankyars/semiconductor-atlas/blob/main/"


def build_bundle(records: list[dict]) -> dict[str, bytes]:
    """Return the download bundle's files, deterministic for identical records."""
    import hashlib

    records = sorted(records, key=lambda record: record["id"])
    summary = counts(records)
    as_of = max(source["accessed"] for record in records for source in record["sources"])
    figures, milestones, sources = [], [], []
    for record in records:
        for metric in record["metrics"]:
            figures.append({"record_id": record["id"], **metric,
                            "source_urls": _urls(record, metric["source_ids"])})
        for milestone in sorted(record["milestones"], key=lambda m: (parse_date(m["date"])[0], m["event"])):
            milestones.append({"record_id": record["id"], **milestone,
                               "date_precision": parse_date(milestone["date"])[2],
                               "source_urls": _urls(record, milestone["source_ids"])})
        for source in record["sources"]:
            sources.append({"record_id": record["id"], "source_id": source["id"],
                            **{k: v for k, v in source.items() if k != "id"}})
    files = {
        "manufacturing_projects.jsonl": "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n" for record in records).encode("utf-8"),
        "manufacturing_projects.csv": _csv_bytes(PROJECT_COLUMNS, project_rows(records)),
        "figures.csv": _csv_bytes(FIGURE_COLUMNS, figures),
        "milestones.csv": _csv_bytes(MILESTONE_COLUMNS, milestones),
        "sources.csv": _csv_bytes(SOURCE_COLUMNS, sources),
        "README.md": bundle_readme(summary, as_of).encode("utf-8"),
    }
    manifest = {
        "format": BUNDLE_FORMAT, "schema_version": SCHEMA_VERSION, "as_of": as_of,
        "counts": summary,
        "files": {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                  for name, data in sorted(files.items())},
    }
    files["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
                              + "\n").encode("utf-8")
    return files


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", type=Path,
                        help="record files to check; default: every record plus cross-record links")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    directory = args.root / RECORDS_DIR
    if args.paths:
        failures = 0
        for path in args.paths:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                problems = validate_record(record, stem=path.stem)
            except (OSError, json.JSONDecodeError) as error:
                problems = [str(error)]
            for problem in problems:
                print(f"{path.name}: {problem}")
            failures += bool(problems)
            if not problems:
                print(f"{path.name}: ok")
        return 1 if failures else 0
    try:
        records = load_records(directory)
    except ValidationError as error:
        print(error)
        return 1
    if not records:
        print(f"no records found in {directory}")
        return 1
    problems = check_targets(records, args.root)
    if problems:
        print("\n".join(problems))
        return 1
    print(f"{len(records)} records valid")
    return 0


def check_targets(records: list[dict], root: Path) -> list[str]:
    """Check archived_record and registry_records against the published release and registry."""
    import csv

    problems = []
    facilities = root / ARCHIVED_FACILITIES
    registry = root / REGISTRY_RECORDS
    keys = {row["stable_key"] for row in csv.DictReader(facilities.open(encoding="utf-8"))} \
        if facilities.exists() else None
    ids = {json.loads(line)["record_id"] for line in registry.open(encoding="utf-8") if line.strip()} \
        if registry.exists() else None
    for record in records:
        if keys is not None and record.get("archived_record") and record["archived_record"] not in keys:
            problems.append(f"{record['id']}: unknown archived record {record['archived_record']}")
        for link in record.get("registry_records") or []:
            if ids is not None and link["record_id"] not in ids:
                problems.append(f"{record['id']}: unknown registry record {link['record_id']}")
    return problems


if __name__ == "__main__":
    raise SystemExit(main())
