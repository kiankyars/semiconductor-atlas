"""Static site and data release builder for GitHub Pages.

``build(...)`` validates the curated records, writes every data format and the static JSON API,
and renders HTML pages that work without JavaScript (the explorer map and filters enhance them).
Output depends only on the inputs and the given base URL and commit, so rebuilding the same
commit reproduces the same files.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import export, markdown
from .contract import STATUS_LABELS, STATUSES, partial_date_bounds, validate_collection
from .derive import DERIVATION_RULES, Countries, headline
from .layers import load_layer
from .tables import LIST_SEP, build_tables
from .xlsx import write_xlsx

SITE_TITLE = "Open Data Center Atlas"
TAGLINE = "An open, source-linked dataset of data center builds"
REPO_URL = "https://github.com/kiankyars/semiconductor-atlas"
DATA_PATH_IN_REPO = "datacenters"
DEFAULT_BASE_URL = "https://kiankyars.github.io/semiconductor-atlas/"
CREATOR = "Kian Kyars"

STATUS_GROUP = {
    "operational": "operating", "partially_operational": "operating",
    "under_construction": "construction", "proposed": "planned", "announced": "planned",
    "paused": "inactive", "cancelled": "inactive",
}
METRIC_LABELS = {
    "power_capacity": "Power capacity", "investment": "Investment", "accelerators": "Accelerators",
    "floor_area": "Floor area", "land_area": "Land area", "buildings": "Buildings",
    "jobs_construction": "Construction jobs", "jobs_permanent": "Permanent jobs",
}
SCOPE_LABELS = {
    "it_load": "IT load", "facility": "facility", "grid_connection": "grid connection",
    "onsite_generation": "on-site generation", "unspecified": "scope not stated",
}
SCOPE_SHORT = {"it_load": "IT", "facility": "facility", "grid_connection": "grid",
               "unspecified": "", "onsite_generation": "on-site gen."}
EVENT_LABELS = {
    "announced": "Announced", "site_acquired": "Site acquired", "zoning_approved":
    "Zoning approved", "permit_approved": "Permit approved", "construction_started":
    "Construction started", "first_operational": "First operation", "fully_operational":
    "Fully operational", "expansion_announced": "Expansion announced", "paused": "Paused",
    "cancelled": "Cancelled", "other": "Other",
}
ROLE_LABELS = {
    "developer": "Developer", "owner": "Owner", "operator": "Operator", "tenant": "Tenant",
    "investor": "Investor", "builder": "Builder", "power_supplier": "Power supplier",
    "utility": "Utility", "hardware_supplier": "Hardware supplier", "government": "Government",
}
POWER_TYPE_LABELS = {
    "grid": "Grid", "natural_gas_onsite": "On-site natural gas", "nuclear": "Nuclear",
    "solar": "Solar", "wind": "Wind", "hydro": "Hydro", "geothermal": "Geothermal",
    "battery_storage": "Battery storage", "fuel_cell": "Fuel cells",
    "diesel_backup": "Diesel backup", "other": "Other",
}
UNIT_LABELS = {"sq_ft": "sq ft", "sq_m": "m²", "acres": "acres", "hectares": "ha",
               "count": "", "MW": "MW"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
NAV = (("", "Explore"), ("projects/", "All projects"), ("data/", "Data & API"),
       ("methodology/", "Methodology"), ("about/", "About"))

LOGO = ('<svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
        '<rect x="3" y="4" width="18" height="5" rx="1.5" fill="none" stroke="currentColor" '
        'stroke-width="1.8"/><rect x="3" y="11" width="18" height="5" rx="1.5" fill="none" '
        'stroke="currentColor" stroke-width="1.8"/><circle cx="7" cy="6.5" r="1.1" '
        'fill="currentColor"/><circle cx="7" cy="13.5" r="1.1" fill="currentColor"/>'
        '<path d="M12 18v3M8 21h8" stroke="currentColor" stroke-width="1.8" '
        'stroke-linecap="round"/></svg>')


def e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


# ---------- formatting (mirrors assets/format.js) ----------

def fmt_number(value: float) -> str:
    text = f"{value:,.1f}"
    return text[:-2] if text.endswith(".0") else text


def _qualify(text: str, qualifier: str | None) -> str:
    return {"approximately": f"~{text}", "up_to": f"up to {text}", "at_least": f"≥ {text}",
            "more_than": f"> {text}"}.get(qualifier or "", text)


def fmt_mw(value: float | None, high: float | None = None, qualifier: str | None = None) -> str:
    if value is None:
        return ""
    if qualifier == "range" and high is not None:
        if value >= 1000:
            return f"{fmt_number(value / 1000)}–{fmt_number(high / 1000)} GW"
        return f"{fmt_number(value)}–{fmt_number(high)} MW"
    text = f"{fmt_number(value / 1000)} GW" if value >= 1000 else f"{fmt_number(value)} MW"
    return _qualify(text, qualifier)


def compact_money(value: float) -> str:
    for size, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if value >= size:
            return f"{fmt_number(value / size)}{suffix}"
    return fmt_number(value)


def fmt_money(value: float | None, high: float | None, currency: str | None,
              qualifier: str | None) -> str:
    if value is None:
        return ""
    if qualifier == "range" and high is not None:
        return f"{currency} {compact_money(value)}–{compact_money(high)}"
    return _qualify(f"{currency} {compact_money(value)}", qualifier)


def fmt_date(value: str | None) -> str:
    if not value:
        return ""
    year, rest = value[:4], value[5:]
    if not rest:
        return year
    if rest[0] in "QH":
        return f"{rest} {year}"
    parts = rest.split("-")
    month = MONTHS[int(parts[0]) - 1]
    return f"{month} {year}" if len(parts) == 1 else f"{int(parts[1])} {month} {year}"


def fmt_metric(m: dict[str, Any]) -> str:
    if m["metric"] == "power_capacity":
        return fmt_mw(m["value"], m.get("value_high"), m["qualifier"])
    if m["metric"] == "investment":
        return fmt_money(m["value"], m.get("value_high"), m["unit"], m["qualifier"])
    unit = UNIT_LABELS.get(m["unit"], m["unit"])
    if m["qualifier"] == "range":
        text = f"{fmt_number(m['value'])}–{fmt_number(m['value_high'])}"
    else:
        text = _qualify(fmt_number(m["value"]), m["qualifier"])
    return f"{text} {unit}".strip()


def status_badge(status: str) -> str:
    group = STATUS_GROUP.get(status, "planned")
    return (f'<span class="status"><span class="dot {group}" aria-hidden="true"></span>'
            f"{e(STATUS_LABELS[status])}</span>")


# ---------- page shell ----------

ASSET_VERSION = ""  # set by build(): short content hash of site/assets for cache busting


def page(*, title: str, description: str, body: str, depth: int, path: str, base_url: str,
         current: str = "", head: str = "", scripts: tuple[str, ...] = (),
         data_as_of: str = "") -> str:
    rel = "../" * depth
    v = f"?v={ASSET_VERSION}" if ASSET_VERSION else ""
    nav = "".join(
        f'<a href="{rel}{href}"{" aria-current=\"page\"" if href == current else ""}>{label}</a>'
        for href, label in NAV
    )
    nav += f'<a href="{REPO_URL}">GitHub</a>'
    script_tags = "".join(f'<script type="module" src="{rel}assets/{s}{v}"></script>'
                          for s in scripts)
    full_title = title if title == SITE_TITLE else f"{title} · {SITE_TITLE}"
    canonical = base_url + path
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(full_title)}</title>
<meta name="description" content="{e(description)}">
<link rel="canonical" href="{e(canonical)}">
<meta property="og:title" content="{e(full_title)}">
<meta property="og:description" content="{e(description)}">
<meta property="og:type" content="website">
<meta property="og:url" content="{e(canonical)}">
<link rel="icon" href="{rel}assets/favicon.svg" type="image/svg+xml">
<link rel="alternate" type="application/atom+xml" title="{e(SITE_TITLE)} updates" href="{rel}feed.xml">
<link rel="stylesheet" href="{rel}assets/style.css{v}">
{head}
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="site-header"><div class="wrap">
<a class="brand" href="{rel}">{LOGO}<span>{e(SITE_TITLE)}</span></a>
<nav class="site-nav" aria-label="Site">{nav}</nav>
</div></header>
<main id="main"><div class="wrap">
{body}
</div></main>
<footer class="site-footer"><div class="wrap">
<p>Curated records are licensed <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>;
cite “{e(SITE_TITLE)}”. OpenStreetMap layer © OpenStreetMap contributors,
<a href="https://opendatacommons.org/licenses/odbl/1-0/">ODbL</a>. Wikidata layer CC0. Basemap:
Natural Earth (public domain).</p>
<p>Data as of {e(data_as_of)}. Not investment advice; unknown values stay unknown.
<a href="{REPO_URL}/issues/new?labels=data-correction&amp;title=Correction%3A%20">Report an error</a> ·
<a href="{rel}feed.xml">Updates feed</a> · <a href="{rel}api/v1/index.json">API</a></p>
</div></footer>
{script_tags}
</body>
</html>
"""


# ---------- build ----------

def load_records(projects_dir: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted(projects_dir.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("id") != path.stem:
            raise ValueError(f"{path}: file name must equal record id")
        records.append(record)
    errors = validate_collection(records)
    if errors:
        raise ValueError("invalid project records:\n" + "\n".join(errors))
    return records


def _api_row(row: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key, value in row.items():
        if key in ("aliases", "workloads", "developers", "owners", "operators", "tenants",
                   "power_source_types"):
            out[key] = value.split(LIST_SEP) if value else []
        else:
            out[key] = export._cell(value)
    return out


def _write(path: Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode("utf-8")
    path.write_bytes(data)


def build(*, root: Path, out_dir: Path, base_url: str = DEFAULT_BASE_URL,
          commit: str | None = None) -> dict[str, Any]:
    """Build the full site into ``out_dir`` (which must not exist or be empty)."""
    if not base_url.endswith("/"):
        base_url += "/"
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"{out_dir} is not empty")
    records = load_records(root / "projects")
    countries = Countries(root / "vendor" / "countries.json")
    layer_meta: dict[str, dict[str, Any]] = {}
    layer_features: dict[str, list[dict[str, Any]]] = {}
    for name in ("osm", "wikidata"):
        directory = root / "layers" / name
        if (directory / "manifest.json").exists():
            layer_meta[name], layer_features[name] = load_layer(directory)
    tables = build_tables(records, countries, base_url, osm=layer_features.get("osm"),
                          wikidata=layer_features.get("wikidata"))
    rows = tables["projects"][1]
    row_by_id = {r["id"]: r for r in rows}
    data_as_of = max(r["last_reviewed"] for r in records) if records else ""
    version = data_as_of.replace("-", ".") if data_as_of else "0"
    meta = {
        "title": SITE_TITLE,
        "description": f"{TAGLINE}: power, investment, status, parties and milestones, each "
                       "linked to the source that reported it.",
        "base_url": base_url,
        "version": version,
        "data_as_of": data_as_of,
        "package_sources": [{"title": "Curated public sources cited per record",
                             "path": base_url + "data/sources.csv"}]
        + ([{"title": "OpenStreetMap contributors", "path": "https://www.openstreetmap.org/"}]
           if "osm" in layer_meta else [])
        + ([{"title": "Wikidata", "path": "https://www.wikidata.org/"}]
           if "wikidata" in layer_meta else []),
    }

    data_dir = out_dir / "data"
    files: dict[str, bytes] = {}
    for name, (columns, table_rows) in tables.items():
        files[f"{name}.csv"] = export.csv_bytes(columns, table_rows)
    enriched = [export.enriched_record(r, row_by_id[r["id"]]) for r in records]
    files["projects.json"] = export.json_bytes(enriched, pretty=True)
    files["projects.jsonl"] = "".join(
        json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in enriched
    ).encode("utf-8")
    files["projects.geojson"] = export.json_bytes(export.geojson_projects(rows))
    for name in layer_features:
        files[f"{name}_data_centers.geojson"] = export.json_bytes(
            {"type": "FeatureCollection", "features": layer_features[name]}
        )
    files["project.schema.json"] = (root / "schema" / "project.schema.json").read_bytes()
    package = export.datapackage(tables, files, meta)
    files["datapackage.json"] = export.json_bytes(package, pretty=True)
    readme = _bundle_readme(meta, tables, layer_meta)
    files["README.md"] = readme.encode("utf-8")
    for name, data in files.items():
        _write(data_dir / name, data)
    sqlite_meta = {"title": SITE_TITLE, "data_as_of": data_as_of, "version": version,
                   "license_curated": "CC-BY-4.0", "license_osm_layer": "ODbL-1.0",
                   "license_wikidata_layer": "CC0-1.0", "homepage": base_url,
                   "derivation_rules": json.dumps(DERIVATION_RULES)}
    export.write_sqlite(data_dir / "datacenters.sqlite", tables, records, sqlite_meta)
    write_xlsx(data_dir / "datacenters.xlsx", [
        (name, [c for c, _, _ in columns],
         [[export._cell(r[c]) for c, _, _ in columns] for r in table_rows])
        for name, (columns, table_rows) in tables.items()
        if name not in ("osm_data_centers",)
    ])
    parquet = export.write_parquet(data_dir, tables)
    bundle = {name: data for name, data in files.items() if name.endswith((".csv", ".json",
              ".md"))}
    _write(data_dir / "open-data-center-atlas.zip", export.zip_bytes(bundle))

    manifest_files = {}
    for path in sorted(data_dir.iterdir()):
        blob = path.read_bytes()
        manifest_files[path.name] = {"bytes": len(blob), "sha256": export.sha256(blob)}
    manifest = {"title": SITE_TITLE, "version": version, "data_as_of": data_as_of,
                "commit": commit, "schema_version": records[0]["schema_version"] if records
                else None, "record_count": len(records), "files": manifest_files,
                "layers": layer_meta, "row_counts": {n: len(t[1]) for n, t in tables.items()}}
    _write(data_dir / "manifest.json", export.json_bytes(manifest, pretty=True))

    # ---- static JSON API ----
    api = out_dir / "api" / "v1"
    columns = [c for c, _, _ in tables["projects"][0]]
    api_rows = [_api_row(r) for r in rows]
    license_note = {"curated": export.CURATED_LICENSE, "osm_layer": export.OSM_LICENSE,
                    "wikidata_layer": export.WIKIDATA_LICENSE}
    _write(api / "projects.json", export.json_bytes({
        "title": SITE_TITLE, "data_as_of": data_as_of, "version": version,
        "count": len(api_rows), "license": export.CURATED_LICENSE, "columns": columns,
        "projects": api_rows,
    }))
    for record in enriched:
        _write(api / "projects" / f"{record['id']}.json", export.json_bytes(record, pretty=True))
    stats = _stats(records, rows)
    _write(api / "stats.json", export.json_bytes({"data_as_of": data_as_of, **stats},
                                                  pretty=True))
    _write(api / "index.json", export.json_bytes({
        "title": SITE_TITLE, "description": meta["description"], "data_as_of": data_as_of,
        "version": version, "license": license_note,
        "endpoints": {
            "projects": base_url + "api/v1/projects.json",
            "project": base_url + "api/v1/projects/{id}.json",
            "stats": base_url + "api/v1/stats.json",
            "datapackage": base_url + "data/datapackage.json",
            "manifest": base_url + "data/manifest.json",
            "schema": base_url + "data/project.schema.json",
        },
        "notes": "Static files served by GitHub Pages with CORS enabled. No key, no rate limit "
                 "beyond GitHub Pages limits. Breaking changes get a new /api/vN/ path.",
    }, pretty=True))

    # ---- assets ----
    assets = out_dir / "assets"
    shutil.copytree(root / "site" / "assets", assets)
    global ASSET_VERSION
    digest = hashlib.sha256()
    for path in sorted((root / "site" / "assets").rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(root / "site" / "assets").as_posix().encode())
            digest.update(path.read_bytes())
    ASSET_VERSION = digest.hexdigest()[:10]
    for script in assets.glob("*.js"):
        text = script.read_text(encoding="utf-8")
        text = re.sub(r'from "\./([\w-]+\.js)"', rf'from "./\1?v={ASSET_VERSION}"', text)
        script.write_text(text, encoding="utf-8")
    shutil.copyfile(root / "vendor" / "basemap.json", assets / "basemap.json")
    for name, features in layer_features.items():
        points = []
        for f in features:
            p = f["properties"]
            lon, lat = f["geometry"]["coordinates"]
            if name == "osm":
                ref = f"{p['osm_type']}/{p['osm_id']}"
                points.append([lon, lat, p.get("name"), p.get("operator"), ref])
            else:
                points.append([lon, lat, p.get("name"), p.get("operators"), p["wikidata_id"]])
        _write(assets / "layers" / f"{name}.json",
               export.json_bytes({"count": len(points), "points": points}))

    # ---- pages ----
    ctx = {"base_url": base_url, "data_as_of": data_as_of,
           "names": {r["id"]: r["name"] for r in records}}
    _write(out_dir / "index.html", _index_page(rows, records, stats, layer_meta, manifest, ctx))
    _write(out_dir / "projects" / "index.html", _projects_page(rows, ctx))
    for record in records:
        _write(out_dir / "projects" / record["id"] / "index.html",
               _project_page(record, row_by_id[record["id"]], ctx))
    _write(out_dir / "data" / "index.html",
           _data_page(tables, manifest, layer_meta, parquet, ctx))
    _write(out_dir / "methodology" / "index.html", _markdown_page(
        root / "METHODOLOGY.md", "Methodology", "methodology/", ctx,
        "How records are researched, what each field means, and what the data does not show."))
    _write(out_dir / "about" / "index.html", _about_page(root, ctx))
    _write(out_dir / "404.html", page(
        title="Not found", description="Page not found.", depth=0, path="404.html",
        base_url=base_url, data_as_of=data_as_of,
        body='<div class="lede"><h1>Page not found</h1><p>Try the <a href="./">explorer</a> or '
             'the <a href="./projects/">list of all projects</a>.</p></div>',
        head=f'<base href="{e(base_url)}">'))
    _write(out_dir / "feed.xml", _feed(records, base_url, data_as_of))
    _write(out_dir / "sitemap.xml", _sitemap(records, base_url, data_as_of))
    _write(out_dir / "robots.txt", f"User-agent: *\nAllow: /\nSitemap: {base_url}sitemap.xml\n")
    _write(out_dir / "llms.txt", _llms_txt(base_url, data_as_of, stats))
    _write(out_dir / ".nojekyll", "")
    return {"records": len(records), "data_as_of": data_as_of, "files": len(manifest_files),
            "parquet": bool(parquet)}


def _stats(records: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_status = Counter(r["status"] for r in records)
    by_country = Counter(r["country"] for r in rows)
    return {
        "projects": len(records),
        "countries": len(by_country),
        "sources": sum(len(r["sources"]) for r in records),
        "statements": sum(r["statement_count"] for r in rows),
        "by_status": {s: by_status.get(s, 0) for s in STATUSES},
        "by_country": dict(sorted(by_country.items(), key=lambda kv: (-kv[1], kv[0]))),
    }


def _bundle_readme(meta: dict[str, Any], tables: dict, layer_meta: dict) -> str:
    lines = [f"# {SITE_TITLE} — data bundle", "", meta["description"], "",
             f"Data as of {meta['data_as_of']}. Homepage: {meta['base_url']}", "",
             "## Files", ""]
    for name, (columns, rows) in tables.items():
        description, license_info = export.TABLE_INFO[name]
        lines.append(f"- `{name}.csv` ({len(rows)} rows, {license_info['name']}): {description}")
    lines += ["", "Column definitions are in `datapackage.json` (Frictionless Data Package).", "",
              "## Licenses", "",
              "- Curated records (projects, statements, milestones, parties, power_sources, "
              "sources): CC BY 4.0. Cite \"Open Data Center Atlas\" with the homepage URL.",
              "- osm_data_centers: © OpenStreetMap contributors, ODbL 1.0. Derived databases must "
              "stay under ODbL.",
              "- wikidata_data_centers: CC0 1.0.", "",
              "## Derivation rules for headline columns", ""]
    for key, rule in DERIVATION_RULES.items():
        lines.append(f"- `{key}`: {rule}")
    return "\n".join(lines) + "\n"


# ---------- page bodies ----------

def _jsonld_dataset(ctx: dict, manifest: dict) -> str:
    base = ctx["base_url"]
    dist = []
    for name, fmt in (("projects.csv", "text/csv"), ("projects.json", "application/json"),
                      ("projects.geojson", "application/geo+json"),
                      ("datacenters.sqlite", "application/vnd.sqlite3"),
                      ("datacenters.xlsx", "application/vnd.openxmlformats-officedocument."
                       "spreadsheetml.sheet")):
        if name in manifest["files"]:
            dist.append({"@type": "DataDownload", "encodingFormat": fmt,
                         "contentUrl": base + "data/" + name})
    doc = {
        "@context": "https://schema.org/", "@type": "Dataset", "name": SITE_TITLE,
        "description": f"{TAGLINE}. Each record links planned and operational power, "
                       "investment, status, parties and milestones to public sources.",
        "url": base, "sameAs": REPO_URL, "license": "https://creativecommons.org/licenses/by/4.0/",
        "isAccessibleForFree": True, "creator": {"@type": "Person", "name": CREATOR},
        "dateModified": ctx["data_as_of"], "version": manifest["version"],
        "keywords": ["data centers", "AI infrastructure", "electric power", "construction",
                     "hyperscale", "open data"],
        "spatialCoverage": {"@type": "Place", "name": "Worldwide"},
        "temporalCoverage": "2022-01-01/..",
        "distribution": dist,
        "variableMeasured": ["planned power (MW)", "operational power (MW)",
                             "announced investment", "accelerator count", "project status"],
    }
    return ('<script type="application/ld+json">'
            + json.dumps(doc, ensure_ascii=False).replace("</", "<\\/") + "</script>")


def _table_row(r: dict[str, Any], rel: str) -> str:
    place = ", ".join(v for v in (r["locality"], r["admin1"]) if v)
    aliases = (r["aliases"] or "").split(LIST_SEP) if r["aliases"] else []
    power = ""
    if r["planned_power_mw"] is not None:
        scope = SCOPE_SHORT.get(r["planned_power_scope"], "")
        power = fmt_mw(r["planned_power_mw"], r["planned_power_mw_high"],
                       r["planned_power_qualifier"])
        if scope:
            power += f'<span class="scope">{e(scope)}</span>'
    if r["onsite_generation_mw"] is not None:
        power += f'<span class="sub">{e(fmt_mw(r["onsite_generation_mw"]))} on-site gen.</span>'
    money = fmt_money(r["investment_value"], r["investment_value_high"],
                      r["investment_currency"], r["investment_qualifier"])
    lead = []
    for name in (r["developers"] or "").split(LIST_SEP) + (r["owners"] or "").split(LIST_SEP):
        if name and name not in lead:
            lead.append(name)
    tenants = [t for t in (r["tenants"] or "").split(LIST_SEP) if t][:2]
    first = fmt_date(r["first_operational"])
    if not first and r["first_operational_target"]:
        first = f'{fmt_date(r["first_operational_target"])} <span class="sub">target</span>'
    href = f"{rel}projects/{quote(r['id'])}/"
    return (
        f'<tr data-id="{e(r["id"])}"><td><a href="{href}">{e(r["name"])}</a>'
        + (f'<span class="sub">{e(" · ".join(aliases[:2]))}</span>' if aliases else "")
        + f'</td><td>{e(place)}<span class="sub">{e(r["country_name"])}</span></td>'
        f'<td>{status_badge(r["status"])}<span class="sub">as of {fmt_date(r["status_as_of"])}'
        f'</span></td><td class="num">{power}</td><td class="num">{e(money)}</td>'
        f'<td>{e(", ".join(lead[:2]))}'
        + (f'<span class="sub">for {e(", ".join(tenants))}</span>' if tenants else "")
        + f'</td><td>{first}</td><td class="num"><a href="{href}#sources">{r["source_count"]}'
        "</a></td></tr>"
    )


TABLE_HEAD = """<thead><tr>
<th scope="col" data-sort="name"><button type="button">Project</button></th>
<th scope="col" data-sort="location"><button type="button">Location</button></th>
<th scope="col" data-sort="status"><button type="button">Status</button></th>
<th scope="col" class="num" data-sort="planned_power_mw"><button type="button">Planned power</button></th>
<th scope="col" class="num" data-sort="investment_value"><button type="button">Investment</button></th>
<th scope="col">Developer / owner</th>
<th scope="col" data-sort="first_operational"><button type="button">First operation</button></th>
<th scope="col" class="num" data-sort="source_count"><button type="button">Sources</button></th>
</tr></thead>"""


def _sorted_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: (r["planned_power_mw"] is None,
                                       -(r["planned_power_mw"] or 0), r["name"]))


def _index_page(rows, records, stats, layer_meta, manifest, ctx) -> str:
    by_status = stats["by_status"]
    building = by_status["under_construction"]
    operating = by_status["operational"] + by_status["partially_operational"]
    osm_count = layer_meta.get("osm", {}).get("feature_count")
    wd_count = layer_meta.get("wikidata", {}).get("feature_count")
    status_options = "".join(
        f'<option value="{s}">{e(STATUS_LABELS[s])}</option>' for s in STATUSES
    )
    layer_toggles = ""
    if osm_count:
        layer_toggles += (f'<label><input type="checkbox" id="f-osm"> OpenStreetMap data centers '
                          f'({osm_count:,}, uncurated)</label>')
    else:
        layer_toggles += '<input type="checkbox" id="f-osm" hidden>'
    if wd_count:
        layer_toggles += (f'<label><input type="checkbox" id="f-wd"> Wikidata items '
                          f'({wd_count:,})</label>')
    else:
        layer_toggles += '<input type="checkbox" id="f-wd" hidden>'
    table_rows = "".join(_table_row(r, "") for r in _sorted_rows(rows))
    body = f"""
<section class="lede">
<h1>Data center builds, with every number sourced</h1>
<p>{e(SITE_TITLE)} tracks {stats['projects']} large data center builds in {stats['countries']}
countries: who is building them, for whom, how much power they plan, what they cost, and where they
stand. Each value carries its basis, scope and date, and links to the public source that reported
it. Everything is free to download and reuse under CC BY 4.0.</p>
<div class="actions">
<a class="button primary" href="data/projects.csv" download>Download CSV</a>
<a class="button" href="data/">All formats &amp; API</a>
<a class="button" href="methodology/">How it is built</a>
<a class="button" href="{REPO_URL}/blob/main/{DATA_PATH_IN_REPO}/CONTRIBUTING.md">Add or correct a project</a>
</div>
</section>
<section class="tiles" aria-label="Summary">
<div class="tile"><div class="value">{stats['projects']}</div><div class="label">projects tracked</div></div>
<div class="tile"><div class="value">{building}</div><div class="label">under construction</div></div>
<div class="tile"><div class="value">{operating}</div><div class="label">operating or partly operating</div></div>
<div class="tile"><div class="value">{stats['sources']:,}</div><div class="label">sources cited</div></div>
<div class="tile"><div class="value">{e(fmt_date(ctx['data_as_of']))}</div><div class="label">last reviewed</div></div>
</section>
<section class="card" data-explorer data-base="" aria-label="Explorer">
<div class="filters">
<label class="grow">Search<input id="f-q" type="search" placeholder="Name, company, place…" autocomplete="off"></label>
<label>Status<select id="f-status"><option value="">All statuses</option>
<option value="group:operating">Operating (any)</option><option value="group:planned">Planned (any)</option>
<option value="group:inactive">Paused or cancelled</option>{status_options}</select></label>
<label>Region<select id="f-region"><option value="">All regions</option></select></label>
<label>Country<select id="f-country"><option value="">All countries</option></select></label>
<label>Workload<select id="f-workload"><option value="">Any workload</option><option value="ai">AI reported</option><option value="other">No AI use reported</option></select></label>
<label>Planned power<select id="f-minmw"><option value="">Any</option><option value="100">≥ 100 MW</option><option value="500">≥ 500 MW</option><option value="1000">≥ 1 GW</option><option value="5000">≥ 5 GW</option></select></label>
</div>
<div class="result-bar">
<span id="count" aria-live="polite">{len(rows)} of {len(rows)} projects</span>
<div class="layer-toggles">{layer_toggles}</div>
<span class="spacer"></span>
<button class="linkish" id="reset" type="button">Reset filters</button>
<button class="button" id="download" type="button">Download filtered CSV</button>
</div>
<div class="map-wrap" id="map">
<div class="map-controls"><button type="button" data-zoom="in" aria-label="Zoom in">+</button>
<button type="button" data-zoom="out" aria-label="Zoom out">−</button>
<button type="button" data-zoom="reset" aria-label="Fit to filtered projects">⤢</button></div>
<div class="legend" aria-label="Map legend">
<div class="legend-row"><span class="dot operating"></span>Operating <span class="note">(ring = partly)</span></div>
<div class="legend-row"><span class="dot construction"></span>Under construction</div>
<div class="legend-row"><span class="dot planned"></span>Proposed or announced</div>
<div class="legend-row"><span class="dot inactive"></span>Paused or cancelled</div>
<div class="note">Size = planned power. Drag to pan; Ctrl/⌘ + scroll or pinch to zoom.</div>
</div>
<div class="tooltip" hidden></div>
<div class="tooltip selection" id="selection" hidden style="pointer-events:auto;left:10px;top:10px"></div>
<span class="attribution">Natural Earth{' · © OpenStreetMap contributors' if osm_count else ''}</span>
</div>
<div class="table-scroll explorer-table"><table id="projects-table">
<caption class="skip">Data center builds. Planned power is the headline figure; see each project for all statements.</caption>
{TABLE_HEAD}
<tbody id="rows">{table_rows}</tbody></table></div>
</section>
<section class="grid-2" aria-label="Charts">
<div class="card chart-card"><h2>Largest planned power</h2><p class="caption">Headline campus figure as reported; scopes differ (IT load vs. total facility), so bars are not strictly comparable. Hover for scope and date.</p><div class="chart" id="chart-power" data-label="Largest planned power by project"></div></div>
<div class="card chart-card"><h2>Projects by status</h2><p class="caption">Status reflects the newest cited source for each project.</p><div class="chart" id="chart-status" data-label="Projects by status"></div></div>
<div class="card chart-card"><h2>Projects by country</h2><p class="caption">Top 12 countries in the current selection.</p><div class="chart" id="chart-country" data-label="Projects by country"></div></div>
<div class="card chart-card"><h2>Announcements by year</h2><p class="caption">Year of the earliest recorded public announcement.</p><div class="chart" id="chart-years" data-label="Announcements by year"></div></div>
</section>
<section class="section">
<h2>Use the data</h2>
<p>Every table is a static file with a stable URL, so you can load it directly in your tools. No
sign-up, no API key.</p>
<pre><code>import pandas as pd
projects = pd.read_csv("{e(ctx['base_url'])}data/projects.csv")
statements = pd.read_csv("{e(ctx['base_url'])}data/statements.csv")  # every sourced value</code></pre>
<p>More: <a href="data/">CSV, JSON, GeoJSON, SQLite, Excel, Parquet and the JSON API</a> ·
<a href="https://lite.datasette.io/?url={quote(ctx['base_url'] + 'data/datacenters.sqlite', safe='')}">query it with SQL in your browser</a>.</p>
</section>
"""
    return page(title=SITE_TITLE, description=f"{TAGLINE}. Interactive map, table and free "
                "downloads (CSV, JSON, GeoJSON, SQLite, Excel) with a source for every value.",
                body=body, depth=0, path="", base_url=ctx["base_url"], current="",
                head=_jsonld_dataset(ctx, manifest), scripts=("explorer.js",),
                data_as_of=ctx["data_as_of"])


def _projects_page(rows, ctx) -> str:
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in sorted(rows, key=lambda r: (r["country_name"] or "", r["name"])):
        groups.setdefault(r["country_name"] or r["country"], []).append(r)
    sections = []
    for country, items in groups.items():
        lis = "".join(
            f'<li><a href="{quote(r["id"])}/">{e(r["name"])}</a> — '
            f'{e(", ".join(v for v in (r["locality"], r["admin1"]) if v))} · '
            f'{status_badge(r["status"])}'
            + (f' · {e(fmt_mw(r["planned_power_mw"], r["planned_power_mw_high"], r["planned_power_qualifier"]))}'
               if r["planned_power_mw"] is not None else "")
            + "</li>"
            for r in items
        )
        sections.append(f'<h2 id="{e(items[0]["country"].lower())}">{e(country)} '
                        f'<span class="tag">{len(items)}</span></h2><ul>{lis}</ul>')
    body = (f'<div class="lede"><h1>All projects</h1><p>{len(rows)} curated builds, grouped by '
            f'country. The <a href="../">explorer</a> adds a map, filters and sorting.</p></div>'
            f'<div class="prose">{"".join(sections)}</div>')
    return page(title="All projects", description=f"List of all {len(rows)} data center builds "
                "in the Open Data Center Atlas.", body=body, depth=1, path="projects/",
                base_url=ctx["base_url"], current="projects/", data_as_of=ctx["data_as_of"])


def _cites(ids: list[str]) -> str:
    links = ",".join(f'<a href="#source-{e(i)}">{e(i[1:])}</a>' for i in ids)
    return f'<sup class="cite">[{links}]</sup>'


def _project_page(record: dict[str, Any], row: dict[str, Any], ctx: dict) -> str:
    rel = "../../"
    derived = headline(record)
    loc = record["location"]
    place = ", ".join(v for v in (loc.get("locality"), loc.get("admin2"), loc.get("admin1"),
                                  row["country_name"]) if v)
    aliases = record.get("aliases") or []
    workloads = ", ".join(w.replace("_", " ") for w in record.get("workloads", []))

    def kv(label: str, value: str, sub: str = "") -> str:
        if not value:
            return ""
        return (f"<div><dt>{e(label)}</dt><dd>{value}"
                + (f'<span class="sub">{sub}</span>' if sub else "") + "</dd></div>")

    planned = ""
    if derived["planned_power_mw"] is not None:
        planned = e(fmt_mw(derived["planned_power_mw"], derived["planned_power_mw_high"],
                           derived["planned_power_qualifier"]))
    operational = ""
    if derived["operational_power_mw"] is not None:
        operational = e(fmt_mw(derived["operational_power_mw"],
                               derived["operational_power_mw_high"],
                               derived["operational_power_qualifier"]))
    money = e(fmt_money(derived["investment_value"], derived["investment_value_high"],
                        derived["investment_currency"], derived["investment_qualifier"]))
    first = fmt_date(derived["first_operational"])
    first_sub = "actual"
    if not first and derived["first_operational_target"]:
        first, first_sub = fmt_date(derived["first_operational_target"]), "target"
    facts = "".join([
        kv("Status", status_badge(record["status"]),
           f"as of {e(fmt_date(record['status_as_of']))} {_cites(record['status_source_ids'])}"),
        kv("Planned power", planned, e(f"{SCOPE_LABELS.get(derived['planned_power_scope'], '')}"
                                       f" · {derived['planned_power_basis'] or ''} · "
                                       f"{fmt_date(derived['planned_power_as_of'])} ")
           + _cites(derived["planned_power_source_ids"]) if planned else ""),
        kv("Operational power", operational,
           e(f"{SCOPE_LABELS.get(derived['operational_power_scope'], '')} · "
             f"{fmt_date(derived['operational_power_as_of'])} ")
           + _cites(derived["operational_power_source_ids"]) if operational else ""),
        kv("On-site generation", e(fmt_mw(derived["onsite_generation_mw"]))
           if derived["onsite_generation_mw"] is not None else "",
           e(f"{(derived['onsite_generation_basis'] or '').replace('_', ' ')} · "
             f"{fmt_date(derived['onsite_generation_as_of'])} ")
           + _cites(derived["onsite_generation_source_ids"])
           if derived["onsite_generation_mw"] is not None else ""),
        kv("Investment", money, e(f"{fmt_date(derived['investment_as_of'])} ")
           + _cites(derived["investment_source_ids"]) if money else ""),
        kv("Accelerators", e(f"{fmt_number(derived['accelerators'])}")
           if derived["accelerators"] else "",
           e(f"{derived['accelerator_model'] or ''} {derived['accelerators_basis'] or ''} ")
           + _cites(derived["accelerators_source_ids"]) if derived["accelerators"] else ""),
        kv("First operation", e(first), first_sub if first else ""),
        kv("Construction started", e(fmt_date(derived["construction_started"]))),
        kv("Announced", e(fmt_date(derived["announced"]))),
    ])

    metric_rows = []
    for m in sorted(record["metrics"], key=lambda m: (list(METRIC_LABELS).index(m["metric"]),
                                                       partial_date_bounds(m["as_of"]))):
        detail = [m["basis"].replace("_", " ")]
        if m.get("power_scope"):
            detail.append(SCOPE_LABELS[m["power_scope"]])
        if m["applies_to"] == "phase":
            detail.append(f"phase: {m.get('phase_label', 'unnamed')}")
        if m.get("accelerator_model"):
            detail.append(m["accelerator_model"])
        metric_rows.append(
            f"<tr><td>{e(METRIC_LABELS[m['metric']])}</td><td class=\"num\">{e(fmt_metric(m))}"
            f"</td><td>{e(' · '.join(detail))}</td><td>{e(fmt_date(m['as_of']))}</td>"
            f"<td>{_cites(m['source_ids'])}</td><td>{e(m.get('note', ''))}</td></tr>"
        )
    metrics_html = (
        '<div class="table-scroll"><table><thead><tr><th>Metric</th><th class="num">Value</th>'
        "<th>Basis · scope</th><th>As of</th><th>Source</th><th>Note</th></tr></thead><tbody>"
        + "".join(metric_rows) + "</tbody></table></div>"
        if metric_rows else '<p class="notice">No quantities found in cited sources yet.</p>'
    )

    events = sorted(record["milestones"], key=lambda m: partial_date_bounds(m["date"]))
    timeline = "".join(
        f'<li><span class="when">{e(fmt_date(m["date"]))}</span><span>'
        f'{e(EVENT_LABELS[m["event"]])}'
        + (' <span class="tag">target</span>' if m["kind"] == "planned" else "")
        + f' {_cites(m["source_ids"])}'
        + (f'<span class="sub">{e(m["note"])}</span>' if m.get("note") else "")
        + "</span></li>"
        for m in events
    )
    parties = "".join(
        f"<tr><td>{e(p['name'])}</td><td>{e(ROLE_LABELS[p['role']])}</td>"
        f"<td>{_cites(p['source_ids'])}</td><td>{e(p.get('note', ''))}</td></tr>"
        for p in record["parties"]
    )
    power_sources = "".join(
        f"<li><strong>{e(POWER_TYPE_LABELS[p['type']])}</strong> — {e(p['description'])} "
        f"{_cites(p['source_ids'])}</li>"
        for p in record.get("power_sources", [])
    )
    sources = "".join(
        f'<li id="source-{e(s["id"])}"><a href="{e(s["url"])}" rel="noopener">{e(s["title"])}</a>'
        f'<span class="sub">{e(s["publisher"])} · {e(s["type"].replace("_", " "))}'
        + (f' · published {e(fmt_date(s["published"]))}' if s["published"] else "")
        + f' · accessed {e(fmt_date(s["accessed"]))}'
        + (f' · <a href="{e(s["archived_url"])}">archived copy</a>' if s.get("archived_url")
           else "")
        + "</span></li>"
        for s in record["sources"]
    )
    related = "".join(
        f'<li><a href="{rel}projects/{quote(other)}/">{e(ctx["names"].get(other, other))}</a></li>'
        for other in record.get("related_ids", [])
    )
    issue_title = quote(f"Correction: {record['name']} ({record['id']})")
    edit_url = f"{REPO_URL}/blob/main/{DATA_PATH_IN_REPO}/projects/{record['id']}.json"
    json_url = f"{rel}api/v1/projects/{record['id']}.json"
    citation = (f"{SITE_TITLE}. “{record['name']}.” Record {record['id']}, last reviewed "
                f"{record['last_reviewed']}. {ctx['base_url']}projects/{record['id']}/. "
                "CC BY 4.0.")
    osm_link = (f"https://www.openstreetmap.org/?mlat={loc['lat']}&amp;mlon={loc['lon']}"
                f"#map={14 if loc['precision'] in ('site', 'approximate_site') else 11}/"
                f"{loc['lat']}/{loc['lon']}")
    body = f"""
<nav class="crumbs" aria-label="Breadcrumb"><a href="{rel}">Explore</a> › <a href="{rel}projects/">All projects</a> › {e(row['country_name'])}</nav>
<header class="project-head">
<h1>{e(record['name'])}</h1>
{f'<p class="meta-line">Also known as {e(", ".join(aliases))}</p>' if aliases else ''}
<p class="meta-line"><span>{status_badge(record['status'])}</span><span>{e(place)}</span>
{f'<span>{e(workloads)}</span>' if workloads else ''}<span>Last reviewed {e(fmt_date(record['last_reviewed']))}</span></p>
<p>{e(record['summary'])}</p>
{f'<p class="notice">{e(record["notes"])}</p>' if record.get('notes') else ''}
</header>
<dl class="kv">{facts}</dl>
<section class="section"><h2>Location</h2>
<div class="map-wrap small" data-locator data-base="{rel}" data-id="{e(record['id'])}" data-label="Locator map for {e(record['name'])}">
<div class="map-controls"><button type="button" data-zoom="in" aria-label="Zoom in">+</button><button type="button" data-zoom="out" aria-label="Zoom out">−</button><button type="button" data-zoom="reset" aria-label="Recenter">⤢</button></div>
<div class="tooltip" hidden></div><span class="attribution">Natural Earth</span></div>
<p>{e(loc['lat'])}, {e(loc['lon'])} · precision: {e(loc['precision'].replace('_', ' '))} {_cites(loc['source_ids'])}
{f'<span class="sub">{e(loc["note"])}</span>' if loc.get('note') else ''}
<a href="{osm_link}">Open in OpenStreetMap</a></p>
</section>
<section class="section"><h2>Quantities</h2>
<p>Every reported figure, kept separate by basis and scope. Headline values above are chosen by the
<a href="{rel}methodology/#derived-headline-values">documented rules</a>.</p>
{metrics_html}</section>
{f'<section class="section"><h2>Timeline</h2><ul class="timeline">{timeline}</ul></section>' if timeline else ''}
{f'<section class="section"><h2>Organizations</h2><div class="table-scroll"><table><thead><tr><th>Name</th><th>Role</th><th>Source</th><th>Note</th></tr></thead><tbody>{parties}</tbody></table></div></section>' if parties else ''}
{f'<section class="section"><h2>Power supply</h2><ul>{power_sources}</ul></section>' if power_sources else ''}
{f'<section class="section"><h2>Related projects</h2><ul>{related}</ul></section>' if related else ''}
<section class="section" id="sources"><h2>Sources</h2><ol class="sources">{sources}</ol></section>
<section class="section"><h2>Use this record</h2>
<p><a href="{json_url}">JSON</a> · <a href="{edit_url}">Source file on GitHub</a> ·
<a href="{REPO_URL}/issues/new?labels=data-correction&amp;title={issue_title}">Suggest a correction</a> ·
<a href="{REPO_URL}/commits/main/{DATA_PATH_IN_REPO}/projects/{record['id']}.json">Change history</a></p>
<pre><code>{e(citation)}</code></pre>
</section>
"""
    description = f"{record['name']}: {record['summary']}"[:300]
    return page(title=record["name"], description=description, body=body, depth=2,
                path=f"projects/{record['id']}/", base_url=ctx["base_url"],
                current="projects/", scripts=("project.js",), data_as_of=ctx["data_as_of"])


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


FILE_INFO = [
    ("projects.csv", "One row per project with headline values", "CSV"),
    ("statements.csv", "Every sourced quantity with basis, scope, date and source URLs", "CSV"),
    ("milestones.csv", "Dated actual and planned events", "CSV"),
    ("parties.csv", "Organizations and roles", "CSV"),
    ("power_sources.csv", "Reported power supply arrangements", "CSV"),
    ("sources.csv", "Every cited source", "CSV"),
    ("projects.json", "Full records with derived values, as one JSON array", "JSON"),
    ("projects.jsonl", "Full records, one JSON object per line", "JSON Lines"),
    ("projects.geojson", "Project points with headline properties", "GeoJSON"),
    ("datacenters.sqlite", "All tables plus full records and column docs", "SQLite"),
    ("datacenters.xlsx", "All curated tables as worksheets (plus Wikidata layer)", "Excel"),
    ("osm_data_centers.csv", "OpenStreetMap data center features (ODbL)", "CSV"),
    ("osm_data_centers.geojson", "OpenStreetMap data center features (ODbL)", "GeoJSON"),
    ("wikidata_data_centers.csv", "Wikidata data center items (CC0)", "CSV"),
    ("wikidata_data_centers.geojson", "Wikidata data center items (CC0)", "GeoJSON"),
    ("datapackage.json", "Frictionless Data Package: schemas, licenses, hashes", "JSON"),
    ("project.schema.json", "JSON Schema for one curated record", "JSON Schema"),
    ("manifest.json", "Byte counts and SHA-256 for every file", "JSON"),
    ("open-data-center-atlas.zip", "All CSV and JSON files plus README in one archive", "ZIP"),
]


def _data_page(tables, manifest, layer_meta, parquet, ctx) -> str:
    base = ctx["base_url"]
    files = manifest["files"]
    rows_html = []
    info = list(FILE_INFO) + [(name, f"{name[:-8]} table", "Parquet") for name in parquet]
    for name, description, fmt in info:
        if name not in files:
            continue
        table = name.rsplit(".", 1)[0]
        count = manifest["row_counts"].get(table) if name.endswith((".csv", ".parquet")) else None
        rows_html.append(
            f'<tr><td><a href="{e(name)}" download>{e(name)}</a></td><td>{e(fmt)}</td>'
            f"<td>{e(description)}</td><td class=\"num\">{'' if count is None else f'{count:,}'}"
            f'</td><td class="num">{_human_bytes(files[name]["bytes"])}</td>'
            f'<td><code title="{files[name]["sha256"]}">{files[name]["sha256"][:12]}…</code></td></tr>'
        )
    dictionary = []
    for name, (columns, _) in tables.items():
        description, license_info = export.TABLE_INFO[name]
        cols = "".join(f"<tr><td><code>{e(c)}</code></td><td>{e(t)}</td><td>{e(d)}</td></tr>"
                       for c, t, d in columns)
        dictionary.append(
            f'<details><summary><strong>{e(name)}</strong> — {e(description)} '
            f'<span class="tag">{e(license_info["name"])}</span></summary>'
            f'<div class="table-scroll"><table><thead><tr><th>Column</th><th>Type</th>'
            f"<th>Description</th></tr></thead><tbody>{cols}</tbody></table></div></details>"
        )
    layers_html = []
    for name, m in layer_meta.items():
        label = "OpenStreetMap" if name == "osm" else "Wikidata"
        layers_html.append(
            f"<li><strong>{label}</strong>: {m['feature_count']:,} features retrieved "
            f"{e(m['retrieved_at'])} from <code>{e(m['endpoint'])}</code>; raw response SHA-256 "
            f"<code>{e(m['raw_response_sha256'][:16])}…</code>; license "
            f"<a href=\"{e(m['license']['url'])}\">{e(m['license']['id'])}</a>.</li>"
        )
    sqlite_url = base + "data/datacenters.sqlite"
    rules = "".join(f"<li><code>{e(k)}</code>: {e(v)}</li>" for k, v in DERIVATION_RULES.items())
    body = f"""
<div class="lede"><h1>Data &amp; API</h1>
<p>Every file below is static, versioned with the repository, and served with open CORS, so you can
read it straight from a notebook, spreadsheet, GIS tool or web page. No account or key. Data as of
{e(fmt_date(ctx['data_as_of']))} (version {e(manifest['version'])}).</p></div>
<section class="section"><h2>Downloads</h2>
<div class="table-scroll card"><table><thead><tr><th>File</th><th>Format</th><th>Contents</th>
<th class="num">Rows</th><th class="num">Size</th><th>SHA-256</th></tr></thead>
<tbody>{''.join(rows_html)}</tbody></table></div>
<p>Stable URLs: <code>{e(base)}data/&lt;file&gt;</code>. Older versions are in the
<a href="{REPO_URL}/commits/main/datacenters">repository history</a> and in
<a href="{REPO_URL}/releases">tagged releases</a>.</p></section>
<section class="section"><h2>Quick start</h2>
<h3>Python (pandas)</h3>
<pre><code>import pandas as pd
base = "{e(base)}data/"
projects = pd.read_csv(base + "projects.csv")
statements = pd.read_csv(base + "statements.csv")
# All operational power statements, with their sources
statements[(statements.metric == "power_capacity") &amp; (statements.basis == "operational")]</code></pre>
<h3>R</h3>
<pre><code>projects &lt;- read.csv("{e(base)}data/projects.csv")</code></pre>
<h3>DuckDB (SQL over HTTP)</h3>
<pre><code>INSTALL httpfs; LOAD httpfs;
SELECT country, count(*) AS projects, sum(planned_power_mw) AS headline_mw
FROM read_csv_auto('{e(base)}data/projects.csv')
GROUP BY country ORDER BY projects DESC;</code></pre>
<h3>SQL in your browser</h3>
<p><a class="button" href="https://lite.datasette.io/?url={quote(sqlite_url, safe='')}">Open the SQLite database in Datasette Lite</a></p>
<h3>Command line</h3>
<pre><code>curl -O {e(base)}data/datacenters.sqlite
sqlite3 datacenters.sqlite "SELECT name, status, planned_power_mw FROM projects ORDER BY planned_power_mw DESC LIMIT 10;"</code></pre>
<h3>JavaScript</h3>
<pre><code>const {{ projects }} = await fetch("{e(base)}api/v1/projects.json").then(r =&gt; r.json());</code></pre>
<h3>GIS (QGIS, kepler.gl, Felt)</h3>
<p>Add <code>{e(base)}data/projects.geojson</code> as a vector layer by URL.</p>
</section>
<section class="section" id="api"><h2>JSON API</h2>
<p>A read-only, static API. Paths under <code>/api/v1/</code> stay backward compatible; breaking
changes get a new version path.</p>
<div class="table-scroll card"><table><thead><tr><th>Endpoint</th><th>Returns</th></tr></thead><tbody>
<tr><td><a href="../api/v1/index.json"><code>/api/v1/index.json</code></a></td><td>Endpoint catalog, licenses, data date.</td></tr>
<tr><td><a href="../api/v1/projects.json"><code>/api/v1/projects.json</code></a></td><td>All projects with headline values; list fields are arrays.</td></tr>
<tr><td><code>/api/v1/projects/&lt;id&gt;.json</code></td><td>One full record: every statement, milestone, party and source, plus derived values.</td></tr>
<tr><td><a href="../api/v1/stats.json"><code>/api/v1/stats.json</code></a></td><td>Counts by status and country.</td></tr>
</tbody></table></div></section>
<section class="section" id="dictionary"><h2>Data dictionary</h2>
<p>Also machine-readable in <a href="datapackage.json">datapackage.json</a> and the
<code>columns</code> table inside the SQLite file.</p>
{''.join(dictionary)}
</section>
<section class="section"><h2>Headline derivation rules</h2><ul class="prose">{rules}</ul></section>
<section class="section"><h2>Uncurated layers</h2>
<p>These layers add breadth, not verification: they show where mappers and editors have recorded
data centers. They are not build records, carry no status or capacity, and keep their own licenses.</p>
<ul>{''.join(layers_html) or '<li>No layers in this build.</li>'}</ul></section>
<section class="section"><h2>License and citation</h2>
<p>Curated tables: <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>. Please cite
“{e(SITE_TITLE)}, {e(base)}, data as of {e(ctx['data_as_of'])}”. The OpenStreetMap layer is
<a href="https://opendatacommons.org/licenses/odbl/1-0/">ODbL 1.0</a> (© OpenStreetMap
contributors); databases you derive from it must stay ODbL. The Wikidata layer is CC0. Cited
sources keep their own rights; the dataset stores links and paraphrases, not copies.</p></section>
"""
    return page(title="Data & API", description="Download the Open Data Center Atlas as CSV, "
                "JSON, GeoJSON, SQLite, Excel or Parquet, or use the static JSON API.",
                body=body, depth=1, path="data/", base_url=base, current="data/",
                head=_jsonld_dataset(ctx, manifest), data_as_of=ctx["data_as_of"])


def _repo_link(target: str, page_dir: str) -> str:
    """Resolve relative links in repository Markdown to GitHub or site URLs."""
    if target.startswith(("http://", "https://", "#", "mailto:")):
        return target
    path = (Path(DATA_PATH_IN_REPO) / page_dir / target).as_posix()
    parts: list[str] = []
    for part in path.split("/"):
        if part == "..":
            if parts:
                parts.pop()
        elif part not in ("", "."):
            parts.append(part)
    return f"{REPO_URL}/blob/main/{'/'.join(parts)}"


def _markdown_page(path: Path, title: str, site_path: str, ctx: dict, description: str) -> str:
    text = path.read_text(encoding="utf-8")
    first, _, rest = text.partition("\n")
    content = markdown.render(rest, link=lambda u: _repo_link(u, ""), shift=1)
    body = (f'<div class="lede"><h1>{e(first.lstrip("# ").strip())}</h1></div>'
            f'<article class="prose">{content}</article>')
    return page(title=title, description=description, body=body, depth=1, path=site_path,
                base_url=ctx["base_url"], current=site_path, data_as_of=ctx["data_as_of"])


def _about_page(root: Path, ctx: dict) -> str:
    about = markdown.render(
        (root / "ABOUT.md").read_text(encoding="utf-8").partition("\n")[2],
        link=lambda u: _repo_link(u, ""), shift=1)
    contributing = markdown.render(
        (root / "CONTRIBUTING.md").read_text(encoding="utf-8").partition("\n")[2],
        link=lambda u: _repo_link(u, ""), shift=2)
    changelog = markdown.render(
        (root / "CHANGELOG.md").read_text(encoding="utf-8").partition("\n")[2],
        link=lambda u: _repo_link(u, ""), shift=2)
    body = f"""<div class="lede"><h1>About</h1></div>
<article class="prose">{about}
<h2 id="contributing">Contributing</h2>{contributing}
<h2 id="changelog">Changelog</h2>{changelog}</article>"""
    return page(title="About", description="What the Open Data Center Atlas is, how to cite it, "
                "and how to contribute.", body=body, depth=1, path="about/",
                base_url=ctx["base_url"], current="about/", data_as_of=ctx["data_as_of"])


def _llms_txt(base_url: str, data_as_of: str, stats: dict[str, Any]) -> str:
    return f"""# {SITE_TITLE}

> {TAGLINE}. {stats['projects']} curated builds in {stats['countries']} countries, data as of
> {data_as_of}. Every value links to its public source with basis, scope and date. CC BY 4.0.

Prefer the machine-readable files below over scraping HTML.

## Data
- [Projects table (CSV)]({base_url}data/projects.csv): one row per build, headline values
- [Statements (CSV)]({base_url}data/statements.csv): every sourced quantity with basis and scope
- [Full records (JSON)]({base_url}data/projects.json) and [JSON Lines]({base_url}data/projects.jsonl)
- [SQLite database]({base_url}data/datacenters.sqlite)
- [Data Package with column definitions]({base_url}data/datapackage.json)
- [API index]({base_url}api/v1/index.json); one record: {base_url}api/v1/projects/<id>.json

## Rules for using the numbers
- Power figures have a basis (planned, contracted, permitted, under_construction, operational)
  and a scope (it_load, facility, grid_connection, onsite_generation, unspecified). Do not add
  figures with different scopes or bases.
- Blank means not found in a cited source, never zero.
- Investment stays in the reported currency.

## Docs
- [Methodology]({base_url}methodology/)
- [Contributing]({REPO_URL}/blob/main/{DATA_PATH_IN_REPO}/CONTRIBUTING.md)
"""


def _feed(records: list[dict[str, Any]], base_url: str, data_as_of: str) -> str:
    items = sorted(records, key=lambda r: (r["last_reviewed"], r["id"]), reverse=True)[:100]
    entries = "".join(
        f"<entry><title>{e(r['name'])}</title><id>{e(base_url)}projects/{e(r['id'])}/</id>"
        f"<link href=\"{e(base_url)}projects/{e(r['id'])}/\"/>"
        f"<updated>{r['last_reviewed']}T00:00:00Z</updated>"
        f"<summary>{e(STATUS_LABELS[r['status']])}. {e(r['summary'])}</summary></entry>"
        for r in items
    )
    return (f'<?xml version="1.0" encoding="utf-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom">'
            f"<title>{e(SITE_TITLE)}</title><id>{e(base_url)}</id>"
            f'<link href="{e(base_url)}"/><link rel="self" href="{e(base_url)}feed.xml"/>'
            f"<updated>{data_as_of}T00:00:00Z</updated><author><name>{e(CREATOR)}</name>"
            f"</author>{entries}</feed>\n")


def _sitemap(records: list[dict[str, Any]], base_url: str, data_as_of: str) -> str:
    paths = ["", "projects/", "data/", "methodology/", "about/"]
    urls = [f"<url><loc>{e(base_url + p)}</loc><lastmod>{data_as_of}</lastmod></url>"
            for p in paths]
    urls += [f"<url><loc>{e(base_url)}projects/{e(r['id'])}/</loc>"
             f"<lastmod>{r['last_reviewed']}</lastmod></url>" for r in records]
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(urls) + "</urlset>\n")
