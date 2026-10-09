"""Flat table views of the dataset, with one column dictionary shared by every export format."""

from __future__ import annotations

import json
from typing import Any, Callable

from .contract import date_precision, partial_date_bounds
from .derive import Countries, headline

LIST_SEP = "; "

# (column, frictionless type, description)
Column = tuple[str, str, str]

PROJECT_COLUMNS: list[Column] = [
    ("id", "string", "Stable project identifier (lowercase slug)."),
    ("name", "string", "Common project name."),
    ("aliases", "string", "Other names used by sources, separated by '; '."),
    ("status", "string", "proposed, announced, under_construction, partially_operational, "
     "operational, paused or cancelled."),
    ("status_as_of", "date", "Publication date of the newest source supporting the status."),
    ("country", "string", "ISO 3166-1 alpha-2 country code."),
    ("country_name", "string", "Country name (Natural Earth)."),
    ("region", "string", "UN region (Natural Earth REGION_UN)."),
    ("subregion", "string", "UN subregion (Natural Earth SUBREGION)."),
    ("admin1", "string", "State, province or equivalent."),
    ("admin2", "string", "County, parish, district or equivalent."),
    ("locality", "string", "City, town or locality."),
    ("lat", "number", "WGS84 latitude."),
    ("lon", "number", "WGS84 longitude."),
    ("location_precision", "string", "site, approximate_site, locality, admin2 or admin1."),
    ("workloads", "string", "Reported intended uses, separated by '; '."),
    ("developers", "string", "Organizations reported as developer, separated by '; '."),
    ("owners", "string", "Organizations reported as owner, separated by '; '."),
    ("operators", "string", "Organizations reported as operator, separated by '; '."),
    ("tenants", "string", "Organizations reported as tenant or customer, separated by '; '."),
    ("planned_power_mw", "number", "Headline planned power in MW (see derivation rules)."),
    ("planned_power_mw_high", "number", "Upper bound when the headline statement is a range."),
    ("planned_power_qualifier", "string", "exact, approximately, up_to, at_least, more_than "
     "or range."),
    ("planned_power_scope", "string", "it_load, facility, grid_connection or unspecified."),
    ("planned_power_basis", "string", "planned, contracted or permitted."),
    ("planned_power_as_of", "string", "Date of the headline planned-power statement."),
    ("operational_power_mw", "number", "Headline operational power in MW."),
    ("operational_power_mw_high", "number", "Upper bound when the statement is a range."),
    ("operational_power_qualifier", "string", "Qualifier of the operational-power statement."),
    ("operational_power_scope", "string", "Scope of the operational-power statement."),
    ("operational_power_basis", "string", "Always operational when present."),
    ("operational_power_as_of", "string", "Date of the operational-power statement."),
    ("investment_value", "number", "Headline announced investment, in investment_currency."),
    ("investment_value_high", "number", "Upper bound when the statement is a range."),
    ("investment_currency", "string", "ISO 4217 currency of the investment value (not "
     "converted)."),
    ("investment_qualifier", "string", "Qualifier of the investment statement."),
    ("investment_as_of", "string", "Date of the investment statement."),
    ("accelerators", "number", "Headline accelerator (GPU/TPU/other) count."),
    ("accelerator_model", "string", "Accelerator model named by the source."),
    ("accelerators_basis", "string", "planned, installed or operational."),
    ("announced", "string", "Earliest actual announcement date (partial date)."),
    ("construction_started", "string", "Earliest actual construction start (partial date)."),
    ("first_operational", "string", "Earliest actual first operation (partial date)."),
    ("first_operational_target", "string", "Latest planned first-operation target when no "
     "actual first operation is recorded."),
    ("power_source_types", "string", "Reported power supply types, separated by '; '."),
    ("source_count", "integer", "Number of cited sources."),
    ("statement_count", "integer", "Number of sourced metric, milestone, party and power "
     "statements."),
    ("summary", "string", "Neutral description written by the maintainers."),
    ("last_reviewed", "date", "Date the record was last checked against its sources."),
    ("url", "string", "Project page."),
    ("api_url", "string", "Full JSON record."),
]

STATEMENT_COLUMNS: list[Column] = [
    ("project_id", "string", "Project identifier."),
    ("metric", "string", "power_capacity, investment, accelerators, floor_area, land_area, "
     "buildings, jobs_construction or jobs_permanent."),
    ("value", "number", "Reported value (lower bound for ranges)."),
    ("value_high", "number", "Upper bound for ranges."),
    ("qualifier", "string", "exact, approximately, up_to, at_least, more_than or range."),
    ("unit", "string", "MW, ISO 4217 currency, count, sq_ft, sq_m, acres or hectares."),
    ("basis", "string", "What the number measures (planned, contracted, permitted, "
     "under_construction, operational, announced, ...)."),
    ("power_scope", "string", "For power: it_load, facility, grid_connection, "
     "onsite_generation or unspecified."),
    ("applies_to", "string", "campus or phase."),
    ("phase_label", "string", "Phase named by the source."),
    ("accelerator_model", "string", "Accelerator model for accelerator counts."),
    ("as_of", "string", "Date the source made the statement (partial date)."),
    ("source_ids", "string", "Record-local source ids, separated by '; '."),
    ("source_urls", "string", "Source URLs, separated by ' | '."),
    ("note", "string", "Maintainer note."),
]

MILESTONE_COLUMNS: list[Column] = [
    ("project_id", "string", "Project identifier."),
    ("event", "string", "announced, site_acquired, zoning_approved, permit_approved, "
     "construction_started, first_operational, fully_operational, expansion_announced, paused, "
     "cancelled or other."),
    ("date", "string", "Partial date: YYYY, YYYY-MM, YYYY-MM-DD, YYYY-Qn or YYYY-Hn."),
    ("date_precision", "string", "year, half, quarter, month or day."),
    ("date_start", "date", "First day of the date interval."),
    ("date_end", "date", "Last day of the date interval."),
    ("kind", "string", "actual or planned (a target)."),
    ("source_ids", "string", "Record-local source ids, separated by '; '."),
    ("source_urls", "string", "Source URLs, separated by ' | '."),
    ("note", "string", "Maintainer note."),
]

PARTY_COLUMNS: list[Column] = [
    ("project_id", "string", "Project identifier."),
    ("name", "string", "Organization name as used by the maintainers."),
    ("role", "string", "developer, owner, operator, tenant, investor, builder, power_supplier, "
     "utility, hardware_supplier or government."),
    ("source_ids", "string", "Record-local source ids, separated by '; '."),
    ("source_urls", "string", "Source URLs, separated by ' | '."),
    ("note", "string", "Maintainer note."),
]

POWER_SOURCE_COLUMNS: list[Column] = [
    ("project_id", "string", "Project identifier."),
    ("type", "string", "grid, natural_gas_onsite, nuclear, solar, wind, hydro, geothermal, "
     "battery_storage, fuel_cell, diesel_backup or other."),
    ("description", "string", "Maintainer paraphrase of the reported arrangement."),
    ("source_ids", "string", "Record-local source ids, separated by '; '."),
    ("source_urls", "string", "Source URLs, separated by ' | '."),
]

SOURCE_COLUMNS: list[Column] = [
    ("project_id", "string", "Project identifier."),
    ("source_id", "string", "Record-local source id."),
    ("url", "string", "Source URL."),
    ("title", "string", "Source title."),
    ("publisher", "string", "Publisher."),
    ("published", "string", "Publication date (partial date) when known."),
    ("accessed", "date", "Date the maintainers opened the source."),
    ("type", "string", "company, government, regulatory_filing, utility, court, news, "
     "trade_press, analyst or other."),
    ("archived_url", "string", "Archived copy, when recorded."),
]

OSM_TABLE_COLUMNS: list[Column] = [
    ("id", "string", "osm-<type>-<id>."),
    ("osm_type", "string", "node, way or relation."),
    ("osm_id", "integer", "OpenStreetMap element id."),
    ("osm_url", "string", "Element page on openstreetmap.org."),
    ("name", "string", "OSM name tag."),
    ("operator", "string", "OSM operator tag."),
    ("owner", "string", "OSM owner tag."),
    ("brand", "string", "OSM brand tag."),
    ("website", "string", "OSM website tag."),
    ("start_date", "string", "OSM start_date tag."),
    ("addr_street", "string", "OSM addr:street tag."),
    ("addr_city", "string", "OSM addr:city tag."),
    ("addr_state", "string", "OSM addr:state tag."),
    ("addr_postcode", "string", "OSM addr:postcode tag."),
    ("addr_country", "string", "OSM addr:country tag."),
    ("building_levels", "string", "OSM building:levels tag."),
    ("telecom", "string", "OSM telecom tag."),
    ("building", "string", "OSM building tag."),
    ("country", "string", "ISO code from addr:country, else Natural Earth boundary lookup."),
    ("country_basis", "string", "How country was assigned."),
    ("lat", "number", "Latitude of the node or element center."),
    ("lon", "number", "Longitude of the node or element center."),
    ("tags", "string", "All OSM tags as a JSON object."),
]

WIKIDATA_TABLE_COLUMNS: list[Column] = [
    ("id", "string", "wikidata-<QID>."),
    ("wikidata_id", "string", "Wikidata item id."),
    ("wikidata_url", "string", "Item page on wikidata.org."),
    ("name", "string", "English label."),
    ("country", "string", "ISO code of the item's country (P17/P297)."),
    ("inception", "string", "Earliest inception date (P571)."),
    ("website", "string", "Official website (P856)."),
    ("operators", "string", "Operators (P137), separated by '; '."),
    ("owners", "string", "Owners (P127), separated by '; '."),
    ("lat", "number", "Latitude (P625)."),
    ("lon", "number", "Longitude (P625)."),
]


def _join(values: list[str] | None) -> str | None:
    return LIST_SEP.join(values) if values else None


def _urls(record: dict[str, Any], ids: list[str]) -> str:
    by_id = {s["id"]: s["url"] for s in record["sources"]}
    return " | ".join(by_id[i] for i in ids)


def project_row(record: dict[str, Any], derived: dict[str, Any], countries: Countries,
                base_url: str) -> dict[str, Any]:
    loc = record["location"]
    row = {
        "id": record["id"],
        "name": record["name"],
        "aliases": _join(record.get("aliases")),
        "status": record["status"],
        "status_as_of": record["status_as_of"],
        "country": loc["country"],
        "country_name": countries.name(loc["country"]),
        "region": countries.region(loc["country"]),
        "subregion": countries.subregion(loc["country"]),
        "admin1": loc.get("admin1"),
        "admin2": loc.get("admin2"),
        "locality": loc.get("locality"),
        "lat": loc["lat"],
        "lon": loc["lon"],
        "location_precision": loc["precision"],
        "workloads": _join(record.get("workloads")),
        "developers": _join(derived["developers"]),
        "owners": _join(derived["owners"]),
        "operators": _join(derived["operators"]),
        "tenants": _join(derived["tenants"]),
        "power_source_types": _join(derived["power_source_types"]),
        "summary": record["summary"],
        "last_reviewed": record["last_reviewed"],
        "url": f"{base_url}projects/{record['id']}/",
        "api_url": f"{base_url}api/v1/projects/{record['id']}.json",
    }
    for name, _, _ in PROJECT_COLUMNS:
        if name not in row:
            row[name] = derived.get(name)
    return {name: row[name] for name, _, _ in PROJECT_COLUMNS}


def statement_rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for m in record["metrics"]:
        rows.append({
            "project_id": record["id"], "metric": m["metric"], "value": m["value"],
            "value_high": m.get("value_high"), "qualifier": m["qualifier"], "unit": m["unit"],
            "basis": m["basis"], "power_scope": m.get("power_scope"),
            "applies_to": m["applies_to"], "phase_label": m.get("phase_label"),
            "accelerator_model": m.get("accelerator_model"), "as_of": m["as_of"],
            "source_ids": _join(m["source_ids"]), "source_urls": _urls(record, m["source_ids"]),
            "note": m.get("note"),
        })
    return rows


def milestone_rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for m in record["milestones"]:
        start, end = partial_date_bounds(m["date"])
        rows.append({
            "project_id": record["id"], "event": m["event"], "date": m["date"],
            "date_precision": date_precision(m["date"]), "date_start": start.isoformat(),
            "date_end": end.isoformat(), "kind": m["kind"],
            "source_ids": _join(m["source_ids"]), "source_urls": _urls(record, m["source_ids"]),
            "note": m.get("note"),
        })
    return rows


def party_rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [{
        "project_id": record["id"], "name": p["name"], "role": p["role"],
        "source_ids": _join(p["source_ids"]), "source_urls": _urls(record, p["source_ids"]),
        "note": p.get("note"),
    } for p in record["parties"]]


def power_source_rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [{
        "project_id": record["id"], "type": p["type"], "description": p["description"],
        "source_ids": _join(p["source_ids"]), "source_urls": _urls(record, p["source_ids"]),
    } for p in record.get("power_sources", [])]


def source_rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [{
        "project_id": record["id"], "source_id": s["id"], "url": s["url"], "title": s["title"],
        "publisher": s["publisher"], "published": s["published"], "accessed": s["accessed"],
        "type": s["type"], "archived_url": s.get("archived_url"),
    } for s in record["sources"]]


def osm_rows(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for f in features:
        p = dict(f["properties"])
        p["lon"], p["lat"] = f["geometry"]["coordinates"]
        p["tags"] = json.dumps(p["tags"], ensure_ascii=False, sort_keys=True)
        rows.append({name: p.get(name) for name, _, _ in OSM_TABLE_COLUMNS})
    return rows


def wikidata_rows(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for f in features:
        p = dict(f["properties"])
        p["lon"], p["lat"] = f["geometry"]["coordinates"]
        rows.append({name: p.get(name) for name, _, _ in WIKIDATA_TABLE_COLUMNS})
    return rows


def build_tables(records: list[dict[str, Any]], countries: Countries, base_url: str,
                 osm: list[dict[str, Any]] | None = None,
                 wikidata: list[dict[str, Any]] | None = None
                 ) -> dict[str, tuple[list[Column], list[dict[str, Any]]]]:
    """Return every published table as (columns, rows), keyed by table name."""
    derived = {r["id"]: headline(r) for r in records}
    tables: dict[str, tuple[list[Column], list[dict[str, Any]]]] = {
        "projects": (PROJECT_COLUMNS,
                     [project_row(r, derived[r["id"]], countries, base_url) for r in records]),
    }
    per_record: list[tuple[str, list[Column], Callable]] = [
        ("statements", STATEMENT_COLUMNS, statement_rows),
        ("milestones", MILESTONE_COLUMNS, milestone_rows),
        ("parties", PARTY_COLUMNS, party_rows),
        ("power_sources", POWER_SOURCE_COLUMNS, power_source_rows),
        ("sources", SOURCE_COLUMNS, source_rows),
    ]
    for name, columns, fn in per_record:
        tables[name] = (columns, [row for r in records for row in fn(r)])
    if osm is not None:
        tables["osm_data_centers"] = (OSM_TABLE_COLUMNS, osm_rows(osm))
    if wikidata is not None:
        tables["wikidata_data_centers"] = (WIKIDATA_TABLE_COLUMNS, wikidata_rows(wikidata))
    return tables
