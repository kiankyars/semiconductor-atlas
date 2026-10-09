"""Write the dataset in every published format: CSV, JSON, JSON Lines, GeoJSON, SQLite, XLSX,
optional Parquet, a Frictionless Data Package, and a static JSON API."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

from .contract import SCHEMA_VERSION
from .derive import DERIVATION_RULES, headline
from .tables import Column
from .xlsx import write_xlsx

CURATED_LICENSE = {
    "name": "CC-BY-4.0",
    "title": "Creative Commons Attribution 4.0 International",
    "path": "https://creativecommons.org/licenses/by/4.0/",
}
OSM_LICENSE = {
    "name": "ODbL-1.0",
    "title": "Open Data Commons Open Database License 1.0",
    "path": "https://opendatacommons.org/licenses/odbl/1-0/",
}
WIKIDATA_LICENSE = {
    "name": "CC0-1.0",
    "title": "Creative Commons CC0 1.0 Universal",
    "path": "https://creativecommons.org/publicdomain/zero/1.0/",
}

TABLE_INFO = {
    "projects": ("One row per curated data center build, with headline values derived by "
                 "documented rules.", CURATED_LICENSE),
    "statements": ("Every sourced quantity (power, investment, accelerators, area, buildings, "
                   "jobs) with its basis, scope, qualifier, date and sources.", CURATED_LICENSE),
    "milestones": ("Dated actual and planned events with sources.", CURATED_LICENSE),
    "parties": ("Organizations and their reported roles, with sources.", CURATED_LICENSE),
    "power_sources": ("Reported power supply arrangements, with sources.", CURATED_LICENSE),
    "sources": ("Every cited source: URL, title, publisher, publication and access dates.",
                CURATED_LICENSE),
    "osm_data_centers": ("OpenStreetMap features tagged telecom=data_center or "
                         "building=data_center, worldwide. Uncurated mapping layer.",
                         OSM_LICENSE),
    "wikidata_data_centers": ("Wikidata items that are instances of data center (Q671224) with "
                              "coordinates. Uncurated reference layer.", WIKIDATA_LICENSE),
}
SQL_TYPES = {"string": "TEXT", "date": "TEXT", "number": "REAL", "integer": "INTEGER"}


def _cell(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def csv_bytes(columns: list[Column], rows: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([name for name, _, _ in columns])
    for row in rows:
        writer.writerow(["" if row[name] is None else _cell(row[name]) for name, _, _ in columns])
    return buffer.getvalue().encode("utf-8")


def json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False)
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    return (text + "\n").encode("utf-8")


def enriched_record(record: dict[str, Any], project_row: dict[str, Any]) -> dict[str, Any]:
    """Curated record plus derived values and links; the curated fields are unchanged."""
    out = dict(record)
    out["derived"] = {
        **headline(record),
        "country_name": project_row["country_name"],
        "region": project_row["region"],
        "subregion": project_row["subregion"],
    }
    out["links"] = {"html": project_row["url"], "json": project_row["api_url"]}
    return out


def geojson_projects(rows: list[dict[str, Any]]) -> dict[str, Any]:
    features = []
    for row in rows:
        props = {k: _cell(v) for k, v in row.items() if k not in ("lat", "lon")}
        features.append({
            "type": "Feature",
            "id": row["id"],
            "geometry": {"type": "Point", "coordinates": [row["lon"], row["lat"]]},
            "properties": props,
        })
    return {"type": "FeatureCollection", "features": features}


def write_sqlite(path: Path, tables: dict[str, tuple[list[Column], list[dict[str, Any]]]],
                 records: list[dict[str, Any]], meta: dict[str, str]) -> None:
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA journal_mode = DELETE")
        con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.executemany("INSERT INTO meta VALUES (?, ?)", sorted(meta.items()))
        con.execute("CREATE TABLE columns (table_name TEXT, column_name TEXT, type TEXT, "
                    "description TEXT, PRIMARY KEY (table_name, column_name))")
        for name, (columns, rows) in tables.items():
            defs = ", ".join(f'"{c}" {SQL_TYPES[t]}' for c, t, _ in columns)
            con.execute(f'CREATE TABLE "{name}" ({defs})')
            placeholders = ", ".join("?" for _ in columns)
            con.executemany(
                f'INSERT INTO "{name}" VALUES ({placeholders})',
                [[_cell(row[c]) for c, _, _ in columns] for row in rows],
            )
            con.executemany("INSERT INTO columns VALUES (?, ?, ?, ?)",
                            [(name, c, t, d) for c, t, d in columns])
            if name == "projects":
                con.execute('CREATE UNIQUE INDEX projects_id ON projects (id)')
                for col in ("country", "status"):
                    con.execute(f'CREATE INDEX projects_{col} ON projects ("{col}")')
            elif "project_id" in {c for c, _, _ in columns}:
                con.execute(f'CREATE INDEX "{name}_project" ON "{name}" (project_id)')
            elif name.endswith("data_centers"):
                con.execute(f'CREATE INDEX "{name}_country" ON "{name}" (country)')
        con.execute("CREATE TABLE records (id TEXT PRIMARY KEY, json TEXT NOT NULL)")
        con.executemany("INSERT INTO records VALUES (?, ?)", [
            (r["id"], json.dumps(r, ensure_ascii=False, sort_keys=True)) for r in records
        ])
        con.commit()
        con.execute("VACUUM")
    finally:
        con.close()


def write_parquet(directory: Path,
                  tables: dict[str, tuple[list[Column], list[dict[str, Any]]]]) -> list[str]:
    """Write one Parquet file per table when pyarrow is installed; return written names."""
    try:
        import pyarrow as pa  # type: ignore[import-not-found]
        import pyarrow.parquet as pq  # type: ignore[import-not-found]
    except ImportError:
        return []
    types = {"string": pa.string(), "date": pa.string(), "number": pa.float64(),
             "integer": pa.int64()}
    written = []
    for name, (columns, rows) in tables.items():
        schema = pa.schema([(c, types[t]) for c, t, _ in columns])
        data = {c: [row[c] for row in rows] for c, _, _ in columns}
        for c, t, _ in columns:
            if t == "number":
                data[c] = [None if v is None else float(v) for v in data[c]]
        table = pa.table(data, schema=schema)
        pq.write_table(table, directory / f"{name}.parquet", compression="zstd")
        written.append(f"{name}.parquet")
    return written


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, files[name])
    return buffer.getvalue()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def datapackage(tables: dict[str, tuple[list[Column], list[dict[str, Any]]]],
                file_bytes: dict[str, bytes], meta: dict[str, Any]) -> dict[str, Any]:
    resources = []
    for name, (columns, rows) in tables.items():
        path = f"{name}.csv"
        description, license_info = TABLE_INFO[name]
        resource = {
            "name": name.replace("_", "-"),
            "path": path,
            "profile": "tabular-data-resource",
            "format": "csv",
            "mediatype": "text/csv",
            "encoding": "utf-8",
            "bytes": len(file_bytes[path]),
            "hash": f"sha256:{sha256(file_bytes[path])}",
            "description": description,
            "licenses": [license_info],
            "schema": {"fields": [{"name": c, "type": t, "description": d}
                                  for c, t, d in columns]},
            "count_of_rows": len(rows),
        }
        if name == "projects":
            resource["schema"]["primaryKey"] = ["id"]
        elif any(c == "project_id" for c, _, _ in columns):
            resource["schema"]["foreignKeys"] = [{
                "fields": "project_id", "reference": {"resource": "projects", "fields": "id"}
            }]
        resources.append(resource)
    return {
        "profile": "tabular-data-package",
        "name": "open-data-center-atlas",
        "title": meta["title"],
        "description": meta["description"],
        "homepage": meta["base_url"],
        "version": meta["version"],
        "created": meta["data_as_of"],
        "licenses": [CURATED_LICENSE, OSM_LICENSE, WIKIDATA_LICENSE],
        "sources": meta["package_sources"],
        "contributors": [{"title": "Open Data Center Atlas maintainers", "role": "author"}],
        "keywords": ["data centers", "AI infrastructure", "power", "construction", "open data"],
        "resources": resources,
        "x_derivation_rules": DERIVATION_RULES,
        "x_schema_version": SCHEMA_VERSION,
    }
