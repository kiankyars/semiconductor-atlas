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
from urllib.parse import urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from semiconductor_atlas import manufacturing_projects, public_registry
from semiconductor_atlas.ai_critical import validate_release

RELEASE = ROOT / "web/data/2026-09-07"
MANIFEST_SHA256 = "e71158131075f70b299fae78951da4ba2c98473d4c14f791d63bf7ce47aac044"
TEMPLATE = Path(__file__).with_name("site-template.html")
BASE_URL = "https://kiankyars.github.io/semiconductor-atlas/"
DATACENTER_URL = "https://kiankyars.github.io/datacenter-atlas/"
DOWNLOAD_PATH = "downloads/2026-09-07/"
REPOSITORY_URL = "https://github.com/kiankyars/semiconductor-atlas"
REGISTRY = ROOT / "web/data/registry-2026-07-20"
REGISTRY_MANIFEST_SHA256 = "f006e842958f818ea004d2022b9597c974475ca5f3353c4feea464020b75221a"
REGISTRY_DOWNLOAD_PATH = "downloads/registry-2026-07-20/"
TAIWAN_OUTLINE = Path(__file__).with_name("taiwan_outline.json")
MANUFACTURING = ROOT / manufacturing_projects.RECORDS_DIR
MANUFACTURING_DOWNLOAD_PATH = "downloads/manufacturing/"
COUNTRY_NAMES = {"TW": "Taiwan", "US": "United States", "GB": "United Kingdom", "DE": "Germany",
                 "AT": "Austria", "FR": "France", "IE": "Ireland"}
REGISTRY_SOURCES = {
    "taiwan-moenv-ems": ("Taiwan environmental registry", "MOENV"),
    "taiwan-ida-factory": ("Taiwan registered factories", "IDA"),
    "us-official-semiconductor-awards": ("U.S. CHIPS award pages", "NIST"),
    "eea-industrial-reporting": ("EU industrial reporting", "EEA"),
}
ACTIVITY_COLORS = {  # validated categorical slots 1-3 (all-pairs safe)
    "Integrated circuit manufacturing": "#2a78d6",
    "Semiconductor packaging and test": "#eb6834",
    "Discrete semiconductor devices": "#1baf7a",
}
AWARD_TOPICS = {"recipient": "Recipients", "description": "Summary", "project_type": "Project type",
                "technology": "Technology",
                "direct_funding": "Direct funding", "expected_capex": "Expected investment",
                "jobs": "Jobs", "timeline": "Timeline"}
IDENTIFIER_LABELS = {"moenv_control_number": "MOENV control number",
                     "unified_business_number": "Unified business number",
                     "factory_registration_number": "Factory registration number",
                     "inspire_id": "INSPIRE ID"}
SOURCE_DATE_LABELS = {"taiwan-moenv-ems": "snapshot", "taiwan-ida-factory": "snapshot",
                      "us-official-semiconductor-awards": "page updated",
                      "eea-industrial-reporting": "version 16 published"}
FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="6" fill="#175b49"/>'
    '<rect x="10" y="10" width="12" height="12" rx="1" fill="none" stroke="#fff" stroke-width="2.5"/>'
    "</svg>\n"
)
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
         base_url: str, path: str = "", script: str = "", head: str = "") -> str:
    values = {"PAGE_TITLE": e(title), "DESCRIPTION": e(description),
              "CANONICAL": e(base_url + path), "HOME": e(base_url),
              "BODY": body, "SCRIPT": script, "HEAD": head}
    return re.sub(r"__(PAGE_TITLE|DESCRIPTION|CANONICAL|HOME|BODY|SCRIPT|HEAD)__",
                  lambda match: values[match.group(1)], template)


def dataset_jsonld(features: list[dict], manifest: dict, base_url: str) -> str:
    """schema.org Dataset markup so dataset search engines can index the public bundle.

    No license is declared: the bundle carries source-specific reuse terms in ATTRIBUTION.md.
    """
    records = [feature["properties"] for feature in features]
    downloads = [("facilities.csv", "text/csv"), ("claims.jsonl", "application/jsonl"),
                 ("evidence.jsonl", "application/jsonl"), ("atlas.geojson", "application/geo+json")]
    document = {
        "@context": "https://schema.org/",
        "@type": "Dataset",
        "name": "Semiconductor Atlas",
        "description": (
            "Source-linked records of semiconductor fabs, memory and advanced packaging "
            "facilities and projects. Each record links reported capabilities, milestones and "
            "capacity to dated primary sources; unknown values stay blank. Coverage is selective "
            "and is not a complete global inventory."),
        "url": base_url,
        "sameAs": REPOSITORY_URL,
        "isAccessibleForFree": True,
        "creator": {"@type": "Person", "name": "Kian Kyars"},
        "dateModified": manifest["recorded_at"][:10],
        "temporalCoverage": f"../{manifest['as_of']}",
        "spatialCoverage": [{"@type": "Place", "name": country}
                            for country in sorted({record["country"] for record in records})],
        "keywords": ["semiconductors", "fabs", "advanced packaging", "HBM", "manufacturing"],
        "distribution": [{"@type": "DataDownload", "encodingFormat": media_type,
                          "contentUrl": base_url + DOWNLOAD_PATH + name}
                         for name, media_type in downloads],
    }
    text = json.dumps(document, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
    return f'\n  <script type="application/ld+json">{text}</script>'


def correction_url(record: dict) -> str:
    query = urlencode({"template": "correction.yml", "title": f"Correction: {record['name']}",
                       "record": record["stable_key"]})
    return f"{REPOSITORY_URL}/issues/new?{query}"


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
               base_url: str, registry_summary: str = "",
               cards: tuple[tuple[str, str, str, str], ...] = ()) -> str:
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
    dataset_cards = ('<nav class="dataset-cards" aria-label="Datasets">' + "".join(
        f'<a class="dataset-card" href="{e(href)}"><strong>{e(count)}</strong><span class="card-title">{e(title)} →</span>'
        f'<span>{e(text)}</span></a>' for count, title, text, href in cards) + '</nav>') if cards else ""
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
<h1>Semiconductor facilities</h1><p class="lede">Fabs, memory and packaging plants and the projects building them, with the sources behind every record.</p></div>
<p class="snapshot">Archived evidence as of<strong>{display_date(manifest["as_of"])}</strong></p></section>
{dataset_cards}
<section id="facilities" aria-label="Browse facilities"><div class="section-head"><h2>Evidence-archived facility records</h2><p>Every source archived; every quoted passage hashed</p></div>
<div class="stats" aria-label="Dataset coverage"><div class="stat"><strong>{len(records)}</strong>selected records</div><div class="stat"><strong>{len(countries)}</strong>countries</div><div class="stat"><strong>{len(sources)}</strong>primary sources</div></div>
<div class="filters">
<label>Search facilities<input id="search" type="search" placeholder="Company, location or technology"></label>
<label>Country<select id="country"><option value="">All countries</option>{"".join(option(x,x) for x in countries)}</select></label>
<label>Activity<select id="activity"><option value="">All activities</option>{"".join(option(x,label(x)) for x in activities)}</select></label>
<p class="result-count" id="result-count" role="status">{len(records)} records</p></div>
<div class="table-shell"><table><thead><tr><th scope="col">Facility / project</th><th scope="col">Location</th><th scope="col">Activity</th><th scope="col">Recorded stage</th><th scope="col"><span class="visually-hidden">Details</span></th></tr></thead><tbody>{"".join(rows)}</tbody></table><p id="empty" hidden>No matching facilities. Try another search or filter.</p></div>
<p class="table-note">Stages reflect the dated evidence shown in each record. Coverage includes selected facilities and projects; it is not a complete global inventory.</p></section>
<section class="section" id="registry"><div class="section-head"><h2>Facility registry</h2><p>Registry facts, not verified operations</p></div><p class="lede">{registry_summary} <a href="{e(base_url)}registry/">Browse the registry →</a></p></section>
<section class="section" id="downloads"><div class="section-head"><h2>Download the data</h2><p>Evidence recorded {display_date(manifest["recorded_at"])}</p></div><div class="download-grid">{download_html}</div><div class="text-links">{extra_downloads}</div></section>
<section class="section" id="sources"><div class="section-head"><h2>Sources</h2><p>Company reports, public filings and government disclosures</p></div><div class="source-grid">{"".join(source_html(source) for source in sources)}</div></section>'''


def facility_body(record: dict, claims: list[dict], evidence: dict[str, dict],
                  sources: list[dict], base_url: str, recorded_at: str,
                  manufacturing_link: tuple[str, str] | None = None) -> str:
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
    manufacturing_dd = (f'<dt>Manufacturing project record</dt><dd><a href="{e(manufacturing_link[0])}">'
                        f'{e(manufacturing_link[1])} →</a><span class="secondary">Linked sources, timeline and figures</span></dd>'
                        if manufacturing_link else "")
    return f'''<a class="back" href="{e(base_url)}#facilities">← All facilities</a>
<section class="detail-hero"><p class="eyebrow">{e(record["company"])}</p><h1>{e(record["name"])}</h1><div class="detail-meta"><span>{e(location)}</span><span>{e(label(record["identity_scope"]))}</span></div></section>
<div class="detail-columns"><div><section class="detail-box"><div class="small-label">Recorded stage · {stage_date(record, sources)}</div><span class="badge" data-stage="{e(record["lifecycle_state"])}">{e(label(record["lifecycle_state"]))}</span><p>{e(record["lifecycle_statement"])}</p>{evidence_links(lifecycle_ids, evidence)}</section>
<section class="detail-box"><h2>Production capabilities</h2>{capabilities}</section></div>
<aside><section class="detail-box"><h2>Record details</h2><dl class="fact-list"><dt>Company</dt><dd>{e(record["company"])}</dd><dt>Location</dt><dd>{e(location)}</dd><dt>Scope</dt><dd>{e(label(record["identity_scope"]))}</dd><dt>Evidence recorded</dt><dd>{display_date(recorded_at)}</dd><dt>Capacity, yield and utilization</dt><dd class="empty-value">No supported figures in this record.</dd>{manufacturing_dd}</dl><p><a href="{e(correction_url(record))}">Report a correction ↗</a></p></section></aside></div>
<section class="section"><div class="section-head"><h2>Source documents</h2><p>{len(source_ids)} sources</p></div><div class="source-grid">{source_blocks}</div></section>
<section class="section"><details><summary>All {len(owned_claims)} sourced claims</summary>{"".join(claim_html(claim, evidence) for claim in owned_claims)}</details></section>'''


def award_line(statements: list[dict]) -> str:
    """NIST statements verbatim.

    A statement is left out only when its words appear in another statement of the group and it
    adds no qualifier or amount that statement lacks, so differing NIST wording stays visible.
    """
    def fold(text: str) -> str:
        return " ".join(text.lower().split())

    def contained(small: dict, large: dict) -> bool:
        pattern = r"(?<![\w$,.])" + re.escape(fold(small["text"])) + r"(?![\w])"
        return (fold(small["text"]) != fold(large["text"]) and bool(re.search(pattern, fold(large["text"])))
                and set(small.get("qualifiers", [])) <= set(large.get("qualifiers", []))
                and set(small.get("amounts_usd", [])) <= set(large.get("amounts_usd", [])))

    kept = [s for s in statements if not any(contained(s, other) for other in statements)]
    texts = {}
    for s in kept:
        text = f"“{' / '.join(s['text'].split(chr(10)))}”"
        for flag in s.get("source_anomalies", []):
            text += f" (source release flag: {flag})"
        texts.setdefault(text, None)
    return "; ".join(texts)


def award_html(record: dict) -> str:
    award = record.get("award_statements")
    if not award:
        return ""
    groups: dict[tuple[str, str], list[dict]] = {}
    for statement in award["statements"]:
        groups.setdefault((statement["topic"], statement["scope"]), []).append(statement)
    lines, program = [], []
    for topic, label in AWARD_TOPICS.items():
        if groups.get((topic, "site")):
            lines.append(f"{label} for this site (NIST): {award_line(groups[(topic, 'site')])}")
        if groups.get((topic, "award")):
            lines.append(f"{label} (NIST award page): {award_line(groups[(topic, 'award')])}")
        if groups.get((topic, "page")):
            lines.append(f"{label} (this NIST page, may cover other program sites): "
                         f"{award_line(groups[(topic, 'page')])}")
        if groups.get((topic, "program")):
            program.append(f"{label.lower()} {award_line(groups[(topic, 'program')])}")
    if program:
        pages = award.get("program_member_pages")
        lines.append(f"Shared program across {pages} award pages, not allocated to this site (NIST): "
                     + "; ".join(program))
    return "".join(f'<span class="secondary">{e(line)}</span>' for line in lines)


def listed(record: dict) -> bool:
    """An MOENV record still listed in a control category (in at least one registry variant)."""
    return record["registry_status"].startswith(("Listed for environmental control",
                                                  "Registry variants differ on release"))


def registry_map(records: list[dict]) -> str:
    outline = json.loads(TAIWAN_OUTLINE.read_text(encoding="utf-8"))
    west, east, south, north = 119.95, 122.05, 21.85, 25.4
    scale = 220.0
    shrink = 0.917  # cos(23.5 degrees): keeps east-west distances true at Taiwan's latitude
    width, height = (east - west) * scale * shrink, (north - south) * scale

    def xy(lon: float, lat: float) -> tuple[float, float]:
        return round((lon - west) * scale * shrink, 1), round((north - lat) * scale, 1)

    shapes = []
    for ring in outline["rings"]:
        if not all(west <= lon <= east and south <= lat <= north for lon, lat in ring):
            continue  # outlying islands with no source points (Kinmen)
        shapes.append("M" + "L".join("%s,%s" % xy(lon, lat) for lon, lat in ring) + "Z")
    dots = []
    for record in records:
        if record["latitude"] is None:
            continue
        x, y = xy(record["longitude"], record["latitude"])
        color = ACTIVITY_COLORS[record["registry_classification"]]
        label = (f'{record["name"]} · {record["admin1"]} · registry classification: '
                 f'{record["registry_classification"]} · {record["registry_status"]}')
        style = (f'fill="{color}" fill-opacity="0.82" stroke="#fff" stroke-width="0.7"' if listed(record)
                 else f'fill="#fff" stroke="{color}" stroke-width="1.4"')
        # Dots are a pointer shortcut to table rows; keyboard and screen-reader users use the table.
        dots.append(f'<a href="#{e(anchor(record))}" tabindex="-1" aria-hidden="true" data-dot>'
                    f'<circle cx="{x}" cy="{y}" r="3.6" {style}><title>{e(label)}</title></circle></a>')
    return (f'<svg class="registry-map" viewBox="0 0 {width:.0f} {height:.0f}" role="img" '
            f'aria-label="Map of {len(dots)} Taiwan registry records with source-reported points">'
            f'<path d="{" ".join(shapes)}" fill="#e8ece6" stroke="#b9c4ba" stroke-width="1"/>'
            f'{"".join(dots)}</svg>')


def source_href(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("registry source links must use HTTPS")
    return value


def anchor(record: dict) -> str:
    return "r-" + hashlib.sha256(record["record_id"].encode()).hexdigest()[:12]


REGISTRY_SCRIPT = """<script>
(() => {
  const ids = ['reg-search', 'reg-source', 'reg-country', 'reg-classification'];
  const [search, source, country, classification] = ids.map(id => document.getElementById(id));
  const rows = Array.from(document.querySelectorAll('[data-reg]'));
  const dots = Array.from(document.querySelectorAll('[data-dot]'));
  function filter() {
    const query = search.value.trim().toLocaleLowerCase();
    let count = 0;
    rows.forEach(row => {
      const match = (!query || row.dataset.search.includes(query)) &&
        (!source.value || row.dataset.source === source.value) &&
        (!country.value || row.dataset.country === country.value) &&
        (!classification.value || row.dataset.classification === classification.value);
      row.hidden = !match;
      if (match) count += 1;
    });
    dots.forEach(dot => {
      const row = document.getElementById(dot.getAttribute('href').slice(1));
      dot.style.display = row && row.hidden ? 'none' : '';
    });
    const format = n => n.toLocaleString('en-US');
    document.getElementById('reg-count').textContent = `${format(count)} of ${format(rows.length)} records`;
    document.getElementById('reg-empty').hidden = count !== 0;
  }
  [search, source, country, classification].forEach(control => control.addEventListener('input', filter));
  filter();
})();
</script>"""


def factory_link_text(link: dict) -> list[str]:
    differs = link.get("differs_from_environmental_registry") or []
    missing = link.get("missing_from_environmental_registry") or []
    number = link["unified_business_number"] or "not recorded"
    if "unified_business_number" in differs:
        number += " (differs)"
    elif "unified_business_number" in missing:
        number += " (not in the environmental registry)"
    facts = [f'name {link["name"]}' + (" (differs)" if "name" in differs else ""),
             f"business number {number}"]
    if link.get("address"):
        facts.append(f'address {link["address"]}')
    if link.get("principal_products"):
        facts.append("products " + " / ".join(link["principal_products"]))
    return [f'Registered factory {link["factory_registration_number"]} (reviewed match)',
            "Factory registry: " + "; ".join(facts)]


def date_range(dates: list[str]) -> str:
    first, last = min(dates), max(dates)
    if first == last:
        return display_date(first)
    start = display_date(first)
    if first[:4] == last[:4]:
        start = start.rsplit(" ", 1)[0]
    return f"{start} – {display_date(last)}"


def registry_body(records: list[dict], leads: list[dict], manifest: dict, base_url: str,
                  project_links: dict[str, list[tuple[str, str]]] | None = None) -> str:
    counts = manifest["counts"]
    rows = []
    for record in records:
        family, short = REGISTRY_SOURCES[record["source_family"]]
        location = ", ".join(x for x in (record["locality"], record["admin1"]) if x)
        ids = record["identifiers"]
        id_text = " · ".join(f"{IDENTIFIER_LABELS[k]} {v}" for k, v in ids.items() if v)
        linked = []
        for link in record["linked_records"]:
            if link["relationship"] == "reviewed_same_facility":
                linked.extend(factory_link_text(link))
            elif link["relationship"] == "unreviewed_same_name_and_business_number":
                linked.append(f'Same name and business number as environmental-registry record '
                              f'{link["moenv_control_number"]} (not reviewed as the same facility)')
            elif link["relationship"] == "other_program_locations":
                linked.append("Other program locations: " + ", ".join(link["sites"]))
            elif link["relationship"] == "other_locations_on_award_page":
                linked.append("Other locations on this award page: " + ", ".join(link["sites"]))
            else:
                raise ValueError(f'unknown registry link {link["relationship"]}')
        award = award_html(record)
        classification = record["registry_classification"] or ""
        haystack = " ".join(str(x) for x in (
            record["name"], location, record["address"], record["admin1_local"], classification,
            record["registry_classification_code"], id_text, " ".join(linked),
            " ".join(s["text"] for s in (record["award_statements"] or {}).get("statements", [])),
            COUNTRY_NAMES[record["country_code"]]) if x).lower()
        haystack = " ".join(haystack.split())
        facts = record["registry_status"] or ""
        if record["source_family"] == "us-official-semiconductor-awards":
            facts = f"Application stage (NIST): {facts}"
        if record["latest_reporting_year"]:
            facts_extra = f'<span class="secondary">Latest EEA reporting year {e(record["latest_reporting_year"])}</span>'
        else:
            facts_extra = ""
        dated = f'{SOURCE_DATE_LABELS[record["source_family"]]} {display_date(record["source_date"])}'
        if record["source_family"] == "us-official-semiconductor-awards":
            class_cell = "U.S. CHIPS award project"
        else:
            class_cell = e(" / ".join(classification.splitlines())) + (
                f'<span class="secondary">{e(record["registry_classification_code"])}</span>'
                if record["registry_classification_code"] else "")
        rows.append(
            f'<tr data-reg id="{anchor(record)}" data-search="{e(haystack)}" data-source="{e(record["source_family"])}" '
            f'data-country="{e(record["country_code"])}" data-classification="{e(classification)}">'
            f'<td><span class="facility-name">{e(record["name"])}</span>'
            + (f'<span class="secondary">{e(id_text)}</span>' if id_text else "")
            + "".join(f'<span class="secondary">{e(x)}</span>' for x in linked)
            + "".join(f'<span class="secondary">Manufacturing project record: <a href="{e(url)}">{e(name)}</a></span>'
                      for url, name in (project_links or {}).get(record["record_id"], []))
            + f'</td><td class="location">{e(location)}<span class="secondary">{e(COUNTRY_NAMES[record["country_code"]])}</span>'
            + (f'<span class="secondary">{e(record["address"])}</span>' if record["address"] else "")
            + f'</td><td class="activity">{class_cell}'
            + f'</td><td>{e(facts)}{facts_extra}{award}</td>'
            f'<td><a href="{e(source_href(record["source_url"]))}">{e(short)} ↗</a>'
            f'<span class="secondary">{e(family)} · {e(dated)}</span></td></tr>')
    option = lambda value, text: f'<option value="{e(value)}">{e(text)}</option>'
    countries = sorted({r["country_code"] for r in records}, key=lambda c: COUNTRY_NAMES[c])
    classifications = sorted({r["registry_classification"] for r in records if r["registry_classification"]})
    states = {}
    for lead in leads:
        states[lead["state_code"]] = states.get(lead["state_code"], 0) + 1
    top_states = ", ".join(f"{state} {n:,}" for state, n in
                           sorted(states.items(), key=lambda kv: (-kv[1], kv[0]))[:8])
    downloads = "".join(
        f'<a class="download" href="{e(base_url + REGISTRY_DOWNLOAD_PATH + name)}"><strong>{title} ↓</strong><span>{text}</span></a>'
        for name, title, text in [
            ("records.csv", "Registry records", "CSV · One row per registry record or award project"),
            ("registry_points.geojson", "Taiwan points", "GeoJSON · Source-reported points"),
            ("us_epa_frs_leads.csv", "U.S. EPA leads", "CSV · Unverified registry leads"),
        ])
    extra = "".join(f'<a href="{e(base_url + REGISTRY_DOWNLOAD_PATH + name)}">{text}</a>' for name, text in [
        ("records.jsonl", "Records (JSON Lines)"), ("us_epa_frs_leads.jsonl", "Leads (JSON Lines)"),
        ("ATTRIBUTION.md", "Attribution"), ("README.md", "How to read the registry"),
        ("manifest.json", "File hashes")])
    legend = "".join(
        f'<span class="legend-item"><span class="swatch" style="background:{color}"></span>{e(name)} '
        f'({sum(1 for r in records if r["registry_classification"] == name and r["latitude"] is not None)})</span>'
        for name, color in ACTIVITY_COLORS.items()) + (
        '<span class="legend-item"><span class="swatch" style="background:#5f6f68"></span>Listed in a control category</span>'
        '<span class="legend-item"><span class="swatch" style="background:#fff;border:1.5px solid #5f6f68"></span>'
        'Released from every listed category, or none listed</span>')
    attribution = "".join(
        f'<li>{e(text)} <a href="{e(source_href(manifest["licenses"][family]))}">Terms ↗</a></li>'
        for family, text in manifest["attribution"].items())
    dates = manifest["source_dates"]
    date_list = "; ".join(f"{name} {display_date(dates[family])}" for family, name in (
        ("taiwan-moenv-ems", "Taiwan registry snapshots"),
        ("us-official-semiconductor-awards", "NIST award pages snapshot"),
        ("eea-industrial-reporting", "EEA version 16 published"), ("epa-frs", "EPA FRS data as of")))
    date_list += " (the NIST snapshot finished downloading on 18 July 2026 UTC)"
    moenv = [r for r in records if r["source_family"] == "taiwan-moenv-ems"]
    points = [r for r in moenv if r["latitude"] is not None]
    status_counts = {  # counts of map points, so they match what the map draws
        "listed": sum(1 for r in points if listed(r)),
        "released": sum(1 for r in points if r["registry_status"].startswith("Released from every")),
        "none": sum(1 for r in points if r["registry_status"].startswith("No environmental-control")),
        "differ": sum(1 for r in moenv if any(len(c["variant_states"]) > 1
                                               for c in (r["environmental_control"] or {}).values())),
        "differ_points": sum(1 for r in points if any(len(c["variant_states"]) > 1
                                                      for c in (r["environmental_control"] or {}).values())),
    }
    latest = [max(d for c in r["environmental_control"].values() for d in c["release_date"])[:4]
              for r in points if r["registry_status"].startswith("Released from every")]
    release_years = f"{min(latest)} to {max(latest)}" if latest else "none"
    jump = "".join(f'<a href="#{target}">{text}</a>' for target, text in (
        ("taiwan-map", "Map"), ("registry-table", "Browse"), ("leads", "EPA leads"),
        ("registry-downloads", "Downloads"), ("attribution", "Sources and terms")))
    return f'''<section class="hero"><div><p class="eyebrow">Facility registry</p>
<h1>Semiconductor-coded records in public registries</h1><p class="lede">Every Taiwan environmental-registry record carrying an exact semiconductor industry code, and every factory in the in-production registered-factory roster (status 生產中) carrying the semiconductor product token, with no review of whether each is a semiconductor facility; some are construction works or firms in other trades. Also U.S. CHIPS final-award pages and European industrial-reporting facilities admitted after an AI-assisted scope review. These are registry facts, not verified operations, ownership or capacity, and the counts are registry records rather than a census of plants.</p></div>
<p class="snapshot">Source dates<strong>{e(date_range(list(dates.values())))}</strong></p></section>
<p class="text-links jump-links">{jump}</p>
<div class="stats" aria-label="Registry coverage"><div class="stat"><strong>{counts["taiwan-moenv-ems"]:,}</strong>Taiwan environmental-registry records ({counts["source_reported_points"]:,} with map points)</div><div class="stat"><strong>{counts["taiwan-ida-factory"]:,}</strong>Taiwan registered-factory records without a reviewed match</div><div class="stat"><strong>{counts["us-official-semiconductor-awards"]}</strong>U.S. CHIPS final-award pages</div><div class="stat"><strong>{counts["eea-industrial-reporting"]}</strong>European facility records</div></div>
<p class="table-note">Source dates: {e(date_list)}.</p>
<section class="map-section" id="taiwan-map" aria-label="Taiwan map"><div class="map-frame">{registry_map(records)}</div>
<div class="map-notes"><h2>Taiwan</h2><p>Points are the coordinates the Ministry of Environment registry reports for each registry record, shown only where every registry variant agrees; several records can share one point. Nothing was geocoded. The coastline is a simplified Natural Earth outline, so a few harbour-side sites sit just offshore. Hover a point for its name; select it to jump to its row. Points follow the table filters.</p><p class="table-note">The dataset includes deregistered historical entities. Of the {len(points):,} points, {status_counts["listed"]:,} are filled: records listed in at least one environmental-control category in some registry variant. {status_counts["released"] + status_counts["none"]:,} are rings: records released from every listed category ({status_counts["released"]:,}, latest release dates {release_years}) or with none listed ({status_counts["none"]}). For {status_counts["differ_points"]} of the points ({status_counts["differ"]} of all {len(moenv):,} records) the registry variants differ on a flag or release date, and the row says so. Colour shows the registered MOENV industry code, not what a site does.</p><div class="legend">{legend}</div>
<p class="table-note">{counts["reviewed_identity_links"]} environmental-registry rows link to a registered factory through a reviewed identity match; each link shows the factory's own registered name, business number, address and products, and {counts["reviewed_links_with_differences"]} links have a name or business number that differs or is missing from the environmental registry. Of the {counts["ida_token_261_factories"]} roster factories with the semiconductor token, {counts["ida_token_261_factories"] - counts["taiwan-ida-factory"] - counts["excluded_private_organization_rows"]} appear inside these links and {counts["taiwan-ida-factory"] + counts["excluded_private_organization_rows"]} have no reviewed match; {counts["taiwan-ida-factory"]} appear separately, without a point, and {counts["excluded_private_organization_rows"]} sole proprietorships are withheld. Only environmental-registry rows whose factory number matched the factory roster, exactly or after the documented legacy-certificate normalization, were review candidates, so a factory without a match is not necessarily distinct: {counts["unreviewed_same_name_and_business_number"]} share a name and business number with an environmental-registry record and are flagged, not merged.</p>
<p class="table-note">Reviews named on this page were AI-assisted, not independent human sign-off: a Codex agent decided the <a href="{REPOSITORY_URL}/blob/main/review_plans/2026-07-20-taiwan-moenv-factory-review.json">identity matches</a> (the file's reasons say "manual review"; that was the agent's review), and the <a href="{REPOSITORY_URL}/blob/main/docs/eea_scope_admission_2026-09-07.md">EEA scope admission</a> was a Codex-assisted review.</p></div></section>
<section id="registry-table" aria-label="Browse registry records"><div class="filters">
<label>Search registry<input id="reg-search" type="search" placeholder="Name, place, company or identifier"></label>
<label>Source<select id="reg-source"><option value="">All sources</option>{"".join(option(k, v[0]) for k, v in REGISTRY_SOURCES.items())}</select></label>
<label>Country<select id="reg-country"><option value="">All countries</option>{"".join(option(c, COUNTRY_NAMES[c]) for c in countries)}</select></label>
<label>Registry classification<select id="reg-classification"><option value="">All classifications</option>{"".join(option(a, a) for a in classifications)}</select></label>
<p class="result-count" id="reg-count" role="status">{len(records):,} records</p></div>
<div class="table-shell"><table><thead><tr><th scope="col">Facility / project</th><th scope="col">Location</th><th scope="col">Registry classification</th><th scope="col">Registry facts</th><th scope="col">Source</th></tr></thead><tbody>{"".join(rows)}</tbody></table><p id="reg-empty" hidden>No matching records. Try another search or filter.</p></div>
<p class="table-note">Taiwan names and addresses are reproduced as registered, in Chinese: search 台灣積體電路 rather than TSMC, or use a unified business number or factory registration number. County names, industry codes and status labels are translated. Environmental-control listings and release dates are registry facts, not operation or closure. NIST statements are quoted as published, under the scope of the page section they come from: this site, the award page, or a program shared across award pages. Amounts are never added together and are not evidence of construction or capacity.</p></section>
<section class="section" id="leads"><div class="section-head"><h2>U.S. EPA leads</h2><p>{len(leads):,} unverified registry rows</p></div>
<p class="lede">EPA Facility Registry Service rows filed under semiconductor industry codes (NAICS 334413 or SIC 3674). They are discovery leads only: a registry code does not establish semiconductor production or operation, and they carry no map points. Most common states: {e(top_states)}. {counts["excluded_personal_name_leads"]} rows named after private individuals or residences are omitted.</p></section>
<section class="section" id="registry-downloads"><div class="section-head"><h2>Download the registry</h2><p>Derived from retained, hash-verified registry snapshots</p></div><div class="download-grid">{downloads}</div><div class="text-links">{extra}</div></section>
<section class="section" id="attribution"><div class="section-head"><h2>Sources and terms</h2><p>{counts["excluded_private_organization_rows"]} sole-proprietorship factory rows and {counts["excluded_personal_name_leads"]} EPA leads are omitted for privacy</p></div><ul class="attribution">{attribution}</ul></section>'''


COUNTRY_LABELS = {
    "AT": "Austria", "BE": "Belgium", "CA": "Canada", "CH": "Switzerland", "CN": "China",
    "CR": "Costa Rica", "CZ": "Czech Republic", "DE": "Germany", "ES": "Spain", "FR": "France",
    "GB": "United Kingdom", "HU": "Hungary", "IE": "Ireland", "IL": "Israel", "IN": "India",
    "IT": "Italy", "JP": "Japan", "KR": "South Korea", "MT": "Malta", "MX": "Mexico",
    "MY": "Malaysia", "NL": "Netherlands", "PH": "Philippines", "PL": "Poland", "PT": "Portugal",
    "SE": "Sweden", "SG": "Singapore", "TH": "Thailand", "TW": "Taiwan", "US": "United States",
    "VN": "Vietnam",
}
UNIT_LABELS = {"wafers_per_month": "wafers a month", "wafers_per_year": "wafers a year",
               "units_per_day": "units a day", "units_per_month": "units a month",
               "units_per_year": "units a year", "m2": "m²", "sq_ft": "sq ft",
               "hectares": "hectares", "acres": "acres", "jobs": "jobs"}
QUALIFIER_WORDS = {"exact": "", "approximately": "about ", "up_to": "up to ",
                   "at_least": "at least ", "more_than": "more than ", "range": ""}
APPLIES_LABELS = {"facility": "this facility", "phase": "one phase", "project": "the whole project",
                  "multi_site": "several sites"}
# How this record relates to the linked one, read as "<this record> is <label> <linked record>".
RELATION_LABELS = {"same_campus": "Same campus as", "phase_of": "A phase of", "has_phase": "Has phase",
                   "successor_of": "Successor of", "predecessor_of": "Predecessor of"}
PARTY_LABELS = {"operator": "Operator", "owner": "Owner", "joint_venture_partner": "Joint-venture partner",
                "technology_partner": "Technology partner", "government": "Government", "customer": "Customer",
                "builder": "Builder"}
REVIEW_NOTE = ("Compiled with AI-assisted research (Claude) and checked in a separate AI-assisted review "
               "that re-opened every cited source. Text is written in our own words; check each "
               "statement against its linked source.")


def country_label(code: str) -> str:
    if code not in COUNTRY_LABELS:
        raise ValueError(f"no display name for country code {code}")
    return COUNTRY_LABELS[code]


def period(value: str) -> str:
    start, _, precision = manufacturing_projects.parse_date(value)
    if precision == "day":
        return f"{start.day} {start:%B %Y}"
    if precision == "month":
        return f"{start:%B %Y}"
    if precision == "quarter":
        return f"Q{(start.month - 1) // 3 + 1} {start.year}"
    if precision == "half":
        return f"{'H1' if start.month == 1 else 'H2'} {start.year}"
    return str(start.year)


def number(value: float, unit: str) -> str:
    if unit in manufacturing_projects.CURRENCIES:
        for size, word in ((10**12, "trillion"), (10**9, "billion"), (10**6, "million")):
            if value >= size:
                scaled = f"{value / size:.3f}".rstrip("0").rstrip(".")
                return f"{scaled} {word}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def amount(metric: dict) -> str:
    unit = metric["unit"]
    value = number(metric["value"], unit)
    if metric["qualifier"] == "range":
        low, high = value.split(" "), number(metric["value_max"], unit).split(" ")
        if len(low) == 2 and len(high) == 2 and low[1] == high[1]:
            value = f"{low[0]}–{high[0]} {high[1]}"  # USD 500–750 million
        else:
            value = f"{value}–{' '.join(high)}"
    text = QUALIFIER_WORDS[metric["qualifier"]] + (
        f"{unit} {value}" if unit in manufacturing_projects.CURRENCIES else f"{value} {UNIT_LABELS[unit]}")
    return text


def refs(record: dict, ids: list[str]) -> str:
    order = [source["id"] for source in record["sources"]]
    by_id = {source["id"]: source for source in record["sources"]}
    links = ", ".join(
        f'<a href="#src-{e(x)}" aria-label="Source {order.index(x) + 1}: {e(by_id[x]["publisher"])}">'
        f'{order.index(x) + 1}</a>' for x in sorted(ids, key=order.index))
    return f'<sup class="refs">[{links}]</sup>'


def manufacturing_location(record: dict, *, with_country: bool = True) -> str:
    location = record["location"]
    parts = [location["locality"], location["admin1"]]
    if with_country:
        parts.append(country_label(location["country"]))
    return ", ".join(dict.fromkeys(x for x in parts if x))


def production_text(record: dict, as_of: str | None = None) -> str:
    """Production start, or the current target; a target whose period ended before ``as_of`` is marked."""
    started, planned = manufacturing_projects.production_dates(record)
    if started:
        return "Production since " + period(started)
    if planned:
        passed = as_of and manufacturing_projects.parse_date(planned)[1].isoformat() < as_of
        return "Target " + period(planned) + (" (date passed)" if passed else "")
    return ""


MANUFACTURING_SCRIPT = """<script>
(() => {
  const ids = ['mp-search', 'mp-country', 'mp-status', 'mp-segment'];
  const [search, country, status, segment] = ids.map(id => document.getElementById(id));
  const rows = Array.from(document.querySelectorAll('[data-mp]'));
  function filter() {
    const query = search.value.trim().toLocaleLowerCase();
    let count = 0;
    rows.forEach(row => {
      const match = (!query || row.dataset.search.includes(query)) &&
        (!country.value || row.dataset.country === country.value) &&
        (!status.value || row.dataset.status === status.value) &&
        (!segment.value || row.dataset.segments.split(' ').includes(segment.value));
      row.hidden = !match;
      if (match) count += 1;
    });
    document.getElementById('mp-count').textContent = `${count} of ${rows.length} records`;
    document.getElementById('mp-empty').hidden = count !== 0;
  }
  [search, country, status, segment].forEach(control => control.addEventListener('input', filter));
  filter();
})();
</script>"""


def manufacturing_jsonld(manifest: dict, base_url: str) -> str:
    """schema.org Dataset markup for the manufacturing-project bundle (source terms vary, so no license)."""
    downloads = [("manufacturing_projects.csv", "text/csv"),
                 ("manufacturing_projects.jsonl", "application/jsonl"),
                 ("figures.csv", "text/csv"), ("milestones.csv", "text/csv"), ("sources.csv", "text/csv")]
    document = {
        "@context": "https://schema.org/", "@type": "Dataset",
        "name": "Semiconductor manufacturing projects",
        "description": (
            "Source-linked records of semiconductor wafer fabs, fab expansions and packaging and test "
            "plants announced, built, opened, paused, cancelled or closed since 2020, with status, technology, "
            "investment, incentives, capacity, milestones and the sources behind each statement. "
            "Coverage is a selection, not a census."),
        "url": base_url + manufacturing_projects.PAGE_ROUTE, "sameAs": REPOSITORY_URL,
        "isAccessibleForFree": True, "creator": {"@type": "Person", "name": "Kian Kyars"},
        "dateModified": manifest["as_of"], "temporalCoverage": f"2020-01-01/{manifest['as_of']}",
        "spatialCoverage": [{"@type": "Place", "name": country_label(code)}
                            for code in manifest["counts"]["countries"]],
        "keywords": ["semiconductors", "fabs", "advanced packaging", "CHIPS Act", "manufacturing"],
        "distribution": [{"@type": "DataDownload", "encodingFormat": media_type,
                          "contentUrl": base_url + MANUFACTURING_DOWNLOAD_PATH + name}
                         for name, media_type in downloads],
    }
    text = json.dumps(document, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
    return f'\n  <script type="application/ld+json">{text}</script>'


def manufacturing_body(records: list[dict], manifest: dict, base_url: str) -> str:
    mp = manufacturing_projects
    summary = manifest["counts"]
    rows = []
    for record in records:
        href = base_url + mp.PAGE_ROUTE + record["id"] + "/"
        investment = mp.headline_investment(record)
        nodes = ", ".join(node["label"] for node in record["process_nodes"])
        makes = "<br>".join(e(mp.SEGMENTS[x]) for x in record["segments"])
        haystack = " ".join(" ".join(str(x) for x in (
            record["name"], *record["aliases"], record["company"], record["operator"],
            *(party["name"] for party in record["parties"]), manufacturing_location(record),
            record["location"]["site"] or "", nodes, *(mp.SEGMENTS[x] for x in record["segments"]),
            mp.FACILITY_TYPES[record["facility_type"]], mp.STATUSES[record["status"]])).lower().split())
        timing = production_text(record, manifest["as_of"])
        rows.append(
            f'<tr data-mp data-search="{e(haystack)}" data-country="{e(record["location"]["country"])}" '
            f'data-status="{e(record["status"])}" data-segments="{e(" ".join(record["segments"]))}">'
            f'<td><a class="facility-name" href="{e(href)}">{e(record["name"])}</a>'
            f'<span class="secondary">{e(record["company"])}</span></td>'
            f'<td class="location">{e(manufacturing_location(record, with_country=False))}'
            f'<span class="secondary">{e(country_label(record["location"]["country"]))}</span></td>'
            f'<td class="activity">{makes}'
            + (f'<span class="secondary">{e(nodes)}</span>' if nodes else "") + '</td>'
            f'<td class="stage"><span class="badge" data-stage="{e(record["status"])}">{e(mp.STATUSES[record["status"]])}</span>'
            f'<span class="secondary">as of {e(period(record["status_as_of"]))}</span></td>'
            f'<td class="timing">{e(timing)}'
            + (f'<span class="secondary">{e(amount(investment))} ({e(mp.BASES[investment["basis"]].lower())})</span>'
               if investment else "") + '</td>'
            f'<td><a class="row-link" href="{e(href)}" aria-label="View {e(record["name"])}">↗</a></td></tr>')
    option = lambda value, text: f'<option value="{e(value)}">{e(text)}</option>'
    countries = sorted(summary["countries"], key=country_label)
    by_country = "".join(
        f'<li><span>{e(country_label(code))}</span><strong>{summary["countries"][code]}</strong></li>'
        for code in sorted(summary["countries"], key=lambda c: (-summary["countries"][c], country_label(c))))
    building = summary["statuses"].get("under_construction", 0)
    downloads = "".join(
        f'<a class="download" href="{e(base_url + MANUFACTURING_DOWNLOAD_PATH + name)}"><strong>{title} ↓</strong><span>{text}</span></a>'
        for name, title, text in [
            ("manufacturing_projects.csv", "Projects", "CSV · One row per record with its headline facts"),
            ("manufacturing_projects.jsonl", "Complete records", "JSON Lines · Every statement and its sources"),
            ("figures.csv", "Figures", "CSV · Investment, incentives, capacity, area and jobs"),
        ])
    extra = "".join(f'<a href="{e(base_url + MANUFACTURING_DOWNLOAD_PATH + name)}">{text}</a>' for name, text in [
        ("milestones.csv", "Milestones"), ("sources.csv", "Sources"), ("manifest.json", "File hashes"),
        ("README.md", "Data notes")])
    return f'''<section class="hero"><div><p class="eyebrow">Semiconductor manufacturing</p>
<h1>Fab and packaging projects since 2020</h1><p class="lede">New wafer fabs, fab expansions and packaging plants announced, built, opened, paused, cancelled or closed since 2020. Every statement links to the sources behind it.</p></div>
<p class="snapshot">Sources read<strong>{display_date(manifest["as_of"])}</strong></p></section>
<div class="stats" aria-label="Dataset coverage"><div class="stat"><strong>{summary["records"]}</strong>records</div><div class="stat"><strong>{len(summary["countries"])}</strong>countries</div><div class="stat"><strong>{summary["companies"]}</strong>companies</div><div class="stat"><strong>{building}</strong>under construction</div><div class="stat"><strong>{summary["distinct_source_urls"]:,}</strong>linked sources</div></div>
<section id="projects" aria-label="Browse manufacturing projects"><div class="filters mp-filters">
<label>Search projects<input id="mp-search" type="search" placeholder="Company, place or technology"></label>
<label>Country<select id="mp-country"><option value="">All countries</option>{"".join(option(x, country_label(x)) for x in countries)}</select></label>
<label>Status<select id="mp-status"><option value="">All statuses</option>{"".join(option(x, mp.STATUSES[x]) for x in mp.STATUSES if x in summary["statuses"])}</select></label>
<label>Makes<select id="mp-segment"><option value="">All products</option>{"".join(option(x, mp.SEGMENTS[x]) for x in mp.SEGMENTS if x in summary["segments"])}</select></label>
<p class="result-count" id="mp-count" role="status">{len(records)} records</p></div>
<div class="table-shell"><table><thead><tr><th scope="col">Project</th><th scope="col">Location</th><th scope="col">Makes</th><th scope="col">Status</th><th scope="col">Production and investment</th><th scope="col"><span class="visually-hidden">Details</span></th></tr></thead><tbody>{"".join(rows)}</tbody></table><p id="mp-empty" hidden>No matching projects. Try another search or filter.</p></div>
<p class="table-note">Status is the latest stage the cited sources support, dated by the newest of them. Targets are shown as targets, not achievements. Investment is the most recent figure for the whole facility or project, in the source currency; phase and multi-site amounts are on each record’s page.</p></section>
<section class="section" id="by-country"><div class="section-head"><h2>Records by country</h2><p>Where the recorded projects are</p></div><ul class="country-counts">{by_country}</ul></section>
<section class="section" id="method"><div class="section-head"><h2>How these records are made</h2><p>Linked sources, not archived copies</p></div>
<p class="lede">{e(REVIEW_NOTE)} Primary sources (company announcements and filings, government agencies and regulatory filings) come first; news and trade press corroborate or fill gaps, and each source is labelled by type. {summary["primary_source_citations"]:,} of {summary["source_citations"]:,} citations are primary sources; {summary["press_only_records"]} {"record cites" if summary["press_only_records"] == 1 else "records cite"} no company, government or regulatory source and {"is" if summary["press_only_records"] == 1 else "are"} flagged on {"its page" if summary["press_only_records"] == 1 else "their pages"}.</p>
<p class="lede">Unlike the <a href="{e(base_url)}#facilities">evidence-archived facility records</a>, these records do not keep copies of their sources, so a linked page can change. Coordinates appear only when a cited source states them for the facility; nothing is geocoded. Coverage is a selection, not a census: projects with little public disclosure are under-represented. <a href="{e(REPOSITORY_URL)}/blob/main/manufacturing_projects/README.md">Field definitions and research rules</a> · <a href="{e(REPOSITORY_URL)}/issues/new?template=missing-facility.yml">Suggest a project</a></p></section>
<section class="section" id="manufacturing-downloads"><div class="section-head"><h2>Download the data</h2><p>Generated from the tracked records on every build</p></div><div class="download-grid">{downloads}</div><div class="text-links">{extra}</div></section>'''


def manufacturing_record_body(record: dict, by_id: dict[str, dict], archived: dict[str, str],
                              base_url: str, registry: dict[str, dict] | None = None) -> str:
    mp = manufacturing_projects
    location = record["location"]
    milestones = sorted(record["milestones"], key=lambda m: (mp.parse_date(m["date"])[0], m["planned"]))
    timeline = "".join(
        f'<li><span class="when">{e(period(m["date"]))}</span><div><strong>{e(mp.MILESTONE_EVENTS[m["event"]])}</strong>'
        + (' <span class="badge" data-stage="announced">Target</span>' if m["planned"] else "")
        + (f' <span class="secondary-inline">{e(m["phase_label"])}</span>' if m["phase_label"] else "")
        + refs(record, m["source_ids"])
        + (f'<p>{e(m["note"])}</p>' if m["note"] else "") + '</div></li>'
        for m in milestones)
    figures = "".join(
        f'<li><strong>{e(mp.METRIC_LABELS[m["metric"]])}</strong> {e(amount(m))}{refs(record, m["source_ids"])}'
        f'<span class="secondary">{e(mp.BASES[m["basis"]])} · {e(APPLIES_LABELS[m["applies_to"]])}'
        + (f' ({e(m["phase_label"])})' if m["phase_label"] else "")
        + f' · as of {e(period(m["as_of"]))}</span>'
        + (f'<p>{e(m["note"])}</p>' if m["note"] else "") + '</li>'
        for m in record["metrics"])
    technology = [f'<dt>Makes</dt><dd>{e(", ".join(mp.SEGMENTS[x] for x in record["segments"]))}</dd>']
    if record["process_nodes"]:
        technology.append('<dt>Process technology</dt><dd>' + "<br>".join(
            f'{e(node["label"])}{refs(record, node["source_ids"])}'
            + (f' <span class="secondary-inline">{e(node["note"])}</span>' if node["note"] else "")
            for node in record["process_nodes"]) + '</dd>')
    if record["wafer_sizes"]:
        technology.append('<dt>Wafer size</dt><dd>' + ", ".join(
            f'{size["mm"]} mm{refs(record, size["source_ids"])}' for size in record["wafer_sizes"]) + '</dd>')
    parties = "".join(
        f'<li>{e(party["name"])} · {e(PARTY_LABELS[party["role"]])}'
        + (f' ({party["share_pct"]:g}%)' if party["share_pct"] is not None else "")
        + refs(record, party["source_ids"])
        + (f'<span class="secondary">{e(party["note"])}</span>' if party["note"] else "") + '</li>'
        for party in record["parties"])
    place = [x for x in (location["site"], location["address"]) if x]
    details = [f'<dt>Company</dt><dd>{e(record["company"])}</dd>']
    if record["operator"] != record["company"]:
        details.append(f'<dt>Operator</dt><dd>{e(record["operator"])}</dd>')
    if parties:
        details.append(f'<dt>Parties</dt><dd><ul class="plain">{parties}</ul></dd>')
    details.append(f'<dt>Location</dt><dd>{"<br>".join(e(x) for x in place + [manufacturing_location(record)])}'
                   f'{refs(record, location["source_ids"])}'
                   + (f'<span class="secondary">{e(location["note"])}</span>' if location["note"] else "") + '</dd>')
    if location["lat"] is not None:
        details.append(f'<dt>Coordinates</dt><dd>{location["lat"]:.5f}, {location["lon"]:.5f}'
                       f'{refs(record, location["coordinate_source_ids"])}</dd>')
    else:
        details.append('<dt>Coordinates</dt><dd class="empty-value">None published: no cited source gives a facility point.</dd>')
    details.append(f'<dt>Record scope</dt><dd>{e(mp.IDENTITY_SCOPES[record["identity_scope"]])} · '
                   f'{e(mp.FACILITY_TYPES[record["facility_type"]])}</dd>')
    related = [link for link in record["related"]]
    if related:
        details.append('<dt>Related records</dt><dd>' + "<br>".join(
            f'{e(RELATION_LABELS[link["relation"]])} '
            f'<a href="{e(base_url + mp.PAGE_ROUTE + link["id"] + "/")}">{e(by_id[link["id"]]["name"])}</a>'
            for link in related) + '</dd>')
    if record.get("registry_records"):
        details.append('<dt>Registry records</dt><dd>' + "<br>".join(
            f'<a href="{e(base_url + "registry/#" + anchor(registry[link["record_id"]]))}">'
            f'{e(registry[link["record_id"]]["name"])}</a> <span class="secondary-inline">'
            f'{e(mp.REGISTRY_RELATIONS[link["relation"]])}</span>'
            + (f'<span class="secondary">{e(link["note"])}</span>' if link["note"] else "")
            for link in record["registry_records"]) + '</dd>')
    if record.get("archived_record"):
        details.append(f'<dt>Evidence-archived record</dt><dd><a href="{e(archived[record["archived_record"]])}">'
                       'Archived sources and quoted evidence →</a></dd>')
    primary = sum(source["type"] in mp.PRIMARY_SOURCE_TYPES for source in record["sources"])
    reference = sum(source["type"] == "reference" for source in record["sources"])
    press = len(record["sources"]) - primary - reference
    kinds = [f"{primary} primary", f"{press} news or trade press"] + ([f"{reference} reference"] if reference else [])
    if primary:
        details.append(f'<dt>Source types</dt><dd>{" · ".join(kinds)}</dd>')
    else:
        details.append('<dt>Source types</dt><dd class="press-only">No company, government or regulatory source '
                       f'is cited ({" · ".join(kinds[1:])}).</dd>')
    review = record.get("review")
    if review:
        details.append(f'<dt>Reviewed</dt><dd>{display_date(review["reviewed_on"])}'
                       f'<span class="secondary">{e(review["method"])}</span></dd>')
    query = urlencode({"template": "correction.yml", "title": f"Correction: {record['name']}",
                       "record": "manufacturing:" + record["id"]})
    sources = "".join(
        f'<li id="src-{e(source["id"])}"><a href="{e(source["url"])}">{e(source["title"])} ↗</a>'
        f'<span class="secondary">{e(source["publisher"])} · {e(mp.SOURCE_TYPES[source["type"]])}'
        + (f' · published {e(period(source["published"]))}' if source["published"] else " · undated")
        + f' · read {e(display_date(source["accessed"]))}</span></li>'
        for source in record["sources"])
    notes = f'<section class="detail-box"><h2>Notes</h2><p>{e(record["notes"])}</p></section>' if record.get("notes") else ""
    return f'''<a class="back" href="{e(base_url + mp.PAGE_ROUTE)}#projects">← All manufacturing projects</a>
<section class="detail-hero"><p class="eyebrow">{e(record["company"])}</p><h1>{e(record["name"])}</h1><div class="detail-meta"><span>{e(manufacturing_location(record))}</span><span>{e(mp.FACILITY_TYPES[record["facility_type"]])}</span>{"".join(f"<span>Also known as {e(x)}</span>" for x in record["aliases"][:2])}</div></section>
<div class="detail-columns"><div><section class="detail-box"><div class="small-label">Status · as of {e(period(record["status_as_of"]))}</div><span class="badge" data-stage="{e(record["status"])}">{e(mp.STATUSES[record["status"]])}</span>{refs(record, record["status_source_ids"])}<p>{e(record["summary"])}</p></section>
<section class="detail-box"><h2>Timeline</h2>{f'<ol class="timeline">{timeline}</ol>' if timeline else '<p class="empty-value">No dated milestones recorded.</p>'}</section>
<section class="detail-box"><h2>Figures</h2>{f'<ul class="figure-list">{figures}</ul>' if figures else '<p class="empty-value">No supported figures in this record.</p>'}</section>
<section class="detail-box"><h2>Technology</h2><dl class="fact-list">{"".join(technology)}</dl></section>{notes}</div>
<aside><section class="detail-box"><h2>Record details</h2><dl class="fact-list">{"".join(details)}</dl><p><a href="{e(REPOSITORY_URL + "/issues/new?" + query)}">Report a correction ↗</a></p></section></aside></div>
<section class="section" id="record-sources"><div class="section-head"><h2>Sources</h2><p>{len(record["sources"])} linked sources</p></div><ol class="source-list">{sources}</ol>
<p class="table-note">{e(REVIEW_NOTE)} Numbers in brackets point to these sources.</p></section>'''


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
          legacy_projects: Path | None = None, registry: Path = REGISTRY,
          manufacturing: Path = MANUFACTURING) -> dict:
    base_url = safe_url(base_url)
    if output.exists():
        raise ValueError("output directory must not already exist")
    if hashlib.sha256((release / "manifest.json").read_bytes()).hexdigest() != MANIFEST_SHA256:
        raise ValueError("public release manifest does not match the pinned snapshot")
    manifest = validate_release(release, require_html=True)
    registry_manifest = public_registry.validate(registry, REGISTRY_MANIFEST_SHA256)
    registry_records = public_registry.read_records(registry)
    registry_leads = public_registry.read_records(registry, "us_epa_frs_leads.jsonl")
    counts = registry_manifest["counts"]
    registry_summary = (
        f"{counts['taiwan-moenv-ems']:,} Taiwan environmental-registry records "
        f"({counts['source_reported_points']:,} with source-reported map points) and "
        f"{counts['taiwan-ida-factory']:,} registered-factory records without a reviewed match, "
        f"{counts['us-official-semiconductor-awards']} U.S. CHIPS final-award pages and "
        f"{counts['eea-industrial-reporting']} European industrial-reporting facility records, plus "
        f"{counts['epa-frs']:,} unverified U.S. registry leads.")
    atlas = json.loads((release / "atlas.geojson").read_text(encoding="utf-8"))
    claims = read_rows(release / "claims.jsonl")
    evidence = {row["evidence_id"]: row for row in read_rows(release / "evidence.jsonl")}
    sources = json.loads((release / "source_inputs.json").read_text(encoding="utf-8"))
    template = TEMPLATE.read_text(encoding="utf-8")
    projects = manufacturing_projects.load_records(manufacturing)
    bundle = manufacturing_projects.build_bundle(projects)
    project_manifest = json.loads(bundle["manifest.json"])
    project_counts = project_manifest["counts"]
    archived_urls = {feature["properties"]["stable_key"]:
                     base_url + "facilities/" + slug(feature["properties"]["stable_key"]) + "/"
                     for feature in atlas["features"]}
    archived_links = {}
    for project in projects:
        key = project.get("archived_record")
        if key:
            if key not in archived_urls:
                raise ValueError(f"{project['id']}: unknown archived record {key}")
            archived_links[key] = (base_url + manufacturing_projects.PAGE_ROUTE + project["id"] + "/",
                                   project["name"])
    for code in project_counts["countries"]:
        country_label(code)
    registry_by_id = {row["record_id"]: row for row in registry_records}
    project_links: dict[str, list[tuple[str, str]]] = {}
    for project in projects:
        for link in project.get("registry_records", []):
            if link["record_id"] not in registry_by_id:
                raise ValueError(f"{project['id']}: unknown registry record {link['record_id']}")
            project_links.setdefault(link["record_id"], []).append(
                (base_url + manufacturing_projects.PAGE_ROUTE + project["id"] + "/", project["name"]))
    manufacturing_summary = (
        f"{project_counts['records']} source-linked records of wafer fabs, fab expansions and packaging "
        f"plants announced, built, opened, paused, cancelled or closed since 2020, from "
        f"{project_counts['companies']} companies in {len(project_counts['countries'])} countries: "
        + ", ".join(f"{count} {manufacturing_projects.STATUSES[status].lower()}"
                    for status, count in sorted(project_counts["statuses"].items(),
                                                key=lambda item: list(manufacturing_projects.STATUSES).index(item[0])))
        + ". Each record has its status, technology, figures, milestones and the sources behind them.")
    cards = (
        (f"{project_counts['records']:,}", "Manufacturing projects",
         f"Fabs, fab expansions and packaging plants since 2020 in {len(project_counts['countries'])} "
         "countries, with linked sources and a separate review.", base_url + manufacturing_projects.PAGE_ROUTE),
        (f"{len(atlas['features'])}", "Evidence-archived records",
         "Selected AI-critical facilities whose sources are archived and whose quoted passages are hashed.",
         base_url + "#facilities"),
        (f"{len(registry_records):,}", "Facility registry",
         "Taiwan, U.S. and EU registry records selected by semiconductor codes: registry facts, not verified operations.",
         base_url + "registry/"),
    )
    routes = {
        "index.html": page(template, title="Semiconductor Atlas", description="Explore semiconductor facilities, production capabilities and primary sources.",
                           body=index_body(atlas["features"], sources, manifest, base_url, registry_summary, cards), base_url=base_url, script=FILTER_SCRIPT,
                           head=dataset_jsonld(atlas["features"], manifest, base_url)),
        "404.html": migration_page(template, base_url, "404.html", missing=True),
        "registry/index.html": page(
            template, title="Facility registry | Semiconductor Atlas",
            description=registry_summary, body=registry_body(registry_records, registry_leads, registry_manifest, base_url, project_links),
            base_url=base_url, path="registry/", script=REGISTRY_SCRIPT),
        "manufacturing/index.html": page(
            template, title="Manufacturing projects | Semiconductor Atlas",
            description=manufacturing_summary,
            body=manufacturing_body(projects, project_manifest, base_url), base_url=base_url,
            path=manufacturing_projects.PAGE_ROUTE, script=MANUFACTURING_SCRIPT,
            head=manufacturing_jsonld(project_manifest, base_url)),
    }
    by_id = {project["id"]: project for project in projects}
    for project in projects:
        route = manufacturing_projects.PAGE_ROUTE + project["id"] + "/"
        routes[route + "index.html"] = page(
            template, title=project["name"] + " | Semiconductor Atlas",
            description=f'{project["name"]} ({project["company"]}): status, timeline, figures and sources.',
            body=manufacturing_record_body(project, by_id, archived_urls, base_url, registry_by_id),
            base_url=base_url, path=route)
    for feature in atlas["features"]:
        record = feature["properties"]
        route = "facilities/" + slug(record["stable_key"]) + "/"
        if route + "index.html" in routes:
            raise ValueError("facility URL collision")
        routes[route + "index.html"] = page(
            template, title=record["name"] + " | Semiconductor Atlas",
            description=f'{record["name"]}: location, production capabilities and source documents.',
            body=facility_body(record, claims, evidence, sources, base_url, manifest["recorded_at"],
                               archived_links.get(record["stable_key"])),
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
        if route + "index.html" in routes:
            raise ValueError(f"legacy redirect {route} collides with a page")
        routes[route + "index.html"] = migration_page(
            template, base_url, route, destination_path=destination_path)
    output.mkdir(parents=True)
    shutil.copytree(release, output / DOWNLOAD_PATH)
    shutil.copytree(registry, output / REGISTRY_DOWNLOAD_PATH)
    (output / MANUFACTURING_DOWNLOAD_PATH).mkdir(parents=True)
    for name, data in bundle.items():
        (output / MANUFACTURING_DOWNLOAD_PATH / name).write_bytes(data)
    for route, contents in routes.items():
        target = output / route
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
    (output / ".nojekyll").write_text("", encoding="utf-8")
    (output / "favicon.svg").write_text(FAVICON, encoding="utf-8")
    canonical_paths = (["", "registry/", manufacturing_projects.PAGE_ROUTE]
                       + ["facilities/" + slug(x["properties"]["stable_key"]) + "/" for x in atlas["features"]]
                       + [manufacturing_projects.PAGE_ROUTE + x["id"] + "/" for x in projects])
    sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + "".join(
        f"<url><loc>{e(base_url + path)}</loc></url>" for path in canonical_paths) + "</urlset>\n"
    (output / "sitemap.xml").write_text(sitemap, encoding="utf-8")
    (output / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base_url}sitemap.xml\n", encoding="utf-8")
    return {"facilities": len(atlas["features"]), "sources": len(sources),
            "registry_records": len(registry_records), "registry_leads": len(registry_leads),
            "manufacturing_projects": len(projects),
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
