#!/usr/bin/env python3
"""Publish the semiconductor website from a verified, retained public release."""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from semiconductor_atlas.ai_critical import validate_release

RELEASE = ROOT / "web/data/2026-09-07"
MANIFEST_SHA256 = "e71158131075f70b299fae78951da4ba2c98473d4c14f791d63bf7ce47aac044"
TEMPLATE = Path(__file__).with_name("site-template.html")
BASE_URL = "https://kiankyars.github.io/semiconductor-atlas/"
DATACENTER_URL = "https://kiankyars.github.io/datacenter-atlas/"
DOWNLOAD_PATH = "downloads/2026-09-07/"
LABELS = {
    "leading_edge_logic": "Leading-edge logic", "hbm_fabrication": "HBM fabrication",
    "hbm_packaging": "HBM packaging", "advanced_packaging": "Advanced packaging",
    "advanced_test": "Advanced test", "complete": "Completed", "ramping": "Ramping production",
    "announced": "Announced", "site_preparation": "Site preparation",
    "tools_installing": "Installing equipment", "named_facility": "Individual facility",
    "project_site_scope": "Project site", "site_aggregate": "Multi-plant site",
    "campus_scope": "Multi-plant site",
}


def e(value: object) -> str:
    return html.escape(str(value), quote=True)


def label(value: str) -> str:
    return LABELS.get(value, value.replace("_", " ").capitalize())


def display_date(value: str, precision: str = "day") -> str:
    parsed = date.fromisoformat(value[:10])
    if precision == "year":
        return str(parsed.year)
    if precision == "month":
        return parsed.strftime("%B %Y")
    return f"{parsed.day} {parsed:%B %Y}"


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def stage_date(record: dict, sources: list[dict]) -> str:
    # Month/year source dates use canonical starts in the retained release. Preserve
    # that precision when a stage date is the same source date (for example ASE).
    precisions = {source["published_at_precision"] for source in sources
                  if source["url"] in record["source_urls"]
                  and source["published_at"] == record["lifecycle_as_of"]}
    precision = "year" if "year" in precisions else "month" if "month" in precisions else "day"
    return display_date(record["lifecycle_as_of"], precision)


def safe_url(value: str) -> str:
    parsed = urlsplit(value)
    local_preview = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (parsed.scheme != "https" and not local_preview) or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("base URL must use HTTPS (or loopback HTTP) without a query or fragment")
    return value.rstrip("/") + "/"


def page(template: str, *, title: str, description: str, body: str,
         base_url: str, path: str = "", script: str = "") -> str:
    values = {"PAGE_TITLE": e(title), "DESCRIPTION": e(description),
              "CANONICAL": e(base_url + path), "HOME": e(base_url),
              "BODY": body, "SCRIPT": script}
    return re.sub(r"__(PAGE_TITLE|DESCRIPTION|CANONICAL|HOME|BODY|SCRIPT)__",
                  lambda match: values[match.group(1)], template)


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def evidence_links(ids: list[str], evidence: dict[str, dict]) -> str:
    sources = {evidence[key]["source_id"]: evidence[key] for key in ids}
    return '<div class="evidence-links">' + "".join(
        f'<a href="{e(source["url"])}">{e(source["publisher"])} ↗</a>'
        for source in sources.values()) + "</div>"


def source_html(source: dict, *, evidence_rows: list[dict] | None = None) -> str:
    fragments = "".join(
        f'<details><summary>Evidence location</summary><p>{e(row["locator"])}</p>'
        f'<blockquote><p>{e(row["excerpt"])}</p></blockquote></details>'
        for row in (evidence_rows or []))
    return (
        f'<article class="source" id="source-{e(source["source_id"])}">'
        f'<h3><a href="{e(source["url"])}">{e(source["title"])} ↗</a></h3>'
        f'<p>{e(source["publisher"])} · {display_date(source["published_at"], source["published_at_precision"])}</p>'
        f'{fragments}<details><summary>Attribution &amp; source details</summary>'
        f'<p>{e(source["rights"]["attribution"])}<br>'
        f'Captured {display_date(source["retrieved_at"])}. '
        f'<a href="{e(source["rights"]["license_url"])}">Source terms</a>.</p>'
        f'<p class="rights">{e(source["rights"]["license"])}</p></details></article>'
    )


def claim_html(claim: dict, evidence: dict[str, dict]) -> str:
    precisions = {evidence[key]["published_at_precision"] for key in claim["evidence_ids"]
                  if evidence[key]["published_at"] == claim["valid_from"]}
    precision = "year" if "year" in precisions else "month" if "month" in precisions else "day"
    value = claim["value"]
    if claim["value_kind"] == "scalar":
        description = e(value.get("value", "Unknown"))
    else:
        description = " · ".join(e(value[k]) for k in ("category", "technology", "readiness") if value.get(k))
        if not description:
            description = e(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return (
        '<article class="claim">'
        f'<h3>{e(claim["predicate"].replace(".", " / ").replace("_", " "))}</h3>'
        f'<p>{description}</p><span class="secondary">'
        f'Evidence applies from {display_date(claim["valid_from"], precision)} · '
        f'Recorded {display_date(claim["recorded_at"])}</span>'
        f'{evidence_links(claim["evidence_ids"], evidence)}</article>'
    )


FILTER_SCRIPT = """<script>
(() => {
  const search = document.getElementById('search');
  const country = document.getElementById('country');
  const activity = document.getElementById('activity');
  const rows = Array.from(document.querySelectorAll('[data-record]'));
  function filter() {
    const query = search.value.trim().toLocaleLowerCase();
    let count = 0;
    rows.forEach(row => {
      const match = (!query || row.dataset.search.includes(query)) &&
        (!country.value || row.dataset.country === country.value) &&
        (!activity.value || row.dataset.activities.split(' ').includes(activity.value));
      row.hidden = !match;
      if (match) count += 1;
    });
    document.getElementById('result-count').textContent = `${count} of ${rows.length} records`;
    document.getElementById('empty').hidden = count !== 0;
  }
  [search, country, activity].forEach(control => control.addEventListener('input', filter));
  filter();
})();
</script>"""


def index_body(features: list[dict], sources: list[dict], manifest: dict,
               base_url: str) -> str:
    records = [feature["properties"] for feature in features]
    countries = sorted({record["country"] for record in records})
    activities = sorted({category for record in records for category in record["scope_categories"]})
    rows = []
    for record in records:
        location = ", ".join(dict.fromkeys(x for x in (record["city"], record["admin1"]) if x))
        href = base_url + "facilities/" + slug(record["stable_key"]) + "/"
        haystack = " ".join([record["name"], record["company"], location, record["country"],
                             record["lifecycle_state"], *record["scope_categories"],
                             *(x.get("technology", "") for x in record["capabilities"])]).lower()
        rows.append(
            f'<tr data-record data-search="{e(haystack)}" data-country="{e(record["country"])}" '
            f'data-activities="{e(" ".join(record["scope_categories"]))}">'
            f'<td><a class="facility-name" href="{e(href)}">{e(record["name"])}</a>'
            f'<span class="secondary">{e(label(record["identity_scope"]))}</span></td>'
            f'<td class="location">{e(location)}<span class="secondary">{e(record["country"])}</span></td>'
            f'<td class="activity">{"<br>".join(e(label(x)) for x in record["scope_categories"])}</td>'
            f'<td class="stage"><span class="badge" data-stage="{e(record["lifecycle_state"])}">'
            f'{e(label(record["lifecycle_state"]))}</span><span class="secondary">'
            f'{stage_date(record, sources)}</span></td>'
            f'<td><a class="row-link" href="{e(href)}" aria-label="View {e(record["name"])}">↗</a></td></tr>'
        )
    option = lambda value, text: f'<option value="{e(value)}">{e(text)}</option>'
    downloads = [
        ("facilities.csv", "Facilities", "CSV · Locations, capabilities and recorded stages"),
        ("claims.jsonl", "Sourced claims", "JSON Lines · Every statement and its evidence links"),
        ("evidence.jsonl", "Evidence", "JSON Lines · Sources, dates and quoted passages"),
    ]
    download_html = "".join(
        f'<a class="download" href="{e(base_url + DOWNLOAD_PATH + name)}"><strong>{title} ↓</strong><span>{text}</span></a>'
        for name, title, text in downloads)
    extra_downloads = "".join(
        f'<a href="{e(base_url + DOWNLOAD_PATH + name)}">{text}</a>' for name, text in
        [("atlas.geojson", "GeoJSON"), ("source_inputs.json", "Source metadata"),
         ("manifest.json", "File hashes"), ("ATTRIBUTION.md", "Attribution"), ("METHODOLOGY.md", "Methodology")])
    return f'''<section class="hero"><div><p class="eyebrow">Semiconductor manufacturing</p>
<h1>Semiconductor facilities</h1><p class="lede">Explore fabs, memory and advanced packaging projects, their production capabilities, and the sources behind each record.</p></div>
<p class="snapshot">Data as of<strong>{display_date(manifest["as_of"])}</strong></p></section>
<div class="stats" aria-label="Dataset coverage"><div class="stat"><strong>{len(records)}</strong>selected records</div><div class="stat"><strong>{len(countries)}</strong>countries</div><div class="stat"><strong>{len(sources)}</strong>primary sources</div></div>
<section id="facilities" aria-label="Browse facilities"><div class="filters">
<label>Search facilities<input id="search" type="search" placeholder="Company, location or technology"></label>
<label>Country<select id="country"><option value="">All countries</option>{"".join(option(x,x) for x in countries)}</select></label>
<label>Activity<select id="activity"><option value="">All activities</option>{"".join(option(x,label(x)) for x in activities)}</select></label>
<p class="result-count" id="result-count" role="status">{len(records)} records</p></div>
<div class="table-shell"><table><thead><tr><th>Facility / project</th><th>Location</th><th>Activity</th><th>Recorded stage</th><th><span aria-label="Details"></span></th></tr></thead><tbody>{"".join(rows)}</tbody></table><p id="empty" hidden>No matching facilities. Try another search or filter.</p></div>
<p class="table-note">Stages reflect the dated evidence shown in each record. Coverage includes selected facilities and projects; it is not a complete global inventory.</p></section>
<section class="section" id="downloads"><div class="section-head"><h2>Download the data</h2><p>Evidence recorded {display_date(manifest["recorded_at"])}</p></div><div class="download-grid">{download_html}</div><div class="text-links">{extra_downloads}</div></section>
<section class="section" id="sources"><div class="section-head"><h2>Sources</h2><p>Company reports, public filings and government disclosures</p></div><div class="source-grid">{"".join(source_html(source) for source in sources)}</div></section>'''


def facility_body(record: dict, claims: list[dict], evidence: dict[str, dict],
                  sources: list[dict], base_url: str, recorded_at: str) -> str:
    owned_claims = [claim for claim in claims if claim["subject_stable_key"] == record["stable_key"]]
    lifecycle = [claim for claim in owned_claims if claim["predicate"] == "lifecycle.evidence_summary"]
    lifecycle_ids = list(dict.fromkeys(key for claim in lifecycle for key in claim["evidence_ids"]))
    evidence_ids = list(dict.fromkeys(key for claim in owned_claims for key in claim["evidence_ids"]))
    source_ids = {evidence[key]["source_id"] for key in evidence_ids}
    location = ", ".join(dict.fromkeys(x for x in (record["city"], record["admin1"], record["country"]) if x))
    capabilities = "".join(
        f'<article class="capability"><h3>{e(label(capability["value"]))}</h3>'
        f'<p>{e(capability["technology"])}</p>{evidence_links(capability["evidence_ids"], evidence)}</article>'
        for capability in record["capabilities"])
    source_blocks = "".join(source_html(source, evidence_rows=[evidence[key] for key in evidence_ids
                                                              if evidence[key]["source_id"] == source["source_id"]])
                            for source in sources if source["source_id"] in source_ids)
    return f'''<a class="back" href="{e(base_url)}#facilities">← All facilities</a>
<section class="detail-hero"><p class="eyebrow">{e(record["company"])}</p><h1>{e(record["name"])}</h1><div class="detail-meta"><span>{e(location)}</span><span>{e(label(record["identity_scope"]))}</span></div></section>
<div class="detail-columns"><div><section class="detail-box"><div class="small-label">Recorded stage · {stage_date(record, sources)}</div><span class="badge" data-stage="{e(record["lifecycle_state"])}">{e(label(record["lifecycle_state"]))}</span><p>{e(record["lifecycle_statement"])}</p>{evidence_links(lifecycle_ids, evidence)}</section>
<section class="detail-box"><h2>Production capabilities</h2>{capabilities}</section></div>
<aside><section class="detail-box"><h2>Record details</h2><dl class="fact-list"><dt>Company</dt><dd>{e(record["company"])}</dd><dt>Location</dt><dd>{e(location)}</dd><dt>Scope</dt><dd>{e(label(record["identity_scope"]))}</dd><dt>Evidence recorded</dt><dd>{display_date(recorded_at)}</dd><dt>Capacity, yield and utilization</dt><dd class="empty-value">No supported figures in this record.</dd></dl></section></aside></div>
<section class="section"><div class="section-head"><h2>Source documents</h2><p>{len(source_ids)} sources</p></div><div class="source-grid">{source_blocks}</div></section>
<section class="section"><details><summary>All {len(owned_claims)} sourced claims</summary>{"".join(claim_html(claim, evidence) for claim in owned_claims)}</details></section>'''


def migration_page(template: str, base_url: str, path: str, *,
                   destination_path: str = "", missing: bool = False) -> str:
    destination = DATACENTER_URL + destination_path
    heading = "Looking for data centres?" if missing else "Data Center Atlas has moved."
    intro = ("This address is no longer available. Semiconductor facilities are listed on the home page. "
             "Data centre projects and downloads are now in the Data Center Atlas." if missing else
             "Data centre projects and downloads are now in the Data Center Atlas.")
    body = (f'<section class="move"><p class="eyebrow">Semiconductor Atlas</p><h1>{heading}</h1>'
            f'<p class="lede">{intro}</p><a class="button" href="{e(destination)}">Open Data Center Atlas ↗</a>'
            f'<p><a href="{e(base_url)}">Browse semiconductor facilities</a></p></section>')
    script = "" if missing else f'<script>window.location.replace({json.dumps(destination)});</script>'
    # A real fallback link remains usable with scripting disabled. Unknown routes never auto-redirect.
    rendered = page(template, title=heading + " | Semiconductor Atlas", description=intro,
                    body=body, base_url=base_url, path=path, script=script)
    if not missing:
        rendered = rendered.replace(f'<link rel="canonical" href="{e(base_url + path)}">',
                                    f'<link rel="canonical" href="{e(destination)}">')
    return rendered


def build(output: Path, *, base_url: str = BASE_URL, release: Path = RELEASE,
          legacy_projects: Path | None = None) -> dict:
    base_url = safe_url(base_url)
    if output.exists():
        raise ValueError("output directory must not already exist")
    if hashlib.sha256((release / "manifest.json").read_bytes()).hexdigest() != MANIFEST_SHA256:
        raise ValueError("public release manifest does not match the pinned snapshot")
    manifest = validate_release(release, require_html=True)
    atlas = json.loads((release / "atlas.geojson").read_text(encoding="utf-8"))
    claims = read_rows(release / "claims.jsonl")
    evidence = {row["evidence_id"]: row for row in read_rows(release / "evidence.jsonl")}
    sources = json.loads((release / "source_inputs.json").read_text(encoding="utf-8"))
    template = TEMPLATE.read_text(encoding="utf-8")
    routes = {
        "index.html": page(template, title="Semiconductor Atlas", description="Explore semiconductor facilities, production capabilities and primary sources.",
                           body=index_body(atlas["features"], sources, manifest, base_url), base_url=base_url, script=FILTER_SCRIPT),
        "404.html": migration_page(template, base_url, "404.html", missing=True),
    }
    for feature in atlas["features"]:
        record = feature["properties"]
        route = "facilities/" + slug(record["stable_key"]) + "/"
        if route + "index.html" in routes:
            raise ValueError("facility URL collision")
        routes[route + "index.html"] = page(
            template, title=record["name"] + " | Semiconductor Atlas",
            description=f'{record["name"]}: location, production capabilities and source documents.',
            body=facility_body(record, claims, evidence, sources, base_url, manifest["recorded_at"]),
            base_url=base_url, path=route)
    legacy = {"projects/": "sites/", "data/": "data/", "coverage/": "countries/",
              "about/": "about/", "methodology/": "methodology/"}
    project_root = legacy_projects if legacy_projects is not None else ROOT / "datacenters/projects"
    for path in sorted(project_root.glob("*.json")):
        if not re.fullmatch(r"[a-z0-9-]+", path.stem):
            raise ValueError("unsafe legacy project slug")
        route = "projects/" + path.stem + "/"
        legacy[route] = route
    for route, destination_path in legacy.items():
        routes[route + "index.html"] = migration_page(
            template, base_url, route, destination_path=destination_path)
    output.mkdir(parents=True)
    shutil.copytree(release, output / DOWNLOAD_PATH)
    for route, contents in routes.items():
        target = output / route
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
    (output / ".nojekyll").write_text("", encoding="utf-8")
    canonical_paths = [""] + ["facilities/" + slug(x["properties"]["stable_key"]) + "/" for x in atlas["features"]]
    sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + "".join(
        f"<url><loc>{e(base_url + path)}</loc></url>" for path in canonical_paths) + "</urlset>\n"
    (output / "sitemap.xml").write_text(sitemap, encoding="utf-8")
    (output / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base_url}sitemap.xml\n", encoding="utf-8")
    return {"facilities": len(atlas["features"]), "sources": len(sources),
            "as_of": manifest["as_of"], "recorded_at": manifest["recorded_at"],
            "legacy_redirects": len(legacy), "output": str(output)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args()
    try:
        result = build(args.output, base_url=args.base_url)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
